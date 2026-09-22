"""Подсказки адреса: что уходит в сеть, что возвращается человеку.

Проверяется не «работает ли DaData», а поведение на границе: короткий ввод не
должен стучаться в сеть вовсе, отказ сети не должен ронять экран, а ответ OSM
не должен попадать в список вместе со страной, индексом и федеральным округом —
их человек не читает, а строку они удлиняют так, что видно только начало.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import geo  # noqa: E402


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    geo._cache.clear()
    geo._coords_cache.clear()
    monkeypatch.delenv("DADATA_TOKEN", raising=False)
    # троттлинг Nominatim в тестах только тратит секунды
    monkeypatch.setattr(geo, "_throttle", lambda: None)
    yield
    geo._cache.clear()
    geo._coords_cache.clear()


class _Answer:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_short_query_never_reaches_the_network(monkeypatch):
    """«мо» — это ещё не адрес: запрос стоит денег и времени, а вариантов даст тысячу."""
    def explode(*args, **kwargs):
        raise AssertionError("запрос ушёл в сеть на двух буквах")

    monkeypatch.setattr(geo.requests, "get", explode)
    monkeypatch.setattr(geo.requests, "post", explode)

    assert geo.suggest("мо") == []
    assert geo.suggest("   ") == []


def test_dadata_is_used_when_the_token_is_set(monkeypatch):
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        seen["query"] = (json or {}).get("query")
        seen["auth"] = (headers or {}).get("Authorization")
        return _Answer({"suggestions": [{"value": "г Москва, Ленинский пр-кт, д 42"},
                                        {"value": "г Москва, Ленинский пр-кт, д 42А"}]})

    monkeypatch.setenv("DADATA_TOKEN", "secret")
    monkeypatch.setattr(geo.requests, "post", fake_post)

    found = geo.suggest("ленинский 42")

    assert found == ["г Москва, Ленинский пр-кт, д 42", "г Москва, Ленинский пр-кт, д 42А"]
    assert seen["url"] == geo.DADATA_URL
    assert seen["query"] == "ленинский 42"
    assert seen["auth"] == "Token secret"


def test_without_a_token_the_free_source_answers(monkeypatch):
    """Без ключа подсказки обязаны работать: иначе установка «из коробки» немая."""
    def fake_get(url, params=None, headers=None, timeout=None):
        assert url == geo.NOMINATIM_URL
        assert params["countrycodes"] == "ru"
        assert headers["User-Agent"] == geo.USER_AGENT
        return _Answer([
            # у OSM первым идёт название заведения — в подсказке его быть не должно
            {"display_name": "Legend City, 42, Ленинский проспект, Москва, 119334, Россия",
             "address": {"house_number": "42", "road": "Ленинский проспект", "city": "Москва"}},
            {"display_name": "42, Ленинский проспект, Москва, 119334, Россия",
             "address": {"house_number": "42", "road": "Ленинский проспект", "city": "Москва"}},
            {"display_name": "Где-то без разбора, Россия"},
        ])

    monkeypatch.setattr(geo.requests, "get", fake_get)

    found = geo.suggest("ленинский 42")

    assert found[0] == "Москва, Ленинский проспект, 42", "город, улица, дом — в привычном порядке"
    assert len(found) == 2, "два ответа про один дом — одна строка в списке"
    assert found[1] == "Где-то без разбора", "без разбора адреса остаётся сама строка"


def test_network_failure_is_not_an_error_for_the_screen(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("сеть недоступна")

    monkeypatch.setattr(geo.requests, "get", fail)

    assert geo.suggest("ленинский 42") == []


def test_the_same_query_asks_the_source_once(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(params["q"])
        return _Answer([{"display_name": "Ходынский бульвар, 4, Москва, Россия",
                         "address": {"house_number": "4", "road": "Ходынский бульвар",
                                     "city": "Москва"}}])

    monkeypatch.setattr(geo.requests, "get", fake_get)

    first = geo.suggest("ходынский 4")
    second = geo.suggest("Ходынский 4")     # тот же адрес, другой регистр

    assert first == second == ["Москва, Ходынский бульвар, 4"]
    assert len(calls) == 1, "повторный набор тех же букв не должен слать второй запрос"


def test_compose_reads_like_an_address():
    full = {"city": "Москва", "road": "Ходынский бульвар", "house_number": "4"}
    assert geo.compose(full) == "Москва, Ходынский бульвар, 4"
    assert geo.compose({"town": "Видное", "road": "Школьная улица"}) == "Видное, Школьная улица"
    assert geo.compose({}) == ""


def test_tidy_keeps_the_part_a_person_reads():
    assert geo.tidy("4, Ходынский бульвар, Москва, 125252, Россия") == "4, Ходынский бульвар, Москва"
    assert geo.tidy("") == ""


# ---------- адрес в точку на карте ----------
def test_coords_from_dadata(monkeypatch):
    """DaData отдаёт широту и долготу СТРОКАМИ — числами их делает наш разбор."""
    def fake_post(url, json=None, headers=None, timeout=None):
        assert url == geo.DADATA_URL
        assert (json or {}).get("count") == 1, "для точки хватает одного варианта"
        return _Answer({"suggestions": [{"value": "г Москва, б-р Ходынский, д 4",
                                         "data": {"geo_lat": "55.7899", "geo_lon": "37.5325"}}]})

    monkeypatch.setenv("DADATA_TOKEN", "secret")
    monkeypatch.setattr(geo.requests, "post", fake_post)

    assert geo.coords("Москва, Ходынский бульвар 4") == (55.7899, 37.5325)


def test_coords_without_a_token_use_the_free_source(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        assert url == geo.NOMINATIM_URL
        return _Answer([{"lat": "56.8361", "lon": "60.6146"}])

    monkeypatch.setattr(geo.requests, "get", fake_get)
    assert geo.coords("Екатеринбург, улица Малышева 51") == (56.8361, 60.6146)


def test_coords_reject_the_empty_answer(monkeypatch):
    """«Ноль-ноль» — это не точка, а «не знаю»: так DaData отвечает про город без координат.

    Принять её за адрес значило бы искать магазины в Гвинейском заливе и получить
    честную пустоту, по которой не понять, дело в адресе или в сети.
    """
    monkeypatch.setenv("DADATA_TOKEN", "secret")
    monkeypatch.setattr(geo.requests, "post", lambda *a, **k: _Answer(
        {"suggestions": [{"data": {"geo_lat": "0", "geo_lon": "0"}},
                         {"data": {"geo_lat": None, "geo_lon": None}}]}))
    assert geo.coords("Некоторое село") is None


def test_coords_short_query_never_reaches_the_network(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("запрос ушёл в сеть на двух буквах")

    monkeypatch.setattr(geo.requests, "get", explode)
    monkeypatch.setattr(geo.requests, "post", explode)
    assert geo.coords("мо") is None
    assert geo.coords("  ") is None


def test_coords_failure_does_not_raise(monkeypatch):
    """Сеть молчит — возвращаем None. Цены возьмутся запасные, экран не упадёт."""
    def boom(*args, **kwargs):
        raise RuntimeError("сеть отвалилась")

    monkeypatch.setattr(geo.requests, "get", boom)
    assert geo.coords("Москва, Ходынский бульвар 4") is None


def test_coords_are_asked_once(monkeypatch):
    """Адрес меняется реже, чем считается корзина: второй раз берём из памяти.

    И ненайденный адрес тоже: иначе каждая опечатка стоила бы запроса в сеть на
    каждый пересчёт.
    """
    calls: list[str] = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((params or {}).get("q"))
        return _Answer([{"lat": "55.79", "lon": "37.53"}] if "Ходынский" in (params or {}).get("q", "")
                       else [])

    monkeypatch.setattr(geo.requests, "get", fake_get)

    assert geo.coords("Москва, Ходынский бульвар 4") == (55.79, 37.53)
    assert geo.coords("Москва, Ходынский бульвар 4") == (55.79, 37.53)
    assert geo.coords("кувырк-кувырк") is None
    assert geo.coords("кувырк-кувырк") is None
    assert len(calls) == 2, "оба адреса спрошены по одному разу"
