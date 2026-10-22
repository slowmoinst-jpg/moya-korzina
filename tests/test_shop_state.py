"""Сохранённый вход в сеть должен восстанавливаться, а не падать на кривой куке.

Замер 20.10.2026 на живом приложении: экран «Кабинеты» показывал
«Browser.new_context: Error setting storage state: Protocol error (Storage.setCookies):
Invalid cookie fields» — вход в Магнит не восстанавливался. Такое состояние приходит от
разных инструментов, и ронять из-за одной куки весь вход нельзя.
"""
from __future__ import annotations

import pytest

from app.shopbrowser import store


def test_a_cookie_without_domain_gets_the_network_domain():
    """Кука без домена — не мусор: это кука этой сети, домен известен из справочника.

    Иначе вход «есть», а браузеру подставлять нечего: Chromium требует домен
    (замер 20.10.2026, «Invalid cookie fields»).
    """
    cleaned = store.clean_state({"cookies": [
        {"name": "session", "value": "1", "domain": "", "path": "/"},
        {"name": "good", "value": "2", "domain": ".magnit.ru", "path": "/"},
    ]}, chain="magnit")

    assert [c["name"] for c in cleaned["cookies"]] == ["session", "good"]
    assert cleaned["cookies"][0]["domain"].endswith("magnit.ru")


def test_a_cookie_without_a_name_is_dropped():
    """Без имени кука бессмысленна: её нечего подставлять."""
    cleaned = store.clean_state({"cookies": [
        {"value": "1", "domain": ".magnit.ru"},
        {"name": "good", "value": "2", "domain": ".magnit.ru"},
    ]}, chain="magnit")

    assert [c["name"] for c in cleaned["cookies"]] == ["good"]


def test_bad_fields_are_normalised_not_crashed():
    """Пустой sameSite, expires строкой, нет path — приводим к безопасному."""
    cleaned = store.clean_state({"cookies": [{
        "name": "a", "value": "b", "domain": ".magnit.ru",
        "sameSite": "", "expires": "когда-то", "httpOnly": "да", "secure": 1,
    }]})

    cookie = cleaned["cookies"][0]
    assert cookie["sameSite"] == "Lax"
    assert cookie["expires"] == -1.0, "сессионная кука, а не выдуманное время"
    assert cookie["path"] == "/"
    assert cookie["httpOnly"] is True and cookie["secure"] is True


def test_known_same_site_values_survive():
    cleaned = store.clean_state({"cookies": [
        {"name": "a", "value": "1", "domain": ".x.ru", "sameSite": "none"},
        {"name": "b", "value": "2", "domain": ".x.ru", "sameSite": "Strict"},
    ]})

    assert [c["sameSite"] for c in cleaned["cookies"]] == ["None", "Strict"]


def test_local_storage_is_kept_and_garbage_dropped():
    cleaned = store.clean_state({"origins": [
        {"origin": "https://magnit.ru", "localStorage": [
            {"name": "token", "value": "x"}, {"value": "без имени"}]},
        {"origin": ""},
    ]})

    assert cleaned["origins"] == [{"origin": "https://magnit.ru",
                                   "localStorage": [{"name": "token", "value": "x"}]}]


def test_state_without_any_named_cookie_loads_without_them(monkeypatch):
    """Совсем нечего подставлять — состояние есть (вход сохранён), кук нет."""
    monkeypatch.setattr(store, "_all", lambda: {"magnit": {"state": {"cookies": [
        {"value": "1", "domain": ".magnit.ru"}]}}})

    assert store.load("magnit") == {"cookies": [], "origins": []}


def test_a_good_state_is_loaded_cleaned(monkeypatch):
    monkeypatch.setattr(store, "_all", lambda: {"magnit": {"state": {"cookies": [
        {"name": "x", "value": "1", "domain": ".magnit.ru"}]}}})

    got = store.load("magnit")

    assert got is not None and got["cookies"][0]["sameSite"] == "Lax"


def test_save_keeps_a_state_it_cannot_clean(monkeypatch):
    """Терять вход из-за строгости нельзя: что не почистилось — сохраняем как есть.

    От кривых кук защищает чтение (`load` чистит всегда), а сохранение должно быть
    всеядным: вход в сеть дороже аккуратности формата.
    """
    written = {}
    monkeypatch.setattr(store, "_all", lambda: {})
    monkeypatch.setattr(store, "_write", lambda data: written.update(data))

    store.save("magnit", {"cookies": [{"name": "x", "value": "1", "domain": ""}]})

    assert written["magnit"]["state"]["cookies"][0]["name"] == "x"


def test_save_cleans_before_writing(monkeypatch):
    written = {}
    monkeypatch.setattr(store, "_all", lambda: {})
    monkeypatch.setattr(store, "_write", lambda data: written.update(data))

    store.save("magnit", {"cookies": [{"name": "x", "value": "1", "domain": ".magnit.ru",
                                       "sameSite": ""}]})

    assert written["magnit"]["state"]["cookies"][0]["sameSite"] == "Lax"
