import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.shopbrowser import live

def test_live_availability(monkeypatch):
    """На Windows окно есть всегда, на Linux — только с графическим окружением.

    Раньше тест ждал True на любой машине и падал на сервере и в облачной
    проверке, где DISPLAY нет, — то есть проверял машину, а не код.
    """
    monkeypatch.setattr(os, "name", "nt")
    assert live.is_live_available() is True
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setenv("DISPLAY", ":99")
    assert live.is_live_available() is True
    monkeypatch.delenv("DISPLAY")
    assert live.is_live_available() is False

def test_live_status_idle():
    st = live.get_live_status("magnit", "79990000001")
    assert st["ok"] is True
    assert st["status"] == "idle"

def test_start_invalid_chain():
    res = live.start_live_login("invalid_store_xyz", "79990000001")
    assert res["ok"] is False
    assert "не поддерживается" in res["error"]

def test_stop_not_running():
    res = live.stop_live_login("magnit", "79990000001")
    assert res["ok"] is True
    assert res["status"] == "closed"

def test_save_not_running():
    res = live.save_live_login("magnit", "79990000001")
    assert res["ok"] is False
    assert "не открыто" in res["error"]


class _Page:
    """Страница живого окна: видимый текст вошедшего человека, без браузера."""

    def __init__(self, text: str):
        self.text = text

    def is_closed(self) -> bool:
        return False

    def inner_text(self, selector: str, timeout: int = 0) -> str:
        return self.text


class _Context:
    def storage_state(self) -> dict:
        return {"cookies": [{"name": "sid", "value": "1", "domain": ".magnit.ru", "path": "/"}],
                "origins": []}


def test_the_live_window_saves_the_login_and_its_points(tmp_path, monkeypatch):
    """Вход из живого окна сохраняется вместе с баллами, прочитанными со страницы.

    Сверка с концепцией 2.0 17.10.2026 нашла чтением: сохранение звало
    signals.points(chain, page_text), а разбор баллов берёт один текст страницы.
    TypeError ловился общим except, и вместо сохранённого входа возвращалась
    ошибка — ни куки, ни подключения, ни баллов. Живьём это не воспроизводилось;
    тест идёт той же дорогой без браузера: окно подменено страницей с текстом
    вошедшего человека.
    """
    from app import store_accounts, users
    from app.shopbrowser import store as shopstore

    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    phone = "79990000001"
    sess = {"chain": "magnit", "phone": phone, "context": _Context(),
            "page": _Page("Профиль Бонусы 340 бонусов Выйти"), "browser": None, "pw": None}

    res = live._capture_and_save(sess)

    assert res["ok"] is True, res
    assert res["points"] == 340.0
    users.open_workspace(phone)
    try:
        assert shopstore.about("magnit")["points"] == 340.0
        assert store_accounts.connection("magnit").connected is True
        assert store_accounts.connection("magnit").points == 340.0
    finally:
        users.deactivate()
