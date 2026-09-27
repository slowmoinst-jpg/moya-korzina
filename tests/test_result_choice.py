"""Какой вариант «Результат» показывает крупно, когда их два: разделить или один магазин.

Раньше экран выбирал по одному итогу (сперва по деньгам, потом по деньгам с
ручной работой) и спорил с расчётом: полный вариант за 600 ₽ проигрывал
магазину за 150 ₽, в котором просто нет сыра. Теперь порядок — тот, в котором
варианты расположил сам расчёт: полные, не ниже минимального заказа, потом итог.
"""
from __future__ import annotations

from app.models import StoreBreakdown, Variant
from app.web.screens.result import _chosen


def _variant(codes, total, missing=()):
    stores = [StoreBreakdown(store_code=c, store_name=c, subtotal=total, delivery=0.0,
                             discount=0.0, total=total) for c in codes]
    return Variant(stores=stores, total=total, baseline=1000.0,
                   missing_products=list(missing))


def test_the_complete_split_wins_over_a_cheaper_store_without_cheese():
    split = _variant(["magnit", "lenta"], 600.0)
    single = _variant(["magnit"], 150.0, missing=["Сыр"])
    best, switch = _chosen([split, single], None)
    assert best is split
    assert switch["split"]["on"] is True


def test_the_order_of_the_calculation_decides_and_not_the_sum():
    single = _variant(["magnit"], 900.0)
    split = _variant(["magnit", "lenta"], 850.0)
    # расчёт поставил один магазин выше (например, с учётом ручной работы)
    best, _switch = _chosen([single, split], None)
    assert best is single


def test_the_address_bar_mode_still_wins():
    split = _variant(["magnit", "lenta"], 600.0)
    single = _variant(["magnit"], 700.0)
    best, _switch = _chosen([split, single], "single")
    assert best is single
