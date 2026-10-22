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


@bp.route("/cart_record", methods=["POST"])
def cart_record():
    """Запись запросов корзины магазина, снятая приложением на телефоне.

    {store, entries: [{method, host, path, query, headers, body, status, response}]}.
    Принимается только от вошедшего в «Мою корзину» (сессия) или по замку: запись —
    шаг к наполнению его же кабинетов. Что хранится и чего нет — app/cartrecord.py.
    """
    from app import cartrecord

    body = request.get_json(silent=True) if request.is_json else None
    if not isinstance(body, dict):
        return _cors_response(make_response(
            jsonify({"ok": False, "error": "Тело запроса должно быть объектом JSON"}), 400))
    try:
        _authenticate(body)
        payload = {k: v for k, v in body.items() if k not in api.AUTH_FIELDS}
        return _cors_response(jsonify(cartrecord.accept(payload)))
    except api.Refused as err:
        log.warning("отказ API /api/cart_record: %s (%s)", err.reason, request.remote_addr)
        return _cors_response(make_response(
            jsonify({"ok": False, "error": err.message}), err.status))
    except ValueError as err:
        return _cors_response(make_response(jsonify({"ok": False, "error": str(err)}), 400))
    except Exception:  # noqa: BLE001
        log.exception("ошибка при обработке /api/cart_record")
        return _cors_response(make_response(
            jsonify({"ok": False, "error": "Запись не сохранилась — попробуйте ещё раз."}), 500))


@bp.route("/carts/report", methods=["POST"])
def carts_report():
    """Отчёт исполнителя о сборке корзины в одной сети: {store, landed, missing, total, …}.

    Строка сети на экране «Сборка корзин» одна для сервера и телефона (app/cartrun.py),
    и эта дверь кладёт в неё отчёт, пришедший снаружи. Приложение 1.4 отчитывается
    своей дверью /api/device/cartreport (app/phonecart.py), и та сама кладёт итог в
    эту же строку. Принимается только от вошедшего в «Мою корзину» (сессия) или по
    замку — строка лежит в его же базе.
    """
    from app import cartrun

    body = request.get_json(silent=True) if request.is_json else None
    if not isinstance(body, dict):
        return _cors_response(make_response(
            jsonify({"ok": False, "error": "Тело запроса должно быть объектом JSON"}), 400))
    try:
        _authenticate(body)
        payload = {k: v for k, v in body.items() if k not in api.AUTH_FIELDS}
        row = cartrun.accept(payload, by=cartrun.DEVICE)
        return _cors_response(jsonify({"ok": True, "row": row.as_dict()}))
    except api.Refused as err:
        log.warning("отказ API /api/carts/report: %s (%s)", err.reason, request.remote_addr)
        return _cors_response(make_response(
            jsonify({"ok": False, "error": err.message}), err.status))
    except ValueError as err:
        return _cors_response(make_response(jsonify({"ok": False, "error": str(err)}), 400))
    except Exception:  # noqa: BLE001
        log.exception("ошибка при обработке /api/carts/report")
        return _cors_response(make_response(
            jsonify({"ok": False, "error": "Отчёт не сохранился — попробуйте ещё раз."}), 500))


def _device_call(name: str, work):
    """Общая дверь двух запросов телефона: JSON, вход «Моей корзины», понятный отказ."""
    body = request.get_json(silent=True) if request.is_json else None
    if not isinstance(body, dict):
        return _cors_response(make_response(
            jsonify({"ok": False, "error": "Тело запроса должно быть объектом JSON"}), 400))
    try:
        _authenticate(body)
        payload = {k: v for k, v in body.items() if k not in api.AUTH_FIELDS}
        return _cors_response(jsonify(work(payload)))
    except api.Refused as err:
        log.warning("отказ API /api/device/%s: %s (%s)", name, err.reason, request.remote_addr)
        return _cors_response(make_response(
            jsonify({"ok": False, "error": err.message}), err.status))
    except ValueError as err:
        return _cors_response(make_response(jsonify({"ok": False, "error": str(err)}), 400))
    except Exception:  # noqa: BLE001
        log.exception("ошибка при обработке /api/device/%s", name)
        return _cors_response(make_response(
            jsonify({"ok": False, "error": "Сервер не справился — попробуйте ещё раз."}), 500))


@bp.route("/device/cartplan", methods=["POST"])
def device_cartplan():
    """Что телефону положить в корзину сети: {store} → наряд и правила кнопок (app/phonecart.py)."""
    from app import phonecart

    return _device_call("cartplan", phonecart.plan)


@bp.route("/device/cartreport", methods=["POST"])
def device_cartreport():
    """Отчёт телефона: что легло и какой итог показала корзина сети (app/phonecart.py)."""
    from app import phonecart

    return _device_call("cartreport", phonecart.accept_report)


@bp.route("/device/cartprogress", methods=["POST"])
def device_cartprogress():
    """Ход сборки телефона в сети: «кладу 3 из 9» — строка «Сборки корзин» живёт его пульсом."""
    from app import phonecart

    return _device_call("cartprogress", phonecart.progress)


@bp.route("/device/cards", methods=["POST"])
def device_cards():
    """Карточки витрины, прочитанные телефоном: {store, cards} → товары в единой базе.

    Единственная дорога каталога для сетей, куда сервер не пробивается (Пятёрочка,
    Самокат — замеры 18–19.10.2026): витрину видит телефон человека.
    """
    from app import phonecart

    return _device_call("cards", phonecart.accept_cards)


def _number(value) -> float | None:
    """Число из тела запроса. None — не прислали, пусто или мусор."""
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _find_in_store(payload: dict) -> dict:
    """Поиск товара в сети сервером. Точка человека — из его настроек, если не прислана.

    Телефон ищет в витрине и упирается в стены (Лента и Дикси — 403, Магнит — пустая
    SPA), а сервер ходит рабочими дорогами (app/storefind.py). Поэтому строка наряда
    без подтверждённого артикула сначала спрашивает эту дверь. Пустые поля и сбой
    сети — ответ с `ok: false` и причиной по-русски, а не молчание и не 500.
    """
    from app import location, storefind

    store = str(payload.get("store") or "").strip().lower()
    name = " ".join(str(payload.get("name") or "").split())
    if not store or not name:
        return {"ok": False, "why": "не указана сеть или название"}

    lat = _number(payload.get("lat"))
    lon = _number(payload.get("lon"))
    address = str(payload.get("address") or "").strip() or None
    # Точку человек не выбирает — она живёт в его настройках (app/location.py). Берём
    # её, только когда не прислали ни широту, ни долготу: половинку координат,
    # склеенную с чужой точкой, сети отдавать нельзя.
    if lat is None and lon is None:
        try:
            point = location.point()
        except Exception:  # noqa: BLE001 — нет базы: ищем без точки
            point = None
        if point:
            lat, lon = float(point[0]), float(point[1])
    if address is None:
        try:
            address = location.address() or None
        except Exception:  # noqa: BLE001 — нет базы: ищем без адреса
            address = None

    try:
        return storefind.find(store, name, pack_g=_number(payload.get("pack_g")),
                              lat=lat, lon=lon, address=address)
    except Exception:  # noqa: BLE001 — сеть/база: причина для телефона, не падение двери
        log.warning("поиск «%s» в сети %s не удался", name[:40], store, exc_info=True)
        return {"ok": False, "why": "сервер не смог найти"}


@bp.route("/device/find", methods=["POST"])
def device_find():
    """Поиск товара в сети сервером: {store, name, pack_g?, product_id?, lat?, lon?, address?}.

    Ответ — как у app/storefind.find: {ok, exact, store, found: [...], why, cached}.
    Телефон кладёт товар только по уверенному `found` с ссылкой на карточку.
    """
    return _device_call("find", _find_in_store)


def _cart_link_for_phone(payload: dict) -> dict:
    """Ссылка на готовую корзину сети, собранная сервером (Z9, договор 21.10.2026).

    ЗАЧЕМ. У Ленты телефон получает 403 на сайте, и строка наряда в корзину не ложится.
    MCP Ленты умеет `storefront_cart_link_create`: сервер собирает корзину сам и отдаёт
    ссылку, где позиции уже лежат с количеством (app/storefind.cart_link). Телефон
    открывает её последним шагом наряда и говорит в отчёте, пустил ли сайт с этого
    подключения. Точку и адрес берём из настроек человека, если их не прислали: телефон
    их не выбирает, они живут в app/location.py.

    `items` — строки наряда: `[{product_id, name, qty, pack_g?}]`. Ответ уходит телефону
    КАК ЕСТЬ: {ok, url, matched, missing, why, note}. Исключение сети/базы — причина
    по-русски, а не пятисотая: телефон должен закрыть наряд, а не зависнуть.
    """
    from app import location, storefind

    store = str(payload.get("store") or "").strip().lower()
    raw_items = payload.get("items")
    items = [item for item in raw_items if isinstance(item, dict)] \
        if isinstance(raw_items, list) else []
    if not store or not items:
        return {"ok": False, "why": "не указана сеть или позиции"}

    lat = _number(payload.get("lat"))
    lon = _number(payload.get("lon"))
    address = str(payload.get("address") or "").strip() or None
    # Половинку координат не склеиваем с сохранённой точкой: сети нужна точка целиком.
    if lat is None and lon is None:
        try:
            point = location.point()
        except Exception:  # noqa: BLE001 — нет базы: собираем без точки
            point = None
        if point:
            lat, lon = float(point[0]), float(point[1])
    if address is None:
        try:
            address = location.address() or None
        except Exception:  # noqa: BLE001 — нет базы: собираем без адреса
            address = None

    try:
        return storefind.cart_link(store, items, lat=lat, lon=lon, address=address)
    except Exception:  # noqa: BLE001 — сеть/база: причина для телефона, не падение двери
        log.warning("ссылку на корзину %s собрать не удалось", store, exc_info=True)
        return {"ok": False, "why": "сервер не смог собрать ссылку"}


@bp.route("/device/cartlink", methods=["POST"])
def device_cartlink():
    """Ссылка на готовую корзину сети: {store, items: [{product_id, name, qty, pack_g?}],
    lat?, lon?, address?}.

    Ответ — как у app/storefind.cart_link: {ok, url, matched, missing, why, note}.
    Телефон открывает ссылку последним шагом наряда Ленты; остальные сети ссылку не
    просят. `ok: false` — тоже ответ: причина и не вошедшие позиции доходят до отчёта.
    """
    return _device_call("cartlink", _cart_link_for_phone)


def install(flask_app):
    flask_app.register_blueprint(bp)
