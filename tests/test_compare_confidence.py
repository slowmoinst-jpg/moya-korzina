"""Похожий товар не должен выигрывать сравнение — только тот же самый.

Живой прогон 20.10.2026 на боевом сервере: на «сыр страчателла» самым дешёвым оказывался
«Напиток кисломолочный Exponenta Bio-Skyr Страчателла» за 149.99 — напиток выигрывал у
сыров, и «где дешевле» отправило бы человека за «сыром», который пьют.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.compare import (CONFIDENT_SCORE, StoreOffer, _fill, _worth, cheapest,  # noqa: E402
                         per_unit_price, spread)


def offer(code: str, price: float, score: float, *, name: str = "Сыр Страчателла 200 г",
          confirmed: bool = False) -> StoreOffer:
    per_unit, label = per_unit_price(price, "pcs", 200.0, name)
    return StoreOffer(store_code=code, store_name=code, sku="1", name=name, price=price,
                      unit="pcs", weight_g=200.0, per_unit=per_unit,
                      per_unit_label=label, score=score, confirmed=confirmed)


def test_a_drink_does_not_win_a_cheese_comparison():
    """Ровно тот случай, что нашёлся живьём: оценка напитка 0.35."""
    offers = [offer("dixy", 149.99, 0.35, name="Напиток кисломолочный Страчателла 200 г"),
              offer("magnit", 159.99, 0.72),
              offer("lenta", 326.99, 0.61)]

    best = cheapest(offers)

    assert best is not None and best.store_code == "magnit", best


def test_a_plausible_match_still_wins():
    offers = [offer("magnit", 159.99, 0.72), offer("lenta", 326.99, 0.61)]

    assert cheapest(offers).store_code == "magnit"


def test_an_unknown_score_is_not_punished():
    """Оценки нет (0) — мы не знаем хуже человека: предложение участвует."""
    offers = [offer("lenta", 100.0, 0.0), offer("magnit", 200.0, 0.7)]

    assert cheapest(offers).store_code == "lenta"


def test_a_confirmed_link_is_never_dropped():
    """Связку подтвердил человек — оценка поиска её не отменяет."""
    offers = [offer("lenta", 100.0, 0.2, confirmed=True), offer("magnit", 200.0, 0.8)]

    assert cheapest(offers).store_code == "lenta"


def test_the_spread_ignores_a_foreign_product():
    """Разброс 500 % был из-за напитка; без него — нормальные проценты."""
    offers = [offer("dixy", 149.99, 0.35, name="Напиток кисломолочный Страчателла 200 г"),
              offer("magnit", 159.99, 0.72), offer("vkusvill", 370.0, 0.66)]

    answer = spread(offers)

    assert answer["min"] == per_unit_price(159.99, "pcs", 200.0, "x")[0]
    assert answer["pct"] < 200


def test_the_reason_is_written_on_the_offer():
    """В списке предложение остаётся, но с честной пометкой."""
    filled = _fill(offer("dixy", 149.99, 0.35, name="Напиток кисломолочный Страчателла 200 г"))

    assert filled.note == "похоже, но это другой товар — в выбор не берём"
    assert not _worth(filled)


def test_a_good_offer_gets_no_such_note():
    filled = _fill(offer("magnit", 159.99, CONFIDENT_SCORE))

    assert filled.note == ""
    assert _worth(filled)
