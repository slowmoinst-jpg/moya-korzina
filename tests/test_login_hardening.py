"""Дыры входа по PIN, найденные независимым ревью безопасности и закрытые 26.10.2026.

1. Демо не принимает вход в магазин через GET — ни кукой, ни ссылкой с секретом демо.
2. PIN установки (общий замок): как им управлять с сервера.
3. Слабые PIN (одна цифра, цифры подряд) не ставятся нигде.
4. Общая пауза стоит только перед настоящей сверкой PIN установки и не занимает потоки пустыми запросами.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.parse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import api, location, repo, users  # noqa: E402
from app.web import login as web_login  # noqa: E402
from conftest import sign_in  # noqa: E402

PHONE = "79990000071"
OTHER = "79990000072"
PIN = "432100"                           # PIN установки
DEMO_REFUSAL = "Это демо — изменения не сохраняются"


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временном корне, счётчики попыток обнулены, витрину демо никто не собирает."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    monkeypatch.setattr(users, "PIN_ITERATIONS", 1_000)

    from app import demo as showcase
    from app.web import create_app

    monkeypatch.setattr(web_login, "_FAILS", {})
    monkeypatch.setattr(web_login, "_INSTALL_FAILS", [])
    monkeypatch.setattr(showcase, "ensure", lambda: False)

    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    users.deactivate()


def text(answer) -> str:
    return answer.data.decode("utf-8")


def packed(store: str = "magnit") -> str:
    """Вход с телефона, как его кладёт закладка в адрес: JSON в base64 без «=»."""
    payload = json.dumps({"store": store, "cookies": [
        {"name": "mg_at", "value": "x" * 40, "domain": ".magnit.ru"}]})
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def saved_login(phone: str, store: str = "magnit") -> bool:
    """Лежит ли у рабочего места сохранённый вход в сеть. Базу открываем и снимаем сами."""
    from app.shopbrowser import store as shopstore

    users.open_workspace(phone)
    try:
        return shopstore.about(store) is not None
    finally:
        users.deactivate()


# ---------- 1. демо не принимает чужой вход через GET ----------

def test_the_demo_takes_no_store_login_from_a_get(web):
    """Проба ревью test_demo_get_writes_store_session: GET /cabinet?store=…&vhod=… в демо сохранял вход."""
    assert web.post("/demo").status_code == 302
    answer = web.get(f"/cabinet?store=magnit&vhod={packed()}")
    assert answer.status_code == 303 and answer.headers["Location"].endswith("/cabinet?store=magnit")
    answer = web.get(f"/cabinet?store=magnit&keys={packed()}&format=json")
    assert answer.status_code == 403 and answer.get_json() == {"ok": False, "error": DEMO_REFUSAL}
    assert not saved_login(users.DEMO), "в демо появился вход в магазин"

    # Контроль: у настоящего места тот же адрес вход сохраняет
    mine = web.application.test_client()
    sign_in(mine, PHONE)
    assert mine.get(f"/cabinet?store=magnit&vhod={packed()}").status_code == 303
    assert saved_login(PHONE)


def test_the_demo_cannot_be_opened_by_a_link_secret(web):
    """Вход ссылкой (?phone=demo&vhod=...&t=...) не отпирает демо: у демо секрета нет."""
    users.open_workspace(users.DEMO)
    try:
        secret = repo.get_setting(users.LINK_SECRET_KEY)
        assert secret is None, "у общего демо почему-то есть секрет ссылки"
    finally:
        users.deactivate()

    answer = web.get(f"/accounts?vhod=pyaterochka&phone={users.DEMO}&t=any-guess")
    assert answer.status_code == 302
    assert "/login" in answer.headers.get("Location", "")


# ---------- 2. PIN установки управляется с сервера ----------

def test_the_installation_pin_is_managed_on_the_server_and_the_docstring_says_how(web):
    """Владелец ставит и снимает PIN установки через users.set_pin; докстринг объясняет команды.

    Проба ревью test_no_setup_pin_from_pin_page_when_not_set: знакомый не вправе ставить
    общий PIN. Поэтому на сервере: docker exec korzina python -c "from app import users; users.set_pin('483915')".
    Снять: users.set_pin('').
    """
    users.set_pin("483915")
    assert users.check_pin("483915")
    users.set_pin("690274")
    assert users.check_pin("690274") and not users.check_pin("483915")
    users.set_pin("")
    assert not users.pin_is_set()

    doc = users.set_pin.__doc__
    assert 'docker exec korzina python -c "from app import users; users.set_pin(' in doc
    assert "users.set_pin('')" in doc, "в докстринге нет команды, которая снимает PIN установки"


# ---------- 3. слабые PIN не принимаются ----------

WEAK = [
    "000000", "111111", "999999",
    "123456", "234567", "345678", "456789", "567890", "012345",
    "987654", "876543", "765432", "654321", "543210", "109876",
]


@pytest.mark.parametrize("pin", WEAK)
def test_a_weak_pin_is_refused_with_words(pin):
    with pytest.raises(ValueError) as why:
        users.check_new_pin(pin)
    assert str(why.value) == users.PIN_TOO_SIMPLE
    with pytest.raises(ValueError):
        users.check_new_pin(f"  {pin}  ")


@pytest.mark.parametrize("pin", ["123457", "135792", "654320", "100000", "432100", "121212",
                                 "112233", "123123", "482915", "73519048261593720481"])
def test_an_ordinary_pin_passes(pin):
    """Ловится только то, что названо: одна цифра и цифры подряд. Остальное — на пределе попыток."""
    assert users.check_new_pin(pin) == pin


def test_a_short_or_non_digit_pin_keeps_its_own_words():
    """«11111» — не слабый, а короткий; «aaaaaa» — не слабый, а нецифровой: объяснение то, что точнее."""
    for pin, words in (("11111", users.PIN_TOO_SHORT), ("abcdef", users.PIN_NOT_DIGITS),
                       ("", users.PIN_TOO_SHORT)):
        with pytest.raises(ValueError) as why:
            users.check_new_pin(pin)
        assert str(why.value) == words, pin


# ---------- 4. пауза общего предела установки ----------

def full_installation_counter() -> None:
    web_login._INSTALL_FAILS[:] = [time.monotonic()] * web_login.INSTALL_FAIL_LIMIT


def test_empty_requests_do_not_sleep_when_the_installation_counter_is_full(web, monkeypatch):
    """Запросы без сверки PIN установки не должны спать."""
    slept: list[float] = []
    monkeypatch.setattr(web_login.time, "sleep", lambda seconds: slept.append(seconds))
    full_installation_counter()

    # Вход без установки PIN (пин не задан):
    assert web.post("/login", data={"phone": PHONE}).status_code == 302
    assert slept == []


def test_the_pause_stands_right_before_each_real_check(web, monkeypatch):
    """Сверка PIN установки делает ровно одну паузу при заполненном счётчике установки."""
    slept: list[float] = []
    monkeypatch.setattr(web_login.time, "sleep", lambda seconds: slept.append(seconds))
    users.set_pin(PIN)
    full_installation_counter()

    assert web.post("/login", data={"phone": PHONE, "pin": PIN, "next": "/"}).status_code == 302
    assert slept == [web_login.INSTALL_PAUSE_SEC]
