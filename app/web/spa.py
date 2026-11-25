"""Раздача единого React SPA поверх Flask-бэкенда.

Старый многостраничный Jinja-сайт заменён единым современным SPA (телефон + ПК).
Собранные статические файлы берутся из `app/web/static/spa` (Docker-сборка) или
`dist/client` (локальная сборка Vite).
"""
from __future__ import annotations

import os

from flask import Blueprint, Flask, abort, send_from_directory

from app import config

bp = Blueprint("spa", __name__)


def _spa_dir() -> str:
    """Найти каталог со собранным SPA (index.html + assets/)."""
    candidates = [
        os.path.join(os.path.dirname(__file__), "static", "spa"),
        os.path.join(config.ROOT, "dist", "client"),
    ]
    for path in candidates:
        if os.path.isfile(os.path.join(path, "index.html")):
            return path
    return candidates[0]


@bp.get("/assets/<path:filename>")
def assets(filename: str):
    """Статические чанки Vite (JS/CSS) с долгим кэшем."""
    assets_dir = os.path.join(_spa_dir(), "assets")
    if not os.path.isdir(assets_dir):
        abort(404)
    resp = send_from_directory(assets_dir, filename)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@bp.get("/")
@bp.get("/login")
@bp.get("/basket")
@bp.get("/cheaper")
@bp.get("/orders")
@bp.get("/baskets")
@bp.get("/prices")
@bp.get("/settings")
@bp.get("/phone")
@bp.get("/phone/")
@bp.get("/phone/<path:_subpath>")
def index(_subpath: str = ""):
    """Главная точка входа нового интерфейса для всех маршрутов приложения."""
    folder = _spa_dir()
    if not os.path.isfile(os.path.join(folder, "index.html")):
        return (
            "Фронтенд SPA не собран. Выполните `npm run build` (или соберите Docker-образ).",
            503,
        )
    resp = send_from_directory(folder, "index.html")
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


def install(flask_app: Flask) -> None:
    flask_app.register_blueprint(bp)

    @flask_app.errorhandler(404)
    def _spa_fallback(_exc):
        from flask import jsonify, request

        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Маршрут не найден"}), 404
        if request.path.startswith(("/assets/", "/static/")):
            return _exc
        folder = _spa_dir()
        if os.path.isfile(os.path.join(folder, "index.html")):
            resp = send_from_directory(folder, "index.html")
            resp.headers["Cache-Control"] = "no-store, max-age=0"
            return resp
        return ("Страница не найдена", 404)


__all__ = ["bp", "install"]
