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

URL = "/static/moya-korzina.apk"
BUNDLED = os.path.join(ROOT, "app", "web", "static", "moya-korzina.apk")


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
