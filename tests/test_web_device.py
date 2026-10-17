"""Мост сайта и телефона: серверная половина (шаг 1 единой архитектуры).

Проверяется ровно то, от чего зависит работа кнопки «Войти» в приложении:
сервер узнаёт телефон по метке в User-Agent с первого запроса (кнопка рисуется
сразу, без мигания), обычный браузер кнопки моста не получает (мёртвая кнопка
хуже её отсутствия), а скрипт-обёртка моста отдаётся любому клиенту — она
нужна и телефону, и страницам, где её просто спросят available().
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import users  # noqa: E402

PHONE_UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 KorzinaApp/1.3"


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временных базах — тот же приём, что в tests/test_web.py."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    users.deactivate()


def enter(client, phone="79990000001"):
    return client.post("/login", data={"phone": phone, "next": "/"})


# ---------- узнавание по User-Agent ----------
def test_version_is_read_from_the_mark():
    from flask import Flask, request
    from app.web import device

    app = Flask(__name__)

    @app.get("/")
    def probe():
        return {"version": device.app_version(), "phone": device.on_phone()}

    client = app.test_client()
    answer = client.get("/", headers={"User-Agent": PHONE_UA})
    assert answer.get_json() == {"version": "1.3", "phone": True}

    answer = client.get("/", headers={"User-Agent": "Mozilla/5.0"})
    assert answer.get_json() == {"version": None, "phone": False}


def test_a_bare_word_without_the_slash_is_not_a_mark():
    """Слово KorzinaApp в чужой строке не должно делать браузер телефоном."""
    from flask import Flask
    from app.web import device

    app = Flask(__name__)

    @app.get("/")
    def probe():
        return {"phone": device.on_phone()}

    client = app.test_client()
    answer = client.get("/", headers={"User-Agent": "KorzinaApp"})
    assert answer.get_json()["phone"] is False


# ---------- кнопка моста на «Кабинетах» ----------
def test_accounts_offer_the_bridge_inside_the_app(web):
    enter(web)
    answer = web.get("/accounts", headers={"User-Agent": PHONE_UA})
    assert answer.status_code == 200
    page = answer.data.decode("utf-8")
    # Кнопка моста и отметка страницы: две половины одного узнавания.
    assert 'data-open-store="magnit"' in page
    assert 'data-device="phone"' in page
    # Окно на сервере не убрано: сбор цен и передача корзины живут в нём.
    assert "Окно на сервере" in page


def test_accounts_stay_quiet_in_a_plain_browser(web):
    """В обычном браузере кнопки моста нет: мёртвая кнопка хуже её отсутствия."""
    enter(web)
    answer = web.get("/accounts")
    assert answer.status_code == 200
    page = answer.data.decode("utf-8")
    assert "data-open-store" not in page
    assert 'data-device="web"' in page


def test_bridge_script_is_served(web):
    answer = web.get("/static/korzina-device.js")
    assert answer.status_code == 200
    body = answer.data.decode("utf-8")
    assert "KorzinaDevice" in body and "openStore" in body
