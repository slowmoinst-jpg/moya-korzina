"""Серверный поиск товара для телефона: POST /api/device/find.

Телефон ищет в витрине и упирается в стены (Лента и Дикси — 403, Магнит — пустая
SPA), а сервер ходит рабочими дорогами (app/storefind.py). Поэтому строка наряда без
подтверждённого артикула сначала спрашивает эту дверь.

Стережётся три вещи. Дверь отвечает только своему рабочему месту — как cartplan:
по замку (workplace + secret) или по входу в «Мою корзину». Ответ storefind уходит
телефону как есть, включая `ok: false` с причиной: телефон по `found` кладёт товар,
а по `why` говорит человеку, почему не положил. И сбой сети — это ответ с причиной,
а не молчание и не пятисотая: телефон должен показать хоть что-то.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import api, location, storefind, users  # noqa: E402

PHONE = "79990000077"
ADDRESS = "Столярный переулок, 2"
LAT, LON = 55.76, 37.64


@pytest.fixture
def door(tmp_path, monkeypatch):
    """Приложение на временных базах и секрет рабочего места — как в tests/test_api.py."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.open_workspace(PHONE)
    secret = api.workplace_secret()
    users.deactivate()

    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    yield application, secret
    users.deactivate()


def signed(secret: str, **kw) -> dict:
    return {"workplace": PHONE, "secret": secret, **kw}


def person_place() -> None:
    """Адрес и точка человека — так, как их сохраняет экран настроек."""
    users.activate(PHONE)
    location.save_address(ADDRESS)
    location.save_point(LAT, LON)
    users.deactivate()


def test_found_is_returned_as_is(door, monkeypatch):
    """Уверенный ответ storefind телефон получает целиком, без пересборки полей."""
    application, secret = door
    found = {"ok": True, "exact": True, "store": "magnit",
             "found": [{"sku": "100001", "url": "https://magnit.ru/product/100001",
                        "name": "Молоко Простоквашино 900 мл", "pack_g": 900,
                        "price": 89.9, "confidence": 1.0, "difference": "",
                        "why": ""}],
             "why": "", "cached": False, "asked": "Молоко Простоквашино 900 мл"}
    calls = []

    def fake(store_code, name, **kw):
        calls.append((store_code, name, kw))
        return found

    monkeypatch.setattr(storefind, "find", fake)

    answer = application.test_client().post("/api/device/find", json=signed(
        secret, store="magnit", name="Молоко Простоквашино 900 мл",
        pack_g=900, product_id=42))

    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json() == found
    # product_id телефон присылает для себя: у storefind такого поля нет, и в
    # поиск оно не попадает.
    assert calls == [("magnit", "Молоко Простоквашино 900 мл",
                      {"pack_g": 900.0, "lat": None, "lon": None, "address": None})]


def test_refusal_comes_with_the_chains_own_reason(door, monkeypatch):
    """`ok: false` — тоже ответ: причину сеть называет словами, телефон её не теряет."""
    application, secret = door
    refusal = {"ok": False, "exact": False, "store": "lenta", "found": [],
               "why": "нашлось похожее, но не то: лишние: edge",
               "cached": False}
    monkeypatch.setattr(storefind, "find", lambda *a, **kw: refusal)

    answer = application.test_client().post(
        "/api/device/find", json=signed(secret, store="lenta", name="Молоко Parmalat"))

    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json() == refusal


def test_a_broken_search_is_a_reason_not_a_500(door, monkeypatch):
    """Сеть или база упали — телефону уходит `ok: false`, а не пятисотая."""
    application, secret = door

    def boom(*args, **kwargs):
        raise RuntimeError("сеть легла")

    monkeypatch.setattr(storefind, "find", boom)

    answer = application.test_client().post(
        "/api/device/find", json=signed(secret, store="magnit", name="Молоко"))

    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json() == {"ok": False, "why": "сервер не смог найти"}


@pytest.mark.parametrize("body, why", [
    ({"store": "", "name": "Молоко"}, "не указана сеть или название"),
    ({"store": "magnit", "name": "   "}, "не указана сеть или название"),
    ({"name": "Молоко"}, "не указана сеть или название"),
    ({"store": "magnit"}, "не указана сеть или название"),
])
def test_empty_fields_are_refused_before_the_chain(door, monkeypatch, body, why):
    """Пустая сеть или название — понятный отказ, и в сеть за ним не ходят."""
    application, secret = door
    calls = []
    monkeypatch.setattr(storefind, "find", lambda *a, **kw: calls.append((a, kw)))

    answer = application.test_client().post("/api/device/find", json=signed(secret, **body))

    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json() == {"ok": False, "why": why}
    assert calls == []


def test_the_persons_point_and_address_are_taken_when_not_sent(door, monkeypatch):
    """Точку человек не выбирает: она лежит в app/location и уходит сети сама."""
    application, secret = door
    person_place()
    seen = {}
    monkeypatch.setattr(
        storefind, "find",
        lambda store_code, name, **kw: seen.update(kw) or {"ok": False, "why": "пусто"})

    answer = application.test_client().post(
        "/api/device/find", json=signed(secret, store="lenta", name="Молоко"))

    assert answer.status_code == 200, answer.get_json()
    assert seen["lat"] == pytest.approx(LAT)
    assert seen["lon"] == pytest.approx(LON)
    assert seen["address"] == ADDRESS


def test_sent_coordinates_win_over_the_saved_point(door, monkeypatch):
    """Присланная точка главнее сохранённой: её поставил сам телефон."""
    application, secret = door
    person_place()
    seen = {}
    monkeypatch.setattr(
        storefind, "find",
        lambda store_code, name, **kw: seen.update(kw) or {"ok": False, "why": "пусто"})

    application.test_client().post(
        "/api/device/find", json=signed(secret, store="magnit", name="Молоко",
                                        lat=59.94, lon=30.31))

    assert seen["lat"] == pytest.approx(59.94)
    assert seen["lon"] == pytest.approx(30.31)


@pytest.mark.parametrize("body", [
    {"store": "magnit", "name": "Молоко"},
    {"workplace": PHONE, "secret": "нет", "store": "magnit", "name": "Молоко"},
])
def test_stranger_gets_nothing(door, monkeypatch, body):
    """Без замка, с чужим секретом и без входа в «Мою корзину» дверь не ищет."""
    application, _ = door
    calls = []
    monkeypatch.setattr(storefind, "find", lambda *a, **kw: calls.append((a, kw)))

    answer = application.test_client().post("/api/device/find", json=body)

    assert answer.status_code in (401, 403), answer.get_json()
    assert calls == []


def test_session_of_the_phone_opens_the_door_too(door, monkeypatch):
    """Вход в «Мою корзину» — второй ключ, как у cartplan: приложение уже вошло."""
    application, _ = door
    monkeypatch.setattr(storefind, "find",
                        lambda *a, **kw: {"ok": True, "store": "magnit", "found": []})

    client = application.test_client()
    from conftest import sign_in

    sign_in(client, PHONE)
    answer = client.post("/api/device/find", json={"store": "magnit", "name": "Молоко"})

    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json()["ok"] is True
