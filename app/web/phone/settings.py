from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from datetime import datetime

import requests
from flask import Response, g, jsonify, redirect, request, url_for

from app import config, freshness, geo, repo
from app.web import device
from app.web.auth import needs_phone

from .common import _login, _render, bp, plural

log = logging.getLogger(__name__)

# ---------- тайлы карты ----------
# Карта на телефоне ходит не прямо в OpenStreetMap, а через сервер, и на это три
# причины. Первая: у телефона в приложении может не быть своего выхода в сеть
# (WebView без доступа к чужим доменам). Вторая: OSM просит подписываться в
# User-Agent, а браузерный запрос картинки этого не позволяет. Третья: один и тот
# же тайл нужен всем, и сервер кэширует его на диске, а не тянет заново.
TILE_URL = "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_UA = "moya-korzina/1.0 (adresa; owner app)"
TILE_MIN_Z, TILE_MAX_Z = 10, 18
TILE_TTL_SEC = 7 * 24 * 3600
TILE_CACHE_CONTROL = "public, max-age=604800"
TILES_PER_HOUR = 500
TILE_WINDOW_SEC = 3600
# Настоящий PNG начинается этими байтами. Источник тайлов иногда отвечает
# HTML-заглушкой («слишком много запросов», капча) с кодом 200: записать её в
# кэш значило бы на неделю показать вместо карты битую картинку.
PNG_MAGIC = b"\x89PNG"

# Лимит живёт в памяти процесса: это защита от случайного зацикливания карты, а не
# учёт. Перезапуск его обнуляет — так же, как счётчик попыток входа (app/web/login.py).
_tile_lock = threading.Lock()
_tile_hits: dict[str, tuple[float, int]] = {}


# ---------- Настройки ----------
@bp.route("/settings", methods=("GET", "POST"))
@needs_phone
def settings():
    from app import cartrun, location, store_accounts

    if request.method == "POST":
        return _save_address()

    names = {s.code: s.name for s in repo.list_stores()}
    stores = []
    for conn in store_accounts.connections():
        if conn.store_code not in names:
            continue
        login = _login(conn.store_code)
        # Последняя попытка — из строки сборки этой сети, если она есть: по ней
        # «вход истёк» перестаёт быть безликим и видно, когда сеть последний раз
        # отвечала. Строки нет — так и остаётся без даты, выдумывать её нечем.
        row = cartrun.get(conn.store_code)
        stores.append({"code": conn.store_code, "name": names[conn.store_code],
                       "connected": not login, "again": login == "again",
                       "last": _when_text((row.finished_at or row.started_at) if row else None)})
    connected = [s for s in stores if s["connected"]]
    waiting = [s for s in stores if not s["connected"]]
    try:
        address = location.address()
    except Exception:  # noqa: BLE001
        address = None

    point = None
    try:
        point = location.point()
    except Exception:  # noqa: BLE001 — битая настройка не повод ронять экран
        point = None
    # Центр карты — только сохранённая точка: она поставлена рукой и ничего не
    # стоит. Адрес в точку здесь не переводится: geo.coords ходит в Nominatim
    # (до десяти секунд плюс пауза «раз в секунду»), а открытие настроек не
    # должно ждать чужую службу. Нет точки — карта откроется по умолчанию,
    # который задан в phone-map.js (Москва).
    center = point
    board = _board({s["code"] for s in connected})
    saved = request.args.get("saved")
    return _render("settings.html", tab="settings", stores=stores,
                   connected=connected, waiting=waiting,
                   # Первый запуск: ни одной подключённой сети, но подключать есть что.
                   # Это не пустое место, а объяснение, зачем вообще входить.
                   first_run=bool(stores) and not connected and bool(waiting),
                   no_stores=not stores,
                   disconnected_name=names.get(request.args.get("store") or "")
                   if saved == "disconnected" else None,
                   fns=_fns(), address=address, on_phone=device.on_phone(),
                   saved=saved, connect=request.args.get("connect"),
                   edit=request.args.get("edit"),
                   point_text=_point_text(point), center=center,
                   map_open=request.args.get("map") == "1",
                   nearby_text=_nearby_line(address) if saved == "address" else None,
                   board=board,
                   tile_base=url_for("phone.map_tile", z=10, x=0, y=0).rsplit("/", 3)[0])


def _board(signed: set[str]) -> list[dict]:
    """Сборка корзины по сетям — только у подключённых: остальным место в «Осталось подключить».

    «Подключённые» здесь те же, что в списке «Подключено» (вход сохранён), и названия
    берутся из справочника магазинов, как везде на экране. Нет ни одной — блока нет.
    """
    if not signed:
        return []
    try:
        from app import humansteps
        return [row for row in humansteps.board(signed_in=signed) if row["code"] in signed]
    except Exception:  # noqa: BLE001
        log.warning("humansteps.board не прочитался", exc_info=True)
        return []


def _when_text(value: str | None) -> str | None:
    """Когда была последняя попытка: «21.10.2026 02:14». Не разобрали — None."""
    dt = freshness.shown(value)
    return f"{dt:%d.%m.%Y %H:%M}" if dt else None


@bp.post("/settings/disconnect")
@needs_phone
def disconnect_store():
    """«Отключить вход»: стереть сохранённый вход и отметку подключения.

    Стираем только то, что лежит у нас на сервере: куки и localStorage сети
    (app/shopbrowser/store.py) и факт подключения (app/store_accounts.py). Из
    аккаунта в самой сети человек так не выходит — и куки WebView на телефоне
    серверу не подчиняются. Поэтому экран говорит об этом прямо, а не обещает
    выход одним нажатием.
    """
    from app import store_accounts
    from app.shopbrowser import store as shopstore

    code = (request.form.get("store") or "").strip().lower()
    names = {s.code: s.name for s in repo.list_stores()}
    if code not in names:
        return redirect(url_for("phone.settings"), 303)
    shopstore.forget(code)
    store_accounts.forget(code)
    log.info("%s: вход отключён на телефоне; сохранённый вход и отметка стёрты", code)
    return redirect(url_for("phone.settings", saved="disconnected", store=code), 303)


def _fns() -> dict:
    """Кабинет «Мои чеки»: подключён ли и сколько чеков уже загружено."""
    from app.fns import sync as cabinet

    try:
        connected = bool(cabinet.is_connected())
    except Exception:  # noqa: BLE001 — кабинет недоступен; строка позовёт подключать
        connected = False
    try:
        count = len(repo.imported_receipts())
    except Exception:  # noqa: BLE001
        count = 0
    word = plural(count, "чек", "чека", "чеков")
    return {"connected": connected, "count": count, "label": f"{count} {word}"}


def _save_address():
    """Сохранить адрес так же, как главная веба (app/web/views.py, /address)."""
    from app import location

    value = (request.form.get("address") or "").strip()
    if not value:
        return redirect(url_for("phone.settings", edit="address"), 303)
    moved = location.save_address(value)
    # Точка с карты — отдельно от адреса: location.save_address ставит очередь цен
    # и ничего не знает про пины. Если человек адрес переписал, а пин не двигал,
    # старая точка относится к прежнему адресу и её надо стереть: иначе карта
    # откроется на чужом месте, а поле адреса будет говорить другое.
    coords = _form_point()
    try:
        if coords:
            location.save_point(*coords)
        elif moved:
            location.save_point(None, None)
    except Exception:  # noqa: BLE001 — точка не повод не сохранить адрес
        log.warning("точка с карты не сохранилась", exc_info=True)
    if moved:
        try:
            from app.web.screens import basket as basket_screen
            basket_screen.refresh_after_move()
        except Exception:  # noqa: BLE001 — адрес сохранён, это главное
            log.warning("обновление цен после смены адреса не запустилось", exc_info=True)
    return redirect(url_for("phone.settings", saved="address"), 303)


def _form_point() -> tuple[str, str] | None:
    """Координаты из формы: одно поле «lat,lon» или два поля lat/lon.

    Пусто — координат нет. Строки, а не числа: проверять и переводить их будет
    location.save_point — там же, где проверяется точка из любого другого места.
    """
    raw = (request.form.get("point") or "").strip()
    if raw:
        parts = raw.replace(";", ",").split(",")
        if len(parts) != 2:
            return None
        return parts[0].strip(), parts[1].strip()
    lat = (request.form.get("lat") or "").strip()
    lon = (request.form.get("lon") or "").strip()
    if lat and lon:
        return lat, lon
    return None


def _point_text(point: tuple[float, float] | None) -> str:
    return f"{point[0]:.6f},{point[1]:.6f}" if point else ""


def _nearby_line(address: str | None) -> str | None:
    """«рядом: Лента 650 м, Магнит 300 м» — проверка, что адрес понят.

    Показывается после сохранения, а не при каждом открытии: это два похода в
    сети, и делать их на пустом месте незачем. Пусто или ошибка — блока нет:
    подсказка о магазинах не должна превращаться в красную плашку.
    """
    if not address:
        return None
    from app import location
    from app.web.screens.home import _distance_text

    parts: list[str] = []
    for name, call in (("Лента", location.nearby), ("Магнит", location.nearby_magnit)):
        try:
            shops = call(address) or []
        except Exception as exc:  # noqa: BLE001 — молчание сети не повод ронять экран
            log.info("%s: магазины рядом с «%s» не спросились (%s)", name, address, exc)
            continue
        best = None
        best_distance = float("inf")
        for shop in shops:
            try:
                distance = float((shop or {}).get("distance"))
            except (TypeError, ValueError):
                continue
            if distance < best_distance:
                best, best_distance = shop, distance
        if best is not None:
            parts.append(f"{name} {_distance_text(best)}")
    return "рядом: " + ", ".join(parts) if parts else None


# ---------- подсказки и обратный геокод ----------
@bp.get("/address/suggest")
@needs_phone
def address_suggest():
    """Подсказки адреса на телефоне. Ошибка службы — пустой список, не 500.

    find=1 — нажата «Найти»: тогда и только тогда в цепочку добавляется
    Nominatim (app/geo.py, suggest_phone). Обычный набор обходится DaData и
    Photon: они быстрее и не упираются в правило «раз в секунду».
    """
    query = (request.args.get("q") or "").strip()
    deep = request.args.get("find") in ("1", "true", "yes")
    try:
        found = geo.suggest_phone(query, limit=6, deep=deep)
    except Exception:  # noqa: BLE001 — подсказка не шаг, без неё адрес вводят руками
        log.warning("подсказки адреса на телефоне не пришли", exc_info=True)
        found = []
    return jsonify({"items": list(found)[:6]})


@bp.get("/address/reverse")
@needs_phone
def address_reverse():
    """Адрес точки, поставленной пином: {"address": "..."} или {"address": null}."""
    try:
        lat = float((request.args.get("lat") or "").replace(",", "."))
        lon = float((request.args.get("lon") or "").replace(",", "."))
    except (TypeError, ValueError):
        return jsonify({"address": None})
    try:
        value = geo.reverse(lat, lon)
    except Exception:  # noqa: BLE001 — не разобрали точку, поле адреса не трогаем
        log.warning("обратный геокод на телефоне не ответил", exc_info=True)
        value = None
    return jsonify({"address": value})


# ---------- прокси тайлов ----------
@bp.get("/map/tile/<int:z>/<int:x>/<int:y>.png")
@needs_phone
def map_tile(z: int, x: int, y: int):
    """Тайл карты из OpenStreetMap через сервер, с дисковым кэшем на 7 дней.

    Почему через сервер, а не прямо с OSM: телефону в приложении нужен один
    источник, сервер подписывается в User-Agent как просят правила OSM и держит
    общий кэш. Зум 10–18 — это город и улица; глубже сервер не отдаёт, потому что
    за пределами этого окна карта либо пустая, либо это уже чужая работа.
    """
    if not _tile_valid(z, x, y):
        return "нет такого тайла", 404

    path = _tile_path(z, x, y)
    try:
        data = _read_tile(path)
        if data is None:
            # Счётчик — только за настоящий поход в OSM. Тайл с диска бесплатен:
            # лимит защищает чужую службу, а не нашу собственную карту.
            if not _tile_allowed():
                answer = Response("карта ненадолго отдыхает", status=429, mimetype="text/plain")
                answer.headers["Retry-After"] = str(TILE_WINDOW_SEC)
                return answer
            fetched = _fetch_tile(z, x, y)
            if fetched == b"":
                return "тайла нет", 404
            if fetched is None:
                return "источник тайлов недоступен", 502
            if not _is_png(fetched):
                log.warning("тайл %s/%s/%s: источник ответил не PNG — в кэш не пишем", z, x, y)
                return "источник тайлов ответил не картинкой", 502
            data = fetched
            _write_tile(path, data)
    except Exception:  # noqa: BLE001 — карта без тайла — досада, а не падение сервера
        log.warning("тайл %s/%s/%s не отдан", z, x, y, exc_info=True)
        return "источник тайлов недоступен", 502

    answer = Response(data, mimetype="image/png")
    answer.headers["Cache-Control"] = TILE_CACHE_CONTROL
    return answer


def _is_png(data: bytes) -> bool:
    return bool(data) and data.startswith(PNG_MAGIC)


def _tile_valid(z: int, x: int, y: int) -> bool:
    if not (TILE_MIN_Z <= z <= TILE_MAX_Z):
        return False
    edge = 1 << z
    return 0 <= x < edge and 0 <= y < edge


def _tile_allowed() -> bool:
    """Не больше TILES_PER_HOUR походов в OSM на телефон (или адрес, если номер неизвестен).

    Вызывается только когда тайла нет в кэше: попадание в кэш — не нагрузка на
    чужую службу и счётчик не двигает.
    """
    key = getattr(g, "phone", None) or request.remote_addr or "?"
    now = time.time()
    with _tile_lock:
        start, count = _tile_hits.get(key, (now, 0))
        if now - start >= TILE_WINDOW_SEC:
            start, count = now, 0
        # Отказ лимита — не поход в OSM: счётчик на нём не растёт, иначе серия
        # отказов «продлевала» бы себе запрет.
        if count >= TILES_PER_HOUR:
            _tile_hits[key] = (start, count)
            return False
        count += 1
        _tile_hits[key] = (start, count)
        if len(_tile_hits) > 4096:
            for stale in [k for k, (at, _) in _tile_hits.items()
                          if now - at >= TILE_WINDOW_SEC]:
                _tile_hits.pop(stale, None)
        return count <= TILES_PER_HOUR


def _tile_path(z: int, x: int, y: int) -> str:
    return os.path.join(config.ROOT, "data", "tiles", str(z), str(x), f"{y}.png")


def _read_tile(path: str) -> bytes | None:
    """Свежий тайл с диска. None — нет файла, он старый, не читается или не PNG."""
    try:
        if time.time() - os.path.getmtime(path) > TILE_TTL_SEC:
            return None
        with open(path, "rb") as fh:
            data = fh.read() or None
    except OSError:
        return None
    # Кэш, отравленный HTML-заглушкой до этой проверки, не отдаём: пусть лучше
    # тайл скачается заново, чем вместо карты останется битая картинка.
    if data is not None and not _is_png(data):
        log.warning("тайл %s: в кэше не PNG — читаем заново", path)
        return None
    return data


def _write_tile(path: str, data: bytes) -> None:
    """Записать тайл атомарно: недописанный файл не должен стать «кэшем».

    Временный файл уникален (mkstemp), поэтому две одновременные просьбы об одном
    тайле не мешают друг другу: кто последний заменил — тот и прав, а битого
    файла на месте кэша не остаётся.
    """
    tmp = None
    try:
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tile-", suffix=".tmp", dir=folder)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("тайл %s не записался в кэш (%s)", path, exc)
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _fetch_tile(z: int, x: int, y: int) -> bytes | None:
    """Тайл у OpenStreetMap: bytes — есть, b"" — источника нет, None — недоступен."""
    url = TILE_URL.format(z=z, x=x, y=y)
    try:
        response = requests.get(
            url,
            headers={"User-Agent": TILE_UA, "Accept": "image/png,image/*;q=0.8,*/*;q=0.5"},
            timeout=_timeout(),
        )
    except requests.RequestException as exc:
        log.warning("тайл %s/%s/%s не скачался (%s)", z, x, y, exc)
        return None
    status = getattr(response, "status_code", 0)
    if status == 404:
        return b""
    if status != 200:
        log.warning("тайл %s/%s/%s: источник ответил %s", z, x, y, status)
        return None
    data = getattr(response, "content", b"") or b""
    if not data:
        log.warning("тайл %s/%s/%s: источник ответил пустотой", z, x, y)
        return None
    return data


def _timeout() -> float:
    try:
        return float(config.get("connectors.timeout_sec", 10) or 10)
    except (TypeError, ValueError):
        return 10.0
