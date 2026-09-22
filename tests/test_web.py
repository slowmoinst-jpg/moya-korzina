"""Новый интерфейс: вход, навигация и главное — чужая база не должна протечь.

Переезд со Streamlit опасен ровно в одном месте, и оно здесь защищается первым.
В Streamlit скрипт исполнялся заново на каждое действие, и база выбиралась в
начале прогона. Обычный сервер держит ПУЛ ПОТОКОВ и раздаёт их запросам по кругу:
поток, обслуживший одного человека, через секунду достанется другому. Не убрать за
собой — и второй откроет базу первого. В интерфейсе это невидимо: чужие чеки
выглядят как свои, просто их больше или меньше, чем помнится.

Остальное здесь — про честность страниц: не перенесённый экран говорит, что не
перенесён, вход без номера никуда не пускает, чужой адрес в «вернуться после
входа» не пролезает.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, users  # noqa: E402


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временных базах: настоящие рабочие места трогать нельзя.

    config.db_path здесь НЕ подменяется нарочно, хотя так делает большинство
    тестов. Подмена вернула бы один и тот же путь всем и отключила бы ровно то,
    что здесь проверяется: выбор базы по человеку. Вместо этого сдвигается корень
    (app.config.ROOT) и папка рабочих мест — а db_path продолжает работать
    по-настоящему, через override.
    """
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    users.deactivate()


def enter(client, phone="79990000001"):
    return client.post("/login", data={"phone": phone, "next": "/"})


# ---------- вход ----------
def test_without_a_phone_nothing_opens(web):
    """Ни один экран не должен открыть базу, пока не известно, чью."""
    answer = web.get("/basket")
    assert answer.status_code == 302
    assert "/login" in answer.headers["Location"]


def test_login_opens_the_workspace(web):
    assert enter(web).status_code == 302
    assert web.get("/").status_code == 200


def test_bad_phone_is_refused_with_words(web):
    answer = web.post("/login", data={"phone": "12", "next": "/"})
    assert answer.status_code == 400
    assert "номер".encode() in answer.data.lower() or "телефон".encode() in answer.data.lower()


def test_logout_closes_everything(web):
    enter(web)
    web.post("/logout")
    assert web.get("/").status_code == 302


def test_a_foreign_address_cannot_ride_the_next_parameter(web):
    """Ссылка «войти и вернуться» не должна уводить на чужой сайт.

    Это классическая беда страниц входа: человек вводит номер и оказывается на
    подделке, причём выглядит это как продолжение работы.
    """
    answer = web.post("/login", data={"phone": "79990000001",
                                      "next": "https://зло.example/забрать"})
    assert answer.headers["Location"] == "/"

    answer = web.post("/login", data={"phone": "79990000001", "next": "//зло.example"})
    assert answer.headers["Location"] == "/"


# ---------- главное: чужая база ----------
def test_workspace_does_not_leak_between_requests(web):
    """Поток из пула не должен унести базу одного человека следующему.

    Проверка идёт по самому пути к базе, а не по содержимому страницы: страница
    двух разных пустых рабочих мест выглядит одинаково, и подмену по ней не
    увидеть — ровно поэтому ошибка и была бы незаметной в жизни.
    """
    enter(web, "79990000001")
    web.get("/")
    web.post("/logout")

    assert config.db_override() is None, "база осталась висеть на потоке после запроса"

    enter(web, "79990000002")
    web.get("/")
    assert config.db_override() is None

    first = users.db_path_for("79990000001")
    second = users.db_path_for("79990000002")
    assert first != second and os.path.exists(first) and os.path.exists(second)


def test_workspace_is_dropped_even_when_a_screen_falls(web, monkeypatch):
    """Экран упал — база всё равно обязана сняться. Иначе ошибка отдаёт чужие чеки."""
    from app.web.screens import stores

    monkeypatch.setattr(stores, "page", lambda: (_ for _ in ()).throw(RuntimeError("экран упал")))
    enter(web)
    try:
        web.get("/stores")
    except RuntimeError:
        pass
    assert config.db_override() is None


# ---------- страницы ----------
def test_home_shows_four_steps(web):
    enter(web)
    page = web.get("/").data.decode("utf-8")
    assert page.count("Шаг") >= 4
    assert "Куда везём" in page


def test_saving_an_address_shows_it_in_the_header(web):
    enter(web)
    web.post("/address", data={"address": "Москва, Ходынский бульвар 4"})
    assert "Ходынский" in web.get("/").data.decode("utf-8")


def test_stores_lists_every_chain(web):
    enter(web)
    page = web.get("/stores").data.decode("utf-8")
    for name in ("Магнит", "ВкусВилл", "Пятёрочка", "Лента", "Дикси", "Самокат"):
        assert name in page


NOT_YET = "не переехал"


def test_a_screen_not_moved_yet_says_so(web):
    """Заглушка честная: экран есть, он просто пока на прежнем интерфейсе.

    Спрятать его из меню значило бы соврать о возможностях приложения, показать
    пустым — соврать о его состоянии.

    Экран для проверки НЕ ПРИБИТ ГВОЗДЁМ нарочно. Переезд идёт по экранам, и
    каждый перенесённый ломал бы тест, названный по имени: агент, переносящий
    «Корзину», упирался бы в красный прогон из-за чужого файла. Поэтому тест сам
    ищет любой ещё не переехавший экран, а когда переедут все — честно снимается,
    а не притворяется пройденным.
    """
    from app.web.views import SCREENS

    enter(web)
    left = [s for s in SCREENS if NOT_YET in web.get(s.path).data.decode("utf-8")]
    if not left:
        pytest.skip("все экраны переехали — заглушке больше нечего проверять")
    page = web.get(left[0].path).data.decode("utf-8")
    assert NOT_YET in page
    assert left[0].title in page, "заглушка обязана называть экран, а не быть безымянной"


def test_every_moved_screen_is_really_moved(web):
    """Перенесённый экран не должен показывать заглушку — и наоборот.

    Сторож от полупереезда: строка в списке готовых есть, а страница всё ещё
    заглушка (или наоборот — экран написан, но в список его вписать забыли).
    И то и другое выглядит как рабочее приложение ровно до первого нажатия.
    """
    from app.web.views import SCREEN_BY_KEY

    enter(web)
    for key in ("home", "stores"):
        page = web.get(SCREEN_BY_KEY[key].path).data.decode("utf-8")
        assert NOT_YET not in page, f"экран {key} перенесён, но отвечает заглушкой"


def test_every_screen_answers(web):
    """Ни одного пункта меню, ведущего в никуда: это первое, что человек нажмёт."""
    from app.web.views import SCREENS

    enter(web)
    for screen in SCREENS:
        assert web.get(screen.path).status_code == 200, screen.path


def test_unknown_address_is_a_page_not_a_crash(web):
    enter(web)
    assert web.get("/такой-страницы-нет").status_code == 404


def test_session_key_survives_a_restart(tmp_path, monkeypatch):
    """Ключ подписи переживает перезапуск: иначе выкладка разлогинивала бы всех."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    from app.web import secret_key

    assert secret_key() == secret_key()


def test_web_api_endpoints(web):
    enter(web)
    h = web.get("/api/health")
    assert h.status_code == 200
    assert h.json["ok"] is True

    sync_resp = web.post("/api/store_accounts/sync", json={
        "store": "magnit",
        "logged_in": True,
        "account": "Карта •••9999",
        "points": 350.0,
        "coupons": [{"id": "c1", "title": "-20% на сыр", "value": "-20%"}]
    })
    assert sync_resp.status_code == 200
    assert sync_resp.json["ok"] is True
    assert sync_resp.json["points"] == 350.0

    stores_html = web.get("/stores").data.decode("utf-8")
    assert "350" in stores_html
    assert "•••9999" in stores_html
    assert "-20% на сыр" in stores_html

    plan_resp = web.post("/api/cartplan", json={"store": "magnit"})
    assert plan_resp.status_code == 200
    assert plan_resp.json["ok"] is True


# ---------- дверь для расширения ----------
def test_the_api_door_opens_for_the_extension_without_a_session(web, monkeypatch):
    """Расширение приходит БЕЗ куки — и дверь обязана его пустить по секрету.

    Самая дорогая из найденных поломок и самая незаметная: общая проверка входа
    (auth._pick_workspace) отвечала на запрос расширения переводом на /login,
    fetch послушно шёл за переводом и получал страницу входа. Со стороны
    расширения это выглядело как успех — «пакет принят», «наряда нет», — и связка
    приложения со сборщиком не работала вовсе, ни разу не пожаловавшись.
    """
    from app import api, users as app_users

    enter(web)                       # завести рабочее место и узнать его секрет
    app_users.open_workspace("79990000001")
    secret = api.workplace_secret()
    app_users.deactivate()

    fresh = web.application.test_client()      # никакой куки: это и есть расширение
    answer = fresh.post("/api/cartplan", json={"workplace": "79990000001", "secret": secret,
                                               "store": "magnit", "waiting": True})
    assert answer.status_code == 200, "дверь не пустила расширение с верным секретом"
    assert answer.json["ok"] is True

    refused = fresh.post("/api/cartplan", json={"workplace": "79990000001",
                                                "secret": "не тот", "store": "magnit"})
    assert refused.status_code == 401, "дверь пустила без секрета — замок открыт всем"


def test_screens_still_need_a_phone(web):
    """Открыв дверь API, не открыли заодно экраны: у них своего замка нет."""
    answer = web.get("/accounts")
    assert answer.status_code == 302
    assert "/login" in answer.headers["Location"]
