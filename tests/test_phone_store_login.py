"""Вход в магазины на телефоне: подключение через приложение, экраны connect и настройки.

ПРОВЕРЯЕТСЯ:
a) вошедший клиент шлёт POST /api/device/connected с json {"store": "magnit", "connected": true};
   после этого в /phone/settings Магнит стоит в разделе «Подключено», а плитки
   с data-open-store="magnit" среди «Осталось подключить» нет;
b) /phone/settings?saved=connected&store=magnit показывает «Подключено: Магнит»;
c) у плиток есть data-login и href="/phone/connect/magnit";
d) /phone/connect/magnit:
   - с User-Agent Android-браузера есть ссылка на APK и /cabinet?store=magnit;
   - с User-Agent iPhone ссылки на APK нет, /cabinet?store=magnit есть;
   - с User-Agent, где есть KorzinaApp/1.26, есть кнопка с data-login и нет ссылки на APK;
e) /phone/connect/nosuch отдаёт 404;
f) GET /static/korzina-device.js содержит login: login.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import users  # noqa: E402

PHONE = "79990000094"
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)
APP_UA = "Mozilla/5.0 (Linux; Android 14; Pixel 8) KorzinaApp/1.26"


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временных базах — как в tests/test_phone_cabinets.py."""
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
    from conftest import sign_in

    return sign_in(client, PHONE)


def text(answer) -> str:
    return answer.data.decode("utf-8")


def test_device_connected_marks_store_connected_in_settings(web):
    """a) Клиент шлёт POST /api/device/connected; Магнит в «Подключено», плитки нет в «Осталось подключить»."""
    enter(web)

    # До подключения Магнит в «Осталось подключить»
    before = text(web.get("/phone/settings"))
    assert 'data-open-store="magnit"' in before
    if "Осталось подключить" in before:
        waiting_before = before.split("Осталось подключить", 1)[1].split("</section>", 1)[0]
        assert 'data-open-store="magnit"' in waiting_before

    resp = web.post("/api/device/connected", json={"store": "magnit", "connected": True})
    assert resp.status_code == 200
    assert resp.get_json().get("ok") is True

    after = text(web.get("/phone/settings"))
    assert "Подключено" in after
    connected_part = after.split("Подключено", 1)[1].split("</section>", 1)[0]
    assert "Магнит" in connected_part

    if "Осталось подключить" in after:
        waiting_part = after.split("Осталось подключить", 1)[1].split("</section>", 1)[0]
        assert 'data-open-store="magnit"' not in waiting_part


def test_saved_connected_shows_store_name(web):
    """b) «Подключено: Магнит» после входа в приложении — и не по одному адресу без входа."""
    enter(web)
    page = text(web.get("/phone/settings?saved=connected&store=magnit"))
    assert "Подключено: Магнит" not in page, "сервер входа не видел — хвалиться нечем"

    web.post("/api/device/connected", json={"store": "magnit", "connected": True})
    page = text(web.get("/phone/settings?saved=connected&store=magnit"))
    assert "Подключено: Магнит" in page


def test_tiles_have_data_login_and_connect_href(web):
    """c) У плиток есть data-login и href="/phone/connect/magnit"."""
    enter(web)
    page = text(web.get("/phone/settings"))
    assert 'href="/phone/connect/magnit"' in page
    assert "data-login" in page
    assert 'data-open-store="magnit"' in page


def test_connect_screen_user_agents(web):
    """d) /phone/connect/magnit ветвление по User-Agent."""
    enter(web)

    # Android-браузер: есть ссылка на APK и /cabinet?store=magnit
    android_page = text(web.get("/phone/connect/magnit", headers={"User-Agent": ANDROID_UA}))
    assert "/static/moya-korzina.apk" in android_page
    assert "/cabinet?store=magnit" in android_page

    # iPhone: ссылки на APK нет, /cabinet?store=magnit есть
    iphone_page = text(web.get("/phone/connect/magnit", headers={"User-Agent": IPHONE_UA}))
    assert "/static/moya-korzina.apk" not in iphone_page
    assert "/cabinet?store=magnit" in iphone_page

    # Приложение KorzinaApp/1.26: есть кнопка с data-login, ссылки на APK нет
    app_page = text(web.get("/phone/connect/magnit", headers={"User-Agent": APP_UA}))
    assert "data-login" in app_page
    assert 'data-open-store="magnit"' in app_page
    assert "/static/moya-korzina.apk" not in app_page


def test_connect_unknown_store_returns_404(web):
    """e) /phone/connect/nosuch отдаёт 404."""
    enter(web)
    resp = web.get("/phone/connect/nosuch")
    assert resp.status_code == 404


def test_korzina_device_js_has_login(web):
    """f) GET /static/korzina-device.js содержит login: login."""
    resp = web.get("/static/korzina-device.js")
    assert resp.status_code == 200
    assert "login: login" in text(resp)
