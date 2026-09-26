"""Проверка подбора точек и коннектора Пятёрочки."""
from __future__ import annotations

import pytest

from app import config, geo
from app.connectors import pyaterochka


def test_nearest_store_empty_address():
    assert pyaterochka.nearest_store("") is None
    assert pyaterochka.nearest_store("   ") is None


def test_distance_m():
    # Москва (Красная площадь) -> СПб (Дворцовая площадь) ~ 634 км
    d = pyaterochka._distance_m(55.7539, 37.6208, 59.9398, 30.3146)
    assert 630_000 < d < 640_000


def test_nearest_store_with_coords(monkeypatch):
    monkeypatch.setattr(geo, "coords", lambda addr: (55.75, 37.61))
    fake_store = {"code": "5ka-999", "address": "Москва, Тестовая 1", "distance": 150.0}
    monkeypatch.setattr(pyaterochka, "stores_near", lambda lat, lon, radius_km=5.0: [fake_store])

    found = pyaterochka.nearest_store("Москва, Тестовая 1")
    assert found == fake_store


def test_nearest_store_fallback_config(monkeypatch):
    monkeypatch.setattr(geo, "coords", lambda addr: None)
    monkeypatch.setattr(config, "get", lambda key, default=None: (
        "5ka-spare-42" if key == "connectors.pyaterochka_store_id" else default
    ))
    found = pyaterochka.nearest_store("Неизвестный адрес")
    assert found is not None
    assert found["code"] == "5ka-spare-42"


def test_nearest_store_none_when_unresolved(monkeypatch):
    monkeypatch.setattr(geo, "coords", lambda addr: None)
    monkeypatch.setattr(config, "get", lambda key, default=None: None)
    assert pyaterochka.nearest_store("Неизвестный адрес") is None
