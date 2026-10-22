"""Адрес на телефоне: подсказки, карта, обратный геокод и кэш тайлов.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Пункт 2 владельца (docs/plans/mobilnaya-versiya-2026-10-21.md,
Z1): на телефоне адрес вводится с подсказками, а не с голого поля, и точку можно
поставить на карте. Ломается это по частям, поэтому и проверок несколько: подсказки
закрыты входом и не роняют экран при отказе службы; Photon разбирается без lang=ru
(иначе 400); пин превращается в адрес; тайлы идут через сервер и второй раз берутся
с диска, а не из сети.

ЧЕГО ЭТИ ТЕСТЫ НЕ ЗНАЮТ. Как карта выглядит на телефоне и попадает ли палец в
подсказку: это смотрят глазами. Живой Photon здесь не спрашивается — вместо него
подменённый requests.get: тест не должен зависеть от чужой службы.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import geo, location, users  # noqa: E402

PHONE = "79990000072"


class _Answer:
    """Ответ requests с тем, что читает наш код."""

    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
        return None

    def json(self):
        return self._payload


class _Tile:
    """Ответ тайла: только status_code и content, как их читает прокси."""

    def __init__(self, content: bytes = b"\x89PNG\r\n\x1a\nkz", status_code: int = 200):
        self.content = content
        self.status_code = status_code


@pytest.fixture(autouse=True)
def clean_geo(monkeypatch):
    """Кэш подсказок и токен DaData не должны протекать между тестами."""
    geo._cache.clear()
    geo._coords_cache.clear()
    # Короткая память о полном отказе сети (app/geo.py) — тоже состояние процесса.
    monkeypatch.setattr(geo, "_down_until", 0.0)
    monkeypatch.delenv("DADATA_TOKEN", raising=False)
    # троттлинг Nominatim в тестах только тратит секунды
    monkeypatch.setattr(geo, "_throttle", lambda: None)
    yield
    geo._cache.clear()
    geo._coords_cache.clear()


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временных базах — как в tests/test_phone_screens.py."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    from app.web.screens import result

    result._CALC.clear()
    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    result._CALC.clear()
    users.deactivate()


def enter(client):
    return client.post("/login", data={"phone": PHONE, "next": "/"})


def workspace() -> None:
    users.open_workspace(PHONE)


def text(answer) -> str:
    return answer.data.decode("utf-8")


# ---------- подсказки ----------
def test_suggestions_need_login(web):
    answer = web.get("/phone/address/suggest?q=ленинский", follow_redirects=False)
    assert answer.status_code in (302, 401, 403), "подсказками можно доить чужую службу"


def test_suggestions_come_from_the_phone_provider(web, monkeypatch):
    seen = {}

    def fake(query, limit=6, deep=False):
        seen.update(query=query, limit=limit, deep=deep)
        return ["Москва, Ленинский проспект, 42"]

    monkeypatch.setattr(geo, "suggest_phone", fake)
    enter(web)

    body = web.get("/phone/address/suggest?q=ленинский 42").get_json()

    assert body == {"items": ["Москва, Ленинский проспект, 42"]}
    assert seen == {"query": "ленинский 42", "limit": 6, "deep": False}


def test_the_find_button_also_asks_nominatim(web, monkeypatch):
    """Обычный набор — DaData/Photon, «Найти» — с Nominatim (deep=True)."""
    seen = {}

    def fake(query, limit=6, deep=False):
        seen["deep"] = deep
        return []

    monkeypatch.setattr(geo, "suggest_phone", fake)
    enter(web)

    web.get("/phone/address/suggest?q=ленинский 42")
    assert seen["deep"] is False

    web.get("/phone/address/suggest?q=ленинский 42&find=1")
    assert seen["deep"] is True


def test_a_broken_suggester_is_an_empty_list_not_a_crash(web, monkeypatch):
    def broken(query, limit=6, deep=False):
        raise RuntimeError("Photon недоступен")

    monkeypatch.setattr(geo, "suggest_phone", broken)
    enter(web)

    answer = web.get("/phone/address/suggest?q=ленинский")

    assert answer.status_code == 200
    assert answer.get_json() == {"items": []}


def test_photon_is_parsed_and_lang_is_not_sent(monkeypatch):
    """Photon не принимает lang=ru (400), а адрес отдаёт в GeoJSON."""
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=dict(params or {}), headers=dict(headers or {}))
        return _Answer({"features": [
            {"properties": {"name": "Ленинский проспект", "street": "Ленинский проспект",
                            "housenumber": "42", "city": "Москва", "state": "Москва"}},
            {"properties": {"name": "Москва", "city": "Москва", "state": "Москва"}},
            {"properties": {}},
        ]})

    monkeypatch.setattr(geo.requests, "get", fake_get)

    found = geo.suggest_phone("Ленинский проспект 42")

    assert found == ["Москва, Ленинский проспект, 42", "Москва"]
    assert seen["url"] == geo.PHOTON_URL
    assert seen["params"]["q"] == "Ленинский проспект 42"
    assert "lang" not in seen["params"], "lang=ru Photon встречает 400"
    assert "Mozilla" in seen["headers"]["User-Agent"], "Photon пускает только браузерный UA"


def test_typing_never_asks_nominatim_but_find_does(monkeypatch):
    """Nominatim медленный и просит раз в секунду — на наборе он не нужен."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return _Answer({"features": []}) if url == geo.PHOTON_URL else _Answer([])

    monkeypatch.setattr(geo.requests, "get", fake_get)

    assert geo.suggest_phone("небывалый адрес") == []
    assert calls == [geo.PHOTON_URL], "набор не должен ходить в Nominatim"

    calls.clear()
    assert geo.suggest_phone("небывалый адрес", deep=True) == []
    assert calls == [geo.PHOTON_URL, geo.NOMINATIM_URL], "«Найти» — последний шанс Nominatim"


# ---------- обратный геокод ----------
def test_reverse_uses_photon(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=dict(params or {}))
        return _Answer({"features": [{"properties": {
            "street": "Ленинский проспект", "housenumber": "42", "city": "Москва"}}]})

    monkeypatch.setattr(geo.requests, "get", fake_get)

    assert geo.reverse(55.7075, 37.5831) == "Москва, Ленинский проспект, 42"
    assert seen["url"] == geo.PHOTON_REVERSE_URL
    assert seen["params"] == {"lat": 55.7075, "lon": 37.5831}


def test_reverse_of_a_city_only_point_is_none(monkeypatch):
    """«Москва» без улицы — не адрес точки: поле не должно затираться городом."""
    def fake_get(url, params=None, headers=None, timeout=None):
        if url == geo.PHOTON_REVERSE_URL:
            return _Answer({"features": [{"properties": {"city": "Москва"}}]})
        return _Answer({"address": {"city": "Москва"}})

    monkeypatch.setattr(geo.requests, "get", fake_get)

    assert geo.reverse(55.7075, 37.5831) is None


def test_a_failed_suggester_is_not_cached_as_empty(monkeypatch):
    """Сбой всей цепочки — не «ничего не нашлось»: пустоту на десять минут не кэшируем.

    Помним только сам факт отказа и коротко (app/geo.NEGATIVE_TTL_SEC, 60–120 с):
    повторный ввод сразу после сбоя не ждёт сеть, а после паузы адрес снова
    спрашивается — одна упавшая Photon не убеждает телефон на десять минут, что
    адреса не существует.
    """
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("Photon недоступен")
        return _Answer({"features": [{"properties": {
            "street": "Ленинский проспект", "housenumber": "42", "city": "Москва"}}]})

    monkeypatch.setattr(geo.requests, "get", fake_get)

    assert geo.suggest_phone("Ленинский проспект 42") == [], "сбой — пустой ответ экрана"
    assert len(calls) == 1
    assert geo.suggest_phone("Ленинский проспект 42") == [], \
        "в короткой паузе сеть не дёргаем"
    assert len(calls) == 1, "второй ввод сразу после сбоя не ждёт те же десять секунд"

    monkeypatch.setattr(geo, "_down_until", 0.0)      # пауза вышла
    assert geo.suggest_phone("Ленинский проспект 42") == ["Москва, Ленинский проспект, 42"], \
        "после паузы адрес снова спрашивается"


def test_a_failed_reverse_is_not_cached_as_empty(monkeypatch):
    """Тот же разбор для обратного геокода: молчание обоих — не «адреса нет»."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        if len(calls) <= 2:
            raise RuntimeError("сеть недоступна")
        return _Answer({"features": [{"properties": {
            "street": "Ленинский проспект", "housenumber": "42", "city": "Москва"}}]})

    monkeypatch.setattr(geo.requests, "get", fake_get)

    assert geo.reverse(55.7075, 37.5831) is None
    assert len(calls) == 2, "Photon и Nominatim оба промолчали"
    assert geo.reverse(55.7075, 37.5831) is None
    assert len(calls) == 2, "в короткой паузе сеть не дёргаем"

    monkeypatch.setattr(geo, "_down_until", 0.0)      # пауза вышла
    assert geo.reverse(55.7075, 37.5831) == "Москва, Ленинский проспект, 42", \
        "после восстановления сети точка должна разбираться, а не отдавать None из кэша"


def test_an_empty_reverse_answer_is_still_cached(monkeypatch):
    """Пустой ОТВЕТ (не сбой) помним: иначе каждое касание пустого места — два запроса."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return _Answer({"features": []}) if url == geo.PHOTON_REVERSE_URL else _Answer({})

    monkeypatch.setattr(geo.requests, "get", fake_get)

    assert geo.reverse(55.7075, 37.5831) is None
    assert geo.reverse(55.7075, 37.5831) is None
    assert calls == [geo.PHOTON_REVERSE_URL, geo.NOMINATIM_REVERSE_URL], \
        "второй раз точка берётся из кэша"


def test_reverse_of_an_impossible_point_is_none(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("для «ноль-ноль» и кривых чисел сеть не нужна")

    monkeypatch.setattr(geo.requests, "get", explode)

    assert geo.reverse(0, 0) is None
    assert geo.reverse("широта", 37.5) is None


def test_reverse_route_returns_the_address(web, monkeypatch):
    seen = {}

    def fake(lat, lon):
        seen.update(lat=lat, lon=lon)
        return "Москва, Ленинский проспект, 42"

    monkeypatch.setattr(geo, "reverse", fake)
    enter(web)

    body = web.get("/phone/address/reverse?lat=55.7075&lon=37.5831").get_json()

    assert body == {"address": "Москва, Ленинский проспект, 42"}
    assert seen == {"lat": 55.7075, "lon": 37.5831}


def test_reverse_route_without_coordinates_is_null(web):
    enter(web)
    assert web.get("/phone/address/reverse?lat=&lon=").get_json() == {"address": None}
    assert web.get("/phone/address/reverse").get_json() == {"address": None}


# ---------- сохранение адреса и точки ----------
def test_the_map_block_and_leaflet_are_local(web):
    """Leaflet лежит у нас, тайлы идут через свой прокси — без CDN и чужих доменов."""
    enter(web)

    page = text(web.get("/phone/settings?map=1"))

    assert 'id="addr-map"' in page
    assert 'data-tile-base="/phone/map/tile"' in page
    assert "/static/leaflet/leaflet.js" in page
    assert "unpkg.com" not in page
    assert "a.tile.openstreetmap.org" not in page, "браузер не должен ходить в OSM напрямую"


def test_leaflet_files_are_vendored():
    base = os.path.join(ROOT, "app", "web", "static", "leaflet")
    for name in ("leaflet.js", "leaflet.css", "images/marker-icon.png",
                 "images/marker-icon-2x.png", "images/marker-shadow.png",
                 "images/layers.png", "images/layers-2x.png"):
        path = os.path.join(base, *name.split("/"))
        assert os.path.getsize(path) > 0, f"нет вендорного файла {name}"


def test_the_map_does_not_go_to_the_network_for_an_address(web, monkeypatch):
    """Адрес сохранён, пина ещё нет — GET настроек не зовёт geocoder.

    geo.coords ходит в Nominatim: до десяти секунд ожидания и пауза «раз в
    секунду». Открытие настроек не должно ждать чужую службу; без сохранённой
    точки карта откроется по клиентскому умолчанию (phone-map.js, Москва).
    """
    calls = []

    def counting_coords(address):
        calls.append(address)
        return (55.7075, 37.5831)

    monkeypatch.setattr(location, "address", lambda: "Москва, Ленинский проспект, 42")
    monkeypatch.setattr(location, "point", lambda: None)
    monkeypatch.setattr(location, "nearby", lambda addr=None: [])
    monkeypatch.setattr(location, "nearby_magnit", lambda addr=None: [])
    monkeypatch.setattr(geo, "coords", counting_coords)
    enter(web)

    page = text(web.get("/phone/settings?map=1"))

    assert calls == [], "GET настроек не должен ходить в геокодер"
    assert 'data-lat=""' in page and 'data-lon=""' in page


def test_the_map_is_centred_on_the_saved_point(web, monkeypatch):
    """Сохранённая точка — единственный источник центра: она поставлена рукой."""
    monkeypatch.setattr(location, "address", lambda: "Москва, Ленинский проспект, 42")
    monkeypatch.setattr(location, "point", lambda: (55.7075, 37.5831))
    monkeypatch.setattr(location, "nearby", lambda addr=None: [])
    monkeypatch.setattr(location, "nearby_magnit", lambda addr=None: [])
    monkeypatch.setattr(geo, "coords", lambda address: (1.0, 2.0))
    enter(web)

    page = text(web.get("/phone/settings?map=1"))

    assert 'data-lat="55.707500"' in page
    assert 'data-lon="37.583100"' in page


def test_saving_keeps_both_the_address_and_the_point(web, monkeypatch):
    from app.web.screens import basket as basket_screen

    monkeypatch.setattr(basket_screen, "refresh_after_move", lambda: None)
    enter(web)

    answer = web.post("/phone/settings", data={
        "address": "Москва, Ленинский проспект, 42",
        "point": "55.707500,37.583100",
    })

    assert answer.status_code == 303
    assert "saved=address" in answer.headers["Location"]

    workspace()
    try:
        assert location.address() == "Москва, Ленинский проспект, 42"
        assert location.point() == pytest.approx((55.7075, 37.5831))
    finally:
        users.deactivate()

    # Подсказка о магазинах ходит в сети — здесь она не нужна.
    monkeypatch.setattr(location, "nearby", lambda addr=None: [])
    monkeypatch.setattr(location, "nearby_magnit", lambda addr=None: [])
    page = text(web.get("/phone/settings?saved=address"))
    assert "Москва, Ленинский проспект, 42" in page
    assert 'id="addr-point" value="55.707500,37.583100"' in page


def test_a_changed_address_drops_the_old_point(web, monkeypatch):
    """Пин относится к прежнему адресу: оставить его — показать чужое место."""
    from app.web.screens import basket as basket_screen

    monkeypatch.setattr(basket_screen, "refresh_after_move", lambda: None)
    monkeypatch.setattr(location, "nearby", lambda addr=None: [])
    monkeypatch.setattr(location, "nearby_magnit", lambda addr=None: [])
    enter(web)

    web.post("/phone/settings", data={"address": "Москва, Ленинский проспект, 42",
                                      "point": "55.707500,37.583100"})
    web.post("/phone/settings", data={"address": "Москва, Ходынский бульвар, 4"})

    workspace()
    try:
        assert location.address() == "Москва, Ходынский бульвар, 4"
        assert location.point() is None
    finally:
        users.deactivate()


def test_nearby_line_appears_after_saving(web, monkeypatch):
    from app.web.screens import basket as basket_screen

    monkeypatch.setattr(basket_screen, "refresh_after_move", lambda: None)
    monkeypatch.setattr(location, "nearby",
                        lambda addr=None: [{"distance": 650, "address": "Лента"}])
    monkeypatch.setattr(location, "nearby_magnit",
                        lambda addr=None: [{"distance": 300, "address": "Магнит"}])
    enter(web)

    web.post("/phone/settings", data={"address": "Москва, Ленинский проспект, 42",
                                      "point": "55.707500,37.583100"})
    page = text(web.get("/phone/settings?saved=address"))

    assert "рядом: Лента 650 м, Магнит 300 м" in page


def test_nearby_errors_leave_no_block(web, monkeypatch):
    from app.web.screens import basket as basket_screen

    monkeypatch.setattr(basket_screen, "refresh_after_move", lambda: None)

    def broken(addr=None):
        raise RuntimeError("сеть недоступна")

    monkeypatch.setattr(location, "nearby", broken)
    monkeypatch.setattr(location, "nearby_magnit", broken)
    enter(web)

    web.post("/phone/settings", data={"address": "Москва, Ленинский проспект, 42",
                                      "point": "55.707500,37.583100"})
    answer = web.get("/phone/settings?saved=address")

    assert answer.status_code == 200
    assert "рядом:" not in text(answer)


# ---------- прокси тайлов ----------
def test_tiles_need_login(web):
    answer = web.get("/phone/map/tile/15/19728/10362.png", follow_redirects=False)
    assert answer.status_code in (302, 401, 403)


def test_tiles_outside_the_zoom_window_are_404(web):
    enter(web)
    assert web.get("/phone/map/tile/9/0/0.png").status_code == 404
    assert web.get("/phone/map/tile/19/0/0.png").status_code == 404
    assert web.get("/phone/map/tile/10/1024/0.png").status_code == 404, "x за краем мира"


def test_a_tile_is_fetched_once_then_taken_from_disk(web, tmp_path, monkeypatch):
    from app.web.phone import settings as phone_settings

    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers or {}))
        return _Tile()

    monkeypatch.setattr(phone_settings.requests, "get", fake_get)
    enter(web)

    first = web.get("/phone/map/tile/15/19728/10362.png")
    second = web.get("/phone/map/tile/15/19728/10362.png")

    assert first.status_code == second.status_code == 200
    assert first.data == second.data
    assert len(calls) == 1, "второй тайл должен прийти с диска, а не из сети"
    assert calls[0][0] == "https://a.tile.openstreetmap.org/15/19728/10362.png"
    assert "moya-korzina" in calls[0][1]["User-Agent"]
    assert first.headers["Cache-Control"] == "public, max-age=604800"
    assert second.headers["Cache-Control"] == "public, max-age=604800"
    assert (tmp_path / "data" / "tiles" / "15" / "19728" / "10362.png").exists()


def test_a_stale_tile_is_fetched_again(web, tmp_path, monkeypatch):
    """Семь дней — срок годности кэша: после него тайл спрашивается заново."""
    from app.web.phone import settings as phone_settings

    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        return _Tile()

    monkeypatch.setattr(phone_settings.requests, "get", fake_get)
    enter(web)

    assert web.get("/phone/map/tile/15/19728/10362.png").status_code == 200
    path = tmp_path / "data" / "tiles" / "15" / "19728" / "10362.png"
    old = time.time() - phone_settings.TILE_TTL_SEC - 60
    os.utime(path, (old, old))

    assert web.get("/phone/map/tile/15/19728/10362.png").status_code == 200
    assert len(calls) == 2, "старый тайл должен быть скачан снова"


def test_a_cache_hit_does_not_use_the_hourly_limit(web, tmp_path, monkeypatch):
    """Лимит 500/час — за походы в OSM, а не за тайлы, отданные с диска."""
    from app.web.phone import settings as phone_settings

    monkeypatch.setattr(phone_settings.requests, "get", lambda *a, **k: _Tile())
    enter(web)
    key = users.normalize_phone(PHONE)
    phone_settings._tile_hits.pop(key, None)
    try:
        assert web.get("/phone/map/tile/15/19728/10362.png").status_code == 200
        after_first = phone_settings._tile_hits[key][1]
        assert after_first == 1

        assert web.get("/phone/map/tile/15/19728/10362.png").status_code == 200
        assert phone_settings._tile_hits[key][1] == after_first, \
            "тайл с диска не должен расходовать лимит OSM"
    finally:
        phone_settings._tile_hits.pop(key, None)


def test_a_non_png_answer_is_not_cached(web, tmp_path, monkeypatch):
    """HTML-заглушка источника с кодом 200 не должна отравить кэш на неделю."""
    from app.web.phone import settings as phone_settings

    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        return _Tile(content=b"<html>too many requests</html>", status_code=200)

    monkeypatch.setattr(phone_settings.requests, "get", fake_get)
    enter(web)

    answer = web.get("/phone/map/tile/15/19728/10362.png")

    assert answer.status_code == 502
    assert not (tmp_path / "data" / "tiles" / "15" / "19728" / "10362.png").exists()

    # Второй запрос снова идёт в источник: мусор не лёг в кэш.
    assert web.get("/phone/map/tile/15/19728/10362.png").status_code == 502
    assert len(calls) == 2


def test_too_many_tiles_are_refused(web, monkeypatch):
    """Пятьсот тайлов в час на телефон — защита от зацикливания карты."""
    from app.web.phone import settings as phone_settings

    enter(web)
    key = users.normalize_phone(PHONE)
    phone_settings._tile_hits[key] = (time.time(), phone_settings.TILES_PER_HOUR)
    try:
        answer = web.get("/phone/map/tile/15/19728/10362.png")
        assert answer.status_code == 429
        assert "карта ненадолго отдыхает" in text(answer).lower(), \
            "телефон должен получить слова, а не серое поле"
    finally:
        phone_settings._tile_hits.pop(key, None)


def test_a_missing_upstream_tile_is_404_not_a_crash(web, monkeypatch):
    from app.web.phone import settings as phone_settings

    monkeypatch.setattr(phone_settings.requests, "get",
                        lambda *a, **k: _Tile(content=b"", status_code=404))
    enter(web)

    answer = web.get("/phone/map/tile/15/19728/10362.png")

    assert answer.status_code == 404


def test_an_unreachable_tile_source_is_502(web, monkeypatch):
    from app.web.phone import settings as phone_settings

    def broken(*args, **kwargs):
        raise phone_settings.requests.RequestException("сеть недоступна")

    monkeypatch.setattr(phone_settings.requests, "get", broken)
    enter(web)

    assert web.get("/phone/map/tile/15/19728/10362.png").status_code == 502
