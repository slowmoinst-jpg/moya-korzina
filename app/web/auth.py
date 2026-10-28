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

ВХОД — ПО НОМЕРУ ТЕЛЕФОНА (app/web/login.py, app/users.py).
Вход по номеру: кода из СМС нет, кто первым пришёл с номером, тот его и занял. Личный PIN
рабочего места (25.10.2026) владелец убрал 27.10.2026, и кука снова помнит только номер.

У установки может быть PIN установки (общий, ставит владелец с сервера): без него куку не
выдают. Но это замок на дверь, а не удостоверение. Поэтому у пути, который открывает
рабочее место адресом (передача входа ссылкой), свой замок — секрет рабочего места в самой
ссылке (app/users.py, link_secret). Без него адрес с номером не открывает ничего. Закладкам
секрет не нужен: они ходят с кукой и номер в адресе не шлют. Ссылка со своим секретом и API
устройства (номер и секрет в теле) — отдельные от входа двери.

ДЕМО ТОЛЬКО ДЛЯ ЧТЕНИЯ (25.10.2026). Рабочее место demo одно на всех, кто нажал «Посмотреть
демо»: когда в нём можно было писать, посетитель вписывал свой домашний адрес, и его
видели следующие. Поэтому вошедшему в демо закрыто всё, кроме чтения и входа-выхода
(_guard_signed_in): форма уходит переводом 303 назад с флагом demo=1 (по нему экраны
телефона пишут «Это демо…»), запрос данных и api.* получают 403 JSON. Дверь API устройства
(app/web/api.py) демо не пускает тоже: иначе приложение положило бы демо-корзину в
настоящие корзины магазинов посетителя.

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
# открывает чью-то базу, и пускать туда «пока без номера» нельзя. Скачивание
# приложения (apk.download) открыто, как было открыто и само файлом из static:
# приложение ставят до того, как входят (app/web/apk.py). Выход открыт, потому что
# выйти можно всегда.
OPEN_ENDPOINTS = {"auth.login", "auth.enter", "auth.demo", "auth.logout", "static", "apk.download"}

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

# Вошедшему в демо закрыта запись: всё, кроме чтения и входа-выхода. Выход и вход нужны, чтобы
# из демо можно было уйти к своему рабочему месту (или заново нажать «Посмотреть демо»).
DEMO_WRITE_OK = {"auth.logout", "auth.demo", "auth.login", "auth.enter"}
DEMO_READ_ONLY = "Это демо — изменения не сохраняются"


def current_phone() -> str | None:
    """Номер из куки или None. Ничего не открывает и базу не трогает."""
    raw = session.get(KEY_PHONE)
    if not isinstance(raw, str):
        return None
    return users.normalize_phone(raw)


def _wants_json() -> bool:
    """Запрос из скрипта (fetch, расширение), а не страницы: отвечать ему надо кодом, не переводом."""
    return request.is_json or request.accept_mimetypes.best == "application/json"


def _back_with_demo_flag() -> str:
    """Адрес, куда вернуть форму демо: страница из Referer того же сайта (иначе «/») + demo=1.

    Берётся только путь внутри сайта, и той же проверкой, что у «вернуться после входа»
    (login._safe_next): Referer шлёт браузер, но подделать его может кто угодно. Флаг
    demo=1 экраны телефона читают сами и пишут заметную строку «Это демо…», чтобы отказ не
    прошёл молча.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit

    from app.web.login import _safe_next          # поздно: login сам импортирует auth

    back = "/"
    try:
        ref = urlsplit(request.referrer or "")
    except ValueError:                            # Referer вида «http://[» — не адрес
        ref = None
    if ref and ref.netloc == request.host:
        back = _safe_next(ref.path + (f"?{ref.query}" if ref.query else ""))
    path, _, query = back.partition("?")
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k != "demo"]
    return f"{path}?{urlencode(pairs + [('demo', '1')])}"


def _guard_signed_in(endpoint: str):
    """Что закрыто уже вошедшему по куке: запись в общем демо.

    Дверь со своим секретом (вход по ссылке) сюда не попадает: она идёт веткой без куки.
    """
    if g.phone == users.DEMO:
        if request.method in ("GET", "HEAD", "OPTIONS") or endpoint in DEMO_WRITE_OK:
            return None
        if endpoint.startswith(API_PREFIX) or _wants_json():
            return {"ok": False, "error": DEMO_READ_ONLY}, 403
        return redirect(_back_with_demo_flag(), code=303)
    return None


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
            return _guard_signed_in(endpoint)

        # Вход, переданный ссылкой: номер и груз входа приезжают в самом адресе.
        #
        # ЭТОТ ПУТЬ ЗАКРЫТ СЕКРЕТОМ САМОЙ ССЫЛКИ, а не PIN. Закладка работает на
        # чужом сайте (в кабинете сети), куки у неё нет, и PIN в неё не вписать.
        # Раньше здесь открывалось рабочее место по одному номеру — то есть всякий,
        # кто знал номер, получал чеки, адрес и куки кабинетов сетей. Теперь ссылка
        # несёт t — секрет этого рабочего места (app/users.py, link_secret), и без
        # него адрес не открывает ничего: запрос идёт общим безлогинным порядком.
        # Закладкам секрет не нужен: они ходят с кукой и phone= в адресе не шлют.
        phone_param = (request.args.get("phone") or request.form.get("phone") or "").strip()
        if phone_param and (request.args.get("vhod") or request.args.get("keys")):
            norm = users.normalize_phone(phone_param)
            given = (request.args.get("t") or request.form.get("t") or "").strip()
            # Демо ссылкой не открывается: его секрет виден каждому, кто открыл «Кабинеты» демо,
            # а эта дверь идёт мимо сторожа (_guard_signed_in) — то есть мимо запрета записи.
            if norm and norm != users.DEMO and users.link_secret_matches(norm, given):
                g.phone = norm
                # Рабочее место уже открыто проверкой секрета: он читается из его базы.
                return None
            # Секрет не подошёл. Если он успел открыть базу (рабочее место есть), —
            # снимаем её тут же: чужой запрос не должен ходить по чужой базе даже
            # те полстроки, что живут между этой проверкой и обработчиком.
            users.deactivate()
            log.info("вход по ссылке: секрет не подошёл (%s)", request.path)

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
           "OPEN_ENDPOINTS", "API_PREFIX", "DEMO_READ_ONLY"]
