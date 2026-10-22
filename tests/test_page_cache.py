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
