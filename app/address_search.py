"""Адресные подсказки с координатами для корзины доставки."""
from __future__ import annotations

import math
import re

import requests

from app import geo


_ROAD = r"улица|проспект|переулок|набережная|бульвар|шоссе|площадь|проезд"
_ABBR = {
    "спб": "санкт-петербург", "мск": "москва", "ул": "улица", "пр-кт": "проспект",
    "пр-т": "проспект", "просп": "проспект", "пер": "переулок", "наб": "набережная",
    "б-р": "бульвар", "пл": "площадь", "ш": "шоссе",
}
_ABBR_RE = re.compile(r"\b(" + "|".join(_ABBR) + r")\b\.?")
_HOUSE = re.compile(r"(?:^|[\s,])(?:д(?:ом)?\.?\s*)?(\d+[а-яa-z]?(?:/\d+[а-я]?)?"
                    r"(?:\s*(?:корпус|корп|к|строение|стр|с|литера|лит)\.?\s*[\dа-я]+)*)\s*$")


def _house(value: str) -> str:
    value = re.sub(r"[\s.]", "", value.lower())
    return re.sub("корпус|корп", "к", re.sub("строение|стр", "с", value)).replace("литера", "лит")


def _words(value: str) -> list[str]:
    return re.findall(r"[а-яa-z0-9]+", value.lower().replace("ё", "е"))


def _query_parts(text: str) -> tuple[str, str, str]:
    match = _HOUSE.search(text)
    house = match[1] if match else ""
    names = (text[:match.start()] if match else text).strip(" ,")
    names = re.sub(r"^г(?:ород)?\.?\s+", "", names)
    city, comma, street = names.partition(",")
    if not comma:
        road_type = re.search(r"\s+(?=(?:" + _ROAD + r")\b)", names)
        if road_type:
            city, street = names[:road_type.start()], names[road_type.end():]
        else:
            city, _, street = names.partition(" ")
    return city.strip(), street.strip(" ,"), house


def _rank(text: str, city: str, street: str, house: str, candidate_house: str) -> tuple | None:
    match = _HOUSE.search(text)
    typed = text[:match.start()] if match else text
    needed = [w for w in _words(typed) if w not in {"г", "город", "россия"}]
    words = _words(city + " " + street)
    if not needed or any(w not in words for w in needed[:-1]):
        return None
    last = needed[-1]
    if last not in words and (house or not any(w.startswith(last) for w in words)):
        return None
    target, offered = _house(house), _house(candidate_house)
    if target and offered != target:
        # У дома без корпуса показываем существующие корпуса с их обозначениями.
        if not (target.isdigit() and re.fullmatch(re.escape(target) + r"(?:к|с|лит)\w+", offered)):
            return None
    return (len(set(words) - set(needed) - set(_ROAD.split("|"))), offered != target, last not in words)


class SearchUnavailable(Exception):
    pass


def suggest(query: str, limit: int = 6) -> list[dict]:
    text = _ABBR_RE.sub(lambda m: _ABBR[m[1]], " ".join(query.lower().split()))
    if not 3 <= len(text) <= 250:
        return []
    city, street, house = _query_parts(text)
    answered = False
    key = geo.token()
    if key:
        try:
            response = requests.post(geo.DADATA_URL, json={"query": text, "count": limit},
                                     headers={"Authorization": f"Token {key}"}, timeout=4)
            response.raise_for_status()
            features = []
            for item in response.json().get("suggestions", []):
                data = item.get("data") or {}
                if not (data.get("geo_lat") and data.get("geo_lon")):
                    continue
                number = " ".join(filter(None, (data.get("house"), data.get("block_type"), data.get("block"))))
                features.append({"properties": {"countrycode": data.get("country_iso_code"),
                                 "city": data.get("settlement") or data.get("city") or data.get("region"),
                                 "street": data.get("street_with_type"), "housenumber": number,
                                 "state": data.get("region_with_type")},
                                 "geometry": {"coordinates": [float(data["geo_lon"]), float(data["geo_lat"])]}})
            items = _matches(features, text, house)
            answered = True
            if items:
                return items[:limit]
        except (requests.RequestException, ValueError, TypeError):
            pass
    attempts = []
    if city and street and not re.match(r"(?:" + _ROAD + r")\b", city):
        attempts.append((geo.PHOTON_URL.removesuffix("api/") + "structured",
                         {"city": city, "street": street, "housenumber": _house(house)}))
    attempts.append((geo.PHOTON_URL, {"q": text}))
    for url, params in attempts:
        try:
            response = requests.get(url, params={**params, "countrycode": "ru", "limit": 24},
                                    headers={"User-Agent": geo.PHOTON_UA, "Accept": "application/json"},
                                    timeout=4)
            response.raise_for_status()
            items = _matches(response.json().get("features", []), text, house)
            answered = True
        except (requests.RequestException, ValueError, TypeError):
            continue
        if items:
            return items[:limit]
    if not answered:
        raise SearchUnavailable
    return []


def _matches(features: list, text: str, house: str) -> list[dict]:
    ranked = []
    seen = set()
    for feature in features:
        p = feature.get("properties") or {}
        xy = (feature.get("geometry") or {}).get("coordinates") or []
        if str(p.get("countrycode")).upper() != "RU" or len(xy) != 2:
            continue
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in xy):
            continue
        city = p.get("city") or p.get("town") or p.get("village") or p.get("state") or ""
        street = p.get("street") or p.get("name") or ""
        number = p.get("housenumber") or ""
        rank = _rank(text, city, street, house, number)
        if rank is None:
            continue
        label = ", ".join(dict.fromkeys(filter(None, (city, street, number))))
        context = ", ".join(dict.fromkeys(x for x in (p.get("state"), p.get("district")) if x and x != city))
        key = (label.casefold(), context.casefold())
        if key not in seen:
            seen.add(key)
            ranked.append((rank, {"label": label, "full": context or label, "lat": xy[1], "lon": xy[0]}))
    ranked.sort(key=lambda item: item[0])
    return [item for _, item in ranked]
