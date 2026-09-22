"""Кто пришёл и чья база открыта. Самое тонкое место переезда со Streamlit.

В Streamlit всё было просто: скрипт исполнялся заново на каждое действие, и
users.activate(phone) вызывался в начале каждого прогона. Обычный веб-сервер
устроен иначе, и разница опасна ровно в одном месте.

ПОЧЕМУ ЭТО ОПАСНО. Путь к базе человека живёт в contextvars (app/users.py,
config.set_db_override). Контекст привязан к потоку, а waitress держит ПУЛ
потоков и раздаёт их запросам по кругу. Значит поток, только что обслуживший
Петра, через секунду достанется Ивану — и если не убрать за собой, Иван откроет
базу Петра. Увидеть это в интерфейсе нельзя: чужие чеки выглядят как свои,
просто их больше или меньше, чем помнится.

Отсюда правило, которое здесь и закреплено: база выбирается в начале КАЖДОГО
запроса и снимается в конце КАЖДОГО запроса, в teardown, который Flask зовёт
даже когда обработчик упал с ошибкой. Не «обычно снимается», а всегда.

ВХОД ПОКА БЕЗ ПОДТВЕРЖДЕНИЯ — решение владельца 16.09.2026: по номеру телефона,
без пароля и без кода. Кто знает номер, тот и войдёт; на экране входа об этом
сказано словами. Здесь это повторено не для красоты: перенося вход на обычный
сайт, легко подумать, что раз появилась кука — появилась и защита. Не появилась.
Кука лишь помнит ответ на вопрос «кто вы», а проверки этого ответа по-прежнему нет.

ЧТО ЕСТЬ В КУКЕ. Только номер и метка времени входа, подписанные ключом сервера
(app/web/__init__.py, secret_key). Ни базы, ни путей, ни токенов магазинов: всё
это лежит в базе человека и наружу не ходит. Подделать номер в куке нельзя —
подпись не сойдётся; украсть куку целиком можно, и от этого спасёт только HTTPS,
которого у приложения пока нет (DEPLOY.md, раздел «что помнить»).
"""
from __future__ import annotations

import functools
import logging
from datetime import datetime

from flask import Flask, g, redirect, request, session, url_for

from app import users

log = logging.getLogger(__name__)

KEY_PHONE = "phone"
KEY_SINCE = "since"

# Страницы, куда пускают без входа. Список короткий нарочно: всё остальное
# открывает чью-то базу, и пускать туда «пока без номера» нельзя.
OPEN_ENDPOINTS = {"auth.login", "auth.enter", "auth.demo", "static"}

# У двери API СВОЙ замок, и через этот она проходить не должна.
#
# Расширение приходит не из браузера человека, а из браузера как программа: куки
# сессии у него нет и быть не может, зато есть номер рабочего места и секрет — в
# ТЕЛЕ запроса (app/web/api._authenticate → app/api._unlock). Пропусти мы её через
# общую проверку — запрос уезжал бы переводом на /login, fetch послушно шёл бы за
# переводом и получал страницу входа с кодом 200. Расширение сочло бы это ответом
# приложения: пакет цен «принят» молча в никуда, наряд «не найден» всегда. Иначе
# говоря, связка приложения с расширением не работала бы вовсе и не жаловалась.
#
# Открывать это безопасно ровно потому, что замок остаётся: без верного секрета
# дверь отвечает 401 сама, а /api/health отвечает всем — выкладке надо проверять
# живость, не зная ничьих секретов.
API_PREFIX = "api."


def current_phone() -> str | None:
    """Номер из куки или None. Ничего не открывает и базу не трогает."""
    raw = session.get(KEY_PHONE)
    if not isinstance(raw, str):
        return None
    return users.normalize_phone(raw)


def install(flask_app: Flask) -> None:
    """Повесить на приложение вход, выбор базы и снятие её после запроса."""
    from app.web.login import bp as login_bp

    flask_app.register_blueprint(login_bp)

    @flask_app.before_request
    def _pick_workspace():
        """Выбрать базу того, кто пришёл. Без номера — на страницу входа."""
        endpoint = request.endpoint or ""
        g.phone = current_phone()
        if g.phone:
            users.open_workspace(g.phone)       # заводит папку и схему, если их нет
            return None
        if endpoint in OPEN_ENDPOINTS or endpoint.startswith(API_PREFIX):
            return None
        # На запрос данных отвечаем отказом, на обычную страницу — переводом ко
        # входу: перевод в ответ на запрос из скрипта выглядел бы как успех.
        if request.accept_mimetypes.best == "application/json":
            return {"ok": False, "error": "нужно войти"}, 401
        return redirect(url_for("auth.login", next=request.path))

    @flask_app.teardown_request
    def _drop_workspace(exc):                   # noqa: ARG001 — исключение нас не касается
        """Снять базу с потока. ВСЕГДА, даже если обработчик упал.

        Это не уборка ради чистоты. Потоки в пуле переиспользуются, и оставленная
        база досталась бы следующему человеку — молча и правдоподобно.
        """
        try:
            users.deactivate()
        except Exception:                        # noqa: BLE001
            log.exception("рабочее место не снялось с потока — это опасно")

    @flask_app.context_processor
    def _who():
        """Номер и его человеческий вид доступны в любом шаблоне."""
        phone = getattr(g, "phone", None)
        return {"phone": phone, "phone_label": users.display(phone) if phone else None,
                "is_demo": phone == users.DEMO}


def sign_in(phone: str) -> None:
    """Запомнить в куке, кто вошёл. Сессию заводим заново — от подмены."""
    session.clear()
    session[KEY_PHONE] = phone
    session[KEY_SINCE] = datetime.now().isoformat(timespec="seconds")
    session.permanent = False


def sign_out() -> None:
    session.clear()


def needs_phone(view):
    """Обёртка для мест, куда точно нельзя без номера.

    Дублирует before_request нарочно: обработчик, случайно названный так же, как
    открытая страница, иначе оказался бы открыт, и заметить это было бы некому.
    """
    @functools.wraps(view)
    def guard(*args, **kwargs):
        if not getattr(g, "phone", None):
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return guard


__all__ = ["install", "current_phone", "sign_in", "sign_out", "needs_phone",
           "OPEN_ENDPOINTS", "API_PREFIX"]
