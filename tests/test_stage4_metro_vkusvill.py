"""Проверка Этапа 4: METRO и ВкусВилл (docs/plans/korzina-vo-vseh-setyah-2026-10-24.md).

- METRO:
  - при наличии токена (metro_cart.writable() == True) дорога API;
  - без токена сеть собирается телефоном владельца (дорога PHONE);
  - phonecart.rules('metro') содержит селекторы, адрес корзины и хост metro-cc.ru;
  - телефонный отчёт accept_report переводит строку в DONE с сетевым итогом.
- ВкусВилл:
  - phonecart.rules('vkusvill') содержит селекторы и адрес корзины https://vkusvill.ru/cart/;
  - чтение сетевого итога и скидок («Любимый продукт», «Разнообразное питание», «Скидка по карте»)
    через page_totals;
  - accept_report переводит строку ВкусВилла в DONE с Total.source == NETWORK;
  - humansteps.board() отражает дороги и факт получения сетевых итогов.
"""
from __future__ import annotations

import pytest

from app import cartrun, carttotal, config, humansteps, phonecart, repo
from app.connectors import metro_cart
from app.db import init_db
from app.models import Product
from app.shopbrowser import signals


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "test.db"))
    init_db()


@pytest.fixture
def basket(db):
    b_id = repo.create_basket("Тест Этап 4")
    p1 = repo.upsert_product(Product(id=None, name="Сыр Сулугуни 300 г", unit="pcs"))
    repo.set_basket_item(b_id, p1, 1.0)
    for code, sku in [("metro", "204411"), ("vkusvill", "12345")]:
        store = repo.get_store(code)
        if store:
            sp = repo.upsert_store_product(store.id, sku, "Сыр Сулугуни 300 г",
                                           url=f"https://{code}.example/{sku}")
            repo.confirm_mapping(p1, sp, confirmed=True)
            repo.save_price(sp, 250.0)
    return b_id


def test_metro_road_depends_on_token_or_phone(monkeypatch):
    """METRO: при настроенном токене — API, без токена — сборка телефоном."""
    monkeypatch.setattr(metro_cart, "writable", lambda: False)
    assert cartrun.road("metro") == cartrun.PHONE

    monkeypatch.setattr(metro_cart, "writable", lambda: True)
    assert cartrun.road("metro") == cartrun.API


def test_metro_phone_rules_and_report(basket):
    """METRO на телефонном маршруте имеет селекторы и сохраняет сетевой итог."""
    rules = phonecart.rules("metro")
    assert rules["selectors"], "селекторы кнопки «в корзину» у METRO"
    assert "https://online.metro-cc.ru/cart" in rules["cart_pages"]
    assert "metro-cc.ru" in rules["hosts"]

    report = {
        "store": "metro",
        "basket": basket,
        "stamp": "stamp_metro_1",
        "items": [
            {"sku": "204411", "name": "Сыр Сулугуни 300 г", "ok": True, "how": "tap"},
        ],
        "page_totals": [
            {"kind": "goods", "label": "Товары", "amount": 250.0},
            {"kind": "discount", "label": "Скидка по карте клиента", "amount": 25.0},
            {"kind": "total", "label": "Итого к оплате", "amount": 225.0},
        ],
        "cart_count": 1,
    }

    phonecart.accept_report(report)

    row = cartrun.get("metro")
    assert row is not None
    assert row.state == cartrun.DONE
    assert row.by == cartrun.DEVICE
    assert "Сыр Сулугуни 300 г" in row.landed
    assert row.total is not None
    assert row.total.source == carttotal.NETWORK
    assert row.total.sum == 225.0
    assert row.total.discount == 25.0


def test_vkusvill_phone_rules_and_reading_totals(basket):
    """ВкусВилл читает итог сети со страницы /cart/ под входом человека с его скидками."""
    rules = phonecart.rules("vkusvill")
    assert rules["selectors"], "селекторы ВкусВилла"
    assert "https://vkusvill.ru/cart/" in rules["cart_pages"]
    assert "vkusvill.ru" in rules["hosts"]

    report = {
        "store": "vkusvill",
        "basket": basket,
        "stamp": "stamp_vv_1",
        "items": [
            {"sku": "12345", "name": "Сыр Сулугуни 300 г", "ok": True, "how": "tap"},
        ],
        "page_totals": [
            {"kind": "goods", "label": "Товары", "amount": 300.0},
            {"kind": "discount", "label": "Любимый продукт -20%", "amount": 60.0},
            {"kind": "total", "label": "К оплате", "amount": 240.0},
        ],
        "cart_count": 1,
    }

    phonecart.accept_report(report)

    row = cartrun.get("vkusvill")
    assert row is not None
    assert row.state == cartrun.DONE
    assert row.by == cartrun.DEVICE
    assert "Сыр Сулугуни 300 г" in row.landed
    assert row.total is not None
    assert row.total.source == carttotal.NETWORK
    assert row.total.sum == 240.0
    assert row.total.discount == 60.0


def test_board_reflects_metro_and_vkusvill_network_totals(basket, monkeypatch):
    """humansteps.board() подтверждает статус готовности и сетевой итог для обеих сетей."""
    test_metro_phone_rules_and_report(basket)
    test_vkusvill_phone_rules_and_reading_totals(basket)

    board = humansteps.board()
    metro_item = next((r for r in board if r["code"] == "metro"), None)
    vv_item = next((r for r in board if r["code"] == "vkusvill"), None)

    assert metro_item is not None
    assert metro_item["network_total"] is True
    assert metro_item["landed"] == 1

    assert vv_item is not None
    assert vv_item["network_total"] is True
    assert vv_item["landed"] == 1
