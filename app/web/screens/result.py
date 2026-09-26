"""Результат: где собирать корзину, сколько это стоит и сколько сберегли.

У ЭКРАНА ПОЯВИЛСЯ АДРЕС, И ЭТО МЕНЯЕТ СУТЬ. В Streamlit расчёт лежал в
session_state: попасть на «Результат» можно было только нажав «Рассчитать» на
корзине, ссылку на него дать было нельзя, а обновление вкладки показывало «расчёта
ещё не было». Здесь страница названа корзиной (/result?basket=7) и способом сборки
(&mode=single), поэтому ссылкой на неё можно поделиться, «назад» работает, а две
вкладки — «разделить» и «один магазин» — открываются рядом и не мешают друг другу.

ПОЧЕМУ РАСЧЁТ НЕ ИДЁТ НА КАЖДОЕ ОТКРЫТИЕ. Прежний экран решал это тем, что считал
один раз по нажатию и клал ответ в session_state — рисовал он только готовое.
Смысл сохранён, способ другой: ответ живёт в памяти сервера, привязанный к БАЗЕ
ЧЕЛОВЕКА и к ОТПЕЧАТКУ КОРЗИНЫ. Отпечаток — список «товар × количество»; изменилась
корзина — отпечаток другой, и старый ответ не подойдёт сам собой, без единого
вызова «сбросить кэш» в других экранах. База в ключе обязательна по той же
причине, что и везде в этом переезде: без неё Иван увидел бы расчёт Петра, и
выглядело бы это как свой собственный.

У памяти есть срок (CALC_TTL). Цены меняются, и вчерашний расчёт с сегодняшней
датой — это не «быстро», а тихо неверно; когда срок вышел, считаем заново. Рядом
стоит «Пересчитать» — для случая, когда цены только что обновили руками.

ПОЧЕМУ ССЫЛКА НА КОРЗИНУ МАГАЗИНА — ЭТО КНОПКА, А НЕ ССЫЛКА. Её создаёт сама сеть
по сетевому вызову, и каждый вызов делает НОВУЮ корзину. Рисовать такую ссылку при
показе страницы значило бы ходить к Ленте и ВкусВиллу на каждое открытие
«Результата» — в том числе когда человек просто вернулся назад. Поэтому вызов
происходит по нажатию, а удачный ответ уводит прямо в магазин.

ГДЕ КОНЧАЕТСЯ ПЕРЕДАЧА. Наполненной корзиной. Кнопки «заказать», оплаты и
оформления здесь нет и не будет: это деньги человека, и последнее слово остаётся
за ним — заодно любая наша ошибка в расчёте остаётся видимой ошибкой, а не
списанием. Пока передача идёт, кнопка погашена: второе нажатие положило бы всё в
корзину повторно, а смотреть за ходом — на «Кабинетах», где он живой.

ЧЕГО ЗДЕСЬ НЕТ. Расчёт, оптимизатор, скидки, способы передачи — всё в app/service.py,
app/optimizer.py, app/handover.py и app/cartplan.py. Экран не решает ничего, кроме
того, что показать первым.
"""
from __future__ import annotations

import logging
import time

from flask import redirect, render_template, request

from app import repo
from app.web.screens.basket import num, pct, plural_items, rub, unit_label
from app.web.views import SCREEN_BY_KEY

log = logging.getLogger(__name__)

PATH = SCREEN_BY_KEY["result"].path

# Сколько живёт посчитанное. Десять минут — это «человек ходит по экранам и
# возвращается», но не «человек вернулся вечером»: за вечер цены успевают уехать,
# и показать старую цифру как свежую хуже, чем посчитать заново.
CALC_TTL = 600

_CALC: dict[tuple[str, int], dict] = {}


def _stamp(items) -> tuple:
    """Отпечаток корзины: по нему видно, что расчёт устарел, без общения с экранами."""
    return tuple(sorted((int(i["product_id"]), round(float(i.get("qty") or 0), 3)) for i in items))


def _key(basket_id: int) -> tuple:
    """Ключ свёртка расчёта: чья база, какая корзина и по какому адресу.

    База в ключе — чтобы расчёт одного человека не показался другому как свой.
    АДРЕС в ключе — по той же причине, только про город: адрес определяет цены, а
    значит и ответ. Сменил адрес — прежний ответ к нему не относится, и показать
    его как свежий значило бы соврать цифрой, а не пустотой.
    """
    from app import config
    from app import location as client_place

    try:
        where = client_place.address() or ""
    except Exception:                       # noqa: BLE001 — адрес не повод ронять расчёт
        where = ""
    return (config.db_path(), int(basket_id), where)


def _bundle(basket_id: int, again: bool = False) -> dict:
    """Всё посчитанное по этой корзине: варианты, удобный вариант и сомнения.

    Три расчёта собраны в один свёрток нарочно. Каждый из них проходит по всем
    позициям и всем сетям, и делать их по отдельности «когда понадобится» значило
    бы проходить корзину трижды на каждое открытие страницы.
    """
    from app import config, service

    key = _key(basket_id)
    items = repo.basket_items(basket_id)
    stamp = _stamp(items)

    kept = _CALC.get(key)
    if kept and not again and kept["stamp"] == stamp and time.time() - kept["at"] < CALC_TTL:
        return kept

    # refresh=False: цены в сеть здесь не спрашиваем. Обновление — отдельное,
    # долгое и видимое действие на экране «Корзина»; страница, которая молча
    # уходит в сеть на две минуты, выглядит зависшей.
    variants, baseline = service.calculate(basket_id, False)

    try:
        effortless, _ = service.effortless_variants(basket_id)
    except Exception:  # noqa: BLE001 — сравнение с удобным вариантом не повод ронять экран
        log.warning("удобный вариант не посчитался", exc_info=True)
        effortless = []

    try:
        doubts = service.doubts_summary(basket_id)
    except Exception:  # noqa: BLE001 — проверка достоверности не повод ронять экран
        doubts = {"rows": [], "products": 0, "total": len(items)}

    fresh = {"stamp": stamp, "at": time.time(), "when": time.strftime("%H:%M"),
             "variants": list(variants or []), "baseline": baseline,
             "effortless": list(effortless or []), "doubts": doubts, "positions": len(items)}
    _CALC[key] = fresh
    _forget_oldest()
    return fresh


# Сколько расчётов держим сразу. Срока мало: срок гасит СТАРОЕ, а память копится
# от РАЗНООБРАЗИЯ — корзин у семьи много, людей на сервере несколько, и без потолка
# сервер, живущий неделями, тащил бы за собой каждый расчёт, который кто-то открыл.
KEEP_CALCS = 32


def _forget_oldest() -> None:
    while len(_CALC) > KEEP_CALCS:
        _CALC.pop(min(_CALC, key=lambda key: _CALC[key]["at"]), None)


def _size(variant) -> int:
    return len(getattr(variant, "stores", None) or [])


def _figures(variant, baseline: float) -> dict:
    total = float(getattr(variant, "total", 0.0) or 0.0)
    base = float(getattr(variant, "baseline", None) or baseline or 0.0)
    saved = float(getattr(variant, "savings_rub", base - total) or 0.0)
    share = float(getattr(variant, "savings_pct", (saved / base * 100) if base else 0.0) or 0.0)
    # Доля оплаченного — ширина тёмной части полосы. Остальное зелёное: зелёный в
    # этом интерфейсе означает ровно одно — выгоду.
    paid = 100.0 if base <= 0 else max(0.0, min(100.0, total / base * 100.0))
    return {"total": total, "base": base, "saved": saved, "pct": share,
            "paid_width": round(paid, 2), "saved_width": round(max(0.0, 100.0 - paid), 2)}


def _chosen(variants, mode: str | None):
    """Какой вариант показан крупно. Способ сборки берём из адреса, а не из памяти.

    Когда способа два — они и есть весь выбор, и переключение между ними обязано
    быть ссылкой: человек сравнивает их, открыв рядом, и «назад» должно возвращать
    к предыдущему сравнению, а не к предыдущему экрану.
    """
    split = next((v for v in variants if _size(v) > 1), None)
    single = next((v for v in variants if _size(v) == 1), None)
    if not split or not single:
        return variants[0], None
    if mode not in ("split", "single"):
        mode = "split" if split.total <= single.total else "single"
    switch = {"split": {"label": f"Разделить · {rub(split.total)}", "on": mode == "split"},
              "single": {"label": f"Один магазин · {rub(single.total)}", "on": mode == "single"}}
    return (split if mode == "split" else single), switch


def _units() -> dict[int, str]:
    """product_id -> единица. У строки варианта единицы нет, а весовые нужны в кг."""
    try:
        return {p.id: p.unit for p in repo.list_products(active_only=False) if p.id}
    except Exception:  # noqa: BLE001 — подпись единиц не повод ронять экран
        return {}


def _going(code: str) -> dict | None:
    """Идёт ли передача корзины в эту сеть прямо сейчас.

    Нужно ровно затем, чтобы не дать нажать второй раз: передача идёт минутами, и
    вторая положила бы человеку всё в корзину повторно. Смотреть за ней сюда не
    зовём — живой ход живёт на «Кабинетах», и рисовать его в двух экранах значило
    бы держать два опроса и два описания одного и того же.
    """
    from app.shopbrowser import cart

    if not cart.running(code):
        return None
    got = cart.progress(code) or {}
    total = int(got.get("total") or 0)
    at = int(got.get("at") or 0)
    return {"at": min(at, total) if total else at, "total": total,
            "now": got.get("now") or ""}


def _store_card(store, units: dict[int, str], with_handover: bool, trouble: str | None) -> dict:
    """Одна сеть в варианте: чем платим, что берём и как это ей отдать."""
    from app import cartplan
    from app import handover as ho
    from app.shopbrowser import cart

    code = getattr(store, "store_code", None) or ""
    lines = getattr(store, "lines", None) or []
    kind = ho.KIND_BY_STORE.get(code, ho.LIST)

    card = {
        "code": code,
        "name": getattr(store, "store_name", None) or code or "Магазин",
        "card_name": getattr(store, "card_name", None),
        "subtotal": getattr(store, "subtotal", 0.0),
        "delivery": getattr(store, "delivery", 0.0),
        "discount": getattr(store, "discount", 0.0),
        "total": getattr(store, "total", 0.0),
        "below_min": bool(getattr(store, "below_min_order", False)),
        "lines": [{"name": getattr(ln, "product_name", "—"),
                   "qty": getattr(ln, "qty", 0),
                   "unit": unit_label(units.get(getattr(ln, "product_id", None))),
                   "price": getattr(ln, "price", 0.0)} for ln in lines],
        "kind": kind,
        "asks_link": kind == ho.LINK,
        "trouble": trouble == code,
        "handover": [],
        "text": "",
        "note": ho.NOTE_BY_STORE.get(code) or ho.NOTE_BY_KIND.get(kind, ""),
        "plan": None,
        # Пока передача идёт, кнопка обязана быть погашена: второе нажатие кладёт
        # всё в корзину человека второй раз.
        "going": _going(code) if (with_handover and kind != ho.LINK) else None,
    }

    if not lines or not with_handover:
        return card

    # К сети, отдающей корзину одной ссылкой, при показе страницы НЕ ходим: ссылку
    # создаёт сетевой вызов, и он делается по нажатию. Остальные способы — карточки
    # товаров и список — сетью не пахнут и собираются прямо здесь.
    if kind != ho.LINK or card["trouble"]:
        try:
            plan = ho.for_store(code, lines)
            card["handover"] = [{"name": i.name, "qty": i.qty, "unit": unit_label(i.unit),
                                 "url": i.url} for i in plan.items]
            card["text"] = ho.as_text(plan)
            card["note"] = plan.note
            card["open_label"] = "открыть →" if plan.kind == ho.ITEMS else "найти →"
        except Exception:  # noqa: BLE001 — способ передачи не повод ронять экран
            log.warning("способ передачи для %s не собрался", code, exc_info=True)

    # Наряд — ступень передачи для сетей, куда корзину кладёт наш браузер.
    # Кнопки «заказать» здесь нет и не будет: наряд кончается наполненной корзиной,
    # заказ оформляет человек своим аккаунтом и своей картой.
    can_auto_cart = (kind == ho.LINK)
    if kind != ho.LINK:
        try:
            plan = cartplan.build(code, lines, force=True)
            if plan.lines:
                can_auto_cart = True
                # МОЖНО ЛИ ВООБЩЕ НАПОЛНИТЬ ЭТУ КОРЗИНУ — ВОПРОС ОТДЕЛЬНЫЙ ОТ ТОГО,
                # СОБРАЛСЯ ЛИ НАРЯД. Наряд собирается у любой сети, где есть
                # сопоставления: он просто список «артикул, сколько». А наполняет
                # его браузер, и до витрины Ленты, Дикси, Пятёрочки и Самоката он
                # не доходит вовсе — сети не пускают наш сервер по адресу (замер
                # 19.09.2026, см. store_accounts.ABILITIES). METRO не принимает
                # запись без именного токена.
                #
                # Пока это не различалось, экран рисовал кнопку «Передать» у
                # каждой сети с наряду — то есть кнопку, которая может только не
                # сработать. Список показываем по-прежнему (человеку полезно
                # видеть, что именно уехало бы), а кнопку гасим и говорим почему.
                #
                # Причину составляет cartplan, а не экран: препятствий два —
                # сеть и точка, — и совет у них разный (20.09.2026 добавилось
                # второе: магазин формата «у дома мини» вообще не имеет
                # интернет-витрины). Экран здесь по-прежнему ничего не решает.
                card["plan_why_not"] = cartplan.why_not(code)
                # «Округлено» рядом с позицией, а не общей сноской внизу: 0,7 кг
                # сыра превращаются в одну упаковку у КОНКРЕТНОГО товара, и
                # человек должен видеть это до передачи, а не потом в чеке.
                card["plan"] = {"lines": [{"name": ln.name, "qty": ln.qty,
                                           "unit": unit_label(ln.unit), "price": ln.price,
                                           "rounded": cart.rounding(ln.qty, ln.unit)}
                                          for ln in plan.lines],
                                "unknown": list(plan.unknown), "total": plan.total,
                                "note": plan.note,
                                "can_fill": not card.get("plan_why_not"),
                                "why_not": card.get("plan_why_not") or ""}
        except Exception:  # noqa: BLE001 — наряд не повод ронять экран
            log.warning("наряд для %s не собрался", code, exc_info=True)

    card["can_auto_cart"] = can_auto_cart
    return card


def _effortless_note(bundle: dict, best) -> dict | None:
    """Во что обходится НЕ перебивать корзину руками.

    Самый дешёвый вариант и самый удобный совпадают редко: одной ссылкой корзину
    принимают только сети, давшие такой инструмент, в остальных человек кладёт
    товары по одному. Разница между двумя итогами и есть цена перебивания —
    показываем её прямо, а решает человек: полчаса бывают дороже трёхсот рублей,
    а бывает наоборот.
    """
    easy = (bundle.get("effortless") or [None])[0]
    if easy is None or best is None:
        return None
    if {s.store_code for s in easy.stores} == {s.store_code for s in best.stores}:
        return {"same": True}
    diff = round(easy.total - best.total, 2)
    return {"same": False, "title": easy.title, "total": easy.total,
            "diff": abs(diff), "dearer": diff > 0,
            "missing": len(easy.missing_products), "best_title": best.title}


def page():
    if request.method == "POST":
        return _act()

    basket_id = request.args.get("basket", type=int)
    baskets = repo.list_baskets()
    if not basket_id:
        basket_id = int(baskets[0]["id"]) if baskets else None
    basket = next((b for b in baskets if int(b["id"]) == (basket_id or 0)), None)

    if basket is None:
        # Пустое состояние объясняет, что сделать, а не сообщает об отсутствии.
        return render_template("result.html", screen=SCREEN_BY_KEY["result"], basket=None,
                               basket_path=SCREEN_BY_KEY["basket"].path,
                               rub=rub, num=num, pct=pct, plural_items=plural_items)

    return _draw(basket, _bundle(int(basket["id"])),
                 mode=request.args.get("mode"), trouble=request.args.get("trouble"))


def _draw(basket: dict, bundle: dict, mode: str | None, trouble: str | None, plan=None):
    """Нарисовать результат. Отдельно от page(), потому что сюда приходят и с POST.

    Неудачная попытка получить ссылку на корзину показывает ту же страницу со
    списком позиций вместо кнопки — перевод на адрес потерял бы единственное, что
    в этот момент есть: уже собранный запасной список.
    """
    variants = bundle["variants"]
    if not variants:
        return render_template("result.html", screen=SCREEN_BY_KEY["result"], basket=basket,
                               bundle=bundle, variants=[], best=None,
                               basket_path=SCREEN_BY_KEY["basket"].path,
                               products_path=SCREEN_BY_KEY["products"].path,
                               rub=rub, num=num, pct=pct, plural_items=plural_items)

    best, switch = _chosen(variants, mode)
    units = _units()
    others = [v for v in variants if v is not best]

    return render_template(
        "result.html",
        screen=SCREEN_BY_KEY["result"],
        basket=basket, bundle=bundle, switch=switch, mode=mode,
        best={"variant": best, "figures": _figures(best, bundle["baseline"]),
              "stores": [_store_card(s, units, True, trouble)
                         for s in (getattr(best, "stores", None) or [])],
              "penalty": float(getattr(best, "penalty", 0.0) or 0.0),
              "missing": list(getattr(best, "missing_products", None) or [])},
        others=[{"n": n, "variant": v, "figures": _figures(v, bundle["baseline"]),
                 "stores": [_store_card(s, units, False, None)
                            for s in (getattr(v, "stores", None) or [])],
                 "missing": list(getattr(v, "missing_products", None) or [])}
                for n, v in enumerate(others, start=2)],
        effortless=_effortless_note(bundle, best),
        doubts=bundle["doubts"],
        variants=variants,
        basket_path=SCREEN_BY_KEY["basket"].path,
        products_path=SCREEN_BY_KEY["products"].path,
        rub=rub, num=num, pct=pct, plural_items=plural_items, unit_label=unit_label,
    )


def _act():
    """Нажатия экрана: собрать корзину в магазине и пересчитать."""
    from app import handover as ho

    basket_id = request.form.get("basket", type=int)
    mode = request.form.get("mode") or None
    do = request.form.get("do") or ""
    what, _, target = do.partition(":")

    if not basket_id:
        return redirect(PATH)

    if what == "again":
        _bundle(basket_id, again=True)
        return redirect(f"{PATH}?basket={basket_id}" + (f"&mode={mode}" if mode else ""))

    if what == "link" and target:
        basket = next((b for b in repo.list_baskets() if int(b["id"]) == basket_id), None)
        bundle = _bundle(basket_id)
        best, _switch = _chosen(bundle["variants"], mode) if bundle["variants"] else (None, None)
        lines = next((s.lines for s in (getattr(best, "stores", None) or [])
                      if s.store_code == target), None)
        if lines:
            from app import cartplan
            kind = ho.KIND_BY_STORE.get(target, ho.LIST)
            if kind == ho.LINK:
                # 1. Прямая ссылка сети на готовую корзину (Лента, ВкусВилл)
                try:
                    plan = ho.for_store(target, lines)
                    if plan is not None and plan.link:
                        return redirect(plan.link)
                except Exception:  # noqa: BLE001 — магазин недоступен, это не повод ронять экран
                    log.warning("корзину по прямой ссылке в %s собрать не вышло", target, exc_info=True)
            else:
                # 2. Браузер на нашем сервере под сохранённым входом человека
                #    (Магнит, Пятёрочка, Самокат, Дикси). Никуда его не уводим:
                #    передача идёт фоном, а смотреть за ней — на «Кабинетах».
                from app.shopbrowser import cart
                from app.shopbrowser import store as shopstore
                from app.web import auth

                try:
                    cp = cartplan.build(target, lines, force=True, verify_prices=True)
                    if cp.lines:
                        if shopstore.load(target) is None:
                            # Класть некуда, пока человек не вошёл. Ведём туда, где
                            # входят, — человеку нужен следующий шаг, а не диагноз.
                            return redirect(f"/cabinet?store={target}")
                        # Пускатель сам не пустит вторую передачу в ту же сеть, и
                        # ответ «не пустил» надо донести: молча увести на «пошла»
                        # значило бы соврать человеку, который нажал дважды.
                        if not cart.start(target, auth.current_phone() or "", cp):
                            return redirect("/accounts?busy=" + target)
                        return redirect("/accounts?sent=" + target)
                except Exception:  # noqa: BLE001
                    log.warning("наряд для %s не собрался", target, exc_info=True)

        # Ссылки нет и наряд пуст — показываем ту же страницу с запасным списком позиций.
        if basket is not None:
            return _draw(basket, bundle, mode=mode, trouble=target)

    return redirect(f"{PATH}?basket={basket_id}" + (f"&mode={mode}" if mode else ""))


# Действия живут на адресе экрана (см. такой же приём в app/web/screens/basket.py).
page.methods = ("GET", "POST")

__all__ = ["page"]
