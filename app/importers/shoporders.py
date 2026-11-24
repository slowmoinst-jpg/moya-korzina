"""Заказы из кабинета сети → история покупок и артикулы сети.

Чеки ФНС приходят редко и без артикулов. Кабинет сети отдаёт вошедшему человеку его заказы,
и у строки заказа есть артикул, по которому сеть кладёт товар в корзину. Телефон читает
заказы запросами сети из её же окна (как корзину, cartapi.js) и шлёт сюда. Заказ становится
историей наравне с чеком (bundle.store_receipts), его строка — подтверждённой связью товара
с артикулом: при сборке корзины сервер подставляет готовые артикулы, а не ищет по названию.

Разговор один, POST /api/device/orders, тело {store, orders?, end?}:
- без orders — спросить, пора ли читать: ответ due;
- заказ без lines — строка описи {id, date, total}. Опись идёт страницами, свежие первыми;
  more в ответе — читать ли следующую, end в запросе — телефон дошёл до конца списка
  (пустая последняя страница — тоже: orders пуст, end стоит). Телефон идёт только вперёд и
  число страниц за заход ограничивает сам: повтор уже присланной страницы опись не закрывает;
- заказ с lines [{sku, name, qty, price, url?}] — состав; need в ответе — чей состав прислать.
Только выполненные заказы. date — день заказа по часам телефона, деньги в рублях, price — за
штуку. sku — артикул, по которому сеть кладёт товар в корзину СЕГОДНЯ: строка, чьей карточки
у сети уже нет, идёт без него — в историю ляжет, связи не даст. Заказ, чей состав сеть не
отдала, телефон шлёт с пустым lines: сервер перестанет его спрашивать. Заказ с негодной
строкой состава не берётся целиком (skipped в ответе) и спросится в следующий заход.

Нагрузку на сеть держат ответы сервера: опись — не чаще раза в orders.sync_hours и только до
страницы без новых заказов, состав — не больше orders.details_per_sync заказов за заход.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import date, timedelta
from urllib.parse import urlsplit

from app import cartrecord, chains, config, repo
from app.catalog import model, nonfood
from app.catalog.link import PRODUCE
from app.importers import bundle
from app.importers.ofd_pdf import Receipt, ReceiptRow
from app.matcher.normalize import display_name, parse_weight

log = logging.getLogger(__name__)

SOURCE = "orders"       # receipts.source — он же код источника «Мои заказы» в app/receipts.py
METHOD = "order"        # product_mapping.method: артикул из строки заказа в кабинете сети
MAX_ORDERS = 200
MAX_LINES = 300
_ID = re.compile(r"[\w.:-]{1,80}")
_SKU = re.compile(r"[\w.-]{1,200}")
_LOCK = threading.Lock()    # повтор телефона по таймауту не идёт рядом с первым запросом


def _state(store: str) -> dict:
    """Память о кабинете сети: at — когда дочитали опись, end — дочитывали ли её до конца,
    open — заказы без состава {номер: день}, left — сколько составов ещё можно спросить,
    done — заказы, которым нечего класть в историю (их покупку уже принёс чек). До конца
    захода живут walk — заказы, найденные этим обходом описи, и tried — заказы, чей состав в
    этот заход пришёл негодным."""
    try:
        saved = json.loads(repo.get_setting(f"shoporders.{store}") or "{}")
    except ValueError:
        saved = {}
    return {"at": 0, "end": False, "left": 0, "open": {}, "done": [], "walk": [], "tried": [],
            **(saved if isinstance(saved, dict) else {})}


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number < 1e7 else None      # nan и inf сюда не проходят


def _order(raw) -> dict | None:
    """Заказ из тела запроса; None — негоден. lines None — строка описи."""
    if not isinstance(raw, dict):
        return None
    oid, total = str(raw.get("id") or ""), _number(raw.get("total"))
    try:
        day = date.fromisoformat(str(raw.get("date")))
    except ValueError:
        return None
    # день из будущего (часы телефона) испортил бы «Из привычного»: давность считается от него
    if not _ID.fullmatch(oid) or total is None or day > date.today() + timedelta(days=1):
        return None
    order: dict = {"id": oid, "date": day.isoformat(), "total": total, "lines": None}
    lines = raw.get("lines")
    if lines is None:
        return order
    if not isinstance(lines, list) or len(lines) > MAX_LINES:
        return None
    order["lines"] = []
    for line in lines:
        # Состав с негодной строкой не берём вовсе: ползаказа в истории хуже повтора.
        if not isinstance(line, dict) or not isinstance(line.get("name"), str):
            return None
        name, qty = " ".join(line["name"].split())[:200], _number(line.get("qty"))
        if not name or qty is None:
            return None
        if not qty:
            continue                                    # строку с количеством 0 не привезли
        price = _number(line.get("price"))
        if price is None:
            return None
        sku, url = line.get("sku"), line.get("url")
        sku = str(sku) if type(sku) in (str, int) else ""
        order["lines"].append({"name": name, "qty": qty, "price": price,
                               "sku": sku if _SKU.fullmatch(sku) else "",
                               "url": url if isinstance(url, str) else ""})
    return order


def _own_url(url: str, chain: str) -> str | None:
    """Адрес карточки — только своей сети: чужой в базу не пишем."""
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return None
    return url[:500] if url.startswith("https://") and cartrecord.own_host(host, chain) else None


def _link(store, line: dict) -> int | None:
    """Товар строки заказа получает артикул сети; ответ — номер товара или None.

    Выбор человека и его отказ от сети не трогаем, строку овощей «самая дешёвая за кг» тоже:
    её ведёт app/catalog/produce.py. Догадка по названию (единая база, слаг из чека) с другим
    артикулом уступает: в заказе карточка та самая, которую человек купил.
    """
    sku, name = line["sku"], line["name"]
    if store.code == "dixy":
        sku = sku.upper()       # в базе артикул Дикси заглавными: строчный завёл бы строку без цены
    if not sku or model.placeholder_sku(sku, store.code) or not nonfood.is_receipt_product(name):
        return None
    product = repo.find_product_by_name(display_name(name))
    if product is None or repo.chain_rejected(product.id, store.id):
        return None

    def held() -> list[dict]:
        return [m for m in repo.product_mappings(product.id)
                if m["store_id"] == store.id and not model.placeholder_sku(m["sku"], store.code)]

    if any(m["method"] == PRODUCE or (m["method"] is None and m["sku"] != sku) for m in held()):
        return None
    known = repo.store_product_by_sku(store.id, sku)        # известной строке название не портим
    sp = known["id"] if known else repo.upsert_store_product(
        store.id, sku, name, weight_g=parse_weight(name)[0],
        url=_own_url(line["url"], store.code), keep_known=True)
    repo.confirm_mapping(product.id, sp, confirmed=True, method=METHOD)
    # Запись молча не проходит, когда человек убирал у товара заглушку этой сети («stub:»):
    # прежние связи снимаем, только убедившись, что новая легла. Читаем их заново: фоновое
    # сопоставление могло дописать свою догадку, пока шла запись.
    after = held()
    if all(m["store_product_id"] != sp for m in after):
        return None
    for m in after:
        if m["sku"] != sku and m["method"] not in (None, PRODUCE):
            repo.unconfirm_mapping(product.id, m["store_product_id"])
    return product.id


def accept(body: dict) -> dict:
    """Пачка заказов сети от телефона → история, артикулы и ответ, что читать дальше."""
    code = str(body.get("store") or "")
    store = repo.get_store(code)
    if store is None or not chains.accepts(store_code=code):
        raise ValueError(f"заказы сети «{code or '—'}» не принимаются")
    raw = body.get("orders") or []
    if not isinstance(raw, list) or len(raw) > MAX_ORDERS:
        raise ValueError(f"orders — список не длиннее {MAX_ORDERS} заказов")
    # настройки читаем до записи: негодное значение — отказ сразу, а не полдела
    budget = int(config.get("orders.details_per_sync", 20))
    pause = float(config.get("orders.sync_hours", 24)) * 3600
    with _LOCK:
        return _take(store, raw, bool(body.get("end")), budget, pause)


def _take(store, raw: list, end: bool, budget: int, pause: float) -> dict:
    code = store.code
    orders = [o for o in map(_order, raw) if o]
    state, now = _state(code), time.time()
    prefix = f"{bundle.ORDER_KEY}{code}:"
    taken = {k[len(prefix):] for k in repo.imported_receipt_keys() if k.startswith(prefix)}
    taken |= set(state["done"])

    listed = fresh = twins = 0
    new: list[dict] = []
    lines: list[dict] = []
    for order in sorted(orders, key=lambda o: o["date"]):   # свежий заказ связывает последним
        oid = order["id"]
        if order["lines"] is None:
            listed += 1
            if oid not in taken and oid not in state["open"]:
                state["open"][oid] = order["date"]
                state["walk"].append(oid)
                fresh += 1
            elif oid in state["walk"]:
                fresh += 1      # страница этого же обхода пришла снова: ответ потерялся, обход оборвался
            continue
        state["open"].pop(oid, None)
        # Связи пишем и у знакомого заказа: память сохраняется последней, и повтор после сбоя
        # посреди пачки довозит артикулы, хотя история уже легла.
        lines += order["lines"]
        if oid in taken:
            continue
        taken.add(oid)
        # shortcut: у заказа-двойника товар ищется по названию из заказа; где чек называет товар
        # иначе (сокращения Пятёрочки), связи не лягут — тогда заводить товар по строке заказа.
        if not order["lines"] or bundle.same_purchase(store.id, order["date"], order["total"],
                                                      orders=False):
            state["done"].append(oid)
            twins += bool(order["lines"])
            continue
        repo.forget_receipt(prefix + oid)       # строки прошлой попытки, оборванной на полпути
        new.append({"key": prefix + oid, "receipt": Receipt(
            date=order["date"], store_name=store.name, total=order["total"],
            rows=[ReceiptRow(raw_name=r["name"], qty=r["qty"], unit_price=r["price"],
                             total=round(r["qty"] * r["price"], 2)) for r in order["lines"]])})

    stored = bundle.store_receipts(new, store_code=code, source=SOURCE) if new else {}
    linked = {pid for pid in (_link(store, line) for line in lines) if pid}

    # Запас составов тратит каждая попытка, и негодная тоже: иначе заказ, чей состав не прошёл
    # проверку, возвращался бы в need без конца, и телефон ходил бы за ним в сеть по кругу.
    # shortcut: такой заказ спрашивается раз в заход и занимает одно место запаса; наберётся
    # много — считать попытки и откладывать.
    asked = [str(o.get("id") or "") for o in raw if isinstance(o, dict) and o.get("lines") is not None]
    state["left"] = max(0, state["left"] - len(asked))
    more = bool(listed) and not end and (not state["end"] or fresh > 0)
    if (listed or end) and not more:                        # опись дочитана: заход окончен
        state["end"] = state["end"] or end
        state["at"] = now
        state["left"] = budget
        state["walk"], state["tried"] = [], []
    # годный состав свой заказ из open уже вынул: остались те, чей состав не прошёл проверку
    state["tried"] = sorted(set(state["tried"]) | (set(asked) & set(state["open"])))
    if raw or end:
        repo.set_setting(f"shoporders.{code}", json.dumps(state))
    need = [oid for oid in sorted(state["open"], key=state["open"].get, reverse=True)
            if oid not in state["tried"]][:state["left"]]
    skipped = len(raw) - len(orders)
    if stored.get("receipts") or twins or linked or skipped:
        log.info("заказы %s: в историю %d (строк %d), уже были чеком %d, артикулов %d,"
                 " негодных %d, без состава %d", code, stored.get("receipts", 0),
                 stored.get("rows", 0), twins, len(linked), skipped, len(state["open"]))
    return {"ok": True, "store": code, "due": now - state["at"] >= pause,
            "more": more, "need": [] if more else need,
            "imported": stored.get("receipts", 0), "twins": twins, "linked": len(linked),
            "skipped": skipped}
