"""Цена позиции корзины: килограмм не из цены фасовки, фасовки — по весу.

Ошибки здесь самые дорогие из найденных ревизией 26.09.2026: они не роняют
экран, а тихо делают выигравшим не тот магазин. Сыр 400 г за 300 ₽ без цены
за кг считался как 300 ₽ за КИЛОГРАММ — на 0,7 кг выходило 210 ₽ вместо
525 ₽, и «экономия» завышалась на 315 ₽.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import repo, service, users  # noqa: E402
from app.models import Product  # noqa: E402

PHONE = "79990000088"


@pytest.fixture
def shop(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.open_workspace(PHONE)
    yield repo.get_store("magnit")
    users.deactivate()


def _mapped(store, name: str, unit: str, weight_g=None, sku="1", shop_unit=None,
            shop_weight=None, price=100.0, per_kg=None, fetched_at=None) -> int:
    pid = repo.upsert_product(Product(id=None, name=name, unit=unit, weight_g=weight_g))
    sp = repo.upsert_store_product(store.id, sku, name, weight_g=shop_weight, unit=shop_unit)
    repo.confirm_mapping(pid, sp, confirmed=True)
    repo.save_price(sp, price, per_kg, fetched_at=fetched_at)
    return pid


def test_weight_item_takes_kilo_price_from_the_pack_weight(shop):
    pid = _mapped(shop, "Сыр Российский 400 г", "kg", shop_unit="pcs", shop_weight=400,
                  price=300.0)
    found = service.line_price(pid, shop, 0.7, "kg")
    assert found["value"] == 525.0
    assert "400" in found["note"]


def test_pack_of_unknown_weight_is_not_a_kilo_price(shop):
    """Упаковка неизвестного веса — сравнить не с чем, и это честнее выдумки."""
    pid = _mapped(shop, "Сыр в упаковке", "kg", shop_unit="pcs", price=300.0)
    assert service.line_price(pid, shop, 0.7, "kg") is None


def test_weight_goods_of_the_shop_are_priced_per_kilo(shop):
    pid = _mapped(shop, "Сыр весовой", "kg", shop_unit="kg", price=800.0)
    assert service.line_price(pid, shop, 0.5, "kg")["value"] == 400.0


def test_price_per_kg_from_the_snapshot_wins(shop):
    pid = _mapped(shop, "Сыр 400 г", "kg", shop_unit="pcs", shop_weight=400, price=300.0,
                  per_kg=700.0)
    assert service.line_price(pid, shop, 1.0, "kg")["value"] == 700.0


def test_different_packs_are_compared_by_weight(shop):
    """800 г за 90 ₽ против эталона 1 кг: килограмм у него 112,5 ₽, а не 90."""
    pid = _mapped(shop, "Гречка 1 кг", "pcs", weight_g=1000, shop_unit="pcs",
                  shop_weight=800, price=90.0)
    found = service.line_price(pid, shop, 1.0, "pcs", 1000)
    assert found["value"] == 112.5
    assert "800" in found["note"] and "1000" in found["note"]


def test_same_pack_keeps_the_shelf_price(shop):
    """930 мл против 0,93 л — одна фасовка, цену полки не трогаем."""
    pid = _mapped(shop, "Молоко 930 мл", "pcs", weight_g=930, shop_unit="pcs",
                  shop_weight=930, price=89.0)
    found = service.line_price(pid, shop, 2.0, "pcs", 930)
    assert found["value"] == 178.0
    assert found["note"] is None


def test_piece_sold_by_weight_in_the_shop(shop):
    pid = _mapped(shop, "Колбаса 300 г", "pcs", weight_g=300, shop_unit="kg", price=900.0)
    assert service.line_price(pid, shop, 1.0, "pcs", 300)["value"] == 270.0


def test_old_price_is_marked_stale(shop):
    old = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    pid = _mapped(shop, "Кефир", "pcs", price=80.0, fetched_at=old)
    found = service.line_price(pid, shop, 1.0, "pcs")
    assert found["stale"] is True
    fresh = _mapped(shop, "Ряженка", "pcs", sku="2", price=80.0)
    assert service.line_price(fresh, shop, 1.0, "pcs")["stale"] is False


def test_stale_price_reaches_the_variant(shop):
    old = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    pid = _mapped(shop, "Кефир", "pcs", price=80.0, fetched_at=old)
    basket = repo.create_basket("проверка")
    repo.set_basket_item(basket, pid, 1)
    variants, _base = service.calculate(basket, refresh=False)
    stale = variants[0].stale_lines
    assert [ln.product_name for ln in stale] == ["Кефир"]
    assert stale[0].stale_since == old[:10]


def test_pieces_are_whole_in_the_basket(shop):
    """Штучный товар — целыми штуками: цена ×1,5 не сходилась с корзиной сети."""
    pid = _mapped(shop, "Батон", "pcs", price=50.0)
    kg = _mapped(shop, "Картофель", "kg", sku="2", shop_unit="kg", price=40.0)
    basket = repo.create_basket("проверка")
    repo.set_basket_item(basket, pid, 1.5)
    repo.set_basket_item(basket, kg, 1.5)
    qty = {row["product_id"]: row["qty"] for row in repo.basket_items(basket)}
    assert qty[pid] == 2.0
    assert qty[kg] == 1.5, "весовой товар округлять нельзя"
    repo.set_basket_item(basket, pid, 0.3)
    assert {r["product_id"]: r["qty"] for r in repo.basket_items(basket)}[pid] == 1.0
    assert repo.whole_pieces(1.3) == 1.0


def test_reference_price_is_never_taken_for_a_live_one(shop):
    """Справочная цена из CSV помечается в расчёте, какой бы свежей ни была дата."""
    pid = repo.upsert_product(Product(id=None, name="Хлебцы", unit="pcs"))
    sp = repo.upsert_store_product(shop.id, "magnit-hlebcy", "Хлебцы")
    repo.confirm_mapping(pid, sp, confirmed=True)
    repo.save_price(sp, 55.0, source="fallback")
    found = service.line_price(pid, shop, 1.0, "pcs")
    assert found["stale"] is True and found["stale_label"] == "справочная цена"


def test_absent_in_the_baseline_store_is_not_a_zero_baseline(shop):
    """Товара нет в базовом магазине — его ноль не база: иначе экономия занижена."""
    from app import config

    base = repo.get_store(config.get("baseline_store", "pyaterochka"))
    pid = repo.upsert_product(Product(id=None, name="Сыр Кубанский", unit="pcs"))
    sp = repo.upsert_store_product(base.id, "absent", "Сыр Кубанский")
    repo.confirm_mapping(pid, sp, confirmed=True)
    repo.save_price(sp, 0.0, in_stock=False)
    other = repo.upsert_store_product(shop.id, "7", "Сыр Кубанский")
    repo.confirm_mapping(pid, other, confirmed=True)
    repo.save_price(other, 300.0)
    basket = repo.create_basket("проверка")
    repo.set_basket_item(basket, pid, 1)
    assert service.baseline_by_product(basket)[pid] == 300.0
