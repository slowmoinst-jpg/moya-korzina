"""Замок входа: PIN установки, предел попыток и список разрешённых номеров.

ЧТО ЗДЕСЬ ОХРАНЯЕТСЯ. Сайт стоит по http на публичном адресе, и вход
идёт по номеру телефона. Замок (план docs/plans/mobilnaya-versiya-2026-10-21.md, Z0)
собирается из трёх необязательных частей, и каждая выключена по умолчанию:

    PIN УСТАНОВКИ. Задан — спрашивается вместе с номером; не задан — этой двери
    нет. Это главное обещание замка: он не имеет права сломать вход тем, кто его не ставил.
    Ставит, меняет и снимает его только владелец с сервера (users.set_pin).

    ПРЕДЕЛ ПОПЫТОК. Перебор шестизначного PIN — это миллион догадок. Пять отказов
    с одного IP-адреса (на все номера сразу) за 15 минут закрывают вход с этого IP-адреса.
    Двадцать отказов на всю установку за те же 15 минут НЕ запирают вход: они
    добавляют паузу и обычную проверку PIN, чтобы четыре чужих адреса не держали
    владельца снаружи.

    СПИСОК РАЗРЕШЁННЫХ НОМЕРОВ. Установка — личный сервер, а не сервис: чужому
    номеру здесь делать нечего. Отказ — тем же текстом, что и «это не номер»:
    разные тексты рассказали бы постороннему, что номер в установке есть.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import urllib.parse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import api, config, location, repo, users  # noqa: E402
from conftest import sign_in  # noqa: E402

PHONE = "79990000051"
STRANGER = "79990000052"
PIN = "432100"                 # PIN установки
DEMO_REFUSAL = "Это демо — изменения не сохраняются"


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временном корне — как в tests/test_phone_screens.py.

    Корень подменяется целиком: pin.json лежит на томе данных рядом с базами
    (data/pin.json), и писать его в настоящий корень репозитория тест не имеет права.
    """
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app import demo as showcase
    from app.web import create_app
    from app.web import login as web_login

    # Счётчики попыток живут в памяти процесса и пережили бы тест: соседние тесты
    # начали бы с чужого счёта. Сбрасываем оба на каждый тест.
    monkeypatch.setattr(web_login, "_FAILS", {})
    monkeypatch.setattr(web_login, "_INSTALL_FAILS", [])
    monkeypatch.setattr(showcase, "ensure", lambda: False)

    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    users.deactivate()


def enter(client, phone: str = PHONE, pin: str | None = None, **kwargs):
    """Вход через SPA API: номер и (если задан) PIN установки."""
    payload = {"phone": phone}
    if pin is not None:
        payload["pin"] = pin
    return client.post("/api/auth/login", json=payload, **kwargs)


def allowed(monkeypatch, value) -> None:
    """Подставить users.allowed так, как его прочитает app.config.get."""
    real = config.get

    def fake(path, default=None):
        if path == "users.allowed":
            return value
        return real(path, default)

    monkeypatch.setattr(config, "get", fake)


# ---------- без PIN установки: вход по одному номеру ----------

def test_without_an_installation_pin_the_number_alone_opens_the_workspace(web):
    """PIN установки не поставлен — вход по одному номеру открывает рабочее место."""
    assert not users.pin_is_set()
    answer = enter(web)
    assert answer.status_code == 200
    assert answer.get_json()["ok"] is True


# ---------- PIN установки поставлен ----------

def test_empty_and_wrong_pins_are_refused_with_the_same_words(web):
    """Ни намёка на то, что PIN был пустой, короткий или почти верный."""
    users.set_pin(PIN)
    empty = enter(web, pin="")
    wrong = enter(web, pin="999999")
    assert empty.status_code == 401 and wrong.status_code == 401
    assert "Неверный PIN" in empty.get_json()["error"]
    assert empty.get_json()["error"] == wrong.get_json()["error"]


# ---------- предел попыток ----------

def test_the_sixth_attempt_is_refused_without_checking_it(web):
    """Пять отказов — и шестая (даже верная) получает 429, не проверяясь."""
    users.set_pin(PIN)
    for _ in range(5):
        answer = enter(web, pin="000000")
        assert answer.status_code == 401, "предел сработал раньше пятой попытки"

    sixth = enter(web, pin=PIN)
    assert sixth.status_code == 429, "шестая попытка не отказ — предел не работает"
    assert "Слишком много попыток, подождите 15 минут" in sixth.get_json()["error"]


def test_different_numbers_from_one_address_share_one_limit(web):
    """Смена номера не даёт новых попыток: счёт идёт по адресу, а не по паре."""
    users.set_pin(PIN)
    for i in range(5):
        answer = enter(web, phone=f"7999000000{i}", pin="000000")
        assert answer.status_code == 401, "предел сработал раньше пятого отказа"

    blocked = enter(web, phone="79990000099", pin=PIN)
    assert blocked.status_code == 429, "новый номер обошёл предел адреса"


def test_the_limit_counts_addresses_separately(web):
    """Предел — по адресу: другой адрес с тем же номером не закрыт."""
    users.set_pin(PIN)
    for number in ("8 (999) 000-00-51", "+7 999 000 00 51",
                   "79990000052", "79990000053", "79990000054"):
        assert enter(web, phone=number, pin="000000").status_code == 401

    answer = enter(web, pin=PIN, environ_base={"REMOTE_ADDR": "10.0.0.9"})
    assert answer.status_code == 200
    assert answer.get_json()["ok"] is True


def test_the_installation_limit_is_logged_with_its_count_and_without_the_phone(web, monkeypatch, caplog):
    """Общий предел называет адрес и счёт, но не номер телефона."""
    from app.web import login as web_login

    users.set_pin(PIN)
    monkeypatch.setattr(web_login.time, "sleep", lambda seconds: None)
    for i in range(20):
        assert enter(web, phone=PHONE, pin="000000",
                     environ_base={"REMOTE_ADDR": f"10.0.3.{i}"}).status_code == 401

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.web.login"):
        assert enter(web, phone=PHONE, pin=PIN,
                     environ_base={"REMOTE_ADDR": "10.0.4.1"}).status_code == 200

    messages = [record.getMessage() for record in caplog.records
                if record.name == "app.web.login"]
    assert any("10.0.4.1" in message and "20" in message for message in messages), messages
    assert all(PHONE not in message for message in messages), "номер попал в журнал"


def test_a_six_digit_pin_is_the_shortest_one_accepted(web):
    """Шесть цифр ставятся и проверяются; пять — уже не замок."""
    users.set_pin("483915")
    assert users.pin_is_set()
    assert users.check_pin("483915"), "поставленный шестизначный PIN не подошёл"
    assert not users.check_pin("48391")
    assert not users.check_pin("4839150")

    with pytest.raises(ValueError) as why:
        users.set_pin("12345")
    assert "шести" in str(why.value), "пятизначный PIN отклонён без объяснения"


def test_a_short_or_non_digit_pin_is_not_set(web):
    """«1», «1234», «12345», «abcd» — не PIN: отказ с объяснением, а не «поставлен»."""
    for bad in ("1", "1234", "12345", "abcd"):
        with pytest.raises(ValueError) as why:
            users.set_pin(bad)
        assert str(why.value), f"отказ на PIN {bad!r} без объяснения"
        assert not users.pin_is_set(), f"PIN {bad!r} всё-таки поставлен"


def test_the_pin_never_comes_back_in_api_response(web):
    """Введённый PIN не возвращается в ответе API."""
    users.set_pin(PIN)
    wrong = enter(web, pin="000000")
    assert "000000" not in wrong.get_data(as_text=True)


def test_the_pin_is_not_stored_in_the_clear(web, tmp_path):
    """В файле замка нет самого PIN — только соль и хэш с числом повторов."""
    users.set_pin(PIN)
    path = os.path.join(str(tmp_path), "data", "pin.json")
    assert users.pin_path() == path, "PIN снова оказался вне тома данных"
    assert os.path.exists(path)

    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    assert PIN not in raw, "PIN записан в файл открытым текстом"

    record = json.loads(raw)
    assert record["salt"] and record["hash"]
    assert record["hash"].lower() != PIN
    assert record["iterations"] == 200_000, "повторов меньше обещанных"

    # Две установки одного PIN не должны давать одинаковый файл: соль своя.
    users.set_pin(PIN)
    with open(path, encoding="utf-8") as fh:
        again = json.load(fh)
    assert again["salt"] != record["salt"], "соль не случайна — одинаковый PIN виден по файлу"


# ---------- список разрешённых номеров ----------

def test_a_number_out_of_the_list_is_refused_like_a_bad_number(web, monkeypatch):
    """Чужой номер — тот же текст, что и «это не номер»: иначе он выдаёт себя."""
    allowed(monkeypatch, "+7 999 000-00-51")
    bad_number = enter(web, phone="12")
    stranger = enter(web, phone=STRANGER)

    assert bad_number.status_code == 400 and stranger.status_code == 400
    assert stranger.get_json()["error"] == bad_number.get_json()["error"]


# ---------- вход по ссылке: секрет в самой ссылке ----------

def workspace(phone: str = PHONE) -> str:
    """Завести рабочее место и вернуть секрет его ссылки. Базу снять с потока."""
    users.open_workspace(phone)
    try:
        return users.link_secret(phone)
    finally:
        users.deactivate()


def link(secret: str | None = None, phone: str = PHONE, **extra) -> str:
    """Адрес закладки: номер, груз входа и — если он известен — секрет ссылки."""
    query = {"store": "magnit", "phone": phone, "vhod": "e30", **extra}
    if secret is not None:
        query["t"] = secret
    return "/cabinet?" + urllib.parse.urlencode(query)


# ---------- демо для просмотра: только для чтения ----------

def test_demo_is_not_a_number_to_type():
    """Слово demo в поле номера не принимается: в демо ведёт только кнопка."""
    assert users.resolve_login("demo") == (None, users.NOT_A_PHONE)
    assert users.resolve_login("DEMO") == (None, users.NOT_A_PHONE)


def test_the_api_door_refuses_a_demo_session_on_its_own(web):
    """Сама дверь, без сторожа на входе: сессия demo не должна проходить и мимо него."""
    import importlib

    web_api = importlib.import_module("app.web.api")
    with web.application.test_request_context("/api/device/cartplan", method="POST", json={}) as ctx:
        ctx.session["phone"] = users.DEMO
        with pytest.raises(api.Refused) as refused:
            web_api._authenticate({})
    assert refused.value.status == 403 and refused.value.message == DEMO_REFUSAL
    assert users.current() is None, "отказ оставил демо открытым на потоке"


