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


# ---------- порог сплита (концепция 2.0, модуль 5) ----------
def test_a_split_that_saves_less_than_the_threshold_gives_way_to_one_store():
    split = _variant(["magnit", "lenta"], 850.0)
    single = _variant(["magnit"], 900.0)
    best, switch = _chosen([split, single], None)
    assert best is single
    assert switch["gain"] == 50.0 and switch["worth"] is False


def test_a_split_that_saves_enough_stays_first():
    split = _variant(["magnit", "lenta"], 600.0)
    single = _variant(["magnit"], 900.0)
    best, switch = _chosen([split, single], None)
    assert best is split and switch["worth"] is True


def test_the_threshold_does_not_push_to_an_incomplete_store():
    split = _variant(["magnit", "lenta"], 850.0)
    single = _variant(["magnit"], 870.0, missing=["Сыр"])
    best, _switch = _chosen([split, single], None)
    assert best is split


def test_the_threshold_comes_from_config(monkeypatch):
    from app import config
    real = config.get
    monkeypatch.setattr(config, "get", lambda key, default=None:
                        0 if key == "optimizer.split_min_saving_rub" else real(key, default))
    split = _variant(["magnit", "lenta"], 850.0)
    single = _variant(["magnit"], 900.0)
    best, _switch = _chosen([split, single], None)
    assert best is split


def test_completeness_counts_covered_positions():
    from app.web.screens.result import _completeness
    assert _completeness(_variant(["magnit"], 1.0, missing=["Сыр"]), 12) == \
        {"have": 11, "of": 12, "full": False}
