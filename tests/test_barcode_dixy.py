"""Сопоставление по штрихкоду и коннектор Дикси.

Штрихкод появился здесь не от любви к стандартам, а после разбора живого прогона:
«Страчателла 200 г» сопоставилась с мороженым Maxiduo, детское пюре — с соком
Сады Придонья. По названию такие промахи неизбежны, по штрихкоду невозможны.

Разбор цены Дикси проверяется на настоящем куске их страницы
(tests/fixtures/dixy_product.html), а не на выдуманной разметке.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, repo  # noqa: E402
from app.connectors import cache, dixy, get_connector, mcp_client, stub  # noqa: E402
from app.db import get_conn, init_db  # noqa: E402
from app.matcher import matcher  # noqa: E402
from app.models import Candidate, Product  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "dixy_product.html")


@pytest.fixture(autouse=True)
def clean_state():
    cache.cache_clear()
    cache.reset_throttle()
    stub.reload_rows()
    yield
    cache.cache_clear()
    cache.reset_throttle()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "bc.db"))
    init_db()


# ---------- оценка кандидата ----------
def test_matching_barcode_beats_a_different_name():
    product = Product(None, "Страчателла 200 г", barcode="4690228015816", weight_g=200)
    candidate = Candidate("vkusvill", "1", "Сыр мягкий в рассоле, 0,2 кг", ean="4690228015816")

    assert matcher.score_candidate(product, candidate) == 1.0


def test_different_barcode_kills_a_perfect_name():
    """Тот самый случай: название совпадает буква в букву, а товар другой."""
    product = Product(None, "Страчателла 200 г", barcode="4690228015816", weight_g=200)
    ice_cream = Candidate("magnit", "2", "Страчателла 200 г", ean="4607091389003")

    assert matcher.score_candidate(product, ice_cream) == 0.0


def test_without_barcodes_nothing_changes():
    product = Product(None, "Молоко 1 л", weight_g=1000)
    candidate = Candidate("magnit", "3", "Молоко 1 л", weight_g=1000)

    assert matcher.score_candidate(product, candidate) > 0.9


def test_barcode_is_read_through_spaces_and_dashes():
    product = Product(None, "Молоко", barcode="4 690-228 015816")
    candidate = Candidate("vkusvill", "4", "Молоко", ean="4690228015816")

    assert matcher.score_candidate(product, candidate) == 1.0


# ---------- поиск по штрихкоду ----------
def test_most_stores_honestly_admit_they_cannot(db):
    assert get_connector("magnit").search_barcode("4690228015816") is None
    assert get_connector("pyaterochka").search_barcode("4690228015816") is None


def test_vkusvill_finds_by_barcode(monkeypatch):
    answer = {"ok": True, "data": {"id": 609, "name": "Огурцы короткоплодные",
                                   "price": {"current": 215}, "unit": "кг"}}
    monkeypatch.setattr(mcp_client, "call_tool",
                        lambda *a, **k: answer if a[2] == "vkusvill_product_barcode" else None)
    found = get_connector("vkusvill").search_barcode("4690228015816")

    assert found and found.sku == "609"
    assert found.ean == "4690228015816" and found.score == 1.0


def test_vkusvill_returns_nothing_when_store_has_no_such_code(monkeypatch):
    refusal = {"ok": False, "error": {"code": "invalid_input",
                                      "message": "Товар по штрих-коду не найден"}}
    monkeypatch.setattr(mcp_client, "call_tool", lambda *a, **k: refusal)
    assert get_connector("vkusvill").search_barcode("0000000000000") is None


def test_matcher_puts_barcode_hit_first(db, monkeypatch):
    """Найденное по штрихкоду вытесняет одноимённого из выдачи по названию."""
    product_id = repo.upsert_product(Product(None, "Страчателла 200 г", barcode="4690228015816"))
    by_name = [Candidate("vkusvill", "wrong", "Мороженое Страчателла брикет 92 г", price=99.0)]
    exact = Candidate("vkusvill", "right", "Сыр Страчателла 200 г", price=219.0, ean="4690228015816")

    monkeypatch.setattr(matcher, "_search", lambda conn, q, limit: list(by_name))
    monkeypatch.setattr(matcher, "_get_connector",
                        lambda code: type("C", (), {"search_barcode": staticmethod(lambda b: exact)})())
    top = matcher.find_candidates(product_id, "vkusvill", limit=3)

    assert top[0].sku == "right" and top[0].score == 1.0


def test_barcode_survives_a_fresh_database(db):
    """Колонки штрихкода должны быть в базе, иначе всё выше бессмысленно."""
    with get_conn() as conn:
        products = {r["name"] for r in conn.execute("PRAGMA table_info(products)")}
        store_products = {r["name"] for r in conn.execute("PRAGMA table_info(store_products)")}
    assert "barcode" in products and "ean" in store_products


def test_barcode_is_added_to_an_old_database(tmp_path, monkeypatch):
    """База прежней версии не должна ронять приложение — колонки досыпаются."""
    path = str(tmp_path / "old.db")
    monkeypatch.setattr(config, "db_path", lambda: path)
    with get_conn() as conn:
        conn.executescript("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL,"
                           " brand TEXT, weight_g REAL, unit TEXT NOT NULL DEFAULT 'pcs',"
                           " category TEXT, active INTEGER NOT NULL DEFAULT 1);")
        conn.commit()
    init_db()
    with get_conn() as conn:
        assert "barcode" in {r["name"] for r in conn.execute("PRAGMA table_info(products)")}


def test_barcode_is_stored_and_read_back(db):
    pid = repo.upsert_product(Product(None, "Молоко 1 л", barcode="4690228015816"))
    assert repo.get_product(pid).barcode == "4690228015816"


# ---------- Дикси ----------
def test_dixy_price_is_read_from_real_markup():
    """Рубли и копейки у Дикси разнесены по узлам: «420.<span>90</span>»."""
    page = open(FIXTURE, encoding="utf-8").read()
    assert dixy.parse_price(page) == 420.90


def test_dixy_price_is_none_when_the_page_has_no_price():
    assert dixy.parse_price("<div>Нет в наличии</div>") is None
    assert dixy.parse_price("") is None


def test_dixy_unit_comes_from_the_cart_attribute():
    page = open(FIXTURE, encoding="utf-8").read()
    assert dixy._unit("", page) == "pcs"
    assert dixy._unit("", '<div data-bsk_data="1 1 1 кг 0">') == "kg"


def test_dixy_builds_product_url_from_category_and_id():
    """Адреса каталога Дикси устроены как /catalog/<категория>/<id>/ — проверено по слепкам."""
    item = {"id": "2000301583", "categories": [{"link_url": "/catalog/ovoshchi-frukty/ekzotika/"}]}
    assert dixy.product_url(item) == "https://dixy.ru/catalog/ovoshchi-frukty/ekzotika/2000301583/"


def test_dixy_url_is_none_without_category():
    assert dixy.product_url({"id": "2000301583"}) is None
    assert dixy.product_url({"categories": [{"link_url": "/catalog/x/"}]}) is None


def test_dixy_search_reads_the_search_engine(db, monkeypatch):
    answer = {"products": [
        {"id": "2000024178", "name": "Молоко Простоквашино 2,5% 930мл пастеризованное",
         "available": True, "price": "0.0",
         "categories": [{"link_url": "/catalog/molochnye-produkty-yaytsa/moloko/"}]},
    ]}
    monkeypatch.setattr(dixy.DixyConnector, "_get", lambda self, url, params=None: (answer, 200))
    found = get_connector("dixy").search("молоко простоквашино", limit=3)

    assert found[0].sku == "2000024178"
    assert found[0].price is None, "поисковый движок цен не знает — выдумывать нельзя"
    assert found[0].url.endswith("/2000024178/")


def test_dixy_falls_back_to_receipts_when_search_is_silent(db, monkeypatch):
    """Поиск молчит — остаются прайс и чеки, а не пустой экран."""
    monkeypatch.setattr(dixy.DixyConnector, "_get", lambda self, url, params=None: (None, 403))
    store = repo.get_store("dixy")
    milk = repo.upsert_product(Product(None, "Молоко 1 л", unit="pcs"))
    repo.add_history_row("2026-09-01", store.id, milk, "Молоко", 1, 88.0, 88.0)

    found = get_connector("dixy").search("молоко", limit=3)
    assert found and found[0].price == 88.0
