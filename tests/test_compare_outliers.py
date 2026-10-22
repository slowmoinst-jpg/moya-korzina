"""Одинокая цена в разы ниже соседей не решает, куда идти за товаром.

Замер 19.10.2026: у Дикси в базе 469 строк с ценой 1.99 ₽ — старый разбор карточки
(крем NIVEA, лейка садовая, мыльный пистолет). Из-за них Дикси выглядел дешевле всех в
50–500 раз, и «где дешевле» отправляло человека в магазин за кремом по 1.99.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.compare import StoreOffer, cheapest, per_unit_price, spread  # noqa: E402


def offer(code: str, price: float, *, unit: str = "pcs", in_stock: bool = True) -> StoreOffer:
    """Предложение, как его строит compare._fill: с уже приведённой ценой."""
    name = "Крем NIVEA 150 мл"
    per_unit, label = per_unit_price(price, unit, 150.0, name)
    return StoreOffer(store_code=code, store_name=code, sku="1", name=name,
                      price=price, unit=unit, weight_g=150.0, in_stock=in_stock,
                      per_unit=per_unit, per_unit_label=label)


def test_a_lone_absurdly_low_price_does_not_win():
    """1.99 ₽ у Дикси рядом с 429 и 479 — не выгода, а мусор: выбор идёт по остальным."""
    offers = [offer("dixy", 1.99), offer("magnit", 429.99), offer("lenta", 372.99),
              offer("metro", 479.0)]

    best = cheapest(offers)

    assert best is not None and best.store_code == "lenta", best


def per(price: float) -> float:
    """Та же приведённая цена, что у предложения: сравнение идёт по ней, не по рублям."""
    return per_unit_price(price, "pcs", 150.0, "Крем NIVEA 150 мл")[0]


def test_a_lone_absurdly_high_price_does_not_spoil_the_spread():
    """Завышенная в разы цена тоже не должна пугать разбросом."""
    offers = [offer("dixy", 199.9), offer("magnit", 189.9), offer("lenta", 210.0),
              offer("metro", 9999.0)]

    answer = spread(offers)

    assert answer["max"] == per(210.0)
    assert answer["pct"] < 20


def test_two_offers_are_taken_as_they_are():
    """Меньше трёх предложений — судить не о чем: оставляем как есть."""
    offers = [offer("dixy", 1.99), offer("lenta", 372.99)]

    assert cheapest(offers).store_code == "dixy"


def test_a_normal_spread_is_untouched():
    offers = [offer("dixy", 199.9), offer("magnit", 189.9), offer("lenta", 210.0)]

    best = cheapest(offers)

    assert best.store_code == "magnit"
    assert spread(offers)["max"] == per(210.0)


def test_a_row_without_stock_still_does_not_win():
    """Наличия нет — предложение не участвует ни в выборе, ни в разбросе."""
    offers = [offer("dixy", 1.99, in_stock=False), offer("magnit", 429.99),
              offer("lenta", 372.99), offer("metro", 479.0)]

    assert cheapest(offers).store_code == "lenta"
    assert spread(offers)["min"] == per(372.99)
