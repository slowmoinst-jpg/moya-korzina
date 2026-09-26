"""Коннектор и подбор точек Самоката.

Даркстор (витрина) подбирается к адресу рабочего места: адрес переводится в точку на карте
(app/geo.py, coords), и в радиусе ищется ближайшая точка Самоката.

Если домашний выход включён и API сети доступен, точка может запрашиваться у шлюза сети.
В обычном режиме и при защите ServicePipe ближайшие дарксторы сети подбираются
по координатам через OpenStreetMap (Nominatim).
Запасной код точки можно задать в config.yaml (connectors.samokat_store_id).
"""
from __future__ import annotations

import logging
import math
from typing import Any

import requests

from app import config, geo, homeexit
from app.connectors.base import USER_AGENT
from app.connectors.cache import cached_call
from app.connectors.history import SamokatConnector

log = logging.getLogger(__name__)

SEARCH_RADIUS_KM = 10.0
OSM_URL = "https://nominatim.openstreetmap.org/search"
SAMOKAT_API_URL = "https://api-web.samokat.ru/v1/addresses/lookup"


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние по поверхности Земли в метрах (гаверсинус)."""
    radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def _fetch_api_stores(lat: float, lon: float, radius_km: float) -> list[dict] | None:
    """Попытка запросить даркстор/витрину через API Самоката (через домашний выход)."""
    proxies = homeexit.requests_proxies("samokat")
    timeout = float(config.get("connectors.timeout_sec", 10) or 10)
    try:
        resp = requests.post(
            SAMOKAT_API_URL,
            json={"location": {"lat": lat, "lon": lon}},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            proxies=proxies,
            timeout=timeout,
        )
        if resp.status_code == 200 and "json" in (resp.headers.get("Content-Type") or ""):
            data = resp.json() or {}
            showcase_id = data.get("showcaseId") or data.get("showcase_id") or data.get("id")
            if showcase_id:
                return [{
                    "code": str(showcase_id),
                    "address": str(data.get("address") or f"Самокат (витрина {showcase_id})"),
                    "distance": 0.0,
                }]
    except Exception as exc:  # noqa: BLE001
        log.debug("самокат: запрос к API lookup не удался (%s)", exc)
    return None


def _fetch_osm_stores(lat: float, lon: float, radius_km: float) -> list[dict]:
    """Поиск дарксторов Самоката вокруг координат через OSM."""
    dlat = radius_km / 111.0
    dlon = radius_km / max(0.01, 111.0 * math.cos(math.radians(lat)))
    viewbox = f"{lon - dlon},{lat + dlat},{lon + dlon},{lat - dlat}"
    timeout = float(config.get("connectors.timeout_sec", 10) or 10)
    try:
        resp = requests.get(
            OSM_URL,
            params={
                "q": "Самокат",
                "format": "jsonv2",
                "viewbox": viewbox,
                "bounded": 1,
                "limit": 10,
                "accept-language": "ru",
                "countrycodes": "ru",
            },
            headers={"User-Agent": "moya-korzina/1.0 (samokat-stores)", "Accept": "application/json"},
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
        log.warning("самокат: поиск дарксторов в OSM не ответил (%s)", exc)
        return []


def stores_near(lat: float, lon: float, radius_km: float = SEARCH_RADIUS_KM,
                limit: int = 10) -> list[dict]:
    """Точки (дарксторы) Самоката вокруг точки: code, address, distance (метры)."""
    key = f"stores:{lat:.4f},{lon:.4f}:{radius_km}"

    def ask() -> list[dict]:
        # Сначала пробуем официальный канал (если поднят домашний выход)
        found = _fetch_api_stores(lat, lon, radius_km)
        if found:
            return found
        # Запасной стабильный путь — геосправочник OpenStreetMap
        return _fetch_osm_stores(lat, lon, radius_km)

    raw = cached_call("samokat", key, ask) or []
    sorted_stores = sorted(raw, key=lambda s: s.get("distance", float("inf")))
    return sorted_stores[:limit]


def nearest_store(address: str) -> dict | None:
    """Точка Самоката, ближайшая к адресу. None — не подобрана."""
    if not address or not address.strip():
        return None
    point = geo.coords(address)
    if point:
        found = stores_near(*point)
        if found:
            return found[0]
        log.info("самокат: рядом с адресом «%s» точек не нашлось", address)
    else:
        log.info("самокат: адрес «%s» не переведён в точку — магазин не подобран", address)

    spare = config.get("connectors.samokat_store_id")
    if spare:
        return {"code": str(spare), "address": address, "distance": 0.0}
    return None


__all__ = ["nearest_store", "stores_near", "SamokatConnector"]
