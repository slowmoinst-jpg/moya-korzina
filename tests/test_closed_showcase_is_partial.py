"""Витрина закрыта, а товары есть — обход обязан сказать, что он НЕПОЛНЫЙ.

Замер 18.10.2026: ночной обход Самоката записался `ok` с двумя товарами, взятыми из
личной базы и чеков, хотя витрина всё это время отвечала страницей защиты ServicePipe.
Полный статус врёт дважды: на экране «Магазины» сеть выглядит обойденной, а плановый
обход считает её свежей и пропускает 12 часов (`store.fresh_chains` принимает только ok).
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import refresh, store  # noqa: E402
from app.catalog.crawlers.pyaterochka import PyaterochkaCrawler  # noqa: E402
from app.catalog.crawlers.samokat import SamokatCrawler  # noqa: E402
from app.catalog.model import ChainProduct, CrawlBlocked  # noqa: E402


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


@pytest.mark.parametrize("cls,code", [(PyaterochkaCrawler, "pyaterochka"),
                                      (SamokatCrawler, "samokat")])
def test_blocked_showcase_with_rows_from_the_base_is_partial(monkeypatch, base, cls, code):
    crawler = cls([])
    crawler.route_note = ""

    def browser(say):
        raise CrawlBlocked(f"{code}: витрина закрыта защитой ServicePipe")
        yield                                     # генератор, как у настоящего маршрута

    monkeypatch.setattr(crawler, "_crawl_browser", browser)
    monkeypatch.setattr(crawler, "_from_store_products",
                        lambda: iter([ChainProduct(sku="111", name="Товар из чека")]))

    result = refresh.run_chain(crawler)

    assert result["status"] == "partial", "закрытая витрина — не полный обход"
    assert "витрина закрыта" in result["note"]
    assert "баз" in result["note"] or "чеков" in result["note"]
    assert store.fresh_chains(1).get(code) is None, "неполный обход не считается свежим"


@pytest.mark.parametrize("cls", [PyaterochkaCrawler, SamokatCrawler])
def test_a_working_showcase_still_reports_ok(monkeypatch, base, cls):
    crawler = cls([])
    crawler.route_note = ""
    monkeypatch.setattr(crawler, "_crawl_browser",
                        lambda say: iter([ChainProduct(sku="222", name="Товар витрины")]))
    monkeypatch.setattr(crawler, "_from_store_products", lambda: iter([]))

    result = refresh.run_chain(crawler)

    assert result["status"] == "ok"
    assert result["note"] is None
