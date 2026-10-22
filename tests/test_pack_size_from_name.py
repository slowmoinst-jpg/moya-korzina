"""Фасовка и единица у товаров, чьё имя собрано из адреса или пришло новой формой.

ДВА ЖИВЫХ ДЕФЕКТА, НАЙДЕННЫХ 18.10.2026 НА БОЕВОЙ БАЗЕ.

1. METRO сменил форму `packing`: было `{"weight": 1, "unit": "kg"}`, стало
   `{"size": 1, "type": "шт"}` / `{"size": 0.25, "type": "кг"}`. Разбор искал прежние
   ключи, и фасовка пропала у ВСЕХ 32 833 строк сети — при том что цена, штрихкод и
   остаток были на месте.
2. Перекрёсток, Впрок и Монетка собирают имя из адреса карты сайта
   («maslo-slivochnoe-…-500g» → «maslo slivochnoe … 500g») и вовсе не разбирали из него
   фасовку: 214 021 строка без веса и без единицы.

Почему это важно не сопоставлению (оно достаёт вес из названия само), а человеку: его
товар привязывается к строке сети, и размер упаковки едет в привязку. Замер: у владельца
140 привязок METRO и ни одной с весом — наряд не мог пересчитать, сколько упаковок класть
в корзину сети, а цена за килограмм не считалась.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import store  # noqa: E402
from app.catalog.crawlers import slug_size  # noqa: E402
from app.catalog.crawlers.metro import _unit, _weight_g  # noqa: E402
from app.catalog.crawlers.monetka import parse as monetka_parse  # noqa: E402
from app.catalog.crawlers.perekrestok import parse as perekrestok_parse  # noqa: E402
from app.catalog.crawlers.vprok import parse as vprok_parse  # noqa: E402


# --- METRO: новая форма packing ---
@pytest.mark.parametrize("row,weight,unit", [
    # Живые ответы шлюза 18.10.2026.
    ({"name": "aro Сахар-песок ТС2 белый кристаллический, 1кг",
      "packing": {"size": 1, "type": "шт"}}, 1000.0, "pcs"),
    ({"name": "Лимоны весовые", "packing": {"size": 0.25, "type": "кг"}}, None, "kg"),
    ({"name": "Огурцы короткоплодные", "packing": {"size": 0.5, "type": "кг"}}, None, "kg"),
    ({"name": "Молоко Простоквашино 2,5% 930 мл", "packing": {"size": 1, "type": "шт"}},
     930.0, "pcs"),
    # Прежняя форма: сеть вправе вернуть её обратно.
    ({"name": "Сахар", "packing": {"weight": 1, "unit": "kg"}}, 1000.0, "pcs"),
])
def test_metro_takes_the_size_from_the_new_packing_and_the_name(row, weight, unit):
    assert _weight_g(row) == weight
    assert _unit(row) == unit


def test_metro_weighted_goods_do_not_get_a_made_up_weight():
    """`size` у весового — подсказка порции, а не фасовка: 0,25 кг лимонов ≠ пачка."""
    row = {"name": "Бананы весовые", "packing": {"size": 0.7, "type": "кг"}}

    assert _weight_g(row) is None
    assert _unit(row) == "kg"


# --- Перекрёсток, Впрок, Монетка: имя из адреса ---
@pytest.mark.parametrize("parse,url,weight,unit", [
    (perekrestok_parse,
     "https://www.perekrestok.ru/cat/121/p/maslo-slivocnoe-icalki-krestanskoe-72-5-500g-3636734",
     500.0, "pcs"),
    (perekrestok_parse,
     "https://www.perekrestok.ru/cat/200/p/kartofel-vesovoj-1234567", None, "kg"),
    (vprok_parse, "https://www.vprok.ru/product/linakva-linakva-bebi-0-9-aerozol-150-ml--1284993",
     150.0, "pcs"),
    (vprok_parse, "https://www.vprok.ru/product/sahar-1-kg--1285000", 1000.0, "pcs"),
    (monetka_parse,
     "https://monetka.ru/product/sok-dobryjj-yabloko-1l-rossiya-810000639/", 1000.0, "pcs"),
    (monetka_parse, "https://monetka.ru/product/banany-vesovye-810000640/", None, "kg"),
])
def test_the_name_from_the_card_address_gives_size_and_unit(parse, url, weight, unit):
    product = parse(url)

    assert product is not None, url
    assert product.weight_g == weight
    assert product.unit == unit


def test_a_named_size_is_a_piece_not_a_weight_sale():
    """«sahar 1kg» — пачка килограмма, а не товар на развес: цена за кг не должна уехать.

    Название без фасовки и без «весов…» ничего не говорит о единице: пусто — честнее,
    чем догадка «pcs», которая заставила бы сопоставление отсекать пары «kg ↔ pcs».
    """
    assert slug_size("sahar 1kg") == (1000.0, "pcs")
    assert slug_size("kartofel vesovoj") == (None, "kg")
    assert slug_size("polotence 50h80sm") == (None, None)


# --- миграция схемы 7: старые строки получают фасовку и единицу ---
@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


def test_the_migration_fills_size_for_rows_written_without_it(base):
    with store.connect() as conn:
        conn.execute("INSERT INTO chain_products (chain, sku, name, weight_g, unit,"
                     " first_seen, last_seen, active) VALUES"
                     " ('perekrestok','1','maslo slivochnoe 500g',NULL,NULL,'2026-09-16','2026-09-16',1),"
                     " ('metro','2','Лимоны весовые',NULL,NULL,'2026-09-16','2026-09-16',1),"
                     " ('metro','3','Сахар, 1кг',NULL,NULL,'2026-09-16','2026-09-16',1),"
                     " ('lenta','4','Молоко 930 мл',930,'pcs','2026-09-16','2026-09-16',1)")
        conn.execute("PRAGMA user_version = 6")      # база до миграции
        conn.commit()

    store.init()                                     # миграция 6 → 7 (и далее до текущей)

    with store.connect() as conn:
        rows = {str(r["sku"]): dict(r) for r in conn.execute(
            "SELECT sku, weight_g, unit FROM chain_products")}
        # Версию сверяем с константой, а не с числом: миграция доводит базу до ТЕКУЩЕЙ
        # схемы, и каждый новый шаг ломал бы этот тест зря (21.10.2026: шаг 8 — метка
        # цены без цены).
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION

    # Фасовку из названия миграция достаёт; единицу — только когда в названии сказано
    # «весов…» (иначе «pcs» было бы догадкой и отсекало верные пары «kg ↔ pcs»).
    assert rows["1"]["weight_g"] == 500 and rows["1"]["unit"] is None
    assert rows["2"]["weight_g"] is None and rows["2"]["unit"] == "kg"
    assert rows["3"]["weight_g"] == 1000 and rows["3"]["unit"] is None
    # Уже проставленное не трогаем: у Ленты вес и единица свои.
    assert rows["4"]["weight_g"] == 930 and rows["4"]["unit"] == "pcs"


def test_the_personal_base_fills_size_in_the_mappings_too():
    """Личная база: строки магазинов, лёгшие без веса, получают его при выкладке.

    Это то, что видит человек: привязка товара к сети едет с размером упаковки, и наряд
    может пересчитать упаковки. Замер 18.10.2026: у владельца 140 привязок METRO и ни
    одной с весом.
    """
    import sqlite3

    from app import db

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA_SQL)
    conn.execute("INSERT INTO stores (id, code, name, delivery_fee, free_delivery_from,"
                 " min_order, connector_type) VALUES (1,'metro','METRO',0,0,0,'api')")
    conn.execute("INSERT INTO store_products (id, store_id, sku, raw_name, weight_g, unit) VALUES"
                 " (1,1,'1001','Сахар-песок белый кристаллический, 1кг',NULL,NULL),"
                 " (2,1,'1002','Лимоны весовые',NULL,NULL),"
                 " (3,1,'1003','Молоко 930 мл',930,'pcs')")
    conn.execute("PRAGMA user_version = 2")

    db._migrate(conn)

    rows = {r["id"]: dict(r) for r in conn.execute("SELECT id, weight_g, unit FROM store_products")}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert rows[1]["weight_g"] == 1000 and rows[1]["unit"] is None
    assert rows[2]["weight_g"] is None and rows[2]["unit"] == "kg"
    assert rows[3]["weight_g"] == 930 and rows[3]["unit"] == "pcs"
