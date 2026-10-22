"""Пометка «дороже обычного» в предложении магазина.

История цены появилась 19.10.2026: база теперь помнит, сколько товар стоил раньше
(store.price_history). Здесь проверяется, что сравнение эту историю использует и не
выдумывает «обычную цену» там, где её нет.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import store  # noqa: E402
from app.catalog.model import ChainProduct  # noqa: E402
from app.compare import StoreOffer, _fill, cheapest  # noqa: E402


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


def _history(chain: str, sku: str, prices: list[float]) -> None:
    for price in prices:
        store.upsert_products(chain, [ChainProduct(sku=sku, name=f"Товар {sku}",
                                                   price=price, point="1")])


def _offer(chain: str, sku: str, price: float, note: str = "") -> StoreOffer:
    return StoreOffer(store_code=chain, store_name=chain, sku=sku, name="Крем NIVEA 150 мл",
                      price=price, unit="pcs", weight_g=150.0, in_stock=True, note=note)


def test_a_price_above_the_usual_is_named(base):
    _history("lenta", "77", [100.0, 110.0, 90.0, 100.0])

    offer = _fill(_offer("lenta", "77", 160.0))

    assert offer.usual == 100.0
    assert offer.vs_usual == 60.0
    assert offer.note == "дороже обычного на 60 %"


def test_a_price_below_the_usual_is_named(base):
    _history("lenta", "77", [100.0, 120.0, 80.0, 100.0])

    offer = _fill(_offer("lenta", "77", 70.0))

    assert offer.note == "дешевле обычного на 30 %"


def test_a_small_difference_is_not_worth_words(base):
    """Два процента человек не заметит — и пометки нет."""
    _history("lenta", "77", [100.0, 110.0, 90.0, 100.0])

    offer = _fill(_offer("lenta", "77", 102.0))

    assert offer.vs_usual == 2.0
    assert offer.note == ""


def test_no_history_means_no_note_and_no_invention(base):
    offer = _fill(_offer("lenta", "нет-такого", 199.0))

    assert offer.usual is None and offer.vs_usual is None
    assert offer.note == ""


def test_a_reason_to_be_empty_is_not_overwritten(base):
    """«Товар не связан с магазином» важнее пометки про цену."""
    _history("lenta", "77", [100.0, 110.0, 90.0, 100.0])

    offer = _fill(_offer("lenta", "77", 160.0, note="товар не связан с магазином"))

    assert offer.note == "товар не связан с магазином"
    assert offer.vs_usual == 60.0, "цифра остаётся, меняется только слово"


def test_the_note_does_not_change_the_choice_of_shop(base):
    """Пометка — это слова, а не новое правило выбора: дешёвый всё равно дешевле."""
    _history("lenta", "11", [100.0])
    _history("magnit", "22", [100.0])

    offers = [_fill(_offer("lenta", "11", 160.0)), _fill(_offer("magnit", "22", 120.0))]

    assert cheapest(offers).store_code == "magnit"
