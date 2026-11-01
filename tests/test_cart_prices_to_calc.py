"""Цены и наличие из корзины сети — в цены рабочего места (решение 01.11.2026).

Проверяется:
- легла с подтверждённым артикулом → снимок source="cart" с ценой и point_key;
- in_stock=False → снимок с in_stock=0;
- без подтверждённой связи / заглушка / весовой → цены нет;
- latest_price отдаёт свежий снимок корзины при более новом снимке витрины
  (source NULL) и отдаёт снимок витрины, если снимок корзины старше fresh_hours;
- price_for со снимком cart не меняет цену купоном (не вызывает personal.apply).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import freshness, location, phonecart, repo, users
from app.models import Product

PHONE = "79991112233"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Изолированное рабочее место с фиксированной точкой доставки."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.open_workspace(PHONE)
    monkeypatch.setattr(location, "point_key", lambda: "55.75000,37.61000")
    yield
    users.deactivate()


def test_cart_item_landed_saves_cart_price(workspace):
    store = repo.get_store("magnit")
    prod = repo.upsert_product(Product(id=None, name="Молоко Простоквашино", unit="pcs"))
    sp_id = repo.upsert_store_product(store.id, "m-milk-1", "Молоко Простоквашино", unit="pcs")
    repo.confirm_mapping(prod, sp_id, confirmed=True)

    report = {
        "store": "magnit",
        "items": [
            {"sku": "m-milk-1", "ok": True, "price": 89.90, "how": "tap"},
        ],
    }
    saved = phonecart._cart_prices(report)
    assert saved == 1

    snap = repo.latest_price(sp_id, point_key="55.75000,37.61000")
    assert snap is not None
    assert snap["price"] == 89.90
    assert snap["source"] == "cart"
    assert snap["in_stock"] == 1
    assert snap["point_key"] == "55.75000,37.61000"


def test_cart_item_out_of_stock_saves_in_stock_zero(workspace):
    store = repo.get_store("magnit")
    prod = repo.upsert_product(Product(id=None, name="Масло сливочное", unit="pcs"))
    sp_id = repo.upsert_store_product(store.id, "m-butter-1", "Масло сливочное", unit="pcs")
    repo.confirm_mapping(prod, sp_id, confirmed=True)

    # 1. in_stock=False с указанной ценой
    report = {
        "store": "magnit",
        "items": [
            {"sku": "m-butter-1", "ok": False, "price": 150.0, "in_stock": False, "why": "нет в наличии"},
        ],
    }
    saved = phonecart._cart_prices(report)
    assert saved == 1

    snap = repo.latest_price(sp_id, point_key="55.75000,37.61000")
    assert snap is not None
    assert snap["price"] == 150.0
    assert snap["in_stock"] == 0
    assert snap["source"] == "cart"

    # 2. in_stock=False без цены, но есть предыдущая цена
    prod2 = repo.upsert_product(Product(id=None, name="Творог 9%", unit="pcs"))
    sp_id2 = repo.upsert_store_product(store.id, "m-curd-1", "Творог 9%", unit="pcs")
    repo.confirm_mapping(prod2, sp_id2, confirmed=True)
    repo.save_price(sp_id2, 110.0, point_key="55.75000,37.61000")

    report2 = {
        "store": "magnit",
        "items": [
            {"sku": "m-curd-1", "ok": False, "price": None, "in_stock": False, "why": "закончился"},
        ],
    }
    saved2 = phonecart._cart_prices(report2)
    assert saved2 == 1

    snap2 = repo.latest_price(sp_id2, point_key="55.75000,37.61000")
    assert snap2 is not None
    assert snap2["price"] == 110.0
    assert snap2["in_stock"] == 0
    assert snap2["source"] == "cart"

    # 3. in_stock=False без цены и без предыдущей цены -> пропускается
    prod3 = repo.upsert_product(Product(id=None, name="Сметана", unit="pcs"))
    sp_id3 = repo.upsert_store_product(store.id, "m-sour-1", "Сметана", unit="pcs")
    repo.confirm_mapping(prod3, sp_id3, confirmed=True)
    report3 = {
        "store": "magnit",
        "items": [
            {"sku": "m-sour-1", "ok": False, "price": 0.0, "in_stock": False},
        ],
    }
    saved3 = phonecart._cart_prices(report3)
    assert saved3 == 0


def test_unconfirmed_placeholder_weight_items_not_saved(workspace):
    store = repo.get_store("magnit")

    # 1. Неподтверждённая связь
    prod1 = repo.upsert_product(Product(id=None, name="Хлеб Бородинский", unit="pcs"))
    sp_id1 = repo.upsert_store_product(store.id, "m-bread-1", "Хлеб Бородинский", unit="pcs")
    # confirm_mapping НЕ вызываем (confirmed=0)
    report1 = {
        "store": "magnit",
        "items": [{"sku": "m-bread-1", "ok": True, "price": 45.0}],
    }
    assert phonecart._cart_prices(report1) == 0

    # 2. Заглушка
    prod2 = repo.upsert_product(Product(id=None, name="Батон", unit="pcs"))
    sp_id2 = repo.upsert_store_product(store.id, "magnit-search-123", "Батон", unit="pcs")
    repo.confirm_mapping(prod2, sp_id2, confirmed=True)
    report2 = {
        "store": "magnit",
        "items": [{"sku": "magnit-search-123", "ok": True, "price": 35.0}],
    }
    assert phonecart._cart_prices(report2) == 0

    # 3. Весовой товар (unit="kg") при ok=True -> цена НЕ пишется
    prod3 = repo.upsert_product(Product(id=None, name="Яблоки Голден", unit="kg"))
    sp_id3 = repo.upsert_store_product(store.id, "m-apples-1", "Яблоки Голден", unit="kg")
    repo.confirm_mapping(prod3, sp_id3, confirmed=True)
    report3 = {
        "store": "magnit",
        "items": [{"sku": "m-apples-1", "ok": True, "price": 120.0}],
    }
    assert phonecart._cart_prices(report3) == 0

    # 4. Весовой товар при in_stock=False -> снимок "нет в наличии" пишется
    report4 = {
        "store": "magnit",
        "items": [{"sku": "m-apples-1", "ok": False, "price": 120.0, "in_stock": False}],
    }
    assert phonecart._cart_prices(report4) == 1
    snap = repo.latest_price(sp_id3, point_key="55.75000,37.61000")
    assert snap is not None
    assert snap["in_stock"] == 0


def test_latest_price_cart_precedence_and_expiration(workspace):
    store = repo.get_store("magnit")
    prod = repo.upsert_product(Product(id=None, name="Кофе", unit="pcs"))
    sp_id = repo.upsert_store_product(store.id, "m-coffee-1", "Кофе", unit="pcs")
    repo.confirm_mapping(prod, sp_id, confirmed=True)

    pk = "55.75000,37.61000"
    now = datetime.now()

    # Снимок корзины час назад (fresh)
    t_cart = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    repo.save_price(sp_id, 350.0, fetched_at=t_cart, source="cart", point_key=pk)

    # Более новый снимок витрины (полчаса назад)
    t_shelf = (now - timedelta(minutes=30)).isoformat(timespec="seconds")
    repo.save_price(sp_id, 400.0, fetched_at=t_shelf, source=None, point_key=pk)

    # latest_price отдаёт свежий снимок корзины, хотя витрина новее!
    snap = repo.latest_price(sp_id, point_key=pk)
    assert snap is not None
    assert snap["source"] == "cart"
    assert snap["price"] == 350.0

    # А если снимок корзины старше fresh_hours (max_age_hours)
    old_cart = (now - timedelta(hours=freshness.max_age_hours() + 2)).isoformat(timespec="seconds")
    # Перезапишем снимок корзины старым временем (чистим и вставляем заново)
    with repo.get_conn() as c:
        c.execute("DELETE FROM store_prices WHERE store_product_id=?", (sp_id,))
        c.commit()

    repo.save_price(sp_id, 350.0, fetched_at=old_cart, source="cart", point_key=pk)
    repo.save_price(sp_id, 400.0, fetched_at=t_shelf, source=None, point_key=pk)

    # Теперь отдаётся снимок витрины
    snap_expired = repo.latest_price(sp_id, point_key=pk)
    assert snap_expired is not None
    assert snap_expired["source"] is None
    assert snap_expired["price"] == 400.0


def test_price_for_cart_source_does_not_apply_coupon(workspace, monkeypatch):
    store = repo.get_store("magnit")
    prod = repo.upsert_product(Product(id=None, name="Чай черный", unit="pcs"))
    sp_id = repo.upsert_store_product(store.id, "m-tea-1", "Чай черный", unit="pcs")
    repo.confirm_mapping(prod, sp_id, confirmed=True)

    pk = "55.75000,37.61000"
    repo.save_price(sp_id, 200.0, source="cart", point_key=pk)

    def fail_apply(*args, **kwargs):
        raise AssertionError("personal.apply не должен вызываться для source='cart'")

    monkeypatch.setattr("app.personal.apply", fail_apply)

    res = freshness.price_for(prod, store.id, point_key=pk)
    assert res is not None
    assert res["price"] == 200.0
    assert res["source"] == "cart"
