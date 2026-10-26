"""Тесты для двери /api/device/board (Этап 0).

Дверь отдаёт состояние автосборки по всем подключённым сетям для экрана «Настройки» на телефоне:
подключена ли, дорога, сколько легло, взят ли итог сети, чем мешает.
"""
from __future__ import annotations

import pytest

from app import api, users
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


def test_device_board_requires_authentication(door):
    application, _ = door
    with application.test_client() as client:
        res = client.post("/api/device/board", json={})
        assert res.status_code in (401, 403)
        data = res.get_json()
        assert data["ok"] is False


def test_device_board_returns_all_chains_state(door):
    application, secret = door
    with application.test_client() as client:
        res = client.post("/api/device/board", json=signed(secret))
        assert res.status_code == 200, res.data
        data = res.get_json()
        assert data["ok"] is True
        assert "board" in data
        assert isinstance(data["board"], list)
        assert len(data["board"]) >= 11

        codes = {r["code"] for r in data["board"]}
        for required in ("pyaterochka", "magnit", "dixy", "lenta", "samokat", "vprok", "monetka"):
            assert required in codes, f"Сеть {required} должна быть в board"

        for row in data["board"]:
            assert "code" in row
            assert "name" in row
            assert row["name"] != row["code"], "название сети — из справочника, а не её код"
            assert "act" in row
            assert "connected" in row
            assert "store_choice" in row
            assert "road" in row
            assert "state" in row
            assert "planned" in row
            assert "landed" in row
            assert "network_total" in row
            assert "ready" in row
            assert "next" in row
