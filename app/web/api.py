"""API-дверь внутри веб-приложения: те же маршруты на основном порту.

Позволяет расширению и внутренним службам стучаться прямо в порт веб-приложения
(например, 8501) без необходимости держать отдельный сервер на 8765.
Поддерживает два вида входа:
1. Замок по секрету (workplace + secret в теле JSON) — для расширения.
2. Сессия веб-интерфейса (если запрос идёт из браузера человека) — без ввода секрета.
"""
from __future__ import annotations

import logging
from flask import Blueprint, jsonify, make_response, request

from app import api, collector, store_accounts, users
from app.web import auth

log = logging.getLogger(__name__)

bp = Blueprint("api", __name__, url_prefix="/api")


def _cors_response(resp):
    origin = request.headers.get("Origin") or "*"
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Max-Age"] = "600"
    resp.headers["Vary"] = "Origin"
    return resp


@bp.route("/<path:_subpath>", methods=["OPTIONS"])
@bp.route("", methods=["OPTIONS"])
def api_options(_subpath=""):
    resp = make_response("", 204)
    return _cors_response(resp)


def _authenticate(body: dict) -> str:
    """Определить рабочее место: из сессии Flask или по замку (workplace + secret)."""
    if body.get("workplace") and body.get("secret"):
        return api._unlock(body)

    phone = auth.current_phone()
    if phone:
        users.activate(phone)
        return phone

    return api._unlock(body)


@bp.route("/health", methods=["GET"])
def health():
    return _cors_response(jsonify({"ok": True, "service": "korzina-api"}))


@bp.route("/prices", methods=["POST"])
def prices():
    if not request.is_json:
        return _cors_response(make_response(
            jsonify({"ok": False, "error": "Тело запроса должно быть JSON"}), 415))

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _cors_response(make_response(
            jsonify({"ok": False, "error": "Тело запроса должно быть объектом JSON"}), 400))

    try:
        _authenticate(body)
        res = api.take_prices(body)
        return _cors_response(jsonify(res))
    except api.Refused as err:
        log.warning("отказ API /api/prices: %s (%s)", err.reason, request.remote_addr)
        return _cors_response(make_response(
            jsonify({"ok": False, "error": err.message}), err.status))
    except Exception as exc:
        log.exception("ошибка при обработке /api/prices")
        return _cors_response(make_response(
            jsonify({"ok": False, "error": str(exc)}), 500))


@bp.route("/cartplan", methods=["POST"])
def cartplan():
    if not request.is_json:
        return _cors_response(make_response(
            jsonify({"ok": False, "error": "Тело запроса должно быть JSON"}), 415))

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _cors_response(make_response(
            jsonify({"ok": False, "error": "Тело запроса должно быть объектом JSON"}), 400))

    try:
        _authenticate(body)
        res = api.take_cartplan(body)
        return _cors_response(jsonify(res))
    except api.Refused as err:
        log.warning("отказ API /api/cartplan: %s (%s)", err.reason, request.remote_addr)
        return _cors_response(make_response(
            jsonify({"ok": False, "error": err.message}), err.status))
    except Exception as exc:
        log.exception("ошибка при обработке /api/cartplan")
        return _cors_response(make_response(
            jsonify({"ok": False, "error": str(exc)}), 500))


@bp.route("/store_accounts/sync", methods=["POST"])
def store_sync():
    """Быстрая фиксация статуса входа / купонов / баллов без обязательного пакета цен."""
    if not request.is_json:
        return _cors_response(make_response(
            jsonify({"ok": False, "error": "Тело запроса должно быть JSON"}), 415))

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _cors_response(make_response(
            jsonify({"ok": False, "error": "Тело запроса должно быть объектом JSON"}), 400))

    try:
        _authenticate(body)
        res = collector.accept(body)
        return _cors_response(jsonify({"ok": True, **res}))
    except api.Refused as err:
        return _cors_response(make_response(
            jsonify({"ok": False, "error": err.message}), err.status))
    except Exception as exc:
        log.exception("ошибка при обработке /api/store_accounts/sync")
        return _cors_response(make_response(
            jsonify({"ok": False, "error": str(exc)}), 500))


@bp.route("/handoff", methods=["POST"])
def handoff_login():
    """Вход в магазин, снятый приложением на телефоне: {store, cookies: [{name, value}]}.

    Раньше телефон слал куки входа в АДРЕСЕ (/cabinet?vhod=…&phone=…): адрес оседает
    в журналах сервера и прокси, то есть сессия магазина хранилась бы там открытым
    текстом, а номер из адреса переключал базу — чужой вход можно было положить в
    чужое рабочее место. Теперь куки едут телом, а рабочее место — только то, чья
    сессия (приложение на телефоне входит в «Мою корзину» тем же номером) или чей
    секрет пришёл.
    """
    import json as _json

    from app.web.screens import cabinet

    body = request.get_json(silent=True) if request.is_json else None
    if not isinstance(body, dict):
        return _cors_response(make_response(
            jsonify({"ok": False, "error": "Тело запроса должно быть объектом JSON"}), 400))
    chain = str(body.get("store") or "").strip().lower()
    if not chain or not isinstance(body.get("cookies"), list):
        return _cors_response(make_response(
            jsonify({"ok": False, "error": "Нужны магазин (store) и куки входа (cookies)"}), 400))
    try:
        _authenticate(body)
        payload = {k: v for k, v in body.items() if k not in api.AUTH_FIELDS}
        res = cabinet._paste(chain, _json.dumps(payload, ensure_ascii=False))
        return _cors_response(make_response(jsonify(res), 200 if res.get("ok") else 400))
    except api.Refused as err:
        log.warning("отказ API /api/handoff: %s (%s)", err.reason, request.remote_addr)
        return _cors_response(make_response(
            jsonify({"ok": False, "error": err.message}), err.status))
    except Exception:  # noqa: BLE001
        log.exception("ошибка при обработке /api/handoff")
        return _cors_response(make_response(
            jsonify({"ok": False, "error": "Вход не сохранился — попробуйте ещё раз."}), 500))


def install(flask_app):
    flask_app.register_blueprint(bp)
