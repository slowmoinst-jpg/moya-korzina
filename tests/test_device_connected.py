"""Тесты для двери /api/device/connected (Концепция 2.0).

Регистрация факта входа сети с телефона без передачи секретов на сервер.
Токены и куки остаются зашифрованными в KeyStore на устройстве.
"""
from __future__ import annotations

import pytest

from app import api, store_accounts, users
from app.web import create_app

PHONE = "79991112233"


@pytest.fixture
def door(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.open_workspace(PHONE)
    secret = api.workplace_secret()
    users.deactivate()

    application = create_app()
    application.config.update(TESTING=True)
    yield application, secret
    users.deactivate()


def signed(secret: str, **kw) -> dict:
    return {"workplace": PHONE, "secret": secret, **kw}


def test_device_connected_registers_store_without_secrets(door):
    application, secret = door
    with application.test_client() as client:
        res = client.post("/api/device/connected", json=signed(
            secret,
            store="dixy",
            connected=True,
            account="+7 999 111-22-33",
            points=120.0,
            gives=["prices", "cart"],
        ))
        assert res.status_code == 200, res.data
        data = res.get_json()
        assert data["ok"] is True
        assert data["store"] == "dixy"
        assert data["connected"] is True
        assert data["points"] == 120.0
        assert "cart" in data["gives"]

    # Проверяем в store_accounts под рабочим местом
    users.open_workspace(PHONE)
    try:
        conn = store_accounts.connection("dixy")
        assert conn.connected is True
        assert conn.points == 120.0
        assert "cart" in conn.gives
        assert store_accounts.can("dixy", "cart") is True
    finally:
        users.deactivate()


def test_device_connected_rejects_unknown_store(door):
    application, secret = door
    with application.test_client() as client:
        res = client.post("/api/device/connected", json=signed(
            secret,
            store="unknown_chain",
            connected=True,
        ))
        assert res.status_code == 200
        data = res.get_json()
        assert data["ok"] is False
        assert "неизвестная сеть" in data["why"]
