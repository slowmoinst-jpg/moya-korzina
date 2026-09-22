"""Подсказки адреса: человек печатает «ленинский 42», а выбирает из готовых строк.

Зачем. Адрес — единственное, что задаёт цены и доставку, и вводится он руками.
Опечатка здесь не видна: сеть просто не поймёт адрес, цены придут пустыми, и
человек решит, что магазин не работает. Подсказка убирает эту ошибку до расчёта.

Источников два, и они сменяют друг друга по наличию ключа.

DaData — то, чем в России подсказывают адреса все доставки: отвечает быстро,
понимает сокращения («лен просп 42») и отдаёт адрес в привычном виде. Нужен
бесплатный ключ, поэтому включается, только если он задан: переменной окружения
DADATA_TOKEN или полем geo.dadata_token в config.yaml.

Nominatim (OpenStreetMap) — запасной путь без ключа и без регистрации, чтобы
подсказки работали сразу после установки. Он строже к вводу и отвечает медленнее,
зато ничего не требует. Правила OSM просят подписываться в User-Agent и не чаще
запроса в секунду — и то, и другое соблюдается ниже.

Пусто в ответе — это не ошибка, а обычный случай: человек ввёл полтора слова или
адрес такой, какого нет. Наружу ошибки не бросаем: подсказка — помощь, а не шаг,
без которого нельзя сохранить адрес.

Вторая работа модуля — перевод адреса в точку на карте (coords ниже). Она здесь по
той же причине, по которой здесь подсказки: строку в координаты переводит тот же
сервис, который эту строку и предложил, и расхождения между показанным адресом и
найденным местом не возникает.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any

import requests

from app import config

log = logging.getLogger(__name__)

DADATA_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "moya-korzina/1.0 (podskazki adresa)"

MIN_QUERY = 3          # на двух буквах подсказка бессмысленна, а запрос уже уходит
CACHE_TTL_SEC = 600    # адреса не меняются; десяти минут хватает и рукам, и серверу

# Части ответа OSM, которые человеку не нужны: страна, индекс, федеральный округ.
_DROP_PARTS = re.compile(r"^\s*(Россия|Russia|\d{6}|.*федеральн\w+ округ.*)\s*$", re.IGNORECASE)

_cache: dict[str, tuple[float, list[str]]] = {}
_lock = threading.Lock()
_last_call = 0.0


def token() -> str | None:
    """Ключ DaData: сначала окружение, потом config.yaml. Пусто — работаем без него."""
    value = os.getenv("DADATA_TOKEN") or config.get("geo.dadata_token")
    value = (value or "").strip()
    return value or None


def suggest(query: str, limit: int = 5) -> list[str]:
    """Варианты адреса для строки поиска. Пустой список — вариантов нет."""
    text = " ".join((query or "").split())
    if len(text) < MIN_QUERY:
        return []

    key = f"{text.lower()}|{limit}"
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_SEC:
            return list(hit[1])

    api_key = token()
    try:
        found = _dadata(text, limit, api_key) if api_key else _nominatim(text, limit)
    except Exception as exc:  # noqa: BLE001 — подсказка не повод ронять экран
        log.warning("подсказки адреса не пришли (%s)", exc)
        found = []

    with _lock:
        _cache[key] = (now, list(found))
    return found


def _timeout() -> float:
    return float(config.get("connectors.timeout_sec", 10) or 10)


def _throttle() -> None:
    """Не чаще запроса в секунду: этого требуют правила Nominatim."""
    global _last_call
    with _lock:
        wait = 1.0 - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def _dadata(text: str, limit: int, api_key: str) -> list[str]:
    response = requests.post(
        DADATA_URL,
        json={"query": text, "count": max(1, min(limit, 20))},
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json",
                 "Accept": "application/json"},
        timeout=_timeout(),
    )
    response.raise_for_status()
    payload = response.json() or {}
    out = []
    for item in payload.get("suggestions") or []:
        value = (item or {}).get("value")
        if value and value not in out:
            out.append(value)
    return out[:limit]


def _nominatim(text: str, limit: int) -> list[str]:
    _throttle()
    response = requests.get(
        NOMINATIM_URL,
        # addressdetails нужен не для красоты: без него ответ начинается с названия
        # здания («Legend City, 4, Ходынский бульвар…»), и в списке пять строк
        # про один и тот же дом
        params={"q": text, "format": "jsonv2", "limit": max(1, min(limit * 3, 30)),
                "accept-language": "ru", "countrycodes": "ru", "addressdetails": 1},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=_timeout(),
    )
    response.raise_for_status()
    out = []
    for item in response.json() or []:
        value = compose((item or {}).get("address") or {}) or tidy((item or {}).get("display_name") or "")
        if value and value not in out:
            out.append(value)
    return out[:limit]


def compose(address: dict) -> str:
    """Адрес из частей ответа OSM: «Москва, Ходынский бульвар, 4».

    Порядок привычный русскому уху — город, улица, дом, — а не как в ответе, где
    первым идёт дом. Название заведения отбрасывается: человек ищет, куда везти,
    а не куда сходить.
    """
    city = (address.get("city") or address.get("town") or address.get("village")
            or address.get("municipality") or address.get("state"))
    road = address.get("road") or address.get("pedestrian") or address.get("neighbourhood")
    house = address.get("house_number")
    parts = [p for p in (city, road, house) if p]
    return ", ".join(parts)


def tidy(display_name: str) -> str:
    """«…, 119334, Москва, Центральный федеральный округ, Россия» → «…, Москва».

    OSM отдаёт адрес со страной, индексом и округом. Человеку они не помогают
    выбрать строку, а длина мешает: в списке видно только начало.
    """
    parts = [p.strip() for p in (display_name or "").split(",")]
    kept = [p for p in parts if p and not _DROP_PARTS.match(p)]
    return ", ".join(kept)


# ---------- координаты ----------
_coords_cache: dict[str, tuple[float, tuple[float, float] | None]] = {}


def coords(address: str) -> tuple[float, float] | None:
    """Точка на карте для адреса: (широта, долгота). None — адрес не разобран.

    Зачем это нужно. Сети спрашивают место по-разному. Ленте довольно строки: она
    разберёт её сама и назовёт хаб доставки. Магнит строк не понимает вовсе — его
    справочник магазинов ищет точки в прямоугольнике на карте (app/connectors/
    magnit.py, stores_near). Значит между строкой, которую ввёл человек, и ценами
    Магнита обязан встать перевод адреса в точку.

    Ответ держим в памяти процесса: адрес меняется куда реже, чем считается корзина,
    а Nominatim к тому же просит ходить не чаще запроса в секунду. Ненайденный адрес
    кэшируется тоже — иначе каждая опечатка стоила бы запроса в сеть на каждый расчёт.
    """
    text = " ".join((address or "").split())
    if len(text) < MIN_QUERY:
        return None

    key = text.lower()
    now = time.time()
    with _lock:
        hit = _coords_cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_SEC:
            return hit[1]

    api_key = token()
    try:
        point = _dadata_point(text, api_key) if api_key else _nominatim_point(text)
    except Exception as exc:  # noqa: BLE001 — не нашли точку, значит цены возьмутся запасные
        log.warning("координаты адреса «%s» не получены (%s)", text, exc)
        point = None

    with _lock:
        _coords_cache[key] = (now, point)
    return point


def _point(lat: Any, lon: Any) -> tuple[float, float] | None:
    """Пара чисел из ответа сервиса; всё сомнительное отсеивается здесь.

    Отсеивать есть что: DaData отдаёт широту и долготу СТРОКАМИ, у ненайденного
    адреса ставит их пустыми, а у города без координат — нулями. Ноль-ноль это
    точка в Гвинейском заливе: справочник магазинов по ней честно ответит пустотой,
    и понять, что дело в адресе, а не в сети, будет неоткуда.
    """
    try:
        latitude = float(str(lat).replace(",", "."))
        longitude = float(str(lon).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= latitude <= 90.0) or not (-180.0 <= longitude <= 180.0):
        return None
    if latitude == 0.0 and longitude == 0.0:
        return None
    return latitude, longitude


def _dadata_point(text: str, api_key: str) -> tuple[float, float] | None:
    response = requests.post(
        DADATA_URL,
        json={"query": text, "count": 1},
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json",
                 "Accept": "application/json"},
        timeout=_timeout(),
    )
    response.raise_for_status()
    for item in (response.json() or {}).get("suggestions") or []:
        data = (item or {}).get("data") or {}
        point = _point(data.get("geo_lat"), data.get("geo_lon"))
        if point:
            return point
    return None


def _nominatim_point(text: str) -> tuple[float, float] | None:
    _throttle()
    response = requests.get(
        NOMINATIM_URL,
        params={"q": text, "format": "jsonv2", "limit": 1,
                "accept-language": "ru", "countrycodes": "ru"},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=_timeout(),
    )
    response.raise_for_status()
    for item in response.json() or []:
        point = _point((item or {}).get("lat"), (item or {}).get("lon"))
        if point:
            return point
    return None
