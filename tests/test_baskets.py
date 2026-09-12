"""Сборка корзины из истории и справочник условий банков.

Все тесты работают на временной БД: app.config.db_path переопределяется через
monkeypatch, рабочая data/basket.db не затрагивается.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import bank_reference, baskets, config, repo  # noqa: E402
from app.db import init_db  # noqa: E402
from app.models import Product  # noqa: E402


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    path = str(tmp_path / "baskets.db")
    monkeypatch.setattr(config, "db_path", lambda: path)
    assert config.db_path() != os.path.join(ROOT, "data", "basket.db")
    init_db()


@pytest.fixture
def history():
    """Две покупки в разных месяцах: одна позиция берётся в обе, одна только раз."""
    store = repo.get_store("pyaterochka")
    milk = repo.upsert_product(Product(None, "Молоко 1 л", unit="pcs"))
    apples = repo.upsert_product(Product(None, "Яблоки", unit="kg"))
    once = repo.upsert_product(Product(None, "Торт", unit="pcs"))

    # июль: молоко 2 шт, яблоки 1,2 кг
    repo.add_history_row("2026-07-10", store.id, milk, "Молоко", 2, 80.0, 160.0)
    repo.add_history_row("2026-07-10", store.id, apples, "Яблоки", 1.2, 150.0, 180.0)
    # август: молоко двумя строками (1+3), яблоки 0,8 кг, торт 1 шт
    repo.add_history_row("2026-08-14", store.id, milk, "Молоко", 1, 80.0, 80.0)
    repo.add_history_row("2026-08-14", store.id, milk, "Молоко", 3, 80.0, 240.0)
    repo.add_history_row("2026-08-14", store.id, apples, "Яблоки", 0.8, 150.0, 120.0)
    repo.add_history_row("2026-08-14", store.id, once, "Торт", 1, 700.0, 700.0)
    return {"milk": milk, "apples": apples, "once": once}


# ---------- как в прошлый раз ----------
def test_last_purchase_takes_only_latest_date(history):
    date, items = baskets.last_purchase_items()
    assert date == "2026-08-14"
    assert {i["product_id"] for i in items} == {history["milk"], history["apples"], history["once"]}


def test_last_purchase_merges_duplicate_lines(history):
    """Молоко в чеке двумя строками — в корзине это одна позиция на 4 штуки."""
    _, items = baskets.last_purchase_items()
    milk = next(i for i in items if i["product_id"] == history["milk"])
    assert milk["qty"] == 4


# ---------- по среднему ----------
def test_average_divides_by_months_with_purchases(history):
    """Месяцев два, молока всего 6 штук — в среднем 3 за месяц."""
    months, items = baskets.average_month_items()
    assert months == 2
    milk = next(i for i in items if i["product_id"] == history["milk"])
    assert milk["qty"] == 3


def test_average_rounds_pieces_and_keeps_weight(history):
    """Штучные — целыми, весовые — до грамма: (1,2 + 0,8) / 2 = 1,0 кг."""
    _, items = baskets.average_month_items()
    apples = next(i for i in items if i["product_id"] == history["apples"])
    assert apples["qty"] == pytest.approx(1.0)
    assert all(float(i["qty"]).is_integer() for i in items if i["unit"] == "pcs")


def test_average_drops_items_that_round_to_zero(history):
    """Торт куплен один раз за два месяца — 0,5 штуки в корзину не кладём."""
    _, items = baskets.average_month_items()
    assert history["once"] not in {i["product_id"] for i in items}


# ---------- создание корзины ----------
def test_build_from_history_creates_basket_with_source(history):
    result = baskets.build_from_history(baskets.LAST)
    assert result["basket_id"]
    saved = next(b for b in repo.list_baskets() if b["id"] == result["basket_id"])
    assert saved["source"] == "history"
    assert len(repo.basket_items(result["basket_id"])) == len(result["items"])


def test_build_average_marks_source_average(history):
    result = baskets.build_from_history(baskets.AVERAGE)
    saved = next(b for b in repo.list_baskets() if b["id"] == result["basket_id"])
    assert saved["source"] == "average"
    assert result["months"] == 2


def test_build_from_empty_history_makes_nothing():
    result = baskets.build_from_history(baskets.LAST)
    assert result["basket_id"] is None
    assert result["items"] == []
    assert repo.list_baskets() == []


# ---------- регулярные покупки ----------
def test_regular_purchases_need_two_different_dates(history):
    regular = {r["product_id"] for r in baskets.regular_purchases(min_times=2)}
    assert history["milk"] in regular and history["apples"] in regular
    assert history["once"] not in regular


def test_missing_regulars_reports_what_is_not_in_basket(history):
    basket_id = repo.create_basket("Только молоко")
    repo.set_basket_item(basket_id, history["milk"], 1)
    missing = {r["product_id"] for r in baskets.missing_regulars(basket_id)}
    assert missing == {history["apples"]}


# ---------- справочник банков ----------
def test_reference_loads_all_banks():
    banks = bank_reference.all_banks()
    assert len(banks) == 8
    assert "Сбер" in bank_reference.bank_names()
    assert bank_reference.find("т-банк")["card"] == "Black"


def test_reference_separates_money_from_points():
    """Рубли складываются с ценой, баллы и ягодки — нет."""
    assert bank_reference.is_money(bank_reference.find("Т-Банк"))
    assert not bank_reference.is_money(bank_reference.find("ВБ Банк"))
    assert not bank_reference.is_money(bank_reference.find("Яндекс Банк"))


def test_reference_warns_about_points_and_subscription():
    wb = bank_reference.warnings(bank_reference.find("ВБ Банк"))
    assert any("не рублями" in w for w in wb)
    alfa = bank_reference.warnings(bank_reference.find("Альфа-Банк"))
    assert any("подписки" in w for w in alfa)


def test_reference_card_title_falls_back_to_program():
    assert bank_reference.card_title(bank_reference.find("ВТБ")) == "Мультибонус"
    assert bank_reference.card_title(bank_reference.find("Сбер")) == "СберСпасибо"
