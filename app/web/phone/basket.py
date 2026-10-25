from __future__ import annotations

import logging

from flask import redirect, render_template, request, url_for

from app import repo
from app.web import device
from app.web.auth import needs_phone

from .common import _basket, _login, _render, _rub, _step, _units, bp

log = logging.getLogger(__name__)

# Сколько привычных товаров показывать на телефоне: это подсказка к корзине, а не каталог.
HABITS_SHOWN = 8


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
    # Сколько корзин всего: ссылка «Корзины · N» нужна, только когда есть из чего
    # выбирать. Одна корзина — незачем показывать список из одной строки.
    baskets_n = len(repo.list_baskets())
    items = repo.basket_items(int(current["id"])) if current else []
    rows = [{"pid": int(i["product_id"]), "name": i.get("name") or "—",
             "unit": i.get("unit") or "pcs", "qty": float(i.get("qty") or 0)} for i in items]
    for row in rows:
        # Лежит в один шаг — «−» его уберёт: на кнопке корзинка, а не минус.
        row["last"] = row["qty"] <= _step(row["unit"]) + 1e-9
    # Привычное показываем и при пустой (или ещё не заведённой) корзине: именно
    # тогда человек набирает её с нуля, и подсказка нужнее всего. Нет истории —
    # список пуст, и блока в шаблоне не будет.
    habits = _habits(int(current["id"]) if current else 0)
    photos = _photos([r["pid"] for r in rows + habits])
    for row in rows + habits:
        row["photo"] = photos.get(row["pid"])
    return _render("basket.html", tab="basket", basket=current, items=rows, habits=habits,
                   runs=_runs(current) if rows else None, baskets_n=baskets_n)


def _photos(pids: list[int]) -> dict[int, str]:
    """Фото товаров из единой базы сетей: {товар: адрес картинки}.

    ЗАЧЕМ. Вид по мотивам Магнита (выбор владельца 22.10.2026): в строке корзины фото —
    товар узнаётся глазом раньше, чем читается название. Своих картинок у приложения
    нет, они есть у строк сетей в единой базе (chain_products.image): у Магнита,
    ВкусВилла, Дикси, FixPrice. Путь: подтверждённый артикул товара в сети → её строка
    в базе → картинка. У этой сети картинок нет (Пятёрочка, Лента) — картинка другой
    сети того же единого товара. Не нашлось — серая плитка. Сбой базы — пустой ответ:
    фото не повод не открыть корзину.
    """
    from app.catalog import store

    def image(rows) -> str | None:
        return next((str(r["image"]) for r in rows
                     if str(r.get("image") or "").startswith(("https://", "http://"))), None)

    pids = sorted({int(p) for p in pids})
    if not pids:
        return {}
    try:
        pairs = {pid: [(m["store_code"], str(m["sku"])) for m in repo.product_mappings(pid)]
                 for pid in pids}
        if not any(pairs.values()):
            return {}
        conn = store.connect()
        try:
            found = {(r["chain"], str(r["sku"])): r
                     for r in store.rows_by_skus([p for ps in pairs.values() for p in ps], conn=conn)}
            out: dict[int, str] = {}
            for pid, ps in pairs.items():
                own = [found[p] for p in ps if p in found]
                url = image(own)
                for item_id in dict.fromkeys(r["item_id"] for r in own):
                    if url:
                        break
                    url = image(store.item_rows(item_id, conn=conn))
                if url:
                    out[pid] = url
            return out
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — фото не повод ронять корзину
        log.warning("фото товаров не прочитались", exc_info=True)
        return {}


def _habits(basket_id: int) -> list[dict]:
    """«Из привычного»: что человек берёт часто и что по циклу пора докупить.

    ЗАЧЕМ. Пункт 5 владельца: в вебе привычное стоит над списком
    (app/web/screens/basket.py, _regulars и _due), а на телефоне его не было — человек
    набирал корзину с нуля. Берём те же два источника, а не свой разбор истории: правила
    «часто» и «пора» живут в app/baskets.py, и вторая их копия разошлась бы с вебом.

    ПОРЯДОК. Сверху «пора докупить»: это не «что я обычно беру», а «что я забыл», и оно
    важнее. Товар, который и частый, и просроченный, остаётся один — с причиной «пора
    докупить». Пустая история или сбой базы — пустой список: подсказка не повод не
    открыть корзину.

    basket_id=0 — корзины ещё нет: «в корзине» тогда пусто, и в привычное попадает всё,
    что человек брал. Оба источника (_due, _regulars) спрашивают позиции по номеру, а
    basket_items(0) честно возвращает пусто.
    """
    try:
        from app.web.screens.basket import _due, _regulars

        units = _units()
        found: list[dict] = []
        seen: set[int] = set()
        for why, rows in (("пора докупить", _due(basket_id)),
                          ("часто берёте", _regulars(basket_id))):
            for row in rows:
                pid = int(row["product_id"])
                if pid in seen:
                    continue
                seen.add(pid)
                item = {"pid": pid, "name": row.get("name") or "—",
                        "unit": units.get(pid) or row.get("unit") or "pcs", "why": why}
                if row.get("qty"):
                    item["qty"] = float(row["qty"])
                found.append(item)
                if len(found) >= HABITS_SHOWN:
                    return found
        return found
    except Exception:  # noqa: BLE001 — привычное не повод ронять корзину
        log.warning("привычные товары не прочитались", exc_info=True)
        return []


@bp.get("/basket/runs")
@needs_phone
def basket_runs():
    """Строки «Корзина в магазинах» для опроса, пока телефон собирает: кусок страницы и признак хода."""
    runs = _runs(_basket())
    return {"html": render_template("phone/_runs.html", runs=runs, rub=_rub()),
            "going": runs["going"], "stamp": runs["stamp"]}


# ---------- Корзины: список и возврат к прежней ----------
@bp.get("/baskets")
@needs_phone
def baskets():
    """Экран «Корзины»: все корзины человека и какая из них открыта.

    ЗАЧЕМ. Телефон показывал только текущую корзину, а «Повторить» в истории
    заводит новую и делает её свежей. Прежняя оставалась в базе, но вернуться к
    ней с телефона было нечем — списка корзин у телефона не было (живой прогон
    на эмуляторе 21.10.2026). «Открыть» пишет basket:last — ту же настройку, что
    читает веб-корзина, поэтому телефон и компьютер дальше показывают одну и ту же.
    """
    from app.web.screens import orders as web_orders

    current = _basket()
    current_id = int(current["id"]) if current else 0
    rows = []
    # Свежие сверху — как их отдаёт repo.list_baskets. Сколько товаров — считаем
    # здесь же: отдельного счётчика в repo нет, а список корзин короткий. Дату
    # рисуем тем же «11 октября», что в «Истории заказов».
    for basket in repo.list_baskets():
        basket_id = int(basket["id"])
        rows.append({"id": basket_id,
                     "name": basket.get("name") or "—",
                     "day": web_orders._day(str(basket.get("created_at") or "")[:10]),
                     "count": len(repo.basket_items(basket_id)),
                     "open": basket_id == current_id})
    return _render("baskets.html", tab=None, baskets=rows)


@bp.post("/baskets/<int:basket_id>")
@needs_phone
def open_basket(basket_id: int):
    """Открыть корзину: записать её в basket:last и вернуться в корзину.

    Корзины могло уже не быть — её удалили, пока список был открыт. Тогда ничего
    не записываем и возвращаемся к списку, а не открываем пустую или чужую.
    """
    from app.web.screens.basket import LAST_OPEN

    if not any(int(b["id"]) == basket_id for b in repo.list_baskets()):
        return redirect(url_for("phone.baskets"), 303)
    repo.set_setting(LAST_OPEN, str(basket_id))
    return redirect(url_for("phone.basket"), 303)


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
        # «осталось ~K с» — только когда есть на чём считать: сколько уже прошло с
        # начала попытки и сколько позиций за это время легло. Нет начала, темпа или
        # остатка — говорим только «собираю N из M» и не выдумываем минуты.
        return {"state": "going", "words": words, "now": str(pulse.get("now") or ""),
                "left": _left_text(row.started_at, done, of), "total": None}
    if (row.state == cartrun.FAILED and (row.lost or row.note == cartrun.PHONE_LOST)) or \
            (row.state == cartrun.GOING and row.by == cartrun.DEVICE
             and not cartrun.alive(row)):
        # Строку оборвало молчание телефона, а не отказ сети: это «прервано», а не
        # «не вышло». Вторая половина условия — для строки, не прошедшей через
        # cartrun.rows(): оборванная она или ещё свежая, решает тот же alive().
        # Дата — время последнего пульса: по нему видно, когда человек закрыл
        # приложение или пропала связь.
        return {"state": "interrupted", "words": "сборка прервана — телефон закрылся или ушёл",
                "note": _pulse_text(row), "total": None}
    fresh = bool(stamp) and row.stamp == stamp
    if not fresh:
        if row.state == cartrun.DONE and money:
            return {"state": "old", "words": "до правки корзины", "total": money}
        return None
    if row.state == cartrun.DONE:
        landed = len(row.landed or [])
        words = f"легло {landed} из {count}" + ("" if money else ", итог сети не прочитан")
        extra = [f"{e['name']} +{int(e.get('more') or 1)}" for e in row.extra or []
                 if e.get("name")]
        return {"state": "done", "words": words, "total": money,
                "gone": sum(1 for m in row.missing or [] if m.get("gone")),
                # «Кефир 1% +2»: сколько лишнего лежит в корзине сети от прежней сборки.
                "extra": extra,
                # Тот же факт короткой фразой: её читают и там, где список не рисуют.
                "extra_note": ("в корзине сети осталось лишнее, не убираю: "
                               + ", ".join(extra)) if extra else ""}
    if row.state == cartrun.EMPTY:
        return {"state": "empty", "words": "класть нечего", "note": row.note, "total": None}
    if row.state == cartrun.LOGIN:
        sname = row.name or row.code
        if row.login == "store":
            words = f"выберите магазин в «{sname}»" if row.code == "magnit" else "выберите магазин"
        else:
            words = f"войдите в «{sname}» заново" if row.code == "dixy" else "войдите заново"
        return {"state": "login", "words": words, "note": row.note, "total": None, "login": row.login}
    if row.state in (cartrun.FAILED, cartrun.REFUSED):
        return {"state": "failed", "words": "не вышло", "note": row.note, "total": None}
    return None


def _left_text(started_at, done: int, of: int) -> str:
    """«осталось ~K с» по прошедшему времени и числу уже собранных позиций.

    Считаем темп самой сборки: сколько секунд ушло на одну позицию и сколько их ещё
    осталось. Оценка появляется не раньше пятой секунды (на одном-двух касаниях темп
    ещё не виден) и не выдумывается, если начала попытки нет или считать нечего.
    """
    from datetime import datetime

    if not started_at or done <= 0 or of <= done:
        return ""
    try:
        started = datetime.fromisoformat(str(started_at))
    except (TypeError, ValueError):
        return ""
    elapsed = (datetime.now() - started).total_seconds()
    if elapsed < 5:
        return ""
    left = elapsed / done * (of - done)
    if left < 1:
        return ""
    if left < 90:
        seconds = max(5, int(round(left / 5.0)) * 5)
        return f"осталось ~{seconds} с"
    return f"осталось ~{int(round(left / 60.0))} мин"


def _pulse_text(row) -> str:
    """Когда строку последний раз трогали: «последний пульс 21.10.2026 02:14».

    Пульса не было (сборка началась и сразу замолчала) — говорим о начале попытки,
    а не выдаём его за пульс.
    """
    from datetime import datetime

    try:
        beat = float(row.beat) if row.beat is not None else 0.0
    except (TypeError, ValueError):
        beat = 0.0
    if beat > 0:
        return f"последний пульс {datetime.fromtimestamp(beat):%d.%m.%Y %H:%M}"
    if row.started_at:
        try:
            started = datetime.fromisoformat(str(row.started_at))
        except (TypeError, ValueError):
            return ""
        return f"попытка начата {started:%d.%m.%Y %H:%M}"
    return ""


def _change(current: dict | None):
    """«−», «+», «убрать» и «добавить из привычного».

    Количество меняется на шаг, ноль убирает товар из корзины. «Добавить» — та же
    арифметика, только товара в корзине может ещё не быть, и корзины может не быть
    вовсе: её тогда заводит само «+» из привычного. Именно ради этого случая блок и
    показывается на пустом экране — человек набирает корзину с нуля.
    """
    what, _, pid_text = (request.form.get("do") or "").partition(":")
    try:
        pid = int(pid_text)
    except ValueError:
        return redirect(url_for("phone.basket"), 303)
    if what == "add":
        unit = _units().get(pid)
        if unit is None:  # товара нет в каталоге — класть нечего, корзину не заводим
            return redirect(url_for("phone.basket"), 303)
        if current is None:
            basket_id = repo.create_basket("Корзина")
            qty = 0.0
        else:
            basket_id = int(current["id"])
            item = next((i for i in repo.basket_items(basket_id)
                         if int(i["product_id"]) == pid), None)
            qty = float(item.get("qty") or 0) if item is not None else 0.0
        repo.set_basket_item(basket_id, pid, round(qty + _step(unit), 3))
        return redirect(url_for("phone.basket") + f"#it-{pid}", 303)
    if current is None:
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
