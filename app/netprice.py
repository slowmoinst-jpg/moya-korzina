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
    назначается только месту из одной сети: копия с итогом сети, без недостающих
    позиций (сеть положила все) и с полной базой экономии. Строки, цены полки и штрафы
    копии прежние; subtotal/delivery/discount в ней остаются нашими, верить нужно
    total и итогу сети в Place.net. Исходные варианты не меняются: они лежат в кэше
    расчёта и переживают смену итога сети.
    """
    out: list[Place] = []
    for variant in variants:
        stores = list(getattr(variant, "stores", None) or [])
        row = cabinets.get(stores[0].store_code) if len(stores) == 1 else None
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
    return out


__all__ = ["Place", "check", "apply"]
