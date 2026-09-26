"""Тесты сборщика каталога Самоката."""
from __future__ import annotations

import os
import sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog.crawlers import registry
from app.catalog.crawlers.samokat import (
    SamokatCrawler,
    parse_card_text,
    sku_from_url,
    to_product,
)
from app.catalog.model import CrawlBlocked


def test_sku_from_url():
    assert sku_from_url("https://samokat.ru/product/123456") == "123456"
    assert sku_from_url("https://samokat.ru/product/moloko-prostokvashino-78001656") == "78001656"
    assert sku_from_url("/product/banany-1kg-889900") == "889900"
    assert sku_from_url("https://samokat.ru/category/molochnoe") is None
    assert sku_from_url(None) is None


def test_parse_card_text_standard():
    raw = "Молоко Самокат 3.2% 900 мл 109 ₽"
    res = parse_card_text(raw)
    assert res is not None
    assert res["name"] == "Молоко Самокат 3.2% 900 мл"
    assert res["price"] == 109.0
    assert res["weight_g"] == 900.0
    assert res["unit"] == "pcs"


def test_parse_card_text_with_rating():
    raw = "4.9 Круассан с миндалём 120 г 159.90 ₽"
    res = parse_card_text(raw)
    assert res is not None
    assert res["name"] == "Круассан с миндалём 120 г"
    assert res["price"] == 159.90
    assert res["weight_g"] == 120.0


def test_to_product():
    card = {
        "sku": "sam-9901",
        "name": "Сыр Тильзитер Самокат 45% 200 г",
        "price": 219.0,
        "brand": "Самокат",
        "url": "https://samokat.ru/product/sam-9901",
    }
    prod = to_product(card)
    assert prod is not None
    assert prod.sku == "sam-9901"
    assert prod.name == "Сыр Тильзитер Самокат 45% 200 г"
    assert prod.price == 219.0
    assert prod.weight_g == 200.0
    assert prod.unit == "pcs"
    assert prod.brand == "Самокат"
    assert prod.in_stock is True


def test_samokat_registered():
    reg = registry()
    assert "samokat" in reg
    assert reg["samokat"] is SamokatCrawler


def test_crawler_from_store_products(monkeypatch):
    from app import repo

    class FakeStore:
        id = 99
        code = "samokat"

    class FakeRow(dict):
        def __getitem__(self, item):
            return self.get(item)

    monkeypatch.setattr(repo, "get_store", lambda code: FakeStore() if code == "samokat" else None)

    class FakeConn:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, q, params):
            class Cur:
                def fetchall(self):
                    return [
                        FakeRow({"sku": "sam-1", "raw_name": "Вода Самокат 1.5 л", "barcode": None, "url": "https://samokat.ru/product/sam-1", "price": 49.0}),
                        FakeRow({"sku": "sam-2", "raw_name": "Масло сливочное 180 г", "barcode": "4601112223334", "url": None, "price": 199.0}),
                    ]
            return Cur()

    monkeypatch.setattr(repo, "get_conn", lambda: FakeConn())

    crawler = SamokatCrawler(categories=[])
    products = list(crawler._from_store_products())
    assert len(products) == 2
    assert products[0].sku == "sam-1"
    assert products[0].price == 49.0
    assert products[0].weight_g == 1500.0
    assert products[1].sku == "sam-2"
    assert products[1].price == 199.0
    assert products[1].weight_g == 180.0
    assert products[1].barcode == "4601112223334"


def test_crawl_blocked_when_empty(monkeypatch):
    crawler = SamokatCrawler(categories=[])
    monkeypatch.setattr(crawler, "_crawl_browser", lambda say: iter([]))
    monkeypatch.setattr(crawler, "_from_store_products", lambda: iter([]))

    with pytest.raises(CrawlBlocked):
        list(crawler.crawl())
