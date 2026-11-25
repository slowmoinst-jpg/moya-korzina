from __future__ import annotations

import os
import sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import repo, users
from app.models import Product

PHONE = "79990000071"


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    from conftest import sign_in

    application = create_app()
    application.config["TESTING"] = True
    users.open_workspace(PHONE)
    repo.init_db()

    # Seed sample products
    p1 = Product(id=None, name="Молоко Простоквашино 2.5%", brand="Простоквашино",
                 weight_g=930.0, unit="pcs", category="Молочное", active=True)
    p2 = Product(id=None, name="Мука Пшеничная высший сорт", brand="Макфа",
                 weight_g=1000.0, unit="pcs", category="Бакалея", active=True)
    repo.upsert_product(p1)
    repo.upsert_product(p2)

    with application.test_client() as client:
        sign_in(client, PHONE)
        yield client
    users.deactivate()


def test_egg_query_does_not_match_milk_in_combined_aisle(web):
    users.activate(PHONE)
    milk = repo.upsert_product(Product(None, "Молоко Село Зеленое", category="Молоко (молочные продукты, яйцо)"))
    eggs = repo.upsert_product(Product(None, "Куриное яйцо С2 10шт", category="Молоко, масло, яйца / Яйца"))
    matches = web.get("/api/search", query_string={"q": "Яйца"}).get_json()["results"]
    ids = {row["product"]["id"] for row in matches}
    assert eggs in ids and milk not in ids


def test_search_personal_products_by_name_and_category(web):
    result = web.get("/api/search", query_string={"q": "Простоквашино"})
    assert result.status_code == 200
    products = [row["product"] for row in result.get_json()["results"]]
    assert any(p["id"] > 0 and p["name"] == "Молоко Простоквашино 2.5%" for p in products)
    assert all(p["brand"] != "Макфа" for p in products)

    result = web.get("/api/search", query_string={"q": "Бакалея"})
    assert result.status_code == 200
    products = [row["product"] for row in result.get_json()["results"]]
    assert any(p["id"] > 0 and p["category"] == "Бакалея" for p in products)
    assert all(p["category"] != "Молочное" for p in products)


def test_add_a_catalog_search_result_to_the_basket(web):
    result = web.get("/api/search", query_string={"q": "Простоквашино"})
    product = next(r["product"] for r in result.get_json()["results"] if r["product"]["id"] > 0)
    response = web.post("/api/sync", json={
        "baskets": [{"id": 1, "name": "Корзина"}],
        "basketItems": {"1": [{"product_id": product["id"], "qty": 1}]},
        "lastBasketId": 1,
    })
    assert response.status_code == 200
    snapshot = web.get("/api/sync").get_json()["snapshot"]
    assert snapshot["lastBasketId"] == 1
    assert snapshot["basketItems"]["1"][0]["product_id"] == product["id"]
    assert snapshot["basketItems"]["1"][0]["qty"] == 1
