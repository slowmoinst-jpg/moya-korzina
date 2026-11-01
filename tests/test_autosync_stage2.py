"""Тесты Этапа 2 (приложение 1.19): drop в plan и data-order у формы «Заказать»."""
from __future__ import annotations

import html
import json
import re

import pytest

from app import cartrun, db, location, phonecart, repo, store_accounts, users
from app.models import Product
from app.web import create_app


PHONE = "+79991112233"


def test_phonecart_plan_drop_skus(tmp_path, monkeypatch):
    """plan() с параметром drop возвращает url и name по store_products."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))     # не трогать базу в data/
    db.init_db()
    store = repo.get_store("magnit")
    assert store is not None

    sp1 = repo.upsert_store_product(
        store.id, "drop-sku-1", "Товар с ссылкой", url="https://magnit.ru/product/drop1"
    )
    sp2 = repo.upsert_store_product(
        store.id, "drop-sku-2", "Товар без ссылки", url=None
    )

    body = {
        "store": "magnit",
        "drop": ["drop-sku-1", "drop-sku-2", "unknown-sku-999"],
    }
    res = phonecart.plan(body)

    assert res["ok"] is True
    assert "drop" in res
    drops = {item["sku"]: item for item in res["drop"]}

    assert "drop-sku-1" in drops
    assert drops["drop-sku-1"]["name"] == "Товар с ссылкой"
    assert drops["drop-sku-1"]["url"] == "https://magnit.ru/product/drop1"

    assert "drop-sku-2" in drops
    assert drops["drop-sku-2"]["name"] == "Товар без ссылки"
    assert "url" not in drops["drop-sku-2"]

    assert "unknown-sku-999" in drops
    assert drops["unknown-sku-999"]["name"] == ""
    assert "url" not in drops["unknown-sku-999"]

    # Пустой drop
    res_empty = phonecart.plan({"store": "magnit", "drop": []})
    assert res_empty["ok"] is True
    assert res_empty.get("drop") == []

    # Уборка после «Заказать» в невыбранной сети: пустой список позиций — пустой наряд,
    # а не вся корзина, и адреса карточек своего для снятия.
    res_clean = phonecart.plan({"store": "magnit", "lines": [], "drop": ["drop-sku-1"]})
    assert res_clean["lines"] == [] and res_clean["search"] == []
    assert res_clean["drop"] == [{"sku": "drop-sku-1", "name": "Товар с ссылкой",
                                  "url": "https://magnit.ru/product/drop1"}]


def test_cheaper_order_form_data_order(tmp_path, monkeypatch):
    """Форма «Заказать» на экране /phone/cheaper содержит атрибут data-order с basket, stamp и stores."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    monkeypatch.setattr("app.web.phone.cheaper.start_prices", lambda *a, **kw: None)
    monkeypatch.setattr("app.web.phone.cheaper.warm", lambda *a, **kw: None)
    monkeypatch.setattr("app.cartrun._login", lambda code: "")
    monkeypatch.setattr("app.web.phone.common._login", lambda code: "")

    db.init_db()

    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    phone = users.normalize_phone(PHONE)
    from conftest import sign_in
    sign_in(client, phone)

    users.open_workspace(phone)

    # Создаём корзину с товаром и ценой в Магните
    prod = repo.upsert_product(Product(id=None, name="Молоко 3.2% 1 л", unit="pcs"))
    basket_id = repo.create_basket("Заказная")
    repo.set_basket_item(basket_id, prod, 2.0)

    store = repo.get_store("magnit")
    sp_id = repo.upsert_store_product(store.id, "sku-mg-1", "Молоко 3.2% 1 л", url="https://magnit.ru/product/1")
    repo.confirm_mapping(prod, sp_id, confirmed=True)
    repo.save_price(sp_id, 80.0)
    expected_stamp = cartrun.stamp_of(basket_id)

    users.deactivate()

    resp = client.get("/phone/cheaper")
    assert resp.status_code == 200
    page = resp.data.decode("utf-8")

    assert 'data-order="' in page
    match = re.search(r'data-order="([^"]+)"', page)
    assert match is not None, "атрибут data-order должен присутствовать у формы «Заказать»"

    raw_order = html.unescape(match.group(1))
    order_data = json.loads(raw_order)

    assert order_data["basket"] == basket_id
    assert order_data["stamp"] == expected_stamp
    assert "stores" in order_data
    assert "magnit" in order_data["stores"]
    assert isinstance(order_data["stores"]["magnit"], list)
    assert len(order_data["stores"]["magnit"]) >= 1
    line0 = order_data["stores"]["magnit"][0]
    assert line0["product_id"] == prod
    assert line0["qty"] == 2.0


def test_cheaper_order_form_data_order_split(tmp_path, monkeypatch):
    """При разбивке на два магазина data-order содержит оба магазина в stores."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    monkeypatch.setattr("app.web.phone.cheaper.start_prices", lambda *a, **kw: None)
    monkeypatch.setattr("app.web.phone.cheaper.warm", lambda *a, **kw: None)
    monkeypatch.setattr("app.cartrun._login", lambda code: "")
    monkeypatch.setattr("app.web.phone.common._login", lambda code: "")

    db.init_db()

    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    phone = users.normalize_phone(PHONE)
    from conftest import sign_in
    sign_in(client, phone)

    users.open_workspace(phone)

    prod1 = repo.upsert_product(Product(id=None, name="Товар 1", unit="pcs"))
    prod2 = repo.upsert_product(Product(id=None, name="Товар 2", unit="pcs"))
    basket_id = repo.create_basket("Сплит")
    repo.set_basket_item(basket_id, prod1, 1.0)
    repo.set_basket_item(basket_id, prod2, 1.0)

    mg = repo.get_store("magnit")
    py = repo.get_store("pyaterochka")

    sp1 = repo.upsert_store_product(mg.id, "sku-mg-1", "Товар 1", url="https://magnit.ru/product/1")
    repo.confirm_mapping(prod1, sp1, confirmed=True)
    repo.save_price(sp1, 50.0)

    sp2 = repo.upsert_store_product(py.id, "sku-py-2", "Товар 2", url="https://5ka.ru/product/2")
    repo.confirm_mapping(prod2, sp2, confirmed=True)
    repo.save_price(sp2, 60.0)

    expected_stamp = cartrun.stamp_of(basket_id)
    users.deactivate()

    resp = client.get("/phone/cheaper")
    assert resp.status_code == 200
    page = resp.data.decode("utf-8")

    matches = re.findall(r'data-order="([^"]+)"', page)
    assert len(matches) >= 2, "у разбивки на два магазина формы содержат data-order"

    order_data = json.loads(html.unescape(matches[0]))
    assert order_data["basket"] == basket_id
    assert order_data["stamp"] == expected_stamp
    assert "magnit" in order_data["stores"]
    assert "pyaterochka" in order_data["stores"]
    assert len(order_data["stores"]["magnit"]) == 1
    assert len(order_data["stores"]["pyaterochka"]) == 1
    assert order_data["stores"]["magnit"][0]["product_id"] == prod1
    assert order_data["stores"]["pyaterochka"][0]["product_id"] == prod2

