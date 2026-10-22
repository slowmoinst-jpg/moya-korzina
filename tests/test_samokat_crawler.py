"""Тесты сборщика каталога Самоката."""
from __future__ import annotations

import inspect
import logging
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
from app.catalog.model import ChainProduct, CrawlBlocked


def test_sku_from_url():
    """Ключ Самоката — весь слаг: артикула в адресе у сети нет (замер 19.10.2026).

    Прежнее правило искало артикул в хвосте и доставало «78001656», «889900» —
    цифры, которых сеть в адресе не печатает; на живой витрине это давало «g», «l»,
    «rulona». Ключом стал слаг: он уникален и открывает ту же карточку.
    """
    assert sku_from_url("https://samokat.ru/product/123456") == "123456"
    assert sku_from_url("https://samokat.ru/product/khurma-korolyok-500-g") == \
        "khurma-korolyok-500-g"
    assert sku_from_url("/product/banany-1kg-889900") == "banany-1kg-889900"
    assert sku_from_url("https://samokat.ru/product/moloko-prostokvashino-78001656") == \
        "moloko-prostokvashino-78001656"
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


# ---------- добор из баз людей и из общей ----------
#
# ЗАЧЕМ ЭТИ СТОРОЖА (18.10.2026). Обходчик каталога работает с ОБЩЕЙ базой, а товары,
# принятые закладкой «Забрать цены» и чеками, лежат в базах рабочих мест. Маршрут
# добора читал только текущую базу и видел 0 товаров — единый каталог Самоката и
# Пятёрочки оставался пустым. Сторожа держат правило: базы ВСЕХ людей плюс общая.


def _row(sku, name, **rest):
    base = {"sku": sku, "raw_name": name, "barcode": None, "url": None, "price": None}
    base.update(rest)

    class FakeRow(dict):
        def __getitem__(self, item):
            return self.get(item)

    return FakeRow(base)


def _patch_dbs(monkeypatch, per_db: dict, workspaces: list[str], broken: set[str] = frozenset()):
    """Базы как настоящие: у каждого рабочего места своя, общая — без контекста.

    per_db — {None: [строки общей базы], "79990000001": [строки его базы]};
    broken — рабочие места, чья база «не открылась».
    """
    from app import repo, users

    class FakeStore:
        id = 99

    monkeypatch.setattr(repo, "get_store", lambda code: FakeStore())
    monkeypatch.setattr(users, "list_workspaces", lambda: list(workspaces))

    class FakeConn:
        def __init__(self, rows):
            self.rows = rows

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, q, params):
            rows = self.rows

            class Cur:
                def fetchall(self):
                    return rows

            return Cur()

    def get_conn():
        phone = users.current()
        if phone in broken:
            raise RuntimeError("database disk image is malformed")
        return FakeConn(per_db.get(phone, []))

    monkeypatch.setattr(repo, "get_conn", get_conn)


def test_from_store_products_reads_workspace_db(monkeypatch):
    """Товары закладки человека лежат в базе ЕГО рабочего места — и попадают в добор."""
    _patch_dbs(monkeypatch, {
        None: [],
        "79990000001": [_row("1184109", "Кола Добрый 1 л",
                             barcode="4600000000001", price=99.0)],
    }, workspaces=["79990000001"])

    products = list(SamokatCrawler(categories=[])._from_store_products())
    assert [p.sku for p in products] == ["1184109"]
    assert products[0].weight_g == 1000.0        # вес из названия
    assert products[0].unit == "pcs"             # единица — только когда известна
    assert products[0].barcode == "4600000000001"
    assert products[0].price == 99.0             # последняя цена из базы
    # адрес по умолчанию — карточка сети, если в базе адреса нет
    assert products[0].url == "https://samokat.ru/product/1184109/"


def test_from_store_products_reads_general_db(monkeypatch):
    """Из общей базы добор тоже читает: часть товаров приходит туда чеками и прайсом."""
    _patch_dbs(monkeypatch, {
        None: [_row("1103386", "Чипсы Lay's 80 г", price=129.0)],
        "79990000001": [],
    }, workspaces=["79990000001"])

    products = list(SamokatCrawler(categories=[])._from_store_products())
    assert [p.sku for p in products] == ["1103386"]
    assert products[0].price == 129.0


def test_from_store_products_skips_placeholders(monkeypatch):
    """Заглушки (чек, прайс, справочник, стенд) в каталог сети не идут."""
    _patch_dbs(monkeypatch, {
        None: [
            _row("hist-1", "Строка чека"),
            _row("list-2", "Строка прайса"),
            _row("samokat-preview-3", "Строка стенда"),
            _row("preview-samokat-4", "Строка стенда"),
            _row("1000043", "Snickers 50 г"),
        ],
        "79990000001": [],
    }, workspaces=["79990000001"])

    products = list(SamokatCrawler(categories=[])._from_store_products())
    assert [p.sku for p in products] == ["1000043"]


def test_from_store_products_dedups_sku(monkeypatch):
    """Один товар в двух базах — в доборе он один раз (правило — в общем помощнике)."""
    _patch_dbs(monkeypatch, {
        None: [_row("1184109", "Кола Добрый 1 л", price=89.0)],
        "79990000001": [_row("1184109", "Кола Добрый 1 л", price=99.0)],
    }, workspaces=["79990000001"])

    products = list(SamokatCrawler(categories=[])._from_store_products())
    assert len(products) == 1
    assert products[0].price == 89.0             # первая база (общая) выигрывает


def test_broken_db_does_not_break_crawl(monkeypatch, caplog):
    """Битая база одного рабочего места — пропуск с предупреждением, а не падение."""
    _patch_dbs(monkeypatch, {
        None: [_row("1000043", "Snickers 50 г")],
        "79990000001": [],
    }, workspaces=["79990000001"], broken={"79990000001"})

    with caplog.at_level(logging.WARNING):
        products = list(SamokatCrawler(categories=[])._from_store_products())

    assert [p.sku for p in products] == ["1000043"]
    assert "не прочиталась" in caplog.text


def test_dobor_returns_own_workspace_back(monkeypatch):
    """Своя база, активная до вызова, остаётся активной и после (finally)."""
    from app import users

    _patch_dbs(monkeypatch, {None: []}, workspaces=[])
    users.activate("79990000009")
    try:
        list(SamokatCrawler(categories=[])._from_store_products())
        assert users.current() == "79990000009"
    finally:
        users.deactivate()


def test_dobor_uses_shared_helper(monkeypatch):
    """Сборщик зовёт общий помощник, а не своё чтение (одно правило на обе сети)."""
    from app.catalog.crawlers import samokat as module

    calls = []

    def fake_rows(chain):
        calls.append(chain)
        return iter([_row("1184109", "Кола Добрый 1 л", price=99.0)])

    monkeypatch.setattr(module, "store_product_rows", fake_rows)
    products = list(module.SamokatCrawler(categories=[])._from_store_products())
    assert calls == ["samokat"]
    assert [p.sku for p in products] == ["1184109"]


def test_no_own_sql_in_crawler_module():
    """SQL чтения store_products живёт в одном месте — у общего помощника."""
    from app.catalog.crawlers import samokat as module

    assert "FROM store_products" not in inspect.getsource(module)


def test_crawl_does_not_repeat_sku(monkeypatch):
    """Артикул, уже взятый с витрины, из базы второй раз не идёт (seen в crawl)."""
    crawler = SamokatCrawler(categories=[])
    from_browser = ChainProduct(sku="1184109", name="Кола Добрый 1 л")
    from_db = [ChainProduct(sku="1184109", name="Кола Добрый 1 л"),
               ChainProduct(sku="1000043", name="Snickers 50 г")]
    monkeypatch.setattr(crawler, "_crawl_browser", lambda say: iter([from_browser]))
    monkeypatch.setattr(crawler, "_from_store_products", lambda: iter(from_db))

    assert [p.sku for p in crawler.crawl()] == ["1184109", "1000043"]
