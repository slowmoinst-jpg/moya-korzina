"""Сборка корзины с любого экрана телефона (решение 01.11.2026).

Проверяется:
- JSON /phone/basket/runs содержит 'collect';
- HTML любого экрана телефона, кроме демо (например «История заказов» или «Настройки»),
  содержит атрибут-метку data-kz-collect-all;
- В демо-режиме data-kz-collect-all отсутствует.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import repo, users
from app.models import Product

PHONE = "79998889900"


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    users.open_workspace(PHONE)
    milk = repo.upsert_product(Product(id=None, name="Молоко 3.2%"))
    basket_id = repo.create_basket("Еженедельная")
    repo.set_basket_item(basket_id, milk, 2.0)
    users.deactivate()

    yield client
    users.deactivate()


def test_basket_runs_json_contains_collect(web):
    from conftest import sign_in
    sign_in(web, PHONE)

    resp = web.get("/phone/basket/runs")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "collect" in data
    collect = data["collect"]
    assert "basket" in collect
    assert "stamp" in collect
    assert "chains" in collect


def test_non_demo_screens_contain_collect_all_script(web):
    from conftest import sign_in
    sign_in(web, PHONE)

    # 1. Экран «История заказов»
    resp_orders = web.get("/phone/orders")
    assert resp_orders.status_code == 200
    html_orders = resp_orders.data.decode("utf-8")
    assert "data-kz-collect-all" in html_orders
    assert "KorzinaDevice.collect" in html_orders

    # 2. Экран «Настройки»
    resp_settings = web.get("/phone/settings")
    assert resp_settings.status_code == 200
    html_settings = resp_settings.data.decode("utf-8")
    assert "data-kz-collect-all" in html_settings


def test_demo_screen_does_not_contain_collect_all_script(web):
    # Вход в демо-режим через POST /demo
    web.post("/demo")
    resp = web.get("/phone/orders")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "data-kz-collect-all" not in html
