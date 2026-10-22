"""Тесты сборщика каталога Пятёрочки."""
from __future__ import annotations

import inspect
import logging
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
from app.catalog.model import ChainProduct, CrawlBlocked


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


# ---------- добор из баз людей и из общей ----------
#
# ЗАЧЕМ ЭТИ СТОРОЖА (18.10.2026). Обходчик каталога работает с ОБЩЕЙ базой, а товары,
# принятые закладкой «Забрать цены» и чеками, лежат в базах рабочих мест. Маршрут
# добора читал только текущую базу и видел 0 товаров — единый каталог Пятёрочки и
# Самоката оставался пустым. Сторожа держат правило: базы ВСЕХ людей плюс общая.


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
        id = 42

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
        "79990000001": [_row("4306830", "Батон Нарезной 400 г",
                             barcode="4600000000002", price=39.5)],
    }, workspaces=["79990000001"])

    products = list(PyaterochkaCrawler(sections=[])._from_store_products())
    assert [p.sku for p in products] == ["4306830"]
    assert products[0].weight_g == 400.0         # вес из названия
    assert products[0].unit == "pcs"             # единица — только когда известна
    assert products[0].barcode == "4600000000002"
    assert products[0].price == 39.5             # последняя цена из базы
    # адрес по умолчанию — карточка сети, если в базе адреса нет
    assert products[0].url == "https://5ka.ru/product/4306830/"


def test_from_store_products_reads_general_db(monkeypatch):
    """Из общей базы добор тоже читает: часть товаров приходит туда чеками и прайсом."""
    _patch_dbs(monkeypatch, {
        None: [_row("78001656", "Творог Домик в деревне 9% 170 г", price=99.99)],
        "79990000001": [],
    }, workspaces=["79990000001"])

    products = list(PyaterochkaCrawler(sections=[])._from_store_products())
    assert [p.sku for p in products] == ["78001656"]
    assert products[0].price == 99.99


def test_from_store_products_skips_placeholders(monkeypatch):
    """Заглушки (чек, прайс, справочник, стенд) в каталог сети не идут."""
    _patch_dbs(monkeypatch, {
        None: [
            _row("hist-1", "Строка чека"),
            _row("list-2", "Строка прайса"),
            _row("pyaterochka-preview-3", "Строка стенда"),
            _row("preview-pyaterochka-4", "Строка стенда"),
            _row("55443322", "Масло сливочное 82.5% 180 г"),
        ],
        "79990000001": [],
    }, workspaces=["79990000001"])

    products = list(PyaterochkaCrawler(sections=[])._from_store_products())
    assert [p.sku for p in products] == ["55443322"]


def test_from_store_products_dedups_sku(monkeypatch):
    """Один товар в двух базах — в доборе он один раз (правило — в общем помощнике)."""
    _patch_dbs(monkeypatch, {
        None: [_row("78001656", "Творог Домик в деревне 9% 170 г", price=89.0)],
        "79990000001": [_row("78001656", "Творог Домик в деревне 9% 170 г", price=99.99)],
    }, workspaces=["79990000001"])

    products = list(PyaterochkaCrawler(sections=[])._from_store_products())
    assert len(products) == 1
    assert products[0].price == 89.0             # первая база (общая) выигрывает


def test_broken_db_does_not_break_crawl(monkeypatch, caplog):
    """Битая база одного рабочего места — пропуск с предупреждением, а не падение."""
    _patch_dbs(monkeypatch, {
        None: [_row("55443322", "Масло сливочное 82.5% 180 г")],
        "79990000001": [],
    }, workspaces=["79990000001"], broken={"79990000001"})

    with caplog.at_level(logging.WARNING):
        products = list(PyaterochkaCrawler(sections=[])._from_store_products())

    assert [p.sku for p in products] == ["55443322"]
    assert "не прочиталась" in caplog.text


def test_dobor_returns_own_workspace_back(monkeypatch):
    """Своя база, активная до вызова, остаётся активной и после (finally)."""
    from app import users

    _patch_dbs(monkeypatch, {None: []}, workspaces=[])
    users.activate("79990000009")
    try:
        list(PyaterochkaCrawler(sections=[])._from_store_products())
        assert users.current() == "79990000009"
    finally:
        users.deactivate()


def test_dobor_uses_shared_helper(monkeypatch):
    """Сборщик зовёт общий помощник, а не своё чтение (одно правило на обе сети)."""
    from app.catalog.crawlers import pyaterochka as module

    calls = []

    def fake_rows(chain):
        calls.append(chain)
        return iter([_row("78001656", "Творог Домик в деревне 9% 170 г", price=99.99)])

    monkeypatch.setattr(module, "store_product_rows", fake_rows)
    products = list(module.PyaterochkaCrawler(sections=[])._from_store_products())
    assert calls == ["pyaterochka"]
    assert [p.sku for p in products] == ["78001656"]


def test_no_own_sql_in_crawler_module():
    """SQL чтения store_products живёт в одном месте — у общего помощника."""
    from app.catalog.crawlers import pyaterochka as module

    assert "FROM store_products" not in inspect.getsource(module)


def test_crawl_does_not_repeat_sku(monkeypatch):
    """Артикул, уже взятый с витрины, из базы второй раз не идёт (seen в crawl)."""
    crawler = PyaterochkaCrawler(sections=[])
    from_browser = ChainProduct(sku="78001656", name="Творог Домик в деревне 9% 170 г")
    from_db = [ChainProduct(sku="78001656", name="Творог Домик в деревне 9% 170 г"),
               ChainProduct(sku="55443322", name="Масло сливочное 82.5% 180 г")]
    monkeypatch.setattr(crawler, "_crawl_browser", lambda say: iter([from_browser]))
    monkeypatch.setattr(crawler, "_from_store_products", lambda: iter(from_db))

    assert [p.sku for p in crawler.crawl()] == ["78001656", "55443322"]
