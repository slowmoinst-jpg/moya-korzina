"""Приложение для телефона: кнопка «Скачать APK» отдаёт свежую сборку, а не ту, что в git.

ЗАЧЕМ. 16.10.2026 владелец поставил приложение кнопкой «Скачать APK» и получил
версию 1.0 от 26.09.2026. В ней есть только «Сохранить вход», а запись корзины
появилась в 1.2. Кнопка отдавала app/web/static/moya-korzina.apk — файл из git, который
никто не обновлял. Сборка идёт локально в Docker и ложится в android/, а класть APK
в git нельзя: в нём зашит адрес сервера, а репозиторий публичный.

КАК. Свежая сборка кладётся в том данных, в data/apk/moya-korzina.apk: она переживает
выкладку и в git не попадает. Прежний адрес /static/moya-korzina.apk отдаёт её, если
она есть, и файл из git — если нет. Шаблоны и ссылки адрес не меняют. Кладут сборку
так (android/app/README.md, «Раздача»):

    scp android/moya-korzina-debug.apk korzina:/tmp/korzina.apk
    ssh korzina 'docker exec korzina mkdir -p /app/data/apk &&
        docker cp /tmp/korzina.apk korzina:/app/data/apk/moya-korzina.apk && rm /tmp/korzina.apk'

Скачивание открыто без входа, как и раньше: приложение ставят до того, как входят.
"""
from __future__ import annotations

import os

from flask import Blueprint, current_app, send_file, send_from_directory

from app import config
from app.web import device

APK_NAME = "moya-korzina.apk"
APK_TYPE = "application/vnd.android.package-archive"
# Версия раздаваемой сборки. Кладёт tools/build_apk.ps1 рядом с APK и ПОСЛЕ него: плашка
# «вышла новая версия» не должна звать к сборке, которой ещё нет.
VERSION_FILE = "version.txt"
# Первая версия, которая умеет скачивать файлы (DownloadListener в MainActivity). Старшим
# плашка даёт кнопку, младшим — адрес сборки для браузера телефона.
DOWNLOADS_FROM = (1, 18)

bp = Blueprint("apk", __name__)


def fresh_path() -> str | None:
    """Свежая сборка из тома данных. Нет её — None, и отдаётся файл из git."""
    path = os.path.join(config.ROOT, "data", "apk", APK_NAME)
    return path if os.path.isfile(path) else None


def latest_version() -> str | None:
    """Версия раздаваемой сборки из data/apk/version.txt; нет файла или он пуст — None."""
    path = os.path.join(config.ROOT, "data", "apk", VERSION_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except (OSError, ValueError):
        # Файл не в UTF-8 (PowerShell 5.1 пишет UTF-16) иначе ронял бы ошибкой 500 каждую
        # страницу приложения.
        return None


def version_key(text: str | None) -> tuple[int, ...] | None:
    """«1.12.1» → (1, 12, 1); None, если это не числа через точку.

    Версия приезжает в User-Agent, то есть от кого угодно: «1.²» (isdigit без isascii
    пропустил бы) или тысячи цифр (int их не возьмёт) уронили бы каждую страницу.
    """
    parts = (text or "").strip().split(".")
    if not all(p.isascii() and p.isdigit() and len(p) <= 6 for p in parts):
        return None
    return tuple(int(p) for p in parts)


def update_for(app_version: str | None) -> dict | None:
    """Плашке: {"version", "can_download"}, если приложение старше раздаваемой сборки."""
    mine = version_key(app_version)
    if mine is None:                        # обычный браузер: файл и не читаем
        return None
    latest = latest_version()
    newest = version_key(latest)
    if newest is None or mine >= newest:
        return None
    return {"version": latest, "can_download": mine >= DOWNLOADS_FROM}


@bp.route("/static/" + APK_NAME)
def download():
    # max_age=0: телефон не должен держать в кэше прошлую сборку под тем же адресом.
    path = fresh_path()
    if path:
        return send_file(path, mimetype=APK_TYPE, as_attachment=True,
                         download_name=APK_NAME, max_age=0)
    return send_from_directory(current_app.static_folder, APK_NAME, mimetype=APK_TYPE,
                               as_attachment=True, download_name=APK_NAME, max_age=0)


def install(flask_app) -> None:
    flask_app.register_blueprint(bp)

    @flask_app.context_processor
    def _apk_update():
        return {"apk_update": update_for(device.app_version())}
