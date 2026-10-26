"""Витрина «Посмотреть демо»: рабочее место demo, налитое настоящими товарами сетей.

ЗАЧЕМ. Демо — первое, что видят знакомые владельца, и оно одно на всех. Вымышленный чек
из tools/seed.py (икра, страчателла, детское пюре) жил на выдуманных ценах: на сервере
«Где дешевле» находила цены у части товаров и советовала вариант ДОРОЖЕ чека. Теперь
корзина собирается из единой базы товаров сетей (data/catalog.db), а «чек из Ленты» берёт
цены оттуда же: «Раньше вы платили» — настоящие деньги, и сравнение с ними честное.

ВЕРСИЯ. Налитая витрина помечена настройкой demo:version. Совпала с DEMO_VERSION и товары
на месте — ensure() ничего не делает: её зовут на КАЖДОЕ «Посмотреть демо», и это два
лёгких запроса. Поменяли список (app/demo_basket.csv), адрес или правила — подняли
DEMO_VERSION, и следующий посетитель получит витрину, опустошённую и собранную заново.

ЗАЩИТА. Опустошение стирает содержимое ОТКРЫТОГО рабочего места, поэтому ensure() работает
только там, где открыто demo: вызов при личной (или общей) базе унёс бы чужие чеки. Само
рабочее место она не открывает и не закрывает — это дело вызывающего (app/web/login.py).
Сбой наружу не выпускается: посетитель в любом случае должен попасть в демо.
"""
from __future__ import annotations

import calendar
import csv
import logging
import os
import sys
import threading
from datetime import date

from app import location, repo, users
from app.catalog import match, refresh, store
from app.db import get_conn, query

log = logging.getLogger(__name__)

# Поднимается при любой правке списка, адреса или правил: витрина с другой версией
# собирается заново. Дата — чтобы по номеру было видно, когда витрину правили в последний раз.
DEMO_VERSION = "2026-10-26"
VERSION_KEY = "demo:version"

# Публичный ориентир, а не чей-то дом: демо видят все, и репозиторий публичный.
ADDRESS = "Санкт-Петербург, Невский проспект, 28"

# «Чек» демо — из Ленты: у неё есть цена почти у каждого товара списка.
RECEIPT_CHAIN = "lenta"

# Меньше стольких товаров из единой базы — витрина «не вышла» (свежая установка без
# каталога), и берётся прежний вымышленный набор.
MIN_GOODS = 5

# Список лежит рядом с кодом, а не в data/. Образ копирует app/ целиком, а из data/ —
# только перечисленные файлы, и том korzina-data закрывает папку образа: новый файл в
# data/ на сервер просто не доехал бы, и демо молча вернулось бы к вымышленному чеку.
LIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_basket.csv")

# Всё, что человек (и посетитель демо) кладёт в рабочее место. Порядок — детьми вперёд:
# внешние ключи включены, а у части ссылок нет каскада (purchase_history -> products,
# variant_lines -> cards и products). Справочник stores НЕ входит: он общий для всех
# рабочих мест и наливается init_db.
CONTENT_TABLES = ("variant_lines", "variants", "basket_items", "baskets", "offers", "cards",
                  "purchase_history", "product_mapping", "store_prices", "link_rejects",
                  "store_products", "products", "receipts", "settings")

_LOCK = threading.Lock()


def offer_window(today: date) -> tuple[str, str]:
    """Срок акций демо: с первого числа этого месяца по последнее число СЛЕДУЮЩЕГО.

    Даты в data/seed_offers.csv давно вышли (30.09.2026), а на дату наливки надо, чтобы
    кэшбэк действовал: не меньше месяца вперёд, в какой бы день витрину ни пересобрали.
    """
    year, month = today.year + (today.month == 12), today.month % 12 + 1
    last = date(year, month, calendar.monthrange(year, month)[1])
    return today.replace(day=1).isoformat(), last.isoformat()


def _is_current() -> bool:
    """Витрина налита этой версией и не пуста. Два лёгких запроса — вызывается на каждое нажатие."""
    return (repo.get_setting(VERSION_KEY) == DEMO_VERSION
            and bool(query("SELECT 1 FROM products LIMIT 1")))


def _wipe() -> None:
    """Опустошить рабочее место ОДНОЙ транзакцией: оборванное стирание не оставит полвитрины."""
    with get_conn() as conn:
        for table in CONTENT_TABLES:
            conn.execute(f"DELETE FROM {table}")


def _reset() -> None:
    """Пустое рабочее место с адресом (он лежит в settings, и стирание уносит его тоже).

    Адрес сохраняется так же, как на экране настроек (app/web/phone/settings._save_address):
    строкой, плюс очередь на загрузку цен по нему. Сеть здесь не нужна — точки подбирает
    фоновый обход, так что оборванная связь ничего не ломает.
    """
    _wipe()
    location.save_address(ADDRESS)


def _seed():
    """tools/seed.py — скрипт, а не пакет: подключается по пути, как делал login._fill_demo."""
    tools = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import seed

    return seed


def _find(name: str) -> int | None:
    """Номер единого товара по названию: точное совпадение, иначе лучший ответ поиска.

    Точное идёт первым нарочно. store.search_items требует каждое слово запроса в norm
    товара, а norm не хранит слов-надписей («без», «змж», «весовые»), поэтому по
    каноническому названию «Кефир …, без змж, 930г» поиск не находит ничего (замер на
    локальной базе: 7 названий списка из 15). Поиск остаётся запасом на случай, когда сеть
    чуть переписала название, — по тем же словам, что попадают в norm.
    """
    with store.connect() as conn:
        row = conn.execute("SELECT id FROM catalog_items WHERE name = ? ORDER BY id LIMIT 1",
                           (name,)).fetchone()
    if row:
        return int(row["id"])
    words = " ".join(match.quiet(match.ordered_words(match.glue(name))))
    hits = store.search_items(words, limit=1)
    return int(hits[0]["id"]) if hits else None


def _paid(chains: list[dict]) -> float | None:
    """Цена за единицу в «чеке из Ленты»: цена Ленты, а нет её — самая высокая у остальных сетей.

    Берётся цена СТРОКИ каталога, а не цены точки (refresh.price_for_me): адрес демо ещё
    может быть не загружен, а чек нужен сразу. Строк Ленты у единого товара бывает
    несколько (соседние фасовки) — берётся та, что в наличии, и из них дешевле. Нет цены
    ни у одной сети — None: строку чека не выдумываем.
    """
    priced = [r for r in chains if (r.get("price") or 0) > 0]
    own = [r for r in priced if r["chain"] == RECEIPT_CHAIN]
    if own:
        return float(min(own, key=lambda r: (r.get("in_stock") == 0, r["price"]))["price"])
    return float(max(r["price"] for r in priced)) if priced else None


def _fill_from_catalog(goods: list[dict], today: date) -> int:
    """Корзина и «чек из Ленты» из единой базы сетей. Возвращает, сколько товаров легло.

    Каждый товар кладётся тем же путём, что «＋» у товара каталога на экране «Корзина»:
    refresh.adopt заводит эталон и подтверждённые связи с сетями, а явный выбор человека
    (link.picked) adopt включает сам. Строка списка, которой нет в базе, пропускается.
    """
    store.init()
    lenta = repo.get_store(RECEIPT_CHAIN)
    basket_id = repo.create_basket(f"Неделя (чек из Ленты, {today:%d.%m.%Y})", source="history")
    # Строки «чека» привязаны к записи чека, как у настоящего импорта: без неё «Мои чеки»
    # сочли бы их позициями, загруженными до того, как появился учёт чеков.
    receipt_key = f"demo:{today.isoformat()}:{RECEIPT_CHAIN}"
    placed, lines, paid = 0, 0, 0.0
    for row in goods:
        try:
            item_id = _find(row["name"])
            if item_id is None:
                continue
            item = store.item(item_id)
            product_id = refresh.adopt(item_id)
            qty = float(row["qty"].replace(",", "."))
            repo.set_basket_item(basket_id, product_id, qty)
            price = _paid(item["chains"])
            if price:
                total = round(qty * price, 2)
                repo.add_history_row(today.isoformat(), lenta.id, product_id, item["name"],
                                     qty, price, total, receipt_key=receipt_key)
                lines, paid = lines + 1, paid + total
            placed += 1
        except Exception:  # noqa: BLE001 — одна строка списка не должна ронять витрину
            log.warning("демо: «%s» не легло", row.get("name"), exc_info=True)
    if lines:
        repo.mark_receipt_imported(receipt_key, today.isoformat(), lenta.name, round(paid, 2),
                                   lines, source="file")
    return placed


def _fill_offers(today: date) -> None:
    """Карты и акции вымышленных банков из data/seed_offers.csv — но с живым сроком."""
    _seed().seed_cards_and_offers()
    valid_from, valid_to = offer_window(today)
    with get_conn() as conn:
        conn.execute("UPDATE offers SET valid_from = ?, valid_to = ?", (valid_from, valid_to))


def _refill() -> None:
    today = date.today()
    # Список читается ДО стирания: не прочитался — прежняя витрина остаётся как была.
    with open(LIST_FILE, encoding="utf-8-sig", newline="") as fh:
        goods = list(csv.DictReader(fh))
    _reset()
    try:
        placed = _fill_from_catalog(goods, today)
    except Exception:  # noqa: BLE001 — единая база не открылась: для витрины то же, что «товаров нет»
        log.exception("демо: единая база не открылась")
        placed = 0
    if placed < MIN_GOODS:
        # Пустая витрина хуже вымышленной: остаётся прежний набор tools/seed.py.
        log.warning("демо: из единой базы легло %d товаров из %d — беру вымышленный набор",
                    placed, len(goods))
        _reset()
        _seed().main()
    else:
        log.info("демо: из единой базы легло %d товаров из %d", placed, len(goods))
    _fill_offers(today)
    # Версия — последней: оборвавшаяся сборка не должна выглядеть законченной.
    repo.set_setting(VERSION_KEY, DEMO_VERSION)


def ensure() -> bool:
    """Витрина демо налита нужной версией? Нет — собрать заново. True — собрана сейчас.

    Зовётся при УЖЕ открытом рабочем месте demo. Два посетителя, нажавшие одновременно,
    не стирают друг другу сборку: второй ждёт замок и видит готовую витрину.
    """
    if users.current() != users.DEMO:
        log.warning("демо: открыто рабочее место %r, а не demo — витрину не трогаю", users.current())
        return False
    try:
        if _is_current():
            return False
        with _LOCK:
            if _is_current():
                return False
            _refill()
        return True
    except Exception:  # noqa: BLE001 — витрина без свежих данных лучше, чем отказ войти
        log.exception("демо: витрина не собралась")
        return False


__all__ = ["ensure", "offer_window", "DEMO_VERSION"]
