"""Цена места «вся корзина в одной сети» — из корзины самой сети, когда она есть.

ЗАЧЕМ. Владелец 22.10.2026: «цену места брать из корзины самой сети, где она
прочитана и легла полностью, иначе как сейчас». Наш расчёт знает цену полки, купоны,
снятые телефоном, и кэшбэк карты банка (app/optimizer/calc.py). Чего он не знает —
скидку по карте лояльности, акцию «три по цене двух» и условия доставки по адресу
человека: их знает только сама сеть. Её итог читается с корзины, которую телефон
положил под входом человека (app/carttotal.py), но до сих пор он только подписывался
на экране, а места ранжировались по нашему расчёту.

КОГДА ИТОГ СЕТИ ИДЁТ В ЦЕНУ. Одновременно:
  * итог прочитан у сети, а не посчитан нами (Total.source == NETWORK) и больше нуля;
  * корзина легла ВСЯ: «легло» не меньше, чем позиций в корзине, и нет «не легло» и
    «нет в наличии». Иначе итог сети — цена части корзины, и сравнивать его с полным
    местом нельзя;
  * в корзине сети нет лишнего (Row.extra): иначе в итоге сети лежат ещё и чужие товары;
  * итог не старше срока свежести цены (freshness.max_age_hours): акции сети меняются
    быстрее, чем человек возвращается к экрану, а телефон повторно собирает корзину,
    только когда она изменилась.
Строка сборки при этом уже про ЭТУ корзину и её состав: это проверяет вызывающий
(app/web/phone/cheaper.py, _cabinets). Не сошлось что-то из перечисленного — место
считается как раньше, а причина называется на экране.

ЧТО ВХОДИТ В ЦЕНУ. Итог сети как он написан, без добавок. Замер показал, что это
«к оплате» целиком: товары за вычетом скидок сети, доставка, сборка и упаковка. Магнит
с доставкой 59 ₽ (эмулятор, 18.10.2026): 199 + 59 + 39 = 297, ответ оформления
сошёлся со страницей; Перекрёсток (эмулятор, 23.10.2026): 862,35 − 85 + 0 + 49 = 826,35.
Поэтому доставку мы не досчитываем, а итог сети от итога места с нашей ценой отличается
ещё и сборкой, которой у нашего расчёта нет: место с итогом сети честнее и на эти
39–49 ₽ выглядит дороже соседа с нашей ценой. Способ получения — тот, что выбран в
корзине самой сети (доставка или самовывоз), у каждой сети свой. Из итога вычитается
кэшбэк карты банка — наш прежний (StoreBreakdown.discount): сеть о нём не знает, а
человеку он приходит поверх.

СКОЛЬКО ЗАМЕРЕНО. Только эти две сети. 23.10.2026 на эмуляторе у остальных не было входа
или корзины (Магнит, ВкусВилл, METRO — гость; Пятёрочка — пусто и без адреса), либо сеть
не открылась (Лента — 403, Дикси — «включён ВПН», Самокат — таймаут). Их итог берётся по
тому же правилу, но состав итога у них не проверен.

ЧЕГО ЗДЕСЬ НЕТ. Разбивок на две сети: в корзине сети лежит вся корзина, а не доля, и
итога доли сеть не писала, поэтому разбивка остаётся нашим расчётом. Flask и баз: это
правило, а не экран, и тесты проверяют его без браузера (как app/faster.py).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from app import freshness
from app.carttotal import NETWORK, Total
from app.models import Variant


@dataclass
class Place:
    """Место экрана: вариант расчёта и откуда взята его цена."""

    variant: Variant
    net: Total | None = None    # итог сети, по которому назначена цена; None — цена наша
    cashback: float = 0.0       # кэшбэк карты, вычтенный из итога сети
    why: str = ""               # итог сети есть, но в цену не взят: почему


def check(row, positions: int, now: datetime | None = None) -> tuple[Total | None, str]:
    """Итог сети по строке сборки — или None и причина, почему он не годится в цену.

    Причина пуста, когда сказать нечего: итога сети нет вовсе («не прочитался» и
    «не собирали» экран уже называет в строке сборки).
    """
    total = getattr(row, "total", None)
    if total is None or total.source != NETWORK or total.sum <= 0:
        return None, ""
    landed = len(row.landed or [])
    if landed < positions:
        return None, f"легло {landed} из {positions}"
    if row.missing or row.unknown:
        return None, "часть позиций не легла"
    if row.extra:
        return None, "в корзине сети лежит лишнее"
    at = total.at or row.finished_at or ""
    if not freshness.is_fresh(at, now):
        return None, "устарел"
    return (total if total.at else replace(total, at=at)), ""


def apply(variants, cabinets: dict, positions: int, baseline: float,
          now: datetime | None = None) -> list[Place]:
    """Места экрана с ценой сети там, где она годится. Число и порядок мест прежние.

    `cabinets` — строки сборки ЭТОЙ корзины по коду сети (cheaper._cabinets). Цена сети
    назначается только тогда, когда корзина или доля сплита проверена самой сетью:
    копия с итогом сети, без недостающих позиций и с полной базой экономии.
    """
    out: list[Place] = []
    for variant in variants:
        stores = list(getattr(variant, "stores", None) or [])
        if len(stores) == 1:
            row = cabinets.get(stores[0].store_code)
            total, why = check(row, positions, now) if row is not None else (None, "")
            if total is None:
                out.append(Place(variant, why=why))
                continue
            store = stores[0]
            cashback = float(store.discount or 0.0)
            price = round(max(0.0, total.sum - cashback), 2)
            out.append(Place(
                replace(variant, stores=[replace(store, total=price)], total=price,
                        missing_products=[],
                        baseline=(baseline or variant.baseline) if variant.missing_products
                        else variant.baseline),
                net=total, cashback=cashback))
        elif len(stores) > 1:
            # Этап 7. Разбивка на два магазина: цена сплита берётся из итогов сетей,
            # только если по КАЖДОЙ сети сплита собран проверенный итог её доли корзины.
            updated_stores = []
            net_totals = []
            total_price = 0.0
            total_cashback = 0.0
            all_ok = True
            why_first = ""
            for s in stores:
                s_lines_count = len(getattr(s, "lines", None) or [])
                row = cabinets.get(s.store_code)
                # Для доли сплита:
                # 1) у строки сборки именно столько запланированных позиций (row.planned == s_lines_count > 0);
                # 2) корзина доли легла полностью (check с s_lines_count).
                # Если строка сборки была на всю корзину (row.planned == positions > s_lines_count),
                # то в ней цена всей корзины, а не доли — её в долю брать нельзя.
                if (row is not None and getattr(row, "planned", 0) == s_lines_count
                        and s_lines_count > 0):
                    t, why = check(row, s_lines_count, now)
                    if t is not None:
                        cb = float(s.discount or 0.0)
                        sp = round(max(0.0, t.sum - cb), 2)
                        updated_stores.append(replace(s, total=sp))
                        net_totals.append(t)
                        total_price += sp
                        total_cashback += cb
                        continue
                    elif why and not why_first:
                        why_first = why
                all_ok = False
                break
            if all_ok and len(net_totals) == len(stores):
                comb_net = Total(sum=round(sum(t.sum for t in net_totals), 2),
                                 discount=round(sum(t.discount or 0.0 for t in net_totals), 2),
                                 delivery=round(sum(t.delivery or 0.0 for t in net_totals), 2),
                                 source=NETWORK)
                out.append(Place(
                    replace(variant, stores=updated_stores, total=round(total_price, 2),
                            missing_products=[],
                            baseline=(baseline or variant.baseline) if variant.missing_products
                            else variant.baseline),
                    net=comb_net, cashback=total_cashback))
            else:
                out.append(Place(variant, why=why_first))
        else:
            out.append(Place(variant))
    return out


__all__ = ["Place", "check", "apply"]
