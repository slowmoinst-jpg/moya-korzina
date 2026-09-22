"""Приём цен, собранных в браузере человека.

Здесь защищается не разбор JSON, а честность цифр, которые из него попадут в
расчёт: ноль не цена, акция не заменяет собой обычную цену, непонятая позиция
считается, а не пропадает, и чужая сеть в этот путь не пролезает.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, pricebundle, repo  # noqa: E402
from app.db import init_db  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "prices.db"))
    init_db()


def bundle(items, **kw):
    body = {"store": "pyaterochka", "address": "Москва, Столярный переулок 2",
            "collected_at": "2026-09-16T22:10:00", "items": items}
    body.update(kw)
    return body


ITEM = {"sku": "4306830", "name": "Азу из курицы с картофельным пюре СытоЕдов 300г",
        "price": 176.99, "base_price": 294.99, "in_stock": True,
        "unit": "pcs", "weight_g": 300,
        "url": "https://5ka.ru/product/azu-iz-kuritsy--4306830/"}


# ---------- разбор ----------
def test_parses_a_plain_bundle():
    got = pricebundle.parse(bundle([ITEM]))
    assert got["store"] == "pyaterochka"
    assert got["address"] == "Москва, Столярный переулок 2"
    assert got["skipped"] == 0
    assert got["items"][0]["price"] == 176.99
    assert got["items"][0]["base_price"] == 294.99


def test_accepts_json_text_as_well_as_an_object():
    """Файл из браузера приходит текстом — разбирать его должен тот же приёмник."""
    assert pricebundle.parse(json.dumps(bundle([ITEM]), ensure_ascii=False))["items"]


def test_price_written_as_text_is_still_a_price():
    """«176,99 ₽» — обычный вид цены в вёрстке, и ронять из-за него пакет незачем."""
    item = {**ITEM, "price": "176,99 ₽", "base_price": "294 99 ₽"}
    got = pricebundle.parse(bundle([item]))
    assert got["items"][0]["price"] == 176.99


def test_zero_price_is_not_a_price():
    """Ноль у витрин значит «здесь не продаётся», а не «бесплатно».

    Принять его ценой — значит собрать человеку корзину, которую ему не отдадут.
    """
    got = pricebundle.parse(bundle([{**ITEM, "price": 0}]))
    assert got["items"] == [] and got["skipped"] == 1


def test_item_without_sku_or_name_is_counted_not_dropped():
    """Непонятая позиция — первый признак сменившейся вёрстки, и её надо видеть."""
    got = pricebundle.parse(bundle([ITEM, {"name": "Без артикула", "price": 10},
                                    {"sku": "1", "price": 10}]))
    assert len(got["items"]) == 1
    assert got["skipped"] == 2


def test_the_same_sku_twice_is_one_position():
    """Товар лежит сразу в нескольких категориях — в базу он должен лечь один раз."""
    got = pricebundle.parse(bundle([ITEM, dict(ITEM)]))
    assert len(got["items"]) == 1


def test_foreign_store_is_refused():
    """Магнит и Лента приложение спрашивает само; пакет по ним смешал бы два источника."""
    with pytest.raises(ValueError, match="magnit"):
        pricebundle.parse(bundle([ITEM], store="magnit"))


def test_bundle_without_items_is_refused():
    with pytest.raises(ValueError, match="items"):
        pricebundle.parse({"store": "samokat"})


def test_weight_units_are_normalised():
    assert pricebundle.parse(bundle([{**ITEM, "unit": "кг"}]))["items"][0]["unit"] == "kg"
    assert pricebundle.parse(bundle([{**ITEM, "unit": "шт"}]))["items"][0]["unit"] == "pcs"


# ---------- запись ----------
def test_import_writes_product_and_price(db):
    summary = pricebundle.import_prices(bundle([ITEM]))

    assert summary["saved"] == 1 and summary["skipped"] == 0
    assert summary["store_name"] == "Пятёрочка"

    store = repo.get_store("pyaterochka")
    product = repo.upsert_store_product(store.id, "4306830", ITEM["name"])
    snap = repo.latest_price(product)
    assert snap["price"] == 176.99
    assert snap["in_stock"] == 1
    assert snap["fetched_at"] == "2026-09-16T22:10:00"


def test_import_keeps_price_history(db):
    """Снимок цены только вставляется: вчерашняя цена — тоже знание."""
    pricebundle.import_prices(bundle([ITEM]))
    pricebundle.import_prices(bundle([{**ITEM, "price": 159.99}],
                                     collected_at="2026-09-17T22:10:00"))

    store = repo.get_store("pyaterochka")
    product = repo.upsert_store_product(store.id, "4306830", ITEM["name"])
    assert repo.latest_price(product)["price"] == 159.99
    with repo.get_conn() as c:
        rows = c.execute("SELECT price FROM store_prices WHERE store_product_id=?"
                         " ORDER BY fetched_at", (product,)).fetchall()
    assert [r["price"] for r in rows] == [176.99, 159.99], "вчерашняя цена обязана остаться"


def test_out_of_stock_is_kept_out_of_stock(db):
    pricebundle.import_prices(bundle([{**ITEM, "in_stock": False}]))
    store = repo.get_store("pyaterochka")
    product = repo.upsert_store_product(store.id, "4306830", ITEM["name"])
    assert repo.latest_price(product)["in_stock"] == 0
