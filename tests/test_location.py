"""Адрес клиента: хранение, разрешение в точку и то, как он доезжает до коннектора.

Главное, что здесь защищается, — правило «сменил адрес, забудь точку». Точка,
найденная по старому адресу, к новому отношения не имеет, а на вид цены из неё
неотличимы от настоящих: это ровно тот сорт ошибки, который не находится глазами.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, location, repo  # noqa: E402
from app.db import init_db  # noqa: E402
from app.models import Location  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "loc.db"))
    init_db()


# ---------- хранение ----------
def test_address_survives_restart(db):
    """Адрес — ответ пользователя, а не настройка запуска: он обязан лежать в базе."""
    location.save_address("Москва, Ходынский бульвар 4")
    assert location.address() == "Москва, Ходынский бульвар 4"
    assert location.is_set()


def test_empty_address_is_the_same_as_no_address(db):
    location.save_address("Москва")
    location.save_address("   ")
    assert location.address() is None
    assert not location.is_set()


def test_without_address_store_gets_nothing_and_falls_back(db):
    """Нет адреса — None, чтобы коннектор взял запасное значение из config.yaml."""
    assert location.for_store("lenta") is None


# ---------- адрес и точка ----------
def test_changing_address_forgets_the_point(db):
    """Самое важное правило модуля."""
    location.save_address("Москва, Ходынский бульвар 4")
    location.save_point("lenta", "5344", "ТК4537")
    assert location.point("lenta") == ("5344", "ТК4537")

    location.save_address("Екатеринбург, улица Щербакова 4")

    assert location.point("lenta") == (None, None), \
        "точка старого города осталась бы и молча считала цены чужого магазина"


def test_same_address_saved_twice_keeps_the_point(db):
    """Повторное сохранение того же адреса — не переезд, точку терять незачем."""
    location.save_address("Москва, Ходынский бульвар 4")
    location.save_point("lenta", "5344", "ТК4537")
    location.save_address("  Москва, Ходынский бульвар 4  ")
    assert location.point("lenta") == ("5344", "ТК4537")


def test_point_wins_over_address_in_the_request(db):
    """Код точки однозначен, адрес сервер разбирает сам — значит код идёт первым."""
    from app.connectors.lenta import _where

    location.save_address("Москва, Ходынский бульвар 4")
    location.save_point("lenta", "5344", "ТК4537")
    where = _where(location.for_store("lenta"))

    assert where["storeId"] == 5344
    assert "address" not in where


def test_address_is_used_while_no_point_is_picked(db):
    """Выбор точки необязателен: пока его нет, спрашиваем по адресу."""
    from app.connectors.lenta import _where

    location.save_address("Москва, Ходынский бульвар 4")
    where = _where(location.for_store("lenta"))

    assert where["address"] == "Москва, Ходынский бульвар 4"
    assert "storeId" not in where


# ---------- доставка до коннектора ----------
def test_saved_address_reaches_the_connector(db, monkeypatch):
    """Адрес из базы должен доехать до магазина сам, без передачи его руками."""
    from app import compare

    seen: dict = {}

    class Silent:
        code = "lenta"

        def search(self, query, limit=3):
            return []

    def fake_get_connector(code, location=None):
        seen[code] = location
        return Silent()

    monkeypatch.setattr("app.connectors.get_connector", fake_get_connector)
    location.save_address("Екатеринбург, улица Щербакова 4")

    compare.compare_query("молоко", store_codes=["lenta"])

    assert isinstance(seen["lenta"], Location)
    assert seen["lenta"].address == "Екатеринбург, улица Щербакова 4"
