"""Цена одной сети в наряде для телефона сверяется с историей этой же позиции.

В расчёте корзины цену можно сверить с соседями по сети (`_drop_implausible`), а в наряде
соседей нет: там одна цена одной сети. Судить её можно только историей (price_history).
Замер-повод: у Дикси 469 строк с ценой 1.99 ₽ — такая цена в наряде означала бы «итог»
втрое меньше настоящего, а телефон по ней кладёт товар в корзину.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import store  # noqa: E402
from app.catalog.model import ChainProduct  # noqa: E402
from app.service import price_suspect  # noqa: E402


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


def _history(chain: str, sku: str, prices: list[float]) -> None:
    for price in prices:
        store.upsert_products(chain, [ChainProduct(sku=sku, name=f"Товар {sku}",
                                                   price=price, point="1")])


def test_a_price_three_times_below_the_usual_is_suspect(base):
    _history("dixy", "2000416287", [100.0, 110.0, 90.0, 100.0])

    why = price_suspect("dixy", "2000416287", 1.99)

    assert why and "втрое ниже" in why and "1.99" in why


def test_a_price_double_the_usual_is_suspect(base):
    _history("dixy", "77", [100.0, 110.0, 90.0, 100.0])

    why = price_suspect("dixy", "77", 350.0)

    assert why and "вдвое выше" in why


def test_a_normal_price_is_fine(base):
    _history("dixy", "77", [100.0, 110.0, 90.0, 100.0])

    assert price_suspect("dixy", "77", 105.0) is None


def test_no_history_means_no_judgement(base):
    """Истории нет — не выдумываем: пусть цена идёт как есть."""
    assert price_suspect("dixy", "нет-такого", 1.99) is None


def test_one_change_is_not_enough_to_judge(base):
    """Две СВЕЖИЕ точки — судить рано: неизвестно, что было раньше."""
    _history("dixy", "88", [100.0, 300.0])

    assert price_suspect("dixy", "88", 1.99) is None


def _old_row(chain: str, sku: str, price: float, days: float) -> None:
    """Одна строка истории, записанная days дней назад."""
    from datetime import datetime, timedelta

    stamp = (datetime.now() - timedelta(days=days)).isoformat(timespec="microseconds")
    with store.connect() as conn:
        conn.execute("insert into price_history (chain, sku, point, price, seen_at)"
                     " values (?,?,?,?,?)", (chain, sku, "1", price, stamp))
        conn.commit()


def test_a_price_that_held_a_week_without_changes_is_a_usual_price(base):
    """История пишется только при СМЕНЕ цены: у стабильного товара в ней одна строка.

    Замер 20.10.2026 (первая ночь с историей: magnit 970 строк, metro 135): у товара со
    стабильной ценой ровно одна запись, и правило «не меньше трёх изменений» не сработало
    бы никогда. Но неделя без изменений — это и есть обычная цена.
    """
    _old_row("magnit", "1000026813", 124.0, days=10)

    assert store.usual_trusted("magnit", "1000026813")
    why = price_suspect("magnit", "1000026813", 20.0)

    assert why and "втрое ниже" in why


def test_a_fresh_single_price_proves_nothing(base):
    _old_row("magnit", "1000026814", 124.0, days=0)

    assert not store.usual_trusted("magnit", "1000026814")
    assert price_suspect("magnit", "1000026814", 20.0) is None


def test_an_empty_or_zero_price_is_not_judged(base):
    _history("dixy", "99", [100.0, 110.0, 90.0])
    assert price_suspect("dixy", "99", None) is None
    assert price_suspect("dixy", "99", 0) is None
