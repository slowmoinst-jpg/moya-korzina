"""Коннектор и подбор точек Пятёрочки.

Магазин подбирается к адресу рабочего места: адрес переводится в точку на карте
(app/geo.py, coords), и в радиусе ищется ближайшая Пятёрочка.

Если домашний выход включён и API сети доступен, справочник запрашивается у сети.
В обычном режиме и при блокировке защита ServicePipe отдаёт 403 — тогда ближайшие
магазины сети подбираются по координатам через OpenStreetMap (Nominatim).
Запасной код магазина можно задать в config.yaml (connectors.pyaterochka_store_id).
"""
from __future__ import annotations

import logging
import math
from typing import Any

import requests

from app import config, geo, homeexit
from app.connectors.base import USER_AGENT
from app.connectors.cache import cached_call
from app.connectors.history import PyaterochkaConnector

log = logging.getLogger(__name__)

SEARCH_RADIUS_KM = 5.0
STORES_API_URL = "https://5d.5ka.ru/api/orders/v1/orders/stores/"
OSM_URL = "https://nominatim.openstreetmap.org/search"


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние по поверхности Земли в метрах (гаверсинус)."""
    radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def _fetch_api_stores(lat: float, lon: float, radius_km: float) -> list[dict] | None:
    """Попытка запросить магазины через официальный шлюз/API (через домашний выход)."""
    proxies = homeexit.requests_proxies("pyaterochka")
    timeout = float(config.get("connectors.timeout_sec", 10) or 10)
    try:
        resp = requests.get(
            STORES_API_URL,
            params={"lat": lat, "lon": lon, "radius": int(radius_km * 1000)},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            proxies=proxies,
            timeout=timeout,
        )
        if resp.status_code == 200 and "json" in (resp.headers.get("Content-Type") or ""):
            data = resp.json()
            items = data if isinstance(data, list) else (data.get("data") or data.get("stores") or [])
            out = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("sap_code") or item.get("id") or item.get("code") or "")
                addr = str(item.get("address") or item.get("name") or "").strip()
                if not code:
                    continue
                slat = float(item.get("lat") or item.get("latitude") or lat)
                slon = float(item.get("lon") or item.get("longitude") or lon)
                out.append({
                    "code": code,
                    "address": addr,
                    "distance": _distance_m(lat, lon, slat, slon),
                })
            if out:
                return out
    except Exception as exc:  # noqa: BLE001
        log.debug("пятёрочка: запрос к API магазинов не удался (%s)", exc)
    return None


def _fetch_osm_stores(lat: float, lon: float, radius_km: float) -> list[dict]:
    """Поиск магазинов Пятёрочки вокруг координат через OSM."""
    dlat = radius_km / 111.0
    dlon = radius_km / max(0.01, 111.0 * math.cos(math.radians(lat)))
    viewbox = f"{lon - dlon},{lat + dlat},{lon + dlon},{lat - dlat}"
    timeout = float(config.get("connectors.timeout_sec", 10) or 10)
    try:
        resp = requests.get(
            OSM_URL,
            params={
                "q": "Пятёрочка",
                "format": "jsonv2",
                "viewbox": viewbox,
                "bounded": 1,
                "limit": 10,
                "accept-language": "ru",
                "countrycodes": "ru",
            },
            headers={"User-Agent": "moya-korzina/1.0 (pyaterochka-stores)", "Accept": "application/json"},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return []
        items = resp.json() or []
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            osm_id = item.get("osm_id")
            if not osm_id:
                continue
            try:
                slat = float(item["lat"])
                slon = float(item["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            raw_addr = item.get("display_name") or ""
            clean_addr = geo.tidy(raw_addr) or raw_addr
            out.append({
                "code": str(osm_id),
                "address": clean_addr,
                "distance": _distance_m(lat, lon, slat, slon),
            })
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("пятёрочка: поиск магазинов в OSM не ответил (%s)", exc)
        return []


def stores_near(lat: float, lon: float, radius_km: float = SEARCH_RADIUS_KM,
                limit: int = 10) -> list[dict]:
    """Магазины Пятёрочки вокруг точки: code, address, distance (метры)."""
    key = f"stores:{lat:.4f},{lon:.4f}:{radius_km}"

    def ask() -> list[dict]:
        # Сначала пробуем официальный канал (если поднят домашний выход)
        found = _fetch_api_stores(lat, lon, radius_km)
        if found:
            return found
        # Запасной стабильный путь — геосправочник OpenStreetMap
        return _fetch_osm_stores(lat, lon, radius_km)

    raw = cached_call("pyaterochka", key, ask) or []
    sorted_stores = sorted(raw, key=lambda s: s.get("distance", float("inf")))
    return sorted_stores[:limit]


def nearest_store(address: str) -> dict | None:
    """Магазин Пятёрочки, ближайший к адресу. None — не подобран."""
    if not address or not address.strip():
        return None
    point = geo.coords(address)
    if point:
        found = stores_near(*point)
        if found:
            return found[0]
        log.info("пятёрочка: рядом с адресом «%s» магазинов не нашлось", address)
    else:
        log.info("пятёрочка: адрес «%s» не переведён в точку — магазин не подобран", address)

    spare = config.get("connectors.pyaterochka_store_id")
    if spare:
        return {"code": str(spare), "address": address, "distance": 0.0}
    return None


__all__ = ["nearest_store", "stores_near", "PyaterochkaConnector"]
