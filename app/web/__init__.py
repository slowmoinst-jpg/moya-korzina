"""Веб-приложение «Моя корзина»: обычный сайт вместо Streamlit.

Решение владельца 17.09.2026: «Streamlit уже не нужен, мы перешли на выделенный
сервер». Уход со Streamlit — не смена вкуса, а снятие трёх ограничений, каждое из
которых упиралось в потолок:

  ПЕРЕРИСОВКА ВСЕГО НА КАЖДОЕ НАЖАТИЕ. Streamlit исполняет скрипт заново целиком,
  и на экране из сорока строк корзины это видно глазом. Обычная страница
  перерисовывает то, что изменилось, и ничего больше.

  ЧУЖОЙ ВИД НА ТЕЛЕФОНЕ. Направление «Цвет магазина» владелец выбирал по мобильным
  макетам, а Streamlit давал его лишь приблизительно: половина оформления в
  app/ui/theme.py — это борьба с его собственной разметкой через data-testid,
  которая ломается с каждым его обновлением.

  НЕТ СВОИХ АДРЕСОВ. Экран жил в session_state, поэтому на корзину нельзя было дать
  ссылку, вернуться «назад» и открыть две вкладки рядом. Для приложения, куда ходят
  с телефона и с компьютера, это не мелочь.

ЧТО ОСТАЁТСЯ НЕТРОНУТЫМ. Вся суть приложения лежит вне интерфейса и переезда не
требует: repo, service, optimizer, connectors, catalog, places, collector, api,
cartplan, store_accounts. Меняется только то, что рисует страницы. Это и есть
причина, по которой переезд вообще возможен за разумное время.

ЧЕМ СДЕЛАНО И ПОЧЕМУ ИМЕННО ТАК. Flask с шаблонами Jinja и обычный CSS — без
сборщиков, без узловых модулей, без единого шага сборки. Так же устроено
окно магазина (app/shopbrowser), и по той же причине: приложение должно ставиться
и чиниться на сервере, где есть только Python. Страницы рисует сервер, JavaScript
добавляется точечно там, где без него неудобно, — а не наоборот.

СЕРВЕР — waitress, а не встроенный отладочный: он один работает и на Windows, где
идёт разработка, и на Linux, где стоит боевой. Запуск: python -m app.web.server.

ПЕРЕЕЗД ШЁЛ ПОСТЕПЕННО И ЗАКОНЧЕН 17.09.2026. Streamlit оставался живым, пока
новый интерфейс не догнал его по экранам: приложение работает на сервере каждый
день, и менять его целиком одним движением значило бы оставить владельца без
рабочего места на всё время правки. Не перенесённый экран при этом стоял в меню и
открывался честной заглушкой — спрятать его значило бы соврать о возможностях
приложения, показать пустым — о его состоянии. Когда сошлись все двенадцать,
Streamlit ушёл вместе с app/ui, streamlit_app.py, .streamlit/ и строкой в
requirements.txt.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from urllib.parse import urlsplit

from flask import Flask, has_request_context, redirect, request
from flask.sessions import SecureCookieSessionInterface

from app import config

log = logging.getLogger(__name__)


class _Session(SecureCookieSessionInterface):
    """Кука сессии с Secure на HTTPS-запросе и без него на http по IP-адресу.

    Secure всегда сломал бы вход по http://<IP> и в приложении 1.17. Без Secure вовсе
    заход на http://<имя> (порт 80 — тот же контейнер, что за Caddy) отдал бы куку
    HTTPS-сессии открытым текстом.

    sslip.io нет в списке публичных суффиксов, для браузера все имена *.sslip.io — один
    сайт, и чужая страница на таком имени подложила бы свою куку session с
    Domain=sslip.io; куку с приставкой __Host- чужой хост поставить не может.
    """

    def get_cookie_name(self, app: Flask) -> str:
        if has_request_context() and request.is_secure:
            return "__Host-" + super().get_cookie_name(app)
        return super().get_cookie_name(app)

    def get_cookie_secure(self, app: Flask) -> bool:
        return request.is_secure if has_request_context() else super().get_cookie_secure(app)


def _is_name_host(host: str) -> bool:
    """Хост запроса — имя, а не IP-адрес и не localhost. Порт и скобки IPv6 отбрасываются."""
    try:
        name = urlsplit("//" + host).hostname or ""
    except ValueError:                      # кривые скобки IPv6
        return False
    if not name or name == "localhost":
        return False
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return True
    return False

# Ключ подписи куки-сессии. Не в репозитории и не в config.yaml: он секрет, а
# config.yaml лежит в git. Живёт файлом в томе данных рядом с базами — там же,
# где всё остальное, что переживает передеплой.
SECRET_FILE = "session.key"


def secret_key() -> bytes:
    """Ключ подписи сессии; заводится при первом запуске и переживает перезапуски.

    Почему файл, а не переменная окружения. Переменную пришлось бы задавать при
    каждом запуске контейнера, и забытая переменная означала бы новый ключ на
    каждой выкладке — то есть разлогин всех людей после каждой правки. Файл в томе
    данных переживает и перезапуск, и передеплой, и его не видно в git.
    """
    # Именно ROOT/data, а не папка текущей базы: база подменяется на базу
    # человека (app/users.py), и ключ подписи гулял бы вместе с ней.
    path = os.path.join(config.ROOT, "data", SECRET_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path, "rb") as fh:
            saved = fh.read().strip()
        if len(saved) >= 32:
            return saved
    fresh = os.urandom(48).hex().encode("ascii")
    with open(path, "wb") as fh:
        fh.write(fresh)
    try:                                    # на Windows прав нет, и это не беда
        os.chmod(path, 0o600)
    except OSError:
        pass
    log.info("заведён новый ключ подписи сессии: %s", path)
    return fresh


def create_app() -> Flask:
    """Собрать приложение. Отдельной функцией — чтобы тесты поднимали своё.

    Без этого тест работал бы с тем же объектом, что и боевой запуск, и настройки
    одного протекали бы в другой: одна забытая правка конфигурации — и тест зелен
    на том, чего в бою нет.
    """
    flask_app = Flask(__name__, static_folder="static", template_folder="templates")
    flask_app.secret_key = secret_key()
    flask_app.session_interface = _Session()
    flask_app.config.update(
        SESSION_COOKIE_HTTPONLY=True,       # куку не должен читать сторонний скрипт
        SESSION_COOKIE_SAMESITE="Lax",      # чужая страница не сделает запрос от имени человека
        MAX_CONTENT_LENGTH=32 * 1024 * 1024,  # чеки и прайсы бывают большими, но не безразмерными
        JSON_AS_ASCII=False,
    )

    from app.web import api as web_api, apk, auth, device, phone, views
    auth.install(flask_app)
    apk.install(flask_app)
    device.install(flask_app)
    views.install(flask_app)
    # Телефон — после входа: его главная ведёт приложение на четыре своих экрана,
    # а решать это можно, только зная, кто пришёл (app/web/phone/).
    phone.install(flask_app)
    web_api.install(flask_app)
    _install_page_headers(flask_app)
    _install_static_version(flask_app)
    _install_https_for_names(flask_app)
    _install_same_origin_posts(flask_app)
    return flask_app


def _install_https_for_names(flask_app: Flask) -> None:
    """Заход по http на имя — перевод на https. Только когда перед приложением стоит Caddy.

    Признак Caddy — переменная TRUSTED_PROXY (tools/server-deploy.sh); в тестах и при
    разработке её нет. IP-адрес и localhost не переводятся никогда: приложение 1.17 ходит
    на http://<IP>, проверка выкладки — на http://127.0.0.1.
    """
    if not os.getenv("TRUSTED_PROXY"):
        return

    @flask_app.before_request
    def _to_https():
        if request.is_secure or not _is_name_host(request.host):
            return None
        # 307 временный и сохраняет метод и тело. Если доверие к прокси не сработало,
        # HTTPS-запрос покажется приложению http, и перевод пойдёт по кругу: это и есть
        # проверка после выкладки (DEPLOY.md).
        return redirect(urlsplit(request.url)._replace(scheme="https").geturl(), code=307)


def _install_static_version(flask_app: Flask) -> None:
    """static_v(файл) в шаблонах — адрес статики с отпечатком содержимого (?v=…).

    Статику телефон держит в кэше, страницы — нет (см. ниже). Новые шаблоны после
    выкладки встречали бы старый phone.css, и экран разваливался, пока кэш не протух
    (смена вида 22.10.2026). Отпечаток меняется вместе с файлом: новый файл — новый
    адрес. Считается раз на процесс: выкладка перезапускает сервер.
    """
    import hashlib
    from functools import cache

    from flask import url_for

    @cache
    def stamp(filename: str) -> str:
        with open(os.path.join(flask_app.static_folder, filename), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:10]

    @flask_app.template_global()
    def static_v(filename: str) -> str:
        return url_for("static", filename=filename, v=stamp(filename))


def _install_page_headers(flask_app: Flask) -> None:
    """Страницы не кэшировать: телефон иначе показывает вчерашнюю.

    ЗАМЕР 20.10.2026. Экран «Кабинеты» в приложении показывал красную плашку
    «Не вышло: … Invalid cookie fields», а в журнале сервера за сутки не было ни одной
    такой ошибки: WebView отдал СТАРУЮ страницу из своего кэша. Для страниц это
    недопустимо — они живые (состояние входа, корзины, цены), а кэш показывает прошлое.
    Статике кэш нужен, её не трогаем.
    """

    @flask_app.after_request
    def _no_cache_html(answer):
        if answer.mimetype == "text/html":
            answer.headers["Cache-Control"] = "no-store, max-age=0"
            answer.headers["Pragma"] = "no-cache"
            answer.headers["Expires"] = "0"
        return answer


def _install_same_origin_posts(flask_app: Flask) -> None:
    """Защита POST-запросов от чужих сайтов.

    SameSite=Lax не спасает от соседей по *.sslip.io; /api/* не трогаем — туда
    шлют закладка и расширение со страниц сетей со своим секретом; приложение
    Android шлёт свои запросы без этих заголовков.
    """

    @flask_app.before_request
    def _check_same_origin():
        if request.method in ("GET", "HEAD", "OPTIONS") or request.path.startswith("/api/"):
            return None
        sec_fetch_site = request.headers.get("Sec-Fetch-Site")
        if sec_fetch_site in ("same-site", "cross-site"):
            return ("Запрос с чужого сайта отклонён", 403)
        origin = request.headers.get("Origin")
        if origin is not None and urlsplit(origin).netloc != request.host:
            return ("Запрос с чужого сайта отклонён", 403)
        return None


__all__ = ["create_app", "secret_key"]
