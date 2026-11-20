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


