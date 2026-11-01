"""Страницы не кэшируются: иначе телефон показывает вчерашнюю.

ЗАМЕР 20.10.2026. Экран «Кабинеты» в приложении показывал красную плашку
«Не вышло: … Invalid cookie fields», а в журнале сервера за сутки не было ни одной такой
ошибки — WebView отдал старую страницу из своего кэша.
"""
from __future__ import annotations

import pytest

from app.web import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "basket.db"))
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_a_page_is_never_cached(client):
    answer = client.get("/", follow_redirects=True)

    assert answer.status_code == 200
    assert "no-store" in answer.headers.get("Cache-Control", "")
    assert answer.headers.get("Pragma") == "no-cache"


def test_static_is_not_forbidden_to_cache(client):
    """Статике кэш нужен: её мы не трогаем, иначе каждая картинка едет заново."""
    answer = client.get("/static/korzina.css")

    assert answer.status_code == 200
    assert "no-store" not in answer.headers.get("Cache-Control", "")


def test_a_stamped_static_address_is_cached_for_a_year(client):
    """Адрес с отпечатком меняется вместе с файлом (static_v), поэтому его не переспрашивают:
    экран «Где дешевле» перезагружается раз в 5 секунд (31.10.2026, «работает медленно»)."""
    answer = client.get("/static/phone.css?v=0123456789")

    assert answer.status_code == 200
    cache = answer.headers.get("Cache-Control", "")
    assert "max-age=31536000" in cache and "immutable" in cache


def test_a_static_address_without_a_stamp_is_still_revalidated(client):
    """Без отпечатка файл может смениться под тем же адресом — вечный кэш ему не положен."""
    answer = client.get("/static/phone.css")

    assert answer.status_code == 200
    assert "immutable" not in answer.headers.get("Cache-Control", "")
