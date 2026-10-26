"""Дыры входа по PIN, найденные независимым ревью безопасности и закрытые 26.10.2026.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Приложение дают чужим людям. Ревью воспроизвело каждую дыру пробой
(sec/test_sec_probe.py: до правки проба проходила, то есть дыра была). Здесь те же сценарии с
правильным ответом — каждая дыра стережётся своим тестом, а не одним общим «вход работает»:

1. Удачный вход не обнуляет счёт неверных попыток адреса (иначе перебор шёл пачками: четыре
   догадки, вход в своё место, ещё четыре).
2. Кука не живёт дольше PIN, под которым выдана: смена PIN отзывает другие устройства, старая кука
   места, у которого появился PIN, не открывает ничего — ни страницы, ни api.* (по куке).
3. Демо не принимает вход в магазин через GET — ни кукой, ни ссылкой с секретом демо.
4. PIN установки (общий замок) со страницы /pin не ставится, не меняется и не снимается — это
   стережёт и tests/test_login_secret.py; здесь — как им управлять с сервера.
5. Слабые PIN (одна цифра, цифры подряд) не ставятся нигде.
6. Общая пауза стоит только перед настоящей сверкой PIN и не занимает потоки пустыми запросами.

ЧЕГО ЭТИ ТЕСТЫ НЕ ЗНАЮТ. Как кука с отпечатком PIN ведёт себя в WebView приложения (там она
живёт отдельно от браузера) и сколько потоков держит waitress: здесь клиент Flask и подменённый
time.sleep, а не сервер под нагрузкой.
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
# Фикстура web — та же, что у входа по PIN: временный корень, счётчики попыток обнулены, витрину демо
# никто не собирает. Дублировать её здесь значило бы разойтись с ней при первой же правке.
from test_login_pin import (MY_PIN, NEW_PIN, OTHER, PHONE, TOO_MANY, WRONG,  # noqa: E402
                            has_a_pin, legacy, step1, step2, text, web)  # noqa: F401

FRIEND_PIN = "246813"
PIN = "432100"                           # PIN установки
DEMO_REFUSAL = "Это демо — изменения не сохраняются"


def signed_in(web, phone: str, pin: str):
    """Новое устройство (свой клиент, своя кука) входит обоими шагами; тот же адрес и то же приложение."""
    client = web.application.test_client()
    assert sign_in(client, phone, pin=pin).status_code == 302
    return client


# ---------- 1. удачный вход не обнуляет счёт адреса ----------

def test_logging_in_to_my_own_place_does_not_give_new_guesses_at_a_strangers(web):
    """4 неверных к чужому номеру, вход к себе, ещё неверная — и следующая попытка закрыта.

    Раньше верный вход обнулял счёт адреса: четыре догадки, вход в СВОЁ место (свой номер и
    свой PIN есть у любого знакомого) — и снова пять догадок. Проба ревью test_reset_by_own_login:
    24 догадки подряд, предела нет.
    """
    has_a_pin(PHONE, MY_PIN)
    has_a_pin(OTHER, FRIEND_PIN)
    step1(web, PHONE)
    for _ in range(4):
        assert step2(web, WRONG, phone=PHONE).status_code == 400

    own = web.application.test_client()                  # тот же адрес, свой номер и свой PIN
    step1(own, OTHER)
    assert step2(own, FRIEND_PIN, phone=OTHER).status_code == 302

    assert step2(web, WRONG, phone=PHONE).status_code == 400      # пятая за окно — ещё проверяется
    sixth = step2(web, MY_PIN, phone=PHONE)                       # шестая закрыта, даже с верным PIN
    assert sixth.status_code == 429 and TOO_MANY in text(sixth)
    assert web.get("/").status_code == 302, "закрытый вход пропустил внутрь"


def test_a_long_run_of_guesses_with_own_logins_between_is_stopped_at_five(web):
    """Круги «4 догадки + вход к себе» (проба ревью): проверяются ровно пять догадок из двадцати четырёх."""
    has_a_pin(PHONE, MY_PIN)
    has_a_pin(OTHER, FRIEND_PIN)
    checked = refused = 0
    for _ in range(6):
        step1(web, PHONE)
        for _ in range(4):
            code = step2(web, WRONG, phone=PHONE).status_code
            checked += code == 400
            refused += code == 429
        own = web.application.test_client()
        step1(own, OTHER)
        step2(own, FRIEND_PIN, phone=OTHER)
    assert checked == 5 and checked + refused == 24, (checked, refused)


def test_a_right_old_pin_on_the_pin_page_does_not_reset_the_guesses_either(web):
    """Старый PIN на /pin верен, новый отклонён — счёт адреса всё равно остаётся (проба test_reset_by_pin_page)."""
    has_a_pin(PHONE, MY_PIN)
    friend = signed_in(web, OTHER, FRIEND_PIN)
    step1(web, PHONE)
    for _ in range(4):
        assert step2(web, WRONG, phone=PHONE).status_code == 400

    answer = friend.post("/pin", data={"do": "my", "my_old": FRIEND_PIN, "my_pin": "1", "my_pin2": "1"})
    assert answer.status_code == 400 and "шести" in text(answer)    # старый верен, новый короток

    assert step2(web, WRONG, phone=PHONE).status_code == 400        # пятая
    assert step2(web, MY_PIN, phone=PHONE).status_code == 429
    assert users.check_workspace_pin(OTHER, FRIEND_PIN), "PIN друга сменился"


# ---------- 2. кука не живёт дольше PIN ----------

def test_an_old_cookie_without_a_stamp_is_dead_once_the_place_gets_a_pin(web):
    """Кука, выданная до PIN-ов, при появившемся PIN не открывает ничего (проба ревью test_old_cookie_...)."""
    has_a_pin(PHONE, MY_PIN)                    # владелец поставил PIN с сервера (место уже было)
    for path in ("/", "/accounts", "/receipts", "/basket", "/phone/basket"):
        legacy(web, PHONE)                      # кука выдана до PIN-ов: номер есть, отпечатка PIN нет
        answer = web.get(path, headers={"Accept": "text/html"})
        assert answer.status_code == 302, f"{path}: старая кука открыла место с PIN"
        assert "/login" in answer.headers["Location"] and "/pin" not in answer.headers["Location"], path

    # Кука стёрта у клиента, а не только отклонена на этом запросе: ответ велит браузеру её забыть.
    legacy(web, PHONE)
    answer = web.get("/", headers={"Accept": "text/html"})
    name = web.application.config["SESSION_COOKIE_NAME"]
    assert any(c.startswith(f"{name}=;") for c in answer.headers.getlist("Set-Cookie")), "кука осталась у клиента"
    assert web.get("/login").status_code == 200

    # Запрос данных получает отказ кодом, форма — перевод; ничего не записано.
    legacy(web, PHONE)
    answer = web.get("/basket", headers={"Accept": "application/json"})
    assert answer.status_code == 401 and answer.get_json()["ok"] is False

    users.open_workspace(PHONE)
    before = location.address()
    users.deactivate()
    legacy(web, PHONE)
    answer = web.post("/address", data={"address": "Москва, Тверская улица 1"})
    assert answer.status_code == 302 and "/login" in answer.headers["Location"]
    users.open_workspace(PHONE)
    assert location.address() == before, "старая кука записала адрес"
    users.deactivate()


def test_an_old_cookie_at_a_place_without_a_pin_still_goes_to_the_pin_page(web):
    """Место без PIN — как раньше: старая кука не стирается, а ведёт придумывать PIN."""
    legacy(web, PHONE)
    answer = web.get("/")
    assert answer.status_code == 302 and answer.headers["Location"] == "/pin?next=/"
    assert web.get("/pin").status_code == 200, "кука места без PIN стёрта"

    # Придумав PIN здесь, это устройство остаётся внутри: оно получает отпечаток сразу.
    answer = web.post("/pin", data={"do": "my", "my_pin": MY_PIN, "my_pin2": MY_PIN})
    assert answer.status_code == 302
    assert web.get("/").status_code == 200, "устройство, придумавшее PIN, вышло"


def test_an_old_cookie_does_not_open_the_api_door_but_the_secret_still_does(web):
    """api.* по старой куке при живом PIN — отказ 401; секрет в теле открывает дверь сам, кука ни при чём.

    Приложение и расширение ходят в api.* с номером и секретом в теле: протухшая кука у них в
    WebView не должна ломать эти вызовы, но и открывать дверь без секрета она не вправе.
    """
    has_a_pin(PHONE, MY_PIN)
    users.open_workspace(PHONE)
    secret = api.workplace_secret()
    users.deactivate()

    legacy(web, PHONE)
    answer = web.post("/api/cartplan", json={"store": "dixy"})
    assert answer.status_code == 401, "старая кука открыла api.* без секрета"

    legacy(web, PHONE)
    answer = web.post("/api/device/connected", json={
        "workplace": PHONE, "secret": secret, "store": "dixy", "connected": True})
    assert answer.status_code == 200 and answer.get_json()["ok"] is True

    # Контроль: кука, выданная за PIN, проходит дверь и без секрета.
    mine = signed_in(web, PHONE, MY_PIN)
    assert mine.post("/api/cartplan", json={"store": "dixy"}).status_code == 200


def test_changing_the_pin_signs_the_other_devices_out_and_keeps_this_one(web):
    """Смена PIN на устройстве А: Б выходит на вход, А остаётся (проба ревью test_pin_change_does_not_kill_...)."""
    has_a_pin(PHONE, MY_PIN)
    a = signed_in(web, PHONE, MY_PIN)
    b = signed_in(web, PHONE, MY_PIN)
    assert a.get("/").status_code == 200 and b.get("/").status_code == 200

    answer = a.post("/pin", data={"do": "my", "my_old": MY_PIN, "my_pin": NEW_PIN, "my_pin2": NEW_PIN})
    assert answer.status_code == 302 and "saved=my_changed" in answer.headers["Location"]
    assert a.get("/").status_code == 200, "устройство, где сменили PIN, вышло"
    assert a.get("/pin?saved=my_changed").status_code == 200

    gone = b.get("/")
    assert gone.status_code == 302 and "/login" in gone.headers["Location"], "второе устройство осталось внутри"
    assert b.get("/login").status_code == 200, "кука второго устройства не стёрта"

    # Войти там можно только новым PIN.
    step1(b, PHONE)
    assert step2(b, MY_PIN, phone=PHONE).status_code == 400
    assert step2(b, NEW_PIN, phone=PHONE).status_code == 302
    assert b.get("/").status_code == 200


def test_the_same_pin_set_again_is_still_a_new_pin_for_the_other_devices(web):
    """Новая запись PIN — новая соль, а с ней новый отпечаток: «сменить на тот же» тоже отзывает другие устройства."""
    has_a_pin(PHONE, MY_PIN)
    a = signed_in(web, PHONE, MY_PIN)
    b = signed_in(web, PHONE, MY_PIN)
    answer = a.post("/pin", data={"do": "my", "my_old": MY_PIN, "my_pin": MY_PIN, "my_pin2": MY_PIN})
    assert answer.status_code == 302
    assert a.get("/").status_code == 200
    assert b.get("/").status_code == 302


def test_a_pin_changed_on_the_server_signs_every_device_out(web):
    """Владелец сменил PIN с сервера (забытый, украденный): все выданные до этого куки больше не годятся."""
    has_a_pin(PHONE, MY_PIN)
    device = signed_in(web, PHONE, MY_PIN)
    assert device.get("/").status_code == 200
    users.set_workspace_pin(PHONE, NEW_PIN)
    answer = device.get("/")
    assert answer.status_code == 302 and "/login" in answer.headers["Location"]


def test_logging_out_works_with_an_outdated_cookie(web):
    """«Выйти» на странице, открытой до смены PIN, ведёт на вход, а не на /login?next=/logout (там только POST)."""
    legacy(web, PHONE)
    has_a_pin(PHONE, MY_PIN)
    answer = web.post("/logout")
    assert answer.status_code == 302 and answer.headers["Location"].endswith("/login")
    assert "next=" not in answer.headers["Location"]


def test_the_cookie_holds_a_stamp_not_the_pin_nor_its_hash(web):
    """В куке (она подписана, но читается) — только короткий след записи PIN: ни PIN, ни хэш, ни соль."""
    from flask.sessions import SecureCookieSessionInterface

    has_a_pin(PHONE, MY_PIN)
    sign_in(web, PHONE, pin=MY_PIN)
    serializer = SecureCookieSessionInterface().get_signing_serializer(web.application)
    session = dict(serializer.loads(web.get_cookie(web.application.config["SESSION_COOKIE_NAME"]).value))

    users.open_workspace(PHONE)
    record = json.loads(repo.get_setting(users.LOGIN_PIN_KEY))
    users.deactivate()
    stamp = session["pin_stamp"]
    assert len(stamp) == 16 and int(stamp, 16) >= 0
    assert stamp not in record["hash"] and stamp not in record["salt"], "в куке кусок записи PIN"
    assert MY_PIN not in json.dumps(session)


# ---------- 3. демо не принимает вход в магазин через GET ----------

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


def test_the_demo_takes_no_store_login_from_a_get(web):
    """Проба ревью test_demo_get_writes_store_session: GET /cabinet?store=…&vhod=… в демо сохранял вход."""
    assert web.post("/demo").status_code == 302
    answer = web.get(f"/cabinet?store=magnit&vhod={packed()}")
    assert answer.status_code == 303 and answer.headers["Location"].endswith("/cabinet?store=magnit")
    answer = web.get(f"/cabinet?store=magnit&keys={packed()}&format=json")
    assert answer.status_code == 403 and answer.get_json() == {"ok": False, "error": DEMO_REFUSAL}
    assert not saved_login(users.DEMO), "в демо появился вход в магазин"

    # Контроль: у настоящего места тот же адрес вход сохраняет, то есть тест не ловит всё подряд.
    mine = signed_in(web, PHONE, MY_PIN)
    assert mine.get(f"/cabinet?store=magnit&vhod={packed()}").status_code == 303
    assert saved_login(PHONE)


def test_the_demo_cannot_be_opened_by_a_link_secret(web):
    """Ссылка ?phone=demo&t=<секрет демо> шла мимо сторожа, то есть мимо запрета записи в демо.

    Секрет демо виден каждому, кто открыл его «Кабинеты». Теперь эта дверь демо не открывает вовсе:
    ни GET с входом в магазин, ни POST любого экрана.
    """
    web.post("/demo")
    users.open_workspace(users.DEMO)
    secret = users.link_secret(users.DEMO)
    before = location.address()
    users.deactivate()

    stranger = web.application.test_client()             # без куки
    link = urllib.parse.urlencode({"store": "magnit", "phone": "demo", "t": secret, "vhod": packed()})
    answer = stranger.get("/cabinet?" + link)
    assert answer.status_code == 302 and "/login" in answer.headers["Location"]
    assert not saved_login(users.DEMO), "демо приняло вход по ссылке с секретом демо"

    answer = stranger.post("/address?" + urllib.parse.urlencode({"phone": "demo", "t": secret, "vhod": "1"}),
                           data={"address": "Москва, Тверская улица 1"})
    assert answer.status_code == 302 and "/login" in answer.headers["Location"]
    users.open_workspace(users.DEMO)
    assert location.address() == before, "адрес в общем демо изменён ссылкой с секретом демо"
    users.deactivate()

    # Контроль: ссылка с секретом настоящего места по-прежнему открывает его (закладки, test_login_secret).
    users.open_workspace(PHONE)
    own = users.link_secret(PHONE)
    users.deactivate()
    answer = stranger.get("/accounts?" + urllib.parse.urlencode({"phone": PHONE, "vhod": "1", "t": own}))
    assert answer.status_code == 200


# ---------- 4. PIN установки ведёт владелец с сервера ----------

def test_the_installation_pin_is_managed_on_the_server_and_the_docstring_says_how(web):
    users.set_pin("483915")
    assert users.check_pin("483915")
    users.set_pin("690274")
    assert users.check_pin("690274") and not users.check_pin("483915")
    users.set_pin("")
    assert not users.pin_is_set()

    doc = users.set_pin.__doc__
    assert 'docker exec korzina python -c "from app import users; users.set_pin(' in doc
    assert "users.set_pin('')" in doc, "в докстринге нет команды, которая снимает PIN установки"


# ---------- 5. слабые PIN ----------

WEAK = ["000000", "111111", "999999", "0000000000",                 # одна цифра
        "123456", "234567", "345678", "456789", "012345", "1234567", "12345678",  # подряд вверх
        "987654", "876543", "765432", "654321", "543210", "9876543",              # подряд вниз
        "567890", "890123", "109876", "1234567890", "9876543210"]                # по кругу цифр


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


def test_a_weak_pin_is_refused_when_inventing_it_at_login(web):
    step1(web)
    for weak in ("123456", "111111", "654321"):
        answer = step2(web, weak, weak)
        page = text(answer)
        assert answer.status_code == 400 and users.PIN_TOO_SIMPLE in page, weak
        assert "Придумайте PIN" in page, "после отказа не вернули на «придумайте»"
        assert not users.exists(PHONE), "слабый PIN завёл рабочее место"

    # Это не догадки: PIN ещё только придумывают, и счёт отказов не растёт.
    for _ in range(8):
        assert step2(web, "123456", "123456").status_code == 400
    assert step2(web, MY_PIN, MY_PIN).status_code == 302


def test_a_weak_pin_is_refused_on_the_pin_page_and_on_the_server(web):
    sign_in(web, PHONE, pin=MY_PIN)
    for weak in ("111111", "123456", "987654"):
        answer = web.post("/pin", data={"do": "my", "my_old": MY_PIN, "my_pin": weak, "my_pin2": weak})
        assert answer.status_code == 400 and users.PIN_TOO_SIMPLE in text(answer), weak
        assert users.check_workspace_pin(PHONE, MY_PIN), f"PIN сменился на слабый {weak}"

    # Придумывание на /pin у места без PIN (старая кука) — то же.
    legacy(web, OTHER)
    answer = web.post("/pin", data={"do": "my", "my_pin": "000000", "my_pin2": "000000"})
    assert answer.status_code == 400 and users.PIN_TOO_SIMPLE in text(answer)
    assert not users.workspace_pin_is_set(OTHER)

    # И владельцу с сервера слабый PIN не ставится — ни личный, ни установки.
    with pytest.raises(ValueError):
        users.set_workspace_pin(PHONE, "123456")
    with pytest.raises(ValueError):
        users.set_pin("111111")
    assert not users.pin_is_set()


def test_the_screens_that_invent_a_pin_carry_the_hint(web):
    """«Не дату рождения и не 123456» — на «Придумайте PIN» и на /pin."""
    hint = "не дату рождения и не 123456"
    assert hint in text(step1(web)).lower(), "на экране «Придумайте PIN» нет подсказки"
    sign_in(web, OTHER, pin=FRIEND_PIN)
    assert hint in text(web.get("/pin")).lower(), "на странице /pin нет подсказки"


# ---------- 6. пауза только перед настоящей сверкой ----------

def full_installation_counter() -> None:
    """Общий счёт установки полон (20 отказов за окно): с этого места каждая сверка PIN ждёт."""
    web_login._INSTALL_FAILS[:] = [time.monotonic()] * web_login.INSTALL_FAIL_LIMIT


def test_empty_requests_do_not_sleep_when_the_installation_counter_is_full(web, monkeypatch):
    """Проба ревью test_restart_path_not_counted_but_sleeps: 30 пустых POST /login держали 30 × 1,5 с."""
    slept: list[float] = []
    monkeypatch.setattr(web_login.time, "sleep", lambda seconds: slept.append(seconds))
    full_installation_counter()

    for _ in range(30):                                      # шаг 2 без шага 1
        assert step2(web, "1", phone=PHONE).status_code == 400
    assert slept == []
    assert web_login._FAILS == {}, "пустые запросы записаны как догадки"

    assert step1(web).status_code == 200                     # шаг 1 нового номера: PIN не сверяется
    assert step2(web, "12345", "12345").status_code == 400   # короткий PIN
    assert step2(web, MY_PIN, WRONG).status_code == 400      # повтор не совпал
    assert step2(web, "123456", "123456").status_code == 400  # слабый PIN
    assert step1(web, "12").status_code == 400               # «это не номер»
    assert slept == [], f"пауза на запросе без сверки PIN: {slept}"


def test_the_pause_stands_right_before_each_real_check(web, monkeypatch):
    """Шаг 1 с PIN установки, шаг 2 с личным PIN, старый PIN на /pin: по одной паузе на сверку, не больше."""
    slept: list[float] = []
    monkeypatch.setattr(web_login.time, "sleep", lambda seconds: slept.append(seconds))
    users.set_pin(PIN)
    has_a_pin(PHONE, MY_PIN)
    full_installation_counter()

    assert web.post("/login", data={"phone": PHONE, "pin": PIN, "next": "/"}).status_code == 200
    assert slept == [web_login.INSTALL_PAUSE_SEC]
    assert step2(web, MY_PIN).status_code == 302
    assert len(slept) == 2

    # /pin: «поменять PIN установки» отказ без сверки, паузы нет; личный PIN без присланного старого — сверка и пауза.
    web.post("/pin", data={"do": "change", "old_pin": PIN, "new_pin": "246802"})
    assert len(slept) == 2, "пауза на запросе, где PIN не сверяется"
    web.post("/pin", data={"do": "my", "my_pin": NEW_PIN, "my_pin2": WRONG})
    assert len(slept) == 3

    answer = web.post("/pin", data={"do": "my", "my_old": MY_PIN, "my_pin": NEW_PIN, "my_pin2": NEW_PIN})
    assert answer.status_code == 302
    assert len(slept) == 4

    # Придумывание PIN у места без PIN сверять нечего — пауза не нужна.
    elder = web.application.test_client()
    legacy(elder, OTHER)
    answer = elder.post("/pin", data={"do": "my", "my_pin": "12345", "my_pin2": "12345"})
    assert answer.status_code == 400
    assert len(slept) == 4, "пауза там, где PIN не сверяется"
