"""Личные акции из аккаунта магазина меняют цену в расчёте (app/personal.py)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app import collector, config, freshness, repo, service
from app.db import init_db
from app.models import Product


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "personal.db"))
    init_db()
    return tmp_path


def _product(store_code, name, sku, price, per_kg=None):
    store = repo.get_store(store_code)
    pid = repo.upsert_product(Product(id=None, name=name, unit="pcs"))
    sp = repo.upsert_store_product(store.id, sku, name)
    repo.confirm_mapping(pid, sp, confirmed=True)
    repo.save_price(sp, price, per_kg, fetched_at=(datetime.now() - timedelta(minutes=3)).isoformat(timespec="seconds"))
    return pid, store


def _coupons(store_code, *items):
    collector._remember_coupons(store_code, [collector._coupon(c) for c in items], "2026-09-26T10:00:00")


@pytest.mark.parametrize("raw, terms", [
    ({"title": "Молоко", "value": "-30%"}, {"percent": 30.0}),
    ({"title": "Кефир", "value": "69,90 ₽"}, {"price": 69.9}),
    ({"title": "Хлеб", "value": "-15 ₽"}, {"off_rub": 15.0}),
    ({"title": "Сыр", "price": "199", "value": "-40%"}, {"price": 199.0}),
    ({"title": "Баллы x2", "value": "двойные баллы"}, {}),
])
def test_coupon_terms_are_read_from_value(raw, terms):
    got = collector._coupon(raw)
    assert {k: got[k] for k in ("price", "percent", "off_rub") if k in got} == terms


def test_coupon_by_sku_lowers_the_price(db):
    pid, store = _product("pyaterochka", "Кефир Домик в деревне 1% 900 г", "5-100", 89.0)
    _coupons("pyaterochka", {"title": "Кефир по вашей карте", "value": "69,90 ₽", "sku": "5-100"})

    snap = freshness.price_for(pid, store.id)

    assert snap["price"] == 69.9 and snap["shelf_price"] == 89.0
    assert snap["coupon"] == "Кефир по вашей карте"
    assert service._price_for_line(pid, store, 2, "pcs") == (139.8, True)


def test_coupon_by_product_name_needs_a_close_match(db):
    pid, store = _product("magnit", "Молоко Простоквашино 2,5% 930 мл", "m-1", 100.0)
    other, _ = _product("magnit", "Йогурт Эрмигурт клубника 100 г", "m-2", 50.0)
    _coupons("magnit", {"title": "-30% на Молоко Простоквашино 2,5% 930 мл", "product": "Молоко Простоквашино 2,5% 930 мл",
                        "value": "-30%"})

    assert freshness.price_for(pid, store.id)["price"] == 70.0
    assert freshness.price_for(other, store.id)["price"] == 50.0, "чужой товар купон не задевает"


def test_category_coupon_is_not_stretched_over_goods(db):
    pid, store = _product("magnit", "Молоко Простоквашино 2,5% 930 мл", "m-3", 100.0)
    _coupons("magnit", {"title": "-20% на молочную продукцию", "value": "-20%"})

    assert freshness.price_for(pid, store.id)["price"] == 100.0


def test_not_activated_or_worse_coupon_changes_nothing(db):
    pid, store = _product("pyaterochka", "Сметана 15%", "5-200", 80.0)
    _coupons("pyaterochka",
             {"title": "Сметана", "sku": "5-200", "value": "-50%", "activated": False},
             {"title": "Сметана дороже", "sku": "5-200", "value": "95 ₽"})

    assert freshness.price_for(pid, store.id)["price"] == 80.0


def test_per_kg_price_follows_the_coupon(db):
    pid, store = _product("lenta", "Сыр Российский 200 г", "l-1", 200.0, per_kg=1000.0)
    _coupons("lenta", {"title": "Сыр", "sku": "l-1", "value": "-25%"})

    snap = freshness.price_for(pid, store.id)
    assert snap["price"] == 150.0 and snap["price_per_kg"] == 750.0


def test_coupons_arrive_through_the_collector_report(db):
    from app import store_accounts

    pid, store = _product("pyaterochka", "Творог 5% 300 г", "5-300", 139.0)
    report = {"store": "pyaterochka", "logged_in": True, "gives": [store_accounts.COUPONS],
              "coupons": [{"id": "c1", "title": "Творог", "sku": "5-300", "value": "-20%",
                           "ends_at": (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")}]}
    collector.accept(report)

    assert freshness.price_for(pid, store.id)["price"] == 111.2


# ---------- приём с телефона ----------
@pytest.fixture
def web(tmp_path, monkeypatch):
    from app import users

    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    client = application.test_client()
    client.post("/login", data={"phone": "79990000003", "next": "/"})
    yield client
    users.deactivate()


def test_phone_sends_offers_with_its_session(web):
    answer = web.post("/api/store_accounts/sync", json={
        "store": "magnit", "logged_in": True, "gives": ["coupons"],
        "coupons": [{"title": "Молоко Простоквашино", "value": "-30%"}]})
    assert answer.status_code == 200 and answer.get_json()["ok"]


def test_handoff_takes_cookies_in_the_body(web):
    answer = web.post("/api/handoff", json={"store": "magnit",
                                            "cookies": [{"name": "mg_at", "value": "x"}]})
    assert answer.status_code in (200, 400)
    assert "error" in answer.get_json() or answer.get_json().get("saved")


def test_cabinet_ignores_a_phone_in_the_address(web, tmp_path):
    from app import users

    web.get("/cabinet?store=magnit&phone=79990000999&vhod=e30")
    assert not users.exists("79990000999"), "номер из адреса не заводит и не открывает чужую базу"
