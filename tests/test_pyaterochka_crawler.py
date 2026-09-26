"""Тесты сборщика каталога Пятёрочки."""
from __future__ import annotations

import os
import sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog.crawlers import registry
from app.catalog.crawlers.pyaterochka import (
    PyaterochkaCrawler,
    parse_card_text,
    sku_from_url,
    to_product,
)
from app.catalog.model import CrawlBlocked


def test_sku_from_url():
    assert sku_from_url("https://5ka.ru/product/moloko-prostokvashino--78001656/") == "78001656"
    assert sku_from_url("https://5ka.ru/product/baton-nareznoi--4306830") == "4306830"
    assert sku_from_url("/product/sahar-1kg--999999?source=search") == "999999"
    assert sku_from_url("https://5ka.ru/catalog/molochnye-produkty/") is None
    assert sku_from_url(None) is None


def test_parse_card_text_standard():
    raw = "4,85 Молоко Простоквашино пастеризованное 2.5% 930 мл 89 99 ₽"
    res = parse_card_text(raw)
    assert res is not None
    assert res["name"] == "Молоко Простоквашино пастеризованное 2.5% 930 мл"
    assert res["price"] == 89.99
    assert res["weight_g"] == 930.0
    assert res["unit"] == "pcs"


def test_parse_card_text_with_price_per_100g():
    raw = "Сыр Российский Брест-Литовск 200 г 199 90 ₽ Цена за 100 г"
    res = parse_card_text(raw)
    assert res is not None
    assert res["name"] == "Сыр Российский Брест-Литовск 200 г"
    assert res["price"] == 199.90
    assert res["weight_g"] == 200.0


def test_parse_card_text_simple_format():
    raw = "Батон Нарезной 400 г 39.50 ₽"
    res = parse_card_text(raw)
    assert res is not None
    assert res["name"] == "Батон Нарезной 400 г"
    assert res["price"] == 39.50
    assert res["weight_g"] == 400.0


def test_to_product():
    card = {
        "sku": "78001656",
        "name": "Творог Домик в деревне 9% 170 г",
        "price": 99.99,
        "brand": "Домик в деревне",
        "url": "https://5ka.ru/product/tvorog--78001656/",
    }
    prod = to_product(card)
    assert prod is not None
    assert prod.sku == "78001656"
    assert prod.name == "Творог Домик в деревне 9% 170 г"
    assert prod.price == 99.99
    assert prod.weight_g == 170.0
    assert prod.unit == "pcs"
    assert prod.brand == "Домик в деревне"
    assert prod.in_stock is True


def test_to_product_sku_from_url():
    card = {
        "name": "Масло сливочное 82.5% 180 г",
        "price": 189.0,
        "url": "https://5ka.ru/product/maslo-slivochnoe--55443322/",
    }
    prod = to_product(card)
    assert prod is not None
    assert prod.sku == "55443322"
    assert prod.weight_g == 180.0


def test_pyaterochka_registered():
    reg = registry()
    assert "pyaterochka" in reg
    assert reg["pyaterochka"] is PyaterochkaCrawler


def test_crawler_from_store_products(monkeypatch):
    from app import repo

    class FakeStore:
        id = 42
        code = "pyaterochka"

    class FakeRow(dict):
        def __getitem__(self, item):
            return self.get(item)

    monkeypatch.setattr(repo, "get_store", lambda code: FakeStore() if code == "pyaterochka" else None)

    class FakeConn:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, q, params):
            class Cur:
                def fetchall(self):
                    return [
                        FakeRow({"sku": "111", "raw_name": "Молоко 1 л", "barcode": "4600000000001", "url": None, "price": 79.9}),
                        FakeRow({"sku": "222", "raw_name": "Хлеб 300 г", "barcode": None, "url": "https://5ka.ru/p/222", "price": 45.0}),
                    ]
            return Cur()

    monkeypatch.setattr(repo, "get_conn", lambda: FakeConn())

    crawler = PyaterochkaCrawler(sections=[])
    # Browser crawl will fail or do nothing since sections is empty, then fallback to DB
    products = list(crawler._from_store_products())
    assert len(products) == 2
    assert products[0].sku == "111"
    assert products[0].price == 79.9
    assert products[0].weight_g == 1000.0
    assert products[1].sku == "222"
    assert products[1].price == 45.0
    assert products[1].weight_g == 300.0


def test_crawl_blocked_when_empty(monkeypatch):
    crawler = PyaterochkaCrawler(sections=[])
    monkeypatch.setattr(crawler, "_crawl_browser", lambda say: iter([]))
    monkeypatch.setattr(crawler, "_from_store_products", lambda: iter([]))

    with pytest.raises(CrawlBlocked):
        list(crawler.crawl())
