"""Приложение на телефоне: четыре экрана, выбранные владельцем 17.10.2026.

ЧТО ЭТО. Владелец увидел приложение так: «максимально просто» — Настройки, Корзина,
История заказов и «Где дешевле», и больше ничего (docs/design/ekrany-telefona-2026-10-17.md,
макеты — канва «Моя корзина — приложение на телефоне»). Веб на компьютере остаётся
прежним, со всеми экранами: это вторая раскладка ТЕХ ЖЕ данных, а не второе
приложение. Корзина, заказы, расчёт и передача корзины в магазин берутся у веба
как есть — здесь только то, что рисует телефон.

ЖИВЁТ ПО АДРЕСАМ /phone/…, а приложение на телефоне (метка KorzinaApp в User-Agent,
app/web/device.py) попадает сюда с главной само. Полная версия открывается ссылкой
«Полная версия» из Настроек — экраны веба никуда не делись, и к ним нужен ход, пока
телефонная часть умеет не всё.

КАКАЯ КОРЗИНА «ТЕКУЩАЯ». Самая свежая — та же, что берут «Результат» без номера и
«Собрать» в приложении (app/phonecart.py). Возьми телефон «последнюю открытую», как
веб-корзина, — и при открытой в вебе старой корзине телефон считал бы одно, а клал
в магазин другое. «Повторить» заводит новую корзину, и она становится свежей.

ПЛОТНОСТЬ. «Элементы экрана по сравнению с текстом очень большие» (владелец, 17.10):
стиль — app/web/static/phone.css, свой, не korzina.css.
"""
from __future__ import annotations

import json
import logging

from flask import Blueprint, g, redirect, render_template, request, url_for

from app import repo
from app.web import device
from app.web.auth import needs_phone

log = logging.getLogger(__name__)

bp = Blueprint("phone", __name__, url_prefix="/phone")

# «Где?» — для «Всё во ВкусВилле», «Заказать в Магните». Падеж у названий сетей свой,
# и собирать его правилом значило бы однажды написать «в ВкусВилле».
WHERE = {
    "magnit": "в Магните", "vkusvill": "во ВкусВилле", "pyaterochka": "в Пятёрочке",
    "lenta": "в Ленте", "dixy": "в Дикси", "samokat": "в Самокате",
    "metro": "в METRO", "perekrestok": "в Перекрёстке",
}

# «Кого?» — для «Подключить Пятёрочку». Остальные названия не склоняются.
WHOM = {"pyaterochka": "Пятёрочку", "lenta": "Ленту"}

# Сколько товаров заказа видно сразу; остальное — «Ещё N товаров» (вариант Б).
SHOWN_LINES = 5

# Сколько заказов на странице истории; дальше — «Показать ещё».
ORDERS_PAGE = 20

# Какой заказ сейчас лежит в корзине: {"key": ключ заказа, "basket": номер корзины}.
# Нужен ровно для пометки «В корзине» в истории — по одному имени корзины этого не
# понять: две покупки в один день и в одной сети назывались бы одинаково.
REPEATED = "phone:repeated"


# ---------- слова и числа ----------
def where(code: str | None, name: str | None = None) -> str:
    return WHERE.get(code or "") or f"в {name or code or 'магазине'}"


def plural(n: int, one: str, few: str, many: str) -> str:
    tail = n % 100
    if 11 <= tail <= 14:
        return many
    last = n % 10
    return one if last == 1 else few if 2 <= last <= 4 else many


def goods(n: int) -> str:
    return f"{n} {plural(n, 'товар', 'товара', 'товаров')}"


def deliveries(n: int) -> str:
    return f"{n} {plural(n, 'доставка', 'доставки', 'доставок')}"


def qty_label(qty, unit: str | None) -> str:
    from app.web.screens.basket import num, unit_label

    return f"{num(qty)} {unit_label(unit)}"


def _step(unit: str | None) -> float:
    """Шаг «−» и «+»: штука для штучного, сто граммов для весового — как в веб-корзине."""
    return 0.1 if (unit or "pcs") == "kg" else 1.0


def _units() -> dict[int, str]:
    try:
        return {p.id: p.unit for p in repo.list_products(active_only=False) if p.id}
    except Exception:  # noqa: BLE001 — подпись единиц не повод ронять экран
        return {}


def _basket() -> dict | None:
    """Текущая корзина — самая свежая (см. «Какая корзина текущая» вверху файла)."""
    baskets = repo.list_baskets()
    return baskets[0] if baskets else None


def _repeated() -> dict:
    try:
        data = json.loads(repo.get_setting(REPEATED) or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _render(template: str, **values):
    from app.web.screens.basket import rub

    return render_template(f"phone/{template}", rub=rub, goods=goods, where=where,
                           qty_label=qty_label, deliveries=deliveries, **values)


# ---------- Корзина ----------
@bp.get("/")
@needs_phone
def home():
    return redirect(url_for("phone.basket"))


@bp.route("/basket", methods=("GET", "POST"))
@needs_phone
def basket():
    current = _basket()
    if request.method == "POST":
        return _change(current)
    items = repo.basket_items(int(current["id"])) if current else []
    rows = [{"pid": int(i["product_id"]), "name": i.get("name") or "—",
             "unit": i.get("unit") or "pcs", "qty": float(i.get("qty") or 0)} for i in items]
    return _render("basket.html", tab="basket", basket=current, items=rows,
                   runs=_runs(current) if rows else None)


@bp.get("/basket/runs")
@needs_phone
def basket_runs():
    """Строки «Корзина в магазинах» для опроса, пока телефон собирает: кусок страницы и признак хода."""
    runs = _runs(_basket())
    return {"html": render_template("phone/_runs.html", runs=runs, rub=_rub()),
            "going": runs["going"], "stamp": runs["stamp"]}


def _rub():
    from app.web.screens.basket import rub

    return rub


# ---------- корзина в магазинах: сборка телефона ----------
def _runs(current: dict | None) -> dict:
    """Корзина в магазинах: строка на сеть — идёт ли сборка, что легло и итог сети.

    ЗАЧЕМ ЗДЕСЬ. Владелец 17.10.2026: человек остаётся в нашем приложении и видит итог
    каждой сети со всеми акциями. Пока открыта эта страница, приложение само собирает
    корзину во всех сетях, где человек вошёл (скрытое окно на сеть, android/app), и ход
    сборки приходит в строки «Сборки корзин» (app/cartrun.py). Здесь те же строки,
    только этой корзины и без лишних слов: «собираю 2 из 5», «легло 4 из 5 · 1 234 ₽».

    КОМУ СОБИРАТЬ, РЕШАЕТ ПРИЛОЖЕНИЕ: страница отдаёт ему отпечаток корзины (stamp) и
    что сервер знает о входе в каждую сеть, а оно сверяет со своей памятью — без
    перемен в корзине повторно не собирает — и со своими куками: в Магнит, например,
    человек вошёл у себя в окне, а сохранённого входа сервер не знает.
    """
    from app import cartrun, phonecart
    from app.shopbrowser import signals

    basket_id = int(current["id"]) if current else None
    stamp = cartrun.stamp_of(basket_id) if basket_id is not None else ""
    names = {s.code: s.name for s in repo.list_stores()}
    try:
        stored = {row.code: row for row in cartrun.rows()}
    except Exception:  # noqa: BLE001 — строки сборки не повод ронять корзину
        log.warning("строки сборки корзин не прочитались", exc_info=True)
        stored = {}
    rows, idle, chains = [], [], []
    for code in phonecart.CHAINS:
        if code not in names:
            continue
        login = _login(code)
        row = stored.get(code)
        # Эту же корзину туда уже положил сервер (браузер или метод сети) — телефону её
        # не повторять: сеть кладёт поверх лежащего.
        sent = bool(row is not None and row.by == cartrun.SERVER and row.state == cartrun.DONE
                    and row.road in (cartrun.BROWSER, cartrun.API)
                    and row.basket_id == basket_id and row.stamp == stamp)
        chains.append({"code": code, "login": login,
                       "cookies": list(signals.AUTH_COOKIE.get(code) or ()), "sent": sent})
        view = _run_view(row, basket_id, stamp)
        if view is None:
            if login:
                idle.append(names[code])
                continue
            view = {"state": "wait", "words": "соберу, пока открыта корзина"
                    if device.on_phone() else "соберёт приложение на телефоне"}
        view.update(code=code, name=names[code])
        rows.append(view)
    return {"basket": basket_id, "stamp": stamp, "rows": rows, "idle": idle,
            "going": any(r["state"] == "going" for r in rows),
            "collect": {"basket": basket_id, "stamp": stamp, "chains": chains}}


def _run_view(row, basket_id: int | None, stamp: str) -> dict | None:
    """Строка сети словами — только если она про эту корзину. Чужая корзина — None."""
    from app import cartrun

    if row is None or basket_id is None or row.basket_id != basket_id:
        return None
    total = row.total
    money = {"sum": total.sum, "network": total.source == cartrun.NETWORK} if total else None
    count = len(row.landed or []) + len(row.missing or [])
    if row.state == cartrun.GOING and cartrun.alive(row):
        pulse = cartrun.live(row) or {}
        done, of = int(pulse.get("at") or 0), int(pulse.get("total") or 0)
        words = f"собираю {done} из {of}" if of else "собираю"
        return {"state": "going", "words": words, "now": str(pulse.get("now") or ""), "total": None}
    fresh = bool(stamp) and row.stamp == stamp
    if not fresh:
        if row.state == cartrun.DONE and money:
            return {"state": "old", "words": "до правки корзины", "total": money}
        return None
    if row.state == cartrun.DONE:
        landed = len(row.landed or [])
        words = f"легло {landed} из {count}" + ("" if money else ", итог сети не прочитан")
        return {"state": "done", "words": words, "total": money,
                "gone": sum(1 for m in row.missing or [] if m.get("gone")),
                # «Кефир 1% +2»: сколько лишнего лежит в корзине сети от прежней сборки.
                "extra": [f"{e['name']} +{int(e.get('more') or 1)}" for e in row.extra or []
                          if e.get("name")]}
    if row.state == cartrun.EMPTY:
        return {"state": "empty", "words": "класть нечего", "note": row.note, "total": None}
    if row.state in (cartrun.FAILED, cartrun.REFUSED, cartrun.LOGIN):
        return {"state": "failed", "words": "не вышло", "note": row.note, "total": None}
    return None


def _change(current: dict | None):
    """«−», «+» и «убрать». Количество меняется на шаг, ноль убирает товар из корзины."""
    if current is None:
        return redirect(url_for("phone.basket"), 303)
    what, _, pid_text = (request.form.get("do") or "").partition(":")
    try:
        pid = int(pid_text)
    except ValueError:
        return redirect(url_for("phone.basket"), 303)
    basket_id = int(current["id"])
    item = next((i for i in repo.basket_items(basket_id) if int(i["product_id"]) == pid), None)
    if item is None:
        return redirect(url_for("phone.basket"), 303)
    qty = float(item.get("qty") or 0)
    step = _step(item.get("unit"))
    if what == "plus":
        qty = round(qty + step, 3)
    elif what == "minus":
        qty = round(qty - step, 3)
    elif what == "remove":
        qty = 0.0
    else:
        return redirect(url_for("phone.basket"), 303)
    repo.set_basket_item(basket_id, pid, max(0.0, qty))
    return redirect(url_for("phone.basket") + f"#it-{pid}", 303)


# ---------- История заказов ----------
@bp.route("/orders", methods=("GET", "POST"))
@needs_phone
def orders():
    from app.web.screens import orders as web_orders

    if request.method == "POST":
        return _repeat(request.form.get("key") or "")

    units = _units()
    current = _basket()
    repeated = _repeated()
    in_basket = repeated.get("key") if current and repeated.get("basket") == int(current["id"]) else None
    # Чеков у владельца больше полутора тысяч: страница со всеми сразу грузилась бы
    # долго и листалась бы бесконечно. Свежие — сразу, остальные — «Показать ещё».
    everything = web_orders.orders()
    limit = max(ORDERS_PAGE, request.args.get("n", type=int) or ORDERS_PAGE)
    shown = []
    for order in everything[:limit]:
        lines = [{"name": ln.get("name") or "—",
                  "qty": qty_label(ln.get("qty") or 0, units.get(ln.get("product_id"))),
                  "total": ln.get("total")} for ln in order["lines"]]
        shown.append({"key": order["key"], "day": order["day"], "store_code": order["store_code"],
                      "store_name": order["store_name"], "total": order["total"],
                      "count": len(lines), "head": lines[:SHOWN_LINES],
                      "rest": lines[SHOWN_LINES:], "in_basket": order["key"] == in_basket})
    more = len(everything) - len(shown)
    return _render("orders.html", tab=None, orders=shown, more=more,
                   next_n=limit + ORDERS_PAGE, fns=web_orders._fns_state())


def _repeat(key: str):
    """«Повторить»: товары заказа ложатся в новую корзину, она становится текущей."""
    from app.web.screens import orders as web_orders
    from app.web.screens.basket import _remember_open

    order = next((o for o in web_orders.orders() if o["key"] == key), None)
    if order is None:
        return redirect(url_for("phone.orders"), 303)
    qty: dict[int, float] = {}
    for line in order["lines"]:
        if line.get("product_id"):
            pid = int(line["product_id"])
            qty[pid] = qty.get(pid, 0.0) + float(line.get("qty") or 1)
    if not qty:
        return redirect(url_for("phone.orders"), 303)
    basket_id = repo.create_basket(f"Из заказа {order['day']}, {order['store_name']}", source="manual")
    for pid, amount in qty.items():
        repo.set_basket_item(basket_id, pid, amount)
    # Веб-корзина откроет ту же корзину: телефон и компьютер не должны спорить о текущей.
    _remember_open(basket_id)
    repo.set_setting(REPEATED, json.dumps({"key": key, "basket": basket_id}, ensure_ascii=False))
    return redirect(url_for("phone.basket"), 303)


# ---------- вход в сеть ----------
def _login(code: str) -> str:
    """Годится ли вход в сеть: «» — да, «again» — войти снова, «connect» — не входили.

    Берём у «Сборки корзин» (app/cartrun.py), а не store_accounts.connection: флаг
    подключения там ставится отчётом о сборе и чаще погашен, чем нет (см.
    app/web/screens/stores.py, _saved_logins). Сеть без сохранённого входа корзину
    не примет, сколько бы ни горела галочка.
    """
    from app import cartrun

    try:
        return cartrun._login(code)
    except Exception:  # noqa: BLE001 — состояние входа не повод ронять экран
        return "connect"


# ---------- Где дешевле ----------
def _places(basket_id: int, bundle: dict) -> list:
    """Каждое место, где можно взять корзину: расчёт «Результата» плюс все сети целиком.

    Расчёт отдаёт три лучших варианта вперемешку, а владелец просил видеть, «сколько
    корзина стоит в каждом месте». Поэтому к ним добавляется вся корзина в каждой
    сети — тем же оптимизатором и на тех же входах, что в service.calculate, только
    одним магазином. Сеть, где нет ни одного товара корзины, местом не считается.
    Список кэшируется вместе с расчётом «Результата» и сбрасывается вместе с ним.
    """
    if "phone_places" in bundle:
        return bundle["phone_places"]
    from app import config, handover, service
    from app.optimizer import optimize

    places = list(bundle["variants"])
    try:
        stores = repo.list_stores()
        by_product = service.baseline_by_product(basket_id)
        singles = optimize(
            lines=service.build_basket_lines(basket_id), stores=stores,
            offers=service.offers_map(), baseline=round(sum(by_product.values()), 2),
            baseline_lines=by_product,
            penalty=float(config.get("extra_order_penalty_rub", 150.0)),
            top_n=len(stores), max_stores=1, handover=handover.penalty_by_store())
        service._resolve_card_names(singles)
        seen = {tuple(sorted(s.store_code for s in v.stores)) for v in places}
        positions = int(bundle.get("positions") or 0)
        for variant in singles:
            key = tuple(sorted(s.store_code for s in variant.stores))
            if key in seen or len(variant.missing_products) >= positions:
                continue
            seen.add(key)
            places.append(variant)
    except Exception:  # noqa: BLE001 — без досчёта остаются варианты «Результата»
        log.warning("итог по каждой сети не посчитался", exc_info=True)
    # Сначала полные, потом с недостающими; внутри — от дешёвого к дорогому.
    places.sort(key=lambda v: (len(v.missing_products), float(v.total)))
    bundle["phone_places"] = places
    return places


def _title(variant) -> str:
    """«Всё во ВкусВилле» или «Часть в Магните, часть во ВкусВилле»."""
    parts = list(getattr(variant, "stores", None) or [])
    if len(parts) == 1:
        return "Всё " + where(parts[0].store_code, parts[0].store_name)
    if len(parts) == 2:
        return (f"Часть {where(parts[0].store_code, parts[0].store_name)}, "
                f"часть {where(parts[1].store_code, parts[1].store_name)}")
    return " + ".join(p.store_name for p in parts)


@bp.route("/cheaper", methods=("GET", "POST"))
@needs_phone
def cheaper():
    from app.web.screens import result

    current = _basket()
    if current is None:
        return _render("cheaper.html", tab=None, basket=None)
    basket_id = int(current["id"])
    if request.method == "POST":
        return _order(basket_id)

    bundle = result._bundle(basket_id)
    variants = bundle["variants"]
    if not variants:
        return _render("cheaper.html", tab=None, basket=current, variants=[])

    best, _switch = result._chosen(variants, None)
    every = _places(basket_id, bundle)
    picked = request.args.get("pick", type=int)
    chosen = every[picked] if picked is not None and 0 <= picked < len(every) else best

    places = []
    for n, variant in enumerate(every):
        size = len(getattr(variant, "stores", None) or [])
        diff = round(float(variant.total) - float(best.total), 2)
        missing = len(getattr(variant, "missing_products", None) or [])
        if missing:
            sub = f"{deliveries(size)} · нет {goods(missing)}"
        elif variant is not best and diff < 0:
            sub = f"дешевле на {result.rub(-diff)}, но {deliveries(size)}"
        else:
            sub = deliveries(size)
        places.append({"n": n, "title": _title(variant), "total": variant.total, "sub": sub,
                       "codes": [s.store_code for s in variant.stores],
                       "best": variant is best, "on": variant is chosen})

    figures = result._figures(chosen, bundle["baseline"])
    parts = []
    for store in (getattr(chosen, "stores", None) or []):
        parts.append(_part(store))
    return _render(
        "cheaper.html", tab=None, basket=current, variants=variants, places=places,
        chosen={"title": _title(chosen), "total": chosen.total, "best": chosen is best,
                "codes": [s.store_code for s in chosen.stores],
                # Вся корзина в одну сеть — телефон может положить её сам («Собрать»
                # в окне сети кладёт корзину целиком). Долю разбивки он так не положит.
                "whole": len(chosen.stores) == 1,
                "figures": figures,
                "base": result._base_words(chosen, bundle.get("from_receipts")),
                "complete": result._completeness(chosen, bundle["positions"]),
                "missing": list(getattr(chosen, "missing_products", None) or []),
                "subtotal": sum(float(s.subtotal or 0) for s in chosen.stores),
                "delivery": sum(float(s.delivery or 0) for s in chosen.stores),
                "discount": sum(float(s.discount or 0) for s in chosen.stores),
                "parts": parts},
        on_phone=device.on_phone(),
        sent=request.args.get("sent"), why=request.args.get("why"),
        why_store=request.args.get("store"), pick=picked)


def _part(store) -> dict:
    """Одна сеть выбранного варианта: сколько и как туда отдать корзину."""
    from app import handover as ho

    code = store.store_code
    kind = ho.KIND_BY_STORE.get(code, ho.LIST)
    by_link = kind == ho.LINK
    login = "" if by_link else _login(code)
    return {"code": code, "name": store.store_name, "where": where(code, store.store_name),
            "whom": WHOM.get(code) or store.store_name,
            "total": store.total, "count": len(getattr(store, "lines", None) or []),
            "by_link": by_link,
            # Ссылкой сеть отдаёт корзину и без входа; остальным нужен вход человека.
            "needs_login": bool(login), "again": login == "again"}


def _order(basket_id: int):
    """«Заказать»: положить корзину в магазин и открыть её там.

    Три дороги — те же, что у веб-«Результата» (app/web/screens/result.py, _act):
    ссылка сети на готовую корзину (ВкусВилл, Лента); корзина под сохранённым входом
    человека, которую кладёт браузер сервера; вход, если его ещё нет. Внутри
    приложения на телефоне сеть без ссылки открывается у него самого (окно сети,
    кнопка «Собрать») — это делает скрипт страницы до отправки формы, а сюда такой
    запрос приходит только из браузера или из старой сборки приложения.
    """
    from app import cartplan
    from app import handover as ho
    from app.shopbrowser import cart
    from app.shopbrowser import store as shopstore
    from app.web import auth
    from app.web.screens import result

    code = (request.form.get("store") or "").strip()
    pick = request.form.get("pick", type=int)
    back = url_for("phone.cheaper", **({"pick": pick} if pick is not None else {}))
    bundle = result._bundle(basket_id)
    variants = bundle["variants"]
    if not variants or not code:
        return redirect(back, 303)
    best, _switch = result._chosen(variants, None)
    every = _places(basket_id, bundle)
    chosen = every[pick] if pick is not None and 0 <= pick < len(every) else best
    lines = next((s.lines for s in chosen.stores if s.store_code == code), None)
    if not lines:
        return redirect(back, 303)

    sep = "&" if "?" in back else "?"
    if ho.KIND_BY_STORE.get(code, ho.LIST) == ho.LINK:
        try:
            plan = ho.for_store(code, lines)
            if plan is not None and plan.link:
                return redirect(plan.link)
        except Exception:  # noqa: BLE001 — сеть не ответила; скажем об этом на экране
            log.warning("ссылка на корзину %s не собралась", code, exc_info=True)
        return redirect(f"{back}{sep}why=link&store={code}", 303)

    if shopstore.load(code) is None:
        return redirect(url_for("phone.settings", connect=code), 303)
    why_not = cartplan.why_not(code)
    if why_not:
        return redirect(f"{back}{sep}why=closed&store={code}", 303)
    try:
        plan = cartplan.build(code, lines, force=True, verify_prices=True)
        if plan.lines and cart.start(code, auth.current_phone() or "", plan):
            return redirect(f"{back}{sep}sent={code}", 303)
    except Exception:  # noqa: BLE001
        log.warning("корзина в %s не ушла", code, exc_info=True)
    return redirect(f"{back}{sep}why=busy&store={code}", 303)


# ---------- Настройки ----------
@bp.route("/settings", methods=("GET", "POST"))
@needs_phone
def settings():
    from app import location, store_accounts

    if request.method == "POST":
        return _save_address()

    names = {s.code: s.name for s in repo.list_stores()}
    stores = []
    for conn in store_accounts.connections():
        if conn.store_code not in names:
            continue
        login = _login(conn.store_code)
        stores.append({"code": conn.store_code, "name": names[conn.store_code],
                       "connected": not login, "again": login == "again"})
    try:
        address = location.address()
    except Exception:  # noqa: BLE001
        address = None
    return _render("settings.html", tab="settings", stores=stores,
                   connected=[s for s in stores if s["connected"]],
                   waiting=[s for s in stores if not s["connected"]],
                   fns=_fns(), address=address, on_phone=device.on_phone(),
                   saved=request.args.get("saved"), connect=request.args.get("connect"),
                   edit=request.args.get("edit"))


def _fns() -> dict:
    """Кабинет «Мои чеки»: подключён ли и сколько чеков уже загружено."""
    from app.fns import sync as cabinet

    try:
        connected = bool(cabinet.is_connected())
    except Exception:  # noqa: BLE001 — кабинет недоступен; строка позовёт подключать
        connected = False
    try:
        count = len(repo.imported_receipts())
    except Exception:  # noqa: BLE001
        count = 0
    word = plural(count, "чек", "чека", "чеков")
    return {"connected": connected, "count": count, "label": f"{count} {word}"}


def _save_address():
    """Сохранить адрес так же, как главная веба (app/web/views.py, /address)."""
    from app import location

    value = (request.form.get("address") or "").strip()
    if not value:
        return redirect(url_for("phone.settings", edit="address"), 303)
    moved = location.save_address(value)
    if moved:
        try:
            from app.web.screens import basket as basket_screen
            basket_screen.refresh_after_move()
        except Exception:  # noqa: BLE001 — адрес сохранён, это главное
            log.warning("обновление цен после смены адреса не запустилось", exc_info=True)
    return redirect(url_for("phone.settings", saved="address"), 303)


# ---------- вход с телефона ----------
def install(flask_app) -> None:
    """Повесить телефонные экраны и привести приложение на телефоне к ним с главной."""
    flask_app.register_blueprint(bp)

    @flask_app.before_request
    def _phone_home():
        # Только главная и только внутри приложения: ссылки на экраны веба, закладки
        # и браузер компьютера живут как жили. «?web=1» — ход в полную версию.
        if (request.method == "GET" and request.path == "/" and device.on_phone()
                and not request.args.get("web") and getattr(g, "phone", None)):
            return redirect(url_for("phone.basket"))
        return None


__all__ = ["bp", "install", "WHERE", "where", "goods", "deliveries", "qty_label"]
