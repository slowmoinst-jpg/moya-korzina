"""Самая низкая цена для денег на экране — без одиноких выбросов.

Замер 19.10.2026: у Дикси 469 строк с ценой 1.99 ₽ (старый разбор карточки: крем NIVEA,
лейка садовая, мыльный пистолет). Функция `_sane_min` берёт самую низкую цену среди тех,
кому можно верить, и не даёт мусору подменить базу корзины.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.service import _sane_min  # noqa: E402


def test_a_lone_absurdly_low_price_is_ignored():
    """1.99 рядом с 430 и 479 — не цена товара: берём следующую по порядку."""
    assert _sane_min([1.99, 429.99, 479.0]) == 429.99


def test_two_prices_are_taken_as_they_are():
    """Меньше трёх цен — судить не о чем."""
    assert _sane_min([1.99, 479.0]) == 1.99


def test_a_normal_lowest_price_wins():
    """Честная низкая цена не выброс: 100 против 150 и 160 — это 100."""
    assert _sane_min([150.0, 100.0, 160.0]) == 100.0


def test_a_lone_absurdly_high_price_does_not_matter():
    assert _sane_min([100.0, 120.0, 99999.0]) == 100.0


def test_nothing_to_choose_from():
    assert _sane_min([]) is None


def test_two_far_apart_prices_still_give_the_lowest():
    """Две цены — судить не о чем: берём самую низкую, какой бы странной ни казалась."""
    assert _sane_min([1.0, 1000.0]) == 1.0
