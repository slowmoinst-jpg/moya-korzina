"""Приёмка MVP по разделу 7 спецификации — сквозной прогон на демо-данных.

Тест работает на отдельной временной базе, рабочую data/basket.db не трогает.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BASELINE = 3690.40


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    """Поднимает чистую БД, накатывает сиды, сопоставления и цены."""
    db_file = tmp_path_factory.mktemp("db") / "test_basket.db"

    from app import config

    config.load_config.cache_clear()
    cfg = config.load_config()
    cfg["db_path"] = str(db_file)

    from app.db import init_db

    init_db()

    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import seed

    seed.main()

    try:
        import bootstrap_demo

        bootstrap_demo.main()
    except Exception as exc:  # noqa: BLE001 — коннекторы могут быть недоступны
        print("bootstrap_demo:", exc)

    from app import repo

    baskets = repo.list_baskets()
    assert baskets, "корзина не создана"
    return baskets[0]["id"]


def test_history_imported(demo_db):
    """Критерий 1: чек загружен в историю, итог совпадает с baseline."""
    from app import repo

    rows = repo.list_history(date_from="2026-08-16", date_to="2026-08-16")
    assert len(rows) == 16
    assert round(sum(r["total"] for r in rows), 2) == BASELINE


def test_mappings_confirmed(demo_db):
    """Критерий 2: для каждой позиции есть подтверждённое сопоставление хотя бы в одном магазине."""
    from app import repo

    matrix = repo.mapping_matrix()
    products = repo.list_products()
    unmatched = [p.name for p in products
                 if not any(matrix.get((p.id, s.id)) for s in repo.list_stores() if s.code != "pyaterochka")]
    assert not unmatched, f"без сопоставления: {unmatched}"


def test_prices_present(demo_db):
    """Критерий 3: коннекторы вернули цены по подтверждённым артикулам."""
    from app import repo, service

    cov = service.price_coverage(demo_db)
    assert cov["magnit"][0] > 0 or cov["vkusvill"][0] > 0
    lines = service.build_basket_lines(demo_db)
    assert all(ln.prices for ln in lines), "есть позиции вообще без цен"
    assert len(repo.list_stores()) >= 3


def test_cards_seeded(demo_db):
    """Критерий 4: минимум две карты с разными условиями."""
    from app import repo

    cards = repo.list_cards()
    offers = repo.list_offers()
    assert len(cards) >= 2
    assert len({(o.percent, o.cap_rub, o.min_check_rub) for o in offers}) >= 2


def test_baseline_matches_receipt(demo_db):
    """Критерий 6: baseline совпадает с суммой чека 3 690,40 ₽."""
    from app import service

    assert service.baseline_total(demo_db) == BASELINE


def test_calculate_returns_top3(demo_db):
    """Критерий 5: расчёт выдаёт топ-3 варианта, итог сходится с формулой раздела 6."""
    from app import repo, service

    variants, baseline = service.calculate(demo_db, refresh=False)
    assert baseline == BASELINE
    assert 1 <= len(variants) <= 3
    assert variants == sorted(variants, key=lambda v: v.total), "варианты не отсортированы по итогу"

    stores = {s.code: s for s in repo.list_stores()}
    for v in variants:
        # пересчёт итога по формуле раздела 6, независимо от кода оптимизатора
        expected = 0.0
        for sb in v.stores:
            store = stores[sb.store_code]
            subtotal = round(sum(ln.price for ln in sb.lines), 2)
            assert abs(subtotal - sb.subtotal) < 0.05, f"subtotal {sb.store_name}"
            delivery = 0.0 if subtotal >= store.free_delivery_from else store.delivery_fee
            assert abs(delivery - sb.delivery) < 0.01, f"доставка {sb.store_name}"
            expected += subtotal + delivery - sb.discount
        expected += v.penalty
        assert abs(expected - v.total) < 0.05, f"итог варианта {v.title}"

    best = variants[0]
    assert abs(best.savings_rub - round(baseline - best.total, 2)) < 0.01
    assert abs(best.savings_pct - round((baseline - best.total) / baseline * 100, 2)) < 0.01


def test_recalculation_keeps_mappings(demo_db):
    """Критерий 7: повторный расчёт не требует повторного сопоставления."""
    from app import repo, service

    before = repo.mapping_matrix()
    service.calculate(demo_db, refresh=False)
    after = repo.mapping_matrix()
    assert before == after
