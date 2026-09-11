"""Тесты импорта чеков и сопоставления номенклатуры (разделы 5.1, 5.2, 8 спецификации).

Все тесты работают на временной БД: app.config.db_path переопределяется через
monkeypatch, рабочая data/basket.db не затрагивается.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import config, repo                                         # noqa: E402
from app.db import init_db                                           # noqa: E402
from app.importers import import_receipt, parse_receipt, parse_receipt_text  # noqa: E402
from app.matcher import (                                            # noqa: E402
    auto_match,
    confirm,
    find_candidates,
    normalize_name,
    parse_weight,
    refresh_prices,
    similarity,
    weight_ok,
)
from app.models import Candidate, PriceSnapshot, Product             # noqa: E402

SAMPLE_TXT = os.path.join(ROOT, "data", "sample_receipt.txt")
SAMPLE_PDF = os.path.join(ROOT, "data", "sample_receipt.pdf")
EXPECTED_ROWS = 16
EXPECTED_TOTAL = 3690.40


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Каждый тест — своя чистая БД во временном каталоге."""
    path = str(tmp_path / "test_basket.db")
    monkeypatch.setattr(config, "db_path", lambda: path)
    init_db()
    assert config.db_path() != os.path.join(ROOT, "data", "basket.db")
    return path


@pytest.fixture
def receipt_text() -> str:
    with open(SAMPLE_TXT, encoding="utf-8") as fh:
        return fh.read()


# --- нормализация ----------------------------------------------------------

def test_normalize_name_spec_example():
    """Пример из раздела 5.1 спецификации."""
    assert normalize_name("ВК/ПОЛ.Сыр СТРАЧАТЕЛЛА мяг.200г") == "страчателла 200 г"


def test_normalize_name_stable():
    assert normalize_name("Хлебцы ржано-пшеничные 240г") == "хлебцы ржано пшеничные 240 г"
    assert normalize_name("СЛИВА") == "слива"
    assert normalize_name("") == ""


def test_parse_weight():
    assert parse_weight("200г") == (200.0, "pcs")
    assert parse_weight("Огурцы короткоплодные 450г") == (450.0, "pcs")
    assert parse_weight("Молоко 1кг")[0] == 1000.0
    # весовой товар: граммовки в названии нет
    grams, unit = parse_weight("Слива")
    assert grams is None and unit == "kg"
    assert parse_weight("Салями сырокопченая")[1] == "kg"


def test_similarity():
    assert similarity("Страчателла 200 г", "ВК/ПОЛ.Сыр СТРАЧАТЕЛЛА мяг.200г") == 1.0
    assert similarity("Слива", "Дезодорант AXE Ice Chill 50мл") < 0.4


def test_weight_ok():
    assert weight_ok(200, 180, 20) is True          # -10 %
    assert weight_ok(200, 150, 20) is False         # -25 %
    assert weight_ok(200, 240, 20) is True          # +20 % на границе
    assert weight_ok(None, 180, 20) is True         # весовой товар — допуск не применяется


# --- разбор чека -----------------------------------------------------------

def test_parse_receipt_text(receipt_text):
    r = parse_receipt_text(receipt_text)
    assert len(r.rows) == EXPECTED_ROWS
    assert r.total == pytest.approx(EXPECTED_TOTAL, abs=0.01)
    assert sum(row.total for row in r.rows) == pytest.approx(EXPECTED_TOTAL, abs=0.01)
    assert r.date == "2026-08-16"
    assert "Пятёрочка" in r.store_name


def test_parse_receipt_weighted_row(receipt_text):
    r = parse_receipt_text(receipt_text)
    plum = next(row for row in r.rows if row.raw_name.lower().startswith("слива"))
    assert plum.qty == pytest.approx(0.900)
    assert plum.unit_price == pytest.approx(239.00)
    assert plum.total == pytest.approx(215.10)


def test_parse_receipt_txt_file():
    r = parse_receipt(SAMPLE_TXT)
    assert len(r.rows) == EXPECTED_ROWS
    assert r.total == pytest.approx(EXPECTED_TOTAL, abs=0.01)


@pytest.mark.skipif(not os.path.exists(SAMPLE_PDF), reason="PDF не собран")
def test_parse_receipt_pdf_matches_txt():
    pdf, txt = parse_receipt(SAMPLE_PDF), parse_receipt(SAMPLE_TXT)
    assert len(pdf.rows) == len(txt.rows) == EXPECTED_ROWS
    assert pdf.total == pytest.approx(txt.total, abs=0.01) == pytest.approx(EXPECTED_TOTAL, abs=0.01)
    assert pdf.date == txt.date
    assert [r.raw_name for r in pdf.rows] == [r.raw_name for r in txt.rows]


# --- импорт в базу ---------------------------------------------------------

def test_import_receipt_writes_history():
    res = import_receipt(SAMPLE_TXT, store_code="pyaterochka")
    assert res["rows"] == EXPECTED_ROWS
    assert res["products_created"] == EXPECTED_ROWS
    assert res["total"] == pytest.approx(EXPECTED_TOTAL, abs=0.01)
    assert res["date"] == "2026-08-16"

    history = repo.list_history()
    assert len(history) == EXPECTED_ROWS
    assert all(h["product_id"] for h in history)
    assert sum(h["total"] for h in history) == pytest.approx(EXPECTED_TOTAL, abs=0.01)


def test_import_receipt_idempotent_products():
    """Повторный импорт того же чека не удваивает эталоны."""
    first = import_receipt(SAMPLE_TXT, store_code="pyaterochka")
    after_first = len(repo.list_products())
    second = import_receipt(SAMPLE_TXT, store_code="pyaterochka")
    after_second = len(repo.list_products())

    assert first["products_created"] == EXPECTED_ROWS
    assert second["products_created"] == 0
    assert after_first == after_second == EXPECTED_ROWS


# --- матчер поверх фейкового коннектора ------------------------------------

class FakeConnector:
    """Заглушка коннектора: контракт search()/get_prices() из раздела 3.1."""

    def __init__(self, store_code: str = "magnit"):
        self.store_code = store_code
        self.catalog = [
            Candidate(store_code, "SKU-1", "Сыр Страчателла мягкий 200 г", 279.0, 200.0, "pcs"),
            Candidate(store_code, "SKU-2", "Сыр Страчателла 400 г", 499.0, 400.0, "pcs"),
            Candidate(store_code, "SKU-3", "Огурцы короткоплодные 450 г", 119.0, 450.0, "pcs"),
        ]

    def search(self, query: str, limit: int = 10) -> list[Candidate]:
        return [Candidate(**vars(c)) for c in self.catalog][:limit]

    def get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        prices = {c.sku: c.price for c in self.catalog}
        return [PriceSnapshot(self.store_code, sku, prices[sku]) for sku in skus if sku in prices]


@pytest.fixture
def fake_connectors(monkeypatch):
    """Подменяет модуль app.connectors на заглушку (реальный пишется параллельно)."""
    module = types.ModuleType("app.connectors")
    module.get_connector = lambda store_code: FakeConnector(store_code)
    monkeypatch.setitem(sys.modules, "app.connectors", module)
    return module


@pytest.fixture
def cheese_id() -> int:
    return repo.upsert_product(Product(None, "Страчателла 200 г", weight_g=200.0, unit="pcs"))


def test_find_candidates_top3_with_sku(fake_connectors, cheese_id):
    cands = find_candidates(cheese_id, "magnit", limit=3)
    assert cands, "кандидаты не найдены"
    assert len(cands) <= 3
    assert all(c.sku for c in cands)                       # sku обязателен для UI
    assert cands[0].sku == "SKU-1"                         # 200 г выигрывает у 400 г
    assert cands[0].score >= cands[-1].score
    store = repo.get_store("magnit")
    # кандидаты сохранены как НЕподтверждённые сопоставления
    assert repo.mapping_matrix().get((cheese_id, store.id)) is False
    assert repo.confirmed_mapping(cheese_id, store.id) is None


def test_find_candidates_without_connector(monkeypatch, cheese_id):
    """Модуля коннекторов нет — матчер не падает, а возвращает пустой список."""
    monkeypatch.setitem(sys.modules, "app.connectors", None)   # -> ImportError при импорте
    assert find_candidates(cheese_id, "magnit") == []
    assert refresh_prices([cheese_id], ["magnit"])["updated"] == 0


def test_confirm_and_refresh_prices(fake_connectors, cheese_id):
    find_candidates(cheese_id, "magnit", limit=3)
    confirm(cheese_id, "magnit", "SKU-1")
    store = repo.get_store("magnit")
    mapping = repo.confirmed_mapping(cheese_id, store.id)
    assert mapping and mapping["sku"] == "SKU-1"

    # подтверждение переносится на другой артикул, старое снимается
    confirm(cheese_id, "magnit", "SKU-2")
    assert repo.confirmed_mapping(cheese_id, store.id)["sku"] == "SKU-2"

    res = refresh_prices([cheese_id], ["magnit"])
    assert res["updated"] == 1 and res["errors"] == []
    assert repo.latest_price_for(cheese_id, store.id)["price"] == pytest.approx(499.0)


def test_auto_match_skips_confirmed(fake_connectors, cheese_id):
    """Критерий приёмки 7: повторный запуск не трогает подтверждённые позиции."""
    first = auto_match([cheese_id], ["magnit"], threshold=0.5)
    assert first["auto"] == 1
    store = repo.get_store("magnit")
    assert repo.confirmed_mapping(cheese_id, store.id)["sku"] == "SKU-1"

    second = auto_match([cheese_id], ["magnit"], threshold=0.5)
    assert second["auto"] == 0 and second["need_review"] == []
    assert repo.confirmed_mapping(cheese_id, store.id)["sku"] == "SKU-1"


def test_auto_match_need_review_on_low_score(fake_connectors):
    pid = repo.upsert_product(Product(None, "Салями сырокопченая", unit="kg"))
    res = auto_match([pid], ["magnit"], threshold=0.95)
    assert res["auto"] == 0
    assert res["need_review"] and res["need_review"][0]["product_id"] == pid
