"""Ссылка на готовую корзину для телефона: POST /api/device/cartlink (Z9).

ЗАЧЕМ. У Ленты телефон упирается в 403 на сайте (замеры 20–21.10.2026), и строка
наряда не ложится. При этом MCP Ленты умеет `storefront_cart_link_create`: сервер
собирает корзину сам и отдаёт ссылку, где позиции уже лежат с количеством. Телефон
открывает её последним шагом наряда и говорит владельцу, пустил ли сайт с этого
подключения.

Стережётся то же, что у соседней двери /api/device/find: отвечаем только своему
рабочему месту (замок workplace+secret или вход в «Мою корзину»), ответ
app/storefind.cart_link уходит телефону КАК ЕСТЬ (включая `ok: false` с причиной и
`missing`), пустые поля — понятный отказ без похода в сеть, а сбой сети — ответ с
причиной, а не пятисотая: телефон должен закрыть наряд, а не зависнуть.
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

ITEMS = [
    {"product_id": 10, "name": "Молоко Parmalat 1 л", "qty": 1, "pack_g": 1000},
    {"product_id": 11, "name": "Сыр Российский", "qty": 0.4, "pack_g": 200},
]


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


def test_a_built_link_is_returned_as_is(door, monkeypatch):
    """Ответ storefind телефон получает целиком: url, matched, missing и note."""
    application, secret = door
    built = {"ok": True, "url": "https://lenta.com/basket/share/abc",
             "matched": [10, 11], "missing": [],
             "why": "", "note": "в ссылку не вошло 0 поз."}
    calls = []

    def fake(store_code, items, **kw):
        calls.append((store_code, items, kw))
        return built

    monkeypatch.setattr(storefind, "cart_link", fake)

    answer = application.test_client().post(
        "/api/device/cartlink", json=signed(secret, store="lenta", items=ITEMS))

    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json() == built
    assert calls == [("lenta", ITEMS, {"lat": None, "lon": None, "address": None})]


def test_a_refusal_keeps_missing_and_the_reason(door, monkeypatch):
    """`ok: false` — тоже ответ: какие позиции не вошли и почему, телефон не теряет."""
    application, secret = door
    refusal = {"ok": False, "url": "", "matched": [10], "missing": [11],
               "why": "ни одна строка не нашлась в Ленте точно",
               "note": "в ссылку не вошло 1 поз."}
    monkeypatch.setattr(storefind, "cart_link", lambda *a, **kw: refusal)

    answer = application.test_client().post(
        "/api/device/cartlink", json=signed(secret, store="lenta", items=ITEMS))

    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json() == refusal


def test_a_broken_link_is_a_reason_not_a_500(door, monkeypatch):
    """Сеть/база упали — телефону уходит `ok: false` с причиной, а не пятисотая."""
    application, secret = door

    def boom(*args, **kwargs):
        raise RuntimeError("MCP Ленты не ответил")

    monkeypatch.setattr(storefind, "cart_link", boom)

    answer = application.test_client().post(
        "/api/device/cartlink", json=signed(secret, store="lenta", items=ITEMS))

    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json() == {"ok": False, "why": "сервер не смог собрать ссылку"}


@pytest.mark.parametrize("body", [
    {"store": "", "items": ITEMS},
    {"store": "lenta", "items": []},
    {"store": "lenta"},
    {"items": ITEMS},
    {"store": "lenta", "items": "мусор"},
])
def test_empty_fields_are_refused_before_the_chain(door, monkeypatch, body):
    """Пустая сеть или позиции — понятный отказ, и в сеть за ним не ходят."""
    application, secret = door
    calls = []
    monkeypatch.setattr(storefind, "cart_link", lambda *a, **kw: calls.append((a, kw)))

    answer = application.test_client().post(
        "/api/device/cartlink", json=signed(secret, **body))

    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json() == {"ok": False, "why": "не указана сеть или позиции"}
    assert calls == []


def test_only_real_rows_reach_the_chain(door, monkeypatch):
    """Мусор в списке позиций отбрасывается: в сеть уходят только объекты."""
    application, secret = door
    seen = {}
    monkeypatch.setattr(
        storefind, "cart_link",
        lambda store_code, items, **kw: seen.update(items=items) or {"ok": False, "why": "пусто"})

    application.test_client().post(
        "/api/device/cartlink",
        json=signed(secret, store="lenta", items=[ITEMS[0], "мусор", {"name": "Молоко"}]))

    assert seen["items"] == [ITEMS[0], {"name": "Молоко"}]


def test_the_persons_point_and_address_are_taken_when_not_sent(door, monkeypatch):
    """Точку человек не выбирает: она лежит в app/location и уходит сети сама."""
    application, secret = door
    person_place()
    seen = {}
    monkeypatch.setattr(
        storefind, "cart_link",
        lambda store_code, items, **kw: seen.update(kw) or {"ok": False, "why": "пусто"})

    answer = application.test_client().post(
        "/api/device/cartlink", json=signed(secret, store="lenta", items=ITEMS))

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
        storefind, "cart_link",
        lambda store_code, items, **kw: seen.update(kw) or {"ok": False, "why": "пусто"})

    application.test_client().post(
        "/api/device/cartlink", json=signed(secret, store="lenta", items=ITEMS,
                                            lat=59.94, lon=30.31))

    assert seen["lat"] == pytest.approx(59.94)
    assert seen["lon"] == pytest.approx(30.31)


@pytest.mark.parametrize("body", [
    {"store": "lenta", "items": ITEMS},
    {"workplace": PHONE, "secret": "нет", "store": "lenta", "items": ITEMS},
])
def test_stranger_gets_nothing(door, monkeypatch, body):
    """Без замка и с чужим секретом дверь ссылку не собирает."""
    application, _ = door
    calls = []
    monkeypatch.setattr(storefind, "cart_link", lambda *a, **kw: calls.append((a, kw)))

    answer = application.test_client().post("/api/device/cartlink", json=body)

    assert answer.status_code in (401, 403), answer.get_json()
    assert calls == []


def test_session_of_the_phone_opens_the_door_too(door, monkeypatch):
    """Вход в «Мою корзину» — второй ключ, как у /api/device/find."""
    application, _ = door
    monkeypatch.setattr(storefind, "cart_link",
                        lambda *a, **kw: {"ok": True, "url": "https://lenta.com/x",
                                          "matched": [], "missing": [], "why": "", "note": ""})

    client = application.test_client()
    client.post("/login", data={"phone": PHONE, "next": "/"})
    answer = client.post("/api/device/cartlink", json={"store": "lenta", "items": ITEMS})

    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json()["url"] == "https://lenta.com/x"
