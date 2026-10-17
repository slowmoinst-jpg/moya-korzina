"""Запись запросов корзины с телефона: что сохраняется, а что не должно сохраниться никогда.

Запись снимается на странице магазина, где человек вошёл, и едет к нам по сети. Поэтому
главное здесь — не форма записи, а то, чего в ней нет: значений заголовков, телефонов,
карт, адресов, имён и всего, что относится ко входу.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import cartrecord  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    return tmp_path


ADD = {
    "method": "POST", "host": "web-gateway.magnit.ru", "path": "/v1/cart/items",
    "headers": ["Content-Type", "Authorization", "X-Device-Id"],
    "body": json.dumps({"productId": 1000537689, "quantity": 2, "storeCode": "784182",
                        "userId": 99887766, "phone": "+79991234567",
                        "address": {"street": "Невский", "house": "28"}}),
    "status": 200,
    "response": json.dumps({"items": [{"productId": 1000537689, "name": "Кунжут 20г",
                                       "price": 31.99, "quantity": 2}],
                            "total": 63.98, "calculatedDiscount": 4.0,
                            "cardNumber": "1234567890123456", "retailer_id": "D"}),
    "page": "/product/1000537689",
}


def test_names_and_secrets_never_survive_the_record():
    row = cartrecord.normalize(ADD, "magnit")
    text = json.dumps(row, ensure_ascii=False)

    for leaked in ("79991234567", "Невский", "1234567890123456", "99887766", "Кунжут"):
        assert leaked not in text, f"в запись попало «{leaked}»"
    assert row["body"]["phone"] == "<скрыто>" and row["body"]["address"] == "<скрыто>"
    assert row["body"]["userId"] == "<скрыто>"
    assert row["response"]["cardNumber"] == "<скрыто>"


def test_what_the_cart_is_made_of_survives():
    """Код товара, количество, итог и скидка — ради них запись и снимается."""
    row = cartrecord.normalize(ADD, "magnit")

    assert row["body"]["productId"] == 1000537689
    assert row["body"]["quantity"] == 2
    assert row["response"]["total"] == 63.98
    assert row["response"]["calculatedDiscount"] == 4.0, "слово «lat» внутри calculated — не широта"
    assert row["response"]["retailer_id"] == "D", "слово «tel» внутри retailer — не телефон"


def test_header_values_are_not_even_accepted():
    row = cartrecord.normalize(dict(ADD, headers=["Authorization", "authorization"]), "magnit")
    assert row["headers"] == ["authorization"], "только имена, без повторов"


def test_ids_in_the_path_are_folded():
    assert cartrecord.template_path("/api/v1/5502aed9e3b63cd2e222822c002cd29a/10/eshop/basket/") \
        == "/api/v1/{id}/10/eshop/basket/"
    assert cartrecord.template_path("/api/orders/v10/orders/3f2b1c4e-1111-2222-3333-444455556666/") \
        == "/api/orders/v10/orders/{id}/"


def test_login_and_strangers_are_not_recorded():
    login = dict(ADD, path="/v1/auth/otp/check", body=json.dumps({"code": "1234"}))
    counter = dict(ADD, host="mc.yandex.ru", path="/watch/56708149/1")
    lookalike = dict(ADD, host="magnit.ru.evil.example")

    assert cartrecord.normalize(login, "magnit") is None
    assert cartrecord.normalize(counter, "magnit") is None
    assert cartrecord.normalize(lookalike, "magnit") is None


def test_markers_from_the_phone_stay_as_they_came():
    """Телефон уже заменил значения отметками; второй разбор их не переписывает."""
    phone_side = dict(ADD, body={"productId": 1, "title": "<строка 12>",
                                 "items": {"<список>": 3, "пример": [{"productId": 7}]}})
    row = cartrecord.normalize(phone_side, "magnit")
    assert row["body"]["title"] == "<строка 12>"
    assert row["body"]["items"] == {"<список>": 3, "пример": [{"productId": 7}]}


def test_second_pass_changes_nothing():
    """Разбор телефона и разбор сервера — одно правило: дважды разобранное не меняется."""
    once = cartrecord.shape(json.loads(ADD["response"]))
    assert cartrecord.shape(once) == once


def test_record_accumulates_per_chain(store):
    first = cartrecord.accept({"store": "magnit", "entries": [ADD]})
    again = cartrecord.accept({"store": "magnit", "entries": [ADD, dict(ADD, method="DELETE")]})

    assert first["taken"] == 1 and again["taken"] == 2
    calls = cartrecord.recipes("magnit")["calls"]
    assert calls["POST web-gateway.magnit.ru/v1/cart/items"]["count"] == 2
    assert "DELETE web-gateway.magnit.ru/v1/cart/items" in calls
    assert os.path.exists(store / "data" / "cart_recipes" / "magnit.json")
    assert "POST web-gateway.magnit.ru/v1/cart/items" in cartrecord.report("magnit")


def test_unknown_chain_and_empty_record_are_refused(store):
    with pytest.raises(ValueError):
        cartrecord.accept({"store": "neizvestno", "entries": [ADD]})
    with pytest.raises(ValueError):
        cartrecord.accept({"store": "magnit", "entries": []})


# ---------- приём с телефона ----------
@pytest.fixture
def web(tmp_path, monkeypatch):
    from app import users

    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    yield application
    users.deactivate()


def test_phone_with_its_session_records_the_cart(web):
    client = web.test_client()
    client.post("/login", data={"phone": "79990000004", "next": "/"})

    answer = client.post("/api/cart_record", json={"store": "magnit", "entries": [ADD]})

    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json()["taken"] == 1


def test_stranger_without_session_cannot_record(web):
    answer = web.test_client().post("/api/cart_record", json={"store": "magnit", "entries": [ADD]})
    assert answer.status_code in (401, 403)
