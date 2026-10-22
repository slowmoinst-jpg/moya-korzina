"""Мусорная цена не должна попадать в подытог сети — от него зависит выбор магазина.

Замер 19.10.2026: у Дикси 469 строк с ценой 1.99 ₽ (старый разбор карточки). Пока такая
цена стоит в prices[store] строки корзины, она входит в подытог сети, а подытог решает,
какой магазин выбрать для ВСЕЙ корзины.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.models import BasketLine  # noqa: E402
from app.service import _drop_implausible  # noqa: E402


def _line(prices: dict[str, float]) -> BasketLine:
    line = BasketLine(product_id=1, name="Крем NIVEA 150 мл", unit="pcs", qty=1.0)
    line.prices.update(prices)
    line.in_stock.update({code: True for code in prices})
    return line


def test_a_lone_absurd_price_is_dropped_from_the_line():
    line = _line({"dixy": 1.99, "lenta": 372.99, "metro": 479.0})

    _drop_implausible(line)

    assert "dixy" not in line.prices, "мусорная цена не участвует в подытоге"
    assert line.prices == {"lenta": 372.99, "metro": 479.0}
    assert "не похожа" in line.notes["dixy"], "и это сказано словами, а не молча"


def test_a_normal_low_price_stays():
    line = _line({"dixy": 199.9, "lenta": 372.99, "metro": 479.0})

    _drop_implausible(line)

    assert line.prices.keys() == {"dixy", "lenta", "metro"}


def test_two_prices_are_not_judged():
    """Меньше трёх цен — судить не о чем: строку не трогаем."""
    line = _line({"dixy": 1.99, "lenta": 372.99})

    _drop_implausible(line)

    assert line.prices.keys() == {"dixy", "lenta"}


def test_a_lone_absurd_high_price_is_dropped_too():
    line = _line({"dixy": 199.9, "lenta": 372.99, "metro": 99999.0})

    _drop_implausible(line)

    assert "metro" not in line.prices


def test_a_dropped_price_leaves_no_shape_or_note_behind():
    """Убранная цена не тянет за собой ни наличия, ни поправки на фасовку."""
    line = _line({"dixy": 1.99, "lenta": 372.99, "metro": 479.0})
    line.adjust["dixy"] = 5.0
    line.notes["dixy"] = "цена с карточки"

    _drop_implausible(line)

    assert "dixy" not in line.adjust
    assert "не похожа" in line.notes["dixy"]
