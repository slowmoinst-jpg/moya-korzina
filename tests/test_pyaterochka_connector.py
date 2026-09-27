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


def test_a_store_without_coordinates_is_not_the_nearest(monkeypatch):
    """Магазин без координат — расстояние неизвестно, а не ноль.

    Раньше вместо его координат подставлялась точка запроса, расстояние выходило
    нулевым, и такой магазин становился «ближайшим».
    """
    class Reply:
        status_code = 200
        headers = {"Content-Type": "application/json"}

        @staticmethod
        def json():
            return [{"sap_code": "far", "address": "без координат"},
                    {"sap_code": "near", "address": "рядом", "lat": 55.7501, "lon": 37.6101}]

    from app import homeexit

    monkeypatch.setattr(homeexit, "requests_proxies", lambda chain: None)
    monkeypatch.setattr(pyaterochka.requests, "get", lambda *a, **k: Reply())
    found = pyaterochka._fetch_api_stores(55.75, 37.61, 3.0)
    by_code = {s["code"]: s for s in found}
    assert by_code["far"]["distance"] == float("inf")
    assert by_code["near"]["distance"] < 100
