"""Артикул Дикси — в заглавном виде, каким его знает сама сеть.

ЗАЧЕМ (замеры 14–15.11.2026). Адрес карточки пишет артикул строчным («…-di00078791/»), а витрина
и корзина сети знают его заглавным (xml_id «DI00078791»). По карте сайта тот же товар заводился
в каталоге второй строкой — латинское название из адреса, без цены: 122 пары, 91 из них в разных
единых товарах. В корзине владельца строка с артикулом «di00078791» получала от сети «в наличии 0».
Здесь сторожа трёх правок: артикул из адреса сразу заглавный, каталог сливает пары (схема 9),
база человека переводит свои строки и связи (схема 4).
"""
from __future__ import annotations

import sqlite3

import pytest

from app import db
from app.catalog import fromurl, refresh, store
from app.catalog.crawlers.dixy import from_address
from app.catalog.model import ChainProduct as P
from app.connectors import dixy as connector

CARD = "https://dixy.ru/product/khlopya-yasno-solnyshko-3-350g-di00078791/"


def test_the_address_gives_the_article_as_the_chain_knows_it():
    assert from_address(CARD).sku == "DI00078791"
    assert fromurl.sku_of("dixy", CARD) == "DI00078791"
    assert fromurl.sku_of("dixy", "https://dixy.ru/product/moloko-parmalat-2000328870/") == "2000328870"
    # У Самоката артикул — строчный слаг: правило только для Дикси.
    assert fromurl.sku_of("samokat", "https://samokat.ru/product/khurma-korolyok-500-g") == "khurma-korolyok-500-g"
    assert connector.is_product_url(CARD, "DI00078791")
    assert connector.is_bare_url("https://dixy.ru/product/di00078791/", "DI00078791")


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()


def test_the_catalog_merges_the_twins_into_the_capital_row(catalog):
    store.upsert_products("dixy", [
        P("DI00078791", "Хлопья Ясно солнышко овсяные №3 350г", weight_g=350, unit="pcs", price=59.99),
        P("di00078791", "khlopya yasno solnyshko 3 350g", url=CARD),
        P("tst0033464", "syrok tvorozhnyy svitlogore 50g", url="https://dixy.ru/product/syrok-tst0033464/"),
    ])
    store.upsert_products("magnit", [P("7", "Сырок творожный 50г", weight_g=50, unit="pcs")])
    refresh.match_all()
    for sku in ("di00078791", "DI00078791", "tst0033464"):
        store.note_miss("dixy", sku)
    with store.connect() as conn:
        items = {r["sku"]: r["item_id"] for r in conn.execute("SELECT sku, item_id FROM chain_products")}
        conn.execute("PRAGMA user_version = 8")                 # база до правки
        conn.commit()
    assert items["di00078791"] != items["DI00078791"], "проба не та: пара должна быть разведена"

    store.init()                                                # миграция 8 → 9

    with store.connect() as conn:
        rows = {r["sku"]: dict(r) for r in conn.execute("SELECT * FROM chain_products WHERE chain='dixy'")}
        misses = {r["sku"] for r in conn.execute("SELECT sku FROM chain_misses WHERE chain='dixy'")}
        left = {r["id"] for r in conn.execute("SELECT id FROM catalog_items")}
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
    assert set(rows) == {"DI00078791", "TST0033464"}
    top = rows["DI00078791"]
    assert (top["price"], top["weight_g"], top["url"], top["active"]) == (59.99, 350, CARD, 1)
    assert top["item_id"] == items["DI00078791"]
    assert rows["TST0033464"]["item_id"] == items["tst0033464"]      # одинокая строка просто переименована
    assert misses == {"DI00078791", "TST0033464"}
    # Единый товар строчной строки остался без строк: он убран, а его номер ведёт к товару заглавной.
    assert items["di00078791"] not in left
    assert store.resolve_alias(items["di00078791"]) == items["DI00078791"]


def test_the_personal_base_moves_links_to_the_capital_article():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(db.SCHEMA_SQL)
    db._add_late_columns(conn)
    conn.execute("INSERT INTO stores (id, code, name, delivery_fee, free_delivery_from, min_order,"
                 " connector_type) VALUES (1,'dixy','Дикси',0,0,0,'api'), (2,'samokat','Самокат',0,0,0,'api')")
    conn.execute("INSERT INTO products (id, name, unit) VALUES (1,'Хлопья','pcs'), (2,'Сырок','pcs'),"
                 " (3,'Конфеты','pcs')")
    conn.execute("INSERT INTO store_products (id, store_id, sku, raw_name) VALUES"
                 " (1,1,'di00078791','khlopya yasno solnyshko'), (2,1,'DI00078791','Хлопья Ясно солнышко'),"
                 " (3,1,'tst0033464','syrok'), (4,1,'hist-abc12345','строка чека'),"
                 " (5,2,'khurma-korolyok-500-g','Хурма')")
    conn.execute("INSERT INTO product_mapping (product_id, store_product_id, confirmed, confirmed_at, method)"
                 " VALUES (1,1,1,'2026-11-01T10:00:00','master'), (1,2,0,NULL,'master'),"
                 " (2,3,1,'2026-11-02T10:00:00',NULL), (3,1,1,'2026-11-03T10:00:00',NULL)")
    conn.execute("INSERT INTO store_prices (store_product_id, price, fetched_at) VALUES (1, 59.99, '2026-11-01')")
    conn.execute("INSERT INTO link_rejects (product_id, store_id, sku, at) VALUES"
                 " (2,1,'di00078791','2026-11-01'), (2,1,'*','2026-11-01')")
    conn.execute("PRAGMA user_version = 3")

    db._migrate(conn)

    skus = {r["id"]: r["sku"] for r in conn.execute("SELECT id, sku FROM store_products")}
    links = {(r["product_id"], r["store_product_id"]): (r["confirmed"], r["method"], r["confirmed_at"])
             for r in conn.execute("SELECT * FROM product_mapping")}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    # Заглушка чека и слаг Самоката не тронуты, одинокий артикул переименован, строчная пара убрана.
    assert skus == {2: "DI00078791", 3: "TST0033464", 4: "hist-abc12345", 5: "khurma-korolyok-500-g"}
    # Подтверждение строчной строки перешло заглавной, выбор человека переехал вместе с ней.
    assert links == {(1, 2): (1, "master", "2026-11-01T10:00:00"), (2, 3): (1, None, "2026-11-02T10:00:00"),
                     (3, 2): (1, None, "2026-11-03T10:00:00")}
    assert [r["store_product_id"] for r in conn.execute("SELECT store_product_id FROM store_prices")] == [2]
    assert {r["sku"] for r in conn.execute("SELECT sku FROM link_rejects")} == {"DI00078791", "*"}
