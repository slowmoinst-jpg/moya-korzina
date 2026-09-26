"""Проверка подбора точек и коннектора Самоката."""
from __future__ import annotations

import pytest

from app import config, geo
from app.connectors import samokat


def test_nearest_store_empty_address():
    assert samokat.nearest_store("") is None
    assert samokat.nearest_store("   ") is None


def test_distance_m():
    d = samokat._distance_m(55.7539, 37.6208, 59.9398, 30.3146)
    assert 630_000 < d < 640_000


def test_nearest_store_with_coords(monkeypatch):
    monkeypatch.setattr(geo, "coords", lambda addr: (59.93, 30.31))
    fake_store = {"code": "samokat-spb-1", "address": "СПб, Тестовая 10", "distance": 200.0}
    monkeypatch.setattr(samokat, "stores_near", lambda lat, lon, radius_km=10.0: [fake_store])

    found = samokat.nearest_store("СПб, Тестовая 10")
    assert found == fake_store


def test_nearest_store_fallback_config(monkeypatch):
    monkeypatch.setattr(geo, "coords", lambda addr: None)
    monkeypatch.setattr(config, "get", lambda key, default=None: (
        "samokat-spare-7" if key == "connectors.samokat_store_id" else default
    ))
    found = samokat.nearest_store("Неизвестный адрес")
    assert found is not None
    assert found["code"] == "samokat-spare-7"


def test_nearest_store_none_when_unresolved(monkeypatch):
    monkeypatch.setattr(geo, "coords", lambda addr: None)
    monkeypatch.setattr(config, "get", lambda key, default=None: None)
    assert samokat.nearest_store("Неизвестный адрес") is None
