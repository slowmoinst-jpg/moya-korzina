"""Лента: марка и штрихкод из карточки — то, чего у её строк не было вовсе.

ЗАМЕР 18.10.2026 (боевой сервер, живой MCP Ленты). Из 60 карточек штрихкод есть
у 59, и 27 из них — единичные (EAN-13), 12 — коробочные (EAN-14). Марка есть у
каждой карточки. При этом у всех 28 574 строк Ленты в единой базе не было ни марки, ни
штрихкода: прежний сборщик брал из карточки только название, фасовку, цену и остаток.
6 карточек из 59 (10 %) уже сейчас сходятся по штрихкоду с METRO — а склейка по
штрихкоду самая твёрдая (app/catalog/match.py, первое правило same_product).
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import refresh, store  # noqa: E402
from app.catalog.crawlers import lenta as lenta_mod  # noqa: E402
from app.catalog.crawlers.lenta import LentaCrawler, to_product  # noqa: E402

# Живой ответ карточки (18.10.2026, артикул 21, хаб 359).
LIVE = {
    "id": 21,
    "name": "Горбуша натуральная, 240г",
    "price": 196.99,
    "priceRegular": 196.99,
    "stock": 0,
    "package": "240г",
    "slug": "rk-gorbusha-naturalnaya-rossiya-240g",
    "url": "https://lenta.com/product/rk-gorbusha-naturalnaya-rossiya-240g-21/",
    "brand": "PL Lenta TempSubst",
    "category": "Консервация",
    "ean": "14607019900708",
}


def test_brand_and_barcode_are_taken_from_the_card():
    product = to_product(LIVE)

    assert product.brand == "PL Lenta TempSubst"
    assert product.barcode == "14607019900708"
    assert product.weight_g == 240.0
    assert product.sku == "21"


def test_a_card_without_brand_or_barcode_does_not_invent_them():
    item = dict(LIVE)
    item.pop("brand")
    item.pop("ean")

    product = to_product(item)

    assert product.brand is None and product.barcode is None
    assert product.name == "Горбуша натуральная, 240г"


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


def test_thin_skus_are_the_ones_without_brand_barcode_or_price(base):
    """В добор идут строки без марки, без штрихкода ИЛИ БЕЗ ЦЕНЫ.

    Строка «2» полна по карточке (марка и штрихкод есть), но цены у неё нет — а карточка
    сети отдаёт и цену. Замер 19.10.2026: 813 строк Ленты лежали без цены, но с маркой и
    штрихкодом, и в добор не попадали вовсе. Строка «3» полная — ей добор не нужен.
    """
    base.upsert_products("lenta", [
        lenta_mod.ChainProduct(sku="1", name="Старое без марки"),
        lenta_mod.ChainProduct(sku="2", name="С маркой, но без цены", brand="Бренд",
                               barcode="4600000000001"),
        lenta_mod.ChainProduct(sku="3", name="Полная", brand="Бренд",
                               barcode="4600000000002", price=199.9),
    ])

    assert base.thin_skus("lenta", 10) == ["1", "2"]
    assert base.thin_skus("lenta", 0) == []


def test_the_backfill_fills_brand_and_barcode_for_known_rows(monkeypatch, base):
    """Добор идёт по живой карточке: строка получает марку и штрихкод."""
    base.upsert_products("lenta", [lenta_mod.ChainProduct(sku="21", name="Горбуша натуральная, 240г")])
    rows = base.thin_skus("lenta", 10)
    assert rows == ["21"]

    crawler = LentaCrawler(hub=359)
    monkeypatch.setattr(crawler, "details", lambda sku: (dict(LIVE, id=int(sku)), lenta_mod.OK))
    monkeypatch.setattr(crawler, "universe", lambda: ["21"])

    result = refresh.run_chain(crawler)               # как в ночном обходе: с записью в базу

    assert result["status"] == "ok"
    stored = next(r for r in base.all_products(active_only=True) if str(r["sku"]) == "21")
    assert stored["brand"] == "PL Lenta TempSubst"
    assert stored["barcode"] == "14607019900708"


def test_the_backfill_stops_at_its_own_cap(monkeypatch, base):
    """За обход — не больше backfill_per_run карточек: 28 574 зараз не спрашивают."""
    base.upsert_products("lenta", [
        lenta_mod.ChainProduct(sku=str(n), name=f"Товар {n}") for n in range(11, 21)])
    crawler = LentaCrawler(hub=359)
    crawler.backfill = 3
    asked: list[str] = []

    def details(sku):
        asked.append(sku)
        return dict(LIVE, id=int(sku)), lenta_mod.OK

    monkeypatch.setattr(crawler, "details", details)
    monkeypatch.setattr(crawler, "universe", lambda: [str(n) for n in range(11, 21)])

    refresh.run_chain(crawler)

    assert len(asked) == 3, asked
    assert len(base.thin_skus("lenta", 10)) == 7


def test_the_backfill_can_be_switched_off(monkeypatch, base):
    base.upsert_products("lenta", [lenta_mod.ChainProduct(sku="21", name="Горбуша, 240г")])
    crawler = LentaCrawler(hub=359)
    crawler.backfill = 0
    monkeypatch.setattr(crawler, "universe", lambda: [])
    monkeypatch.setattr(crawler, "details", lambda sku: (LIVE, lenta_mod.OK))

    assert list(crawler.crawl()) == []
    assert base.thin_skus("lenta", 10) == ["21"]
