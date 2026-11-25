"""Кнопка «Скачать APK» отдаёт свежую сборку из тома данных, а без неё — прежний файл.

16.10.2026 владелец скачал кнопкой версию 1.0 от 26.09.2026 и не нашёл в ней записи
корзины: файл в git никто не обновлял. Здесь проверяется, что прежний адрес теперь
отдаёт сборку из data/apk, что без неё ничего не ломается и что входа не требуется.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.web.apk import VERSION_FILE, latest_version, update_for, version_key  # noqa: E402

URL = "/static/moya-korzina.apk"
APP_UA = "Mozilla/5.0 KorzinaApp/{} Mobile"
BANNER = "Вышла версия {} приложения — установите её поверх этой."
BUNDLED = os.path.join(ROOT, "app", "web", "static", "moya-korzina.apk")


@pytest.mark.parametrize("fresh_artifact", [False, True])
def test_paused_distribution_hides_download_and_update(web, fresh_artifact):
    client, root = web
    folder = root / "data" / "apk"
    folder.mkdir(parents=True)
    (folder / "distribution.paused").touch()
    (folder / VERSION_FILE).write_text("1.66\n", encoding="utf-8")
    if fresh_artifact:
        (folder / "moya-korzina.apk").write_bytes(b"PK-paused")

    download = client.get(URL)
    assert download.status_code == 503
    assert download.headers["Cache-Control"] == "no-store"
    assert latest_version() is None
    me = client.get("/api/auth/me", headers={"User-Agent": APP_UA.format("1.65")})
    assert me.status_code == 200
    assert me.get_json().get("apkUpdate") is None


@pytest.fixture
def web(tmp_path, monkeypatch):
    from app import users

    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client(), tmp_path
    users.deactivate()


def test_without_a_fresh_build_the_old_file_is_given(web):
    client, _ = web
    answer = client.get(URL)

    assert answer.status_code == 200, "скачивание открыто без входа"
    assert answer.mimetype == "application/vnd.android.package-archive"
    with open(BUNDLED, "rb") as fh:
        assert answer.data == fh.read()


def test_fresh_build_from_the_data_volume_wins(web):
    client, root = web
    folder = root / "data" / "apk"
    folder.mkdir(parents=True)
    (folder / "moya-korzina.apk").write_bytes(b"PK-fresh-1.2")

    answer = client.get(URL)

    assert answer.status_code == 200
    assert answer.data == b"PK-fresh-1.2"
    assert "no-cache" in (answer.headers.get("Cache-Control") or "") \
        or "max-age=0" in (answer.headers.get("Cache-Control") or ""), \
        "телефон не должен держать прошлую сборку в кэше"


def test_version_key_ordering_and_invalid():
    assert version_key("1.12.1") < version_key("1.17") < version_key("1.18") \
        < version_key("1.18.1")
    # Версия приходит в User-Agent от кого угодно: мусор — None, а не ошибка страницы.
    for junk in ("мусор", "1.foo", "", None, "1.²", "1." + "9" * 5000, "1..2"):
        assert version_key(junk) is None, junk


def test_latest_version_and_update_for(web):
    client, root = web
    folder = root / "data" / "apk"
    folder.mkdir(parents=True, exist_ok=True)

    # Без version.txt
    assert latest_version() is None
    assert update_for("1.17") is None

    # version.txt с версией 1.18
    (folder / VERSION_FILE).write_text("1.18\n", encoding="utf-8")
    assert latest_version() == "1.18"

    # Приложение 1.17 не умеет скачивать (can_download=False)
    up_17 = update_for("1.17")
    assert up_17 == {"version": "1.18", "can_download": False}

    # Приложение той же или большей версии обновления не просит
    assert update_for("1.18") is None
    assert update_for("1.19") is None

    # Обычный браузер (пустая версия) или мусор
    assert update_for(None) is None
    assert update_for("") is None
    assert update_for("мусор") is None

    # version.txt с версией 1.19: 1.18 уже умеет скачивать
    (folder / VERSION_FILE).write_text("1.19\n", encoding="utf-8")
    up_18 = update_for("1.18")
    assert up_18 == {"version": "1.19", "can_download": True}


def test_utf16_version_file_is_ignored(web):
    _, root = web
    folder = root / "data" / "apk"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / VERSION_FILE).write_text("1.18", encoding="utf-16")
    assert latest_version() is None

@pytest.mark.parametrize(("user_agent", "cookie", "can_download"), [
    (APP_UA.format("1.17"), None, False),
    (APP_UA.format("1.33"), "1.62", True),
    (APP_UA.format("1.33"), "1.64", None),
    (APP_UA.format("1.33"), "1.65", None),
    ("Mozilla/5.0 Android Mobile", "1.62", None),
])
def test_spa_auth_me_offers_update_only_to_older_android(web, user_agent, cookie, can_download):
    client, root = web
    folder = root / "data" / "apk"
    folder.mkdir(parents=True)
    (folder / VERSION_FILE).write_text("1.64", encoding="utf-8")
    (folder / "moya-korzina.apk").write_bytes(b"PK-test")
    if cookie is not None:
        client.set_cookie("kz_app", cookie)

    response = client.get("/api/auth/me", headers={"User-Agent": user_agent})
    assert response.status_code == 200
    update = response.get_json().get("apkUpdate")
    if can_download is None:
        assert update is None
    else:
        assert update == {
            "version": "1.64",
            "canDownload": can_download,
            "url": "http://localhost" + URL,
        }
