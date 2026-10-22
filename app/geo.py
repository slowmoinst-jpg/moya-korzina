"""Подсказки адреса: человек печатает «ленинский 42», а выбирает из готовых строк.

Зачем. Адрес — единственное, что задаёт цены и доставку, и вводится он руками.
Опечатка здесь не видна: сеть просто не поймёт адрес, цены придут пустыми, и
человек решит, что магазин не работает. Подсказка убирает эту ошибку до расчёта.

Источников три, и они сменяют друг друга по наличию ключа и по тому, кто спросил.

DaData — то, чем в России подсказывают адреса все доставки: отвечает быстро,
понимает сокращения («лен просп 42») и отдаёт адрес в привычном виде. Нужен
бесплатный ключ, поэтому включается, только если он задан: переменной окружения
DADATA_TOKEN или полем geo.dadata_token в config.yaml.

Photon (photon.komoot.io) — бесплатный подсказчик на данных OpenStreetMap: без
ключа, без регистрации и без ограничения «раз в секунду», поэтому он первый, кому
достаётся набор с телефона. Проверено 21.10.2026: с браузерным User-Agent отдаёт
200 и русские названия, а параметр lang=ru не принимает вовсе (400) — поэтому
строки подписываются как браузер, а lang не передаётся.

Nominatim (OpenStreetMap) — запасной путь без ключа и без регистрации, чтобы
подсказки работали сразу после установки. Он строже к вводу и отвечает медленнее,
зато ничего не требует. Правила OSM просят подписываться в User-Agent и не чаще
запроса в секунду — и то, и другое соблюдается ниже. На телефоне Nominatim
спрашивается только по явному нажатию «Найти»: набор с задержкой упирался бы в
его троттлинг.

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
from collections.abc import Callable
from typing import Any

import requests

from app import config

log = logging.getLogger(__name__)

DADATA_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
PHOTON_URL = "https://photon.komoot.io/api/"
PHOTON_REVERSE_URL = "https://photon.komoot.io/reverse"
USER_AGENT = "moya-korzina/1.0 (podskazki adresa)"

# Photon с браузерным User-Agent отвечает 200 и русскими названиями (проверено
# 21.10.2026). lang=ru он не принимает вовсе — отвечает 400, поэтому lang не
# передаём. Остальное — как у браузера: без подписи служба может не ответить.
PHOTON_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")

MIN_QUERY = 3          # на двух буквах подсказка бессмысленна, а запрос уже уходит
CACHE_TTL_SEC = 600    # адреса не меняются; десяти минут хватает и рукам, и серверу
# Полный отказ сети (не ответил НИ ОДИН провайдер) помним куда короче, чем пустой
# ОТВЕТ службы: 60–120 секунд хватает, чтобы человек не ждал по десять секунд на
# каждую букву, и чтобы после возврата связи подсказки не молчали десять минут.
NEGATIVE_TTL_SEC = 90

# Части ответа OSM, которые человеку не нужны: страна, индекс, федеральный округ.
_DROP_PARTS = re.compile(r"^\s*(Россия|Russia|\d{6}|.*федеральн\w+ округ.*)\s*$", re.IGNORECASE)

_cache: dict[str, tuple[float, list[str]]] = {}
_lock = threading.Lock()
_last_call = 0.0
_down_until = 0.0      # до какого времени не спрашивать провайдеров после полного сбоя


def _recently_down() -> bool:
    """Все провайдеры недавно упали — не ходим к ним снова до конца короткой паузы."""
    with _lock:
        return _down_until > time.time()


def _note_down(where: str) -> None:
    """Запомнить полный отказ сети на NEGATIVE_TTL_SEC. Значений адреса в лог не пишем."""
    global _down_until
    now = time.time()
    with _lock:
        if _down_until > now:
            return
        _down_until = now + NEGATIVE_TTL_SEC
    log.warning("%s: службы не ответили — не спрашиваю %d с", where, NEGATIVE_TTL_SEC)


def token() -> str | None:
    """Ключ DaData: сначала окружение, потом config.yaml. Пусто — работаем без него."""
    value = os.getenv("DADATA_TOKEN") or config.get("geo.dadata_token")
    value = (value or "").strip()
    return value or None


def suggest(query: str, limit: int = 5) -> list[str]:
    """Варианты адреса для веб-подсказчика. Пустой список — вариантов нет.

    Это исторический путь веба, и он сохранён как был: с ключом отвечает DaData,
    без ключа — Nominatim. Photon подхватывает только пустой ответ DaData. Телефону
    такой порядок не подходит — там Photon идёт первым, а Nominatim спрашивается
    лишь по кнопке; см. suggest_phone.
    """
    text = " ".join((query or "").split())
    if len(text) < MIN_QUERY:
        return []

    key = f"{text.lower()}|{limit}"
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_SEC:
            return list(hit[1])
    if _recently_down():
        return []

    api_key = token()
    answered = False
    try:
        if api_key:
            # Порядок для веба остался прежним: ключ есть — отвечает DaData, а
            # Photon и Nominatim подхватывают, только если она промолчала. Без
            # ключа веб по-прежнему спрашивает Nominatim: это его историческое
            # поведение, и менять его здесь незачем — у телефона своя цепочка
            # (suggest_phone), где Photon идёт первым.
            found, answered = _first_answer([
                ("DaData", lambda: _dadata(text, limit, api_key)),
                ("Photon", lambda: _photon(text, limit)),
                ("Nominatim", lambda: _nominatim(text, limit)),
            ])
        else:
            found = _nominatim(text, limit)
            answered = True
    except Exception as exc:  # noqa: BLE001 — подсказка не повод ронять экран
        log.warning("подсказки адреса не пришли")
        log.debug("подсказки адреса не пришли (%s)", exc)
        found = []

    # Пустой ответ службы — обычный случай, и его можно помнить. Полный отказ
    # сети — нет: иначе десятиминутный кэш держал бы «ничего не нашлось» даже
    # после того, как служба снова отвечает. Но помним сам факт отказа коротко
    # (_note_down), чтобы следующий ввод не ждал те же десять секунд.
    if answered:
        with _lock:
            _cache[key] = (now, list(found))
    else:
        _note_down("подсказки адреса")
    return found


def suggest_phone(query: str, limit: int = 5, deep: bool = False) -> list[str]:
    """Подсказки для телефонного экрана: DaData → Photon, Nominatim — по кнопке.

    Чем отличается от suggest. Веб без ключа исторически спрашивает Nominatim —
    это закреплено его поведением и тестами. Телефону Nominatim нужен только по
    явному нажатию «Найти»: он отвечает медленнее и просит не чаще запроса в
    секунду, поэтому подсказки с задержкой 400–600 мс упирались бы в его
    троттлинг. Обычный набор идёт в DaData (если есть ключ) и Photon, а deep=True
    добавляет Nominatim последним — когда первые два ничего не нашли.

    Пустой список — обычный ответ: человек ввёл полтора слова или такого адреса
    нет. Наружу ошибки не бросаем по той же причине, что и в suggest.
    """
    text = " ".join((query or "").split())
    if len(text) < MIN_QUERY:
        return []

    key = f"phone|{'deep' if deep else 'type'}|{text.lower()}|{limit}"
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_SEC:
            return list(hit[1])
    if _recently_down():
        return []

    api_key = token()
    providers: list[tuple[str, Callable[[], list[str]]]] = []
    if api_key:
        providers.append(("DaData", lambda: _dadata(text, limit, api_key)))
    providers.append(("Photon", lambda: _photon(text, limit)))
    if deep:
        providers.append(("Nominatim", lambda: _nominatim(text, limit)))
    found, answered = _first_answer(providers)

    # Как и в suggest: помним только ответ службы, а не её молчание. Иначе
    # упавшая Photon на десять минут «доказывала» бы, что адреса не существует.
    # Полный отказ запоминаем коротко — чтобы не ждать сеть на каждой букве.
    if answered:
        with _lock:
            _cache[key] = (now, list(found))
    else:
        _note_down("подсказки адреса")
    return found


def _first_answer(providers: list[tuple[str, Callable[[], list[str]]]]) -> tuple[list[str], bool]:
    """Первый провайдер, у которого есть варианты, и признак «кто-то ответил».

    Отказ одного — не отказ всех. Именно поэтому провайдеры перебираются здесь,
    а не в одном try: упавшая Photon не должна уносить с собой шанс ответить у
    Nominatim. Второй элемент результата отделяет пустой ОТВЕТ службы (его можно
    кэшировать) от полного отказа, когда не ответил никто (его кэшировать нельзя).
    """
    answered = False
    for name, call in providers:
        try:
            found = call()
        except Exception as exc:  # noqa: BLE001 — молчание службы не повод ронять экран
            # Значения адреса и текст сетевой ошибки (в нём бывает URL с запросом)
            # остаются в DEBUG: в рабочем логе хватает имени провайдера.
            log.warning("подсказки адреса: %s не ответил", name)
            log.debug("подсказки адреса: %s не ответил (%s)", name, exc)
            continue
        answered = True
        if found:
            log.debug("подсказки адреса: %s дал %d вариант(ов)", name, len(found))
            return found, True
        log.debug("подсказки адреса: %s ничего не нашёл", name)
    return [], answered


def _photon(text: str, limit: int) -> list[str]:
    """Подсказки Photon: GeoJSON, из которого нам нужны только properties.

    lang не передаётся намеренно: Photon отвечает на него 400 (см. PHOTON_UA).
    """
    response = requests.get(
        PHOTON_URL,
        params={"q": text, "limit": max(1, min(limit * 3, 20))},
        headers={"User-Agent": PHOTON_UA, "Accept": "application/json"},
        timeout=_timeout(),
    )
    response.raise_for_status()
    payload = response.json() or {}
    out: list[str] = []
    for feature in payload.get("features") or []:
        value = _photon_address((feature or {}).get("properties") or {})
        if value and value not in out:
            out.append(value)
    return out[:limit]


def _photon_address(properties: dict) -> str:
    """«Город, улица, дом» из properties Photon.

    У Photon свои имена полей (street, housenumber), а у compose — имена ответа
    Nominatim (road, house_number), поэтому их и переводим. Название заведения
    берём, только когда улицы нет вовсе: человеку нужен адрес, а не вывеска.
    """
    city = (properties.get("city") or properties.get("town")
            or properties.get("village") or properties.get("state"))
    street = properties.get("street")
    house = properties.get("housenumber")
    value = compose({"city": city, "road": street, "house_number": house})
    if not value:
        # У города и района улицы в ответе нет — остаётся имя места.
        value = tidy(str(properties.get("name") or ""))
    return value


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
    if _recently_down():
        return None

    api_key = token()
    answered = False
    try:
        point = _dadata_point(text, api_key) if api_key else _nominatim_point(text)
        answered = True
    except Exception as exc:  # noqa: BLE001 — не нашли точку, значит цены возьмутся запасные
        log.warning("координаты адреса не получены (%s)", type(exc).__name__)
        log.debug("координаты адреса «%s» не получены (%s)", text, exc)
        point = None

    # Ненайденный адрес помним: иначе каждая опечатка стоила бы запроса в сеть на
    # каждый расчёт. А вот полный отказ службы не помним — иначе после перезапуска
    # сети адрес ещё десять минут считался бы неразобранным. Сам факт отказа
    # запоминаем коротко (_note_down), чтобы не ждать те же десять секунд снова.
    if answered:
        with _lock:
            _coords_cache[key] = (now, point)
    else:
        _note_down("координаты адреса")
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


# ---------- точка на карте → адрес ----------
def reverse(lat: Any, lon: Any) -> str | None:
    """Адрес точки на карте: «Москва, Ленинский проспект, 42». None — не разобрали.

    Зачем. На телефоне человек ставит пин на карте, и эта точка должна стать
    адресом в поле — иначе карта была бы картинкой, а не способом задать адрес.
    Порядок источников тот же, что у подсказок: сначала Photon (быстрый, без
    ключа), при пустом ответе — Nominatim (медленнее, зато знает больше).

    ЧТО СЧИТАЕТСЯ АДРЕСОМ. В ответе обязана быть улица: «Москва» без улицы — это
    не адрес точки, а название города, и подставлять его в поле нельзя — человек
    сохранил бы город вместо дома. Нет улицы — None, поле остаётся прежним.

    Ответ кэшируется вместе с подсказками: одна и та же точка при перетаскивании
    пина туда-обратно не должна стоить двух запросов. Ненайденная точка тоже,
    но только если хоть один источник ОТВЕТИЛ пусто: полный отказ сети не
    кэшируется, иначе после восстановления связи точка ещё десять минут
    выглядела бы неразобранной.
    """
    point = _point(lat, lon)
    if not point:
        return None
    latitude, longitude = point
    key = f"rev|{latitude:.5f}|{longitude:.5f}"
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL_SEC:
            return hit[1][0] if hit[1] else None
    if _recently_down():
        return None

    value = None
    answered = False
    try:
        value = _photon_reverse(latitude, longitude)
        answered = True
    except Exception as exc:  # noqa: BLE001 — Photon промолчал, спросим Nominatim
        log.warning("обратный геокод Photon не ответил")
        log.debug("обратный геокод Photon не ответил (%s)", exc)
    if not value:
        try:
            value = _nominatim_reverse(latitude, longitude)
            answered = True
        except Exception as exc:  # noqa: BLE001 — адрес не нашёлся, это обычный случай
            log.warning("обратный геокод Nominatim не ответил")
            log.debug("обратный геокод Nominatim не ответил (%s)", exc)
    if value:
        log.debug("обратный геокод %.5f,%.5f → «%s»", latitude, longitude, value)
    else:
        log.debug("обратный геокод %.5f,%.5f: адрес не найден", latitude, longitude)

    if answered:
        with _lock:
            _cache[key] = (now, [value] if value else [])
    else:
        _note_down("обратный геокод")
    return value


def _photon_reverse(latitude: float, longitude: float) -> str | None:
    response = requests.get(
        PHOTON_REVERSE_URL,
        params={"lat": latitude, "lon": longitude},
        headers={"User-Agent": PHOTON_UA, "Accept": "application/json"},
        timeout=_timeout(),
    )
    response.raise_for_status()
    for feature in (response.json() or {}).get("features") or []:
        properties = (feature or {}).get("properties") or {}
        # Улица обязательна: иначе _photon_address вернёт название города или
        # заведения, и «Москва» станет адресом дома, где человек не живёт.
        if not properties.get("street"):
            continue
        value = _photon_address(properties)
        if value:
            return value
    return None


def _nominatim_reverse(latitude: float, longitude: float) -> str | None:
    _throttle()
    response = requests.get(
        NOMINATIM_REVERSE_URL,
        params={"lat": latitude, "lon": longitude, "format": "jsonv2",
                "accept-language": "ru", "addressdetails": 1},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=_timeout(),
    )
    response.raise_for_status()
    address = (response.json() or {}).get("address") or {}
    if not (address.get("road") or address.get("pedestrian") or address.get("neighbourhood")):
        # Только город/район — не адрес. display_name здесь не спасает: в нём
        # первым идёт то же название города.
        return None
    return compose(address) or None
