"""Замок входа: PIN установки, предел попыток и список разрешённых номеров.

ЧТО ЗДЕСЬ ОХРАНЯЕТСЯ. Сайт стоит по http на публичном адресе, и до 21.10.2026 вход
был один — номер телефона. Значит, чужой человек, знающий номер, видел чеки, адрес
и куки кабинетов сетей. Замок (план docs/plans/mobilnaya-versiya-2026-10-21.md, Z0)
собирается из трёх необязательных частей, и каждая выключена по умолчанию.

С 25.10.2026 вход идёт в два шага: номер (и PIN установки), затем личный PIN рабочего
места. Личный PIN, второй шаг и страница /pin для него проверяются в
tests/test_login_pin.py; здесь — то, что осталось от замка 21.10.2026, на двух шагах:

    PIN УСТАНОВКИ. Задан — спрашивается на шаге 1 вместе с номером; не задан — этой двери
    нет. Это главное обещание замка: он не имеет права сломать вход тем, кто его не ставил.
    Ставит, меняет и снимает его только владелец с сервера (users.set_pin): со страницы /pin
    его не тронуть, даже зная его (tests/test_login_hardening.py).

    ПРЕДЕЛ ПОПЫТОК. Перебор шестизначного PIN — это миллион догадок. Пять отказов
    с одного IP-адреса (на все номера сразу) за 15 минут закрывают вход с этого IP-адреса.
    Двадцать отказов на всю установку за те же 15 минут НЕ запирают вход: они
    добавляют паузу и обычную проверку PIN, чтобы четыре чужих адреса не держали
    владельца снаружи.

    СПИСОК РАЗРЕШЁННЫХ НОМЕРОВ. Установка — личный сервер, а не сервис: чужому
    номеру здесь делать нечего. Отказ — тем же текстом, что и «это не номер»:
    разные тексты рассказали бы постороннему, что номер в установке есть.

ЧЕГО ЭТИ ТЕСТЫ НЕ ЗНАЮТ. Что замок действительно трудно подобрать на живом сервере:
время PBKDF2 и поведение под нагрузкой здесь не измеряются. И что PIN не угадан
человеком, который пишет его на стикере.
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

from app import config, users  # noqa: E402
from conftest import sign_in  # noqa: E402

PHONE = "79990000051"
STRANGER = "79990000052"
PIN = "432100"                 # PIN установки
MY_PIN = "482915"              # личный PIN рабочего места (его же ставит conftest.sign_in)


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временном корне — как в tests/test_phone_screens.py.

    Корень подменяется целиком: pin.json лежит на томе данных рядом с базами
    (data/pin.json), и писать его в настоящий корень репозитория тест не имеет права.
    """
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    from app.web import login as web_login

    # Счётчики попыток живут в памяти процесса и пережили бы тест: соседние тесты
    # начали бы с чужого счёта. Сбрасываем оба на каждый тест.
    monkeypatch.setattr(web_login, "_FAILS", {})
    monkeypatch.setattr(web_login, "_INSTALL_FAILS", [])

    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    users.deactivate()


def enter(client, phone: str = PHONE, pin: str | None = None, **kwargs):
    """ШАГ 1 входа: номер и (если задан) PIN установки. Вошёл ли человек, тут ещё не решено."""
    data = {"phone": phone, "next": "/"}
    if pin is not None:
        data["pin"] = pin
    return client.post("/login", data=data, **kwargs)


def second(client, phone: str = PHONE, pin: str = MY_PIN, **kwargs):
    """ШАГ 2 входа: личный PIN. Кука шага 1 едет с тем же клиентом."""
    return client.post("/login", data={"step": "2", "phone": phone, "next": "/",
                                       "my_pin": pin, "my_pin2": pin}, **kwargs)


def text(answer) -> str:
    return answer.data.decode("utf-8")


def allowed(monkeypatch, value) -> None:
    """Подставить users.allowed так, как его прочитает app.config.get.

    config.yaml в тестах не правится (он в git и общий), поэтому подменяется сам
    get — и только для этого ключа: остальные настройки остаются настоящими.
    """
    real = config.get

    def fake(path, default=None):
        if path == "users.allowed":
            return value
        return real(path, default)

    monkeypatch.setattr(config, "get", fake)


# ---------- без PIN установки остаётся личный PIN ----------

def test_without_an_installation_pin_the_number_alone_is_not_enough(web):
    """PIN установки не поставлен — общей двери нет, но одного номера мало: нужен личный PIN."""
    assert not users.pin_is_set()
    first = enter(web)
    assert first.status_code == 200 and "Придумайте PIN" in text(first)
    assert web.get("/").status_code == 302, "номер без личного PIN открыл рабочее место"

    answer = sign_in(web, PHONE)
    assert answer.status_code == 302
    assert web.get("/").status_code == 200


def test_without_an_installation_pin_the_login_page_does_not_ask_for_one(web):
    """Поля PIN установки на экране нет: поле обещало бы защиту, которой не существует."""
    page = text(web.get("/login"))
    assert "Вход" in page
    assert 'name="pin"' not in page


# ---------- PIN установки поставлен ----------

def test_with_an_installation_pin_the_number_alone_does_not_open(web):
    """PIN установки задан — без него не пускают даже на второй шаг, и на экране появляется поле."""
    users.set_pin(PIN)
    assert users.pin_is_set()

    page = text(web.get("/login"))
    assert 'name="pin"' in page, "PIN задан, а спросить его негде — войти нельзя"
    assert "6 цифр" in page, "страница входа не говорит, какой длины PIN"

    answer = enter(web)                        # без PIN
    assert answer.status_code == 400
    assert "Неверный PIN" in text(answer)
    assert web.get("/").status_code == 302      # и не вошёл

    answer = enter(web, pin="000000")
    assert answer.status_code == 400
    assert "Неверный PIN" in text(answer)
    assert web.get("/").status_code == 302


def test_with_an_installation_pin_the_right_one_opens_the_second_step_only(web):
    """Верный PIN установки пускает на шаг 2, а не внутрь; сам он в страницу не попадает."""
    users.set_pin(PIN)
    first = enter(web, pin=PIN)
    assert first.status_code == 200 and "Придумайте PIN" in text(first)
    assert PIN not in text(first), "PIN установки лежит в странице шага 2 (скрытым полем?)"
    assert web.get("/").status_code == 302

    answer = second(web)
    assert answer.status_code == 302
    assert answer.headers["Location"] == "/"
    assert web.get("/").status_code == 200

    # И весь вход помощником теста — тот же путь.
    web.post("/logout")
    assert sign_in(web, PHONE, install_pin=PIN).status_code == 302


def test_empty_and_wrong_pins_are_refused_with_the_same_words(web):
    """Ни намёка на то, что PIN был пустой, короткий или почти верный."""
    users.set_pin(PIN)
    empty = text(enter(web, pin=""))
    wrong = text(enter(web, pin="999999"))
    for page in (empty, wrong):
        assert "Неверный PIN" in page
    assert _complaint(empty) == _complaint(wrong)


def _complaint(page: str) -> str:
    """Текст ошибки со страницы входа — по разметке, а не по всему ответу."""
    marker = '<div class="note bad">'
    start = page.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    return page[start:page.find("</div>", start)].strip()


# ---------- предел попыток ----------

def test_the_sixth_attempt_is_refused_without_checking_it(web):
    """Пять отказов — и шестая (даже верная) получает отказ, не проверяясь."""
    users.set_pin(PIN)
    for _ in range(5):
        answer = enter(web, pin="000000")
        assert answer.status_code == 400, "предел сработал раньше пятой попытки"

    sixth = enter(web, pin=PIN)
    assert sixth.status_code == 429, "шестая попытка не отказ — предел не работает"
    assert "Слишком много попыток, подождите 15 минут" in text(sixth)
    assert web.get("/").status_code == 302, "отказ пропустил внутрь"


def test_different_numbers_from_one_address_share_one_limit(web):
    """Смена номера не даёт новых попыток: счёт идёт по адресу, а не по паре.

    PIN проверяется ДО номера, поэтому перебирающий может слать любой номер — или
    мусор в phone. С ключом «номер + адрес» каждая новая строка получала бы свои
    пять попыток, и PIN подбирался бы заметно быстрее. Здесь пять разных
    номеров с одного IP-адреса, а шестая попытка — уже с новым номером и верным PIN:
    и она не проверяется.
    """
    users.set_pin(PIN)
    for i in range(5):
        answer = enter(web, phone=f"7999000000{i}", pin="000000")
        assert answer.status_code == 400, "предел сработал раньше пятого отказа"

    blocked = enter(web, phone="79990000099", pin=PIN)
    assert blocked.status_code == 429, "новый номер обошёл предел адреса"
    assert "Слишком много попыток, подождите 15 минут" in text(blocked)
    assert web.get("/").status_code == 302, "отказ пропустил внутрь"


def test_the_limit_counts_addresses_separately(web):
    """Предел — по адресу: другой адрес с тем же номером не закрыт.

    Значит, чужие догадки с одного места не запирают владельца, зашедшего с другого.
    """
    users.set_pin(PIN)
    for number in ("8 (999) 000-00-51", "+7 999 000 00 51",
                   "79990000052", "79990000053", "79990000054"):
        assert enter(web, phone=number, pin="000000").status_code == 400

    # Другой адрес — не закрыт: счёт свой (первый шаг пройден, дальше — личный PIN).
    answer = enter(web, pin=PIN, environ_base={"REMOTE_ADDR": "10.0.0.9"})
    assert answer.status_code == 200 and "Придумайте PIN" in text(answer)


def test_the_installation_limit_pauses_instead_of_locking_the_owner_out(web, monkeypatch):
    """Двадцать отказов со многих адресов замедляют вход, но не запирают владельца.

    Раньше общий счёт установки отвечал 429 ДО проверки PIN: четыре чужих адреса
    держали владельца снаружи. Теперь на этом счёте — пауза в 1-2 секунды и
    обычная проверка: перебор не ускоряется, а верный PIN проходит.
    """
    from app.web import login as web_login

    users.set_pin(PIN)
    for i in range(20):
        answer = enter(web, phone=PHONE, pin="000000",
                       environ_base={"REMOTE_ADDR": f"10.0.1.{i}"})
        assert answer.status_code == 400, "предел установки сработал раньше двадцатого отказа"

    sleeps: list[float] = []
    checked_after_pause: list[int] = []
    monkeypatch.setattr(web_login.time, "sleep", lambda seconds: sleeps.append(seconds))
    real_check = users.check_pin

    def check(pin):
        checked_after_pause.append(len(sleeps))
        return real_check(pin)

    monkeypatch.setattr(users, "check_pin", check)

    answer = enter(web, pin=PIN, environ_base={"REMOTE_ADDR": "10.0.2.1"})
    assert answer.status_code == 200, "общий предел запер владельца снаружи"
    assert sleeps and 1 <= sleeps[-1] <= 2, "перебор не замедлился паузой"
    assert checked_after_pause and checked_after_pause[-1] >= 1, "PIN проверили до паузы"

    # Шаг 2 идёт на том же счёте: пауза и там, а верный личный PIN проходит.
    assert second(web, environ_base={"REMOTE_ADDR": "10.0.2.1"}).status_code == 302
    check_page = web.get("/")
    assert check_page.status_code == 200, "после верного PIN внутрь не пустили"

    # Неверный PIN на том же счёте — по-прежнему отказ, а не 429: пауза не отменяет
    # проверку и не превращает её в отказ без проверки.
    web.post("/logout")
    wrong = enter(web, pin="000000", environ_base={"REMOTE_ADDR": "10.0.2.2"})
    assert wrong.status_code == 400, "общий предел выдал 429 вместо проверки PIN"
    assert "Неверный PIN" in text(wrong)


def test_the_address_limit_is_logged_with_its_count_and_without_the_phone(web, caplog):
    """Адресный предел говорит в журнал адрес и счёт — и не выдаёт номер телефона.

    По журналу владелец должен понять, откуда и сколько было отказов, но не должен
    найти там номер, который вводили: PIN проверяется до номера, и журнал не станет
    вторым местом, где номер утекает.
    """
    users.set_pin(PIN)
    for _ in range(5):
        assert enter(web, phone=PHONE, pin="000000").status_code == 400

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.web.login"):
        assert enter(web, phone=PHONE, pin=PIN).status_code == 429

    messages = [record.getMessage() for record in caplog.records
                if record.name == "app.web.login"]
    assert any("127.0.0.1" in message and "5" in message for message in messages), messages
    assert all(PHONE not in message for message in messages), "номер попал в журнал"


def test_the_installation_limit_is_logged_with_its_count_and_without_the_phone(web, monkeypatch, caplog):
    """Общий предел тоже называет адрес и счёт, но не номер телефона."""
    from app.web import login as web_login

    users.set_pin(PIN)
    monkeypatch.setattr(web_login.time, "sleep", lambda seconds: None)
    for i in range(20):
        assert enter(web, phone=PHONE, pin="000000",
                     environ_base={"REMOTE_ADDR": f"10.0.3.{i}"}).status_code == 400

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.web.login"):
        assert enter(web, phone=PHONE, pin=PIN,
                     environ_base={"REMOTE_ADDR": "10.0.4.1"}).status_code == 200

    messages = [record.getMessage() for record in caplog.records
                if record.name == "app.web.login"]
    assert any("10.0.4.1" in message and "20" in message for message in messages), messages
    assert all(PHONE not in message for message in messages), "номер попал в журнал"


def test_a_successful_login_does_not_reset_the_address_counter(web):
    """Удачный вход (оба шага) счёт адреса НЕ обнуляет: окно в 15 минут истекает само.

    Иначе перебор чужого PIN шёл бы пачками: четыре догадки, вход в своё место — и снова пять
    (tests/test_login_hardening.py — то же на двух номерах).
    """
    for _ in range(4):
        assert enter(web, phone="12").status_code == 400, "до предела ещё далеко"

    assert sign_in(web, PHONE).status_code == 302
    web.post("/logout")

    # Счёт остался (4): ещё один отказ — пятый, и вход с этого адреса закрыт на окно.
    assert enter(web, phone="12").status_code == 400
    assert enter(web, phone=PHONE).status_code == 429, "вход обнулил счёт адреса"


# ---------- страница /pin глазами установки с общим замком: PIN установки с неё не трогается ----------
#
# Личный PIN рабочего места (придумать, сменить) — tests/test_login_pin.py. PIN установки не
# ставится, не меняется и не снимается со страницы: его знает каждый, кого пустили, и любой
# запер бы им всех остальных. Это делает владелец на сервере (users.set_pin).

def locked_in(web) -> None:
    """У установки стоит PIN установки (его ставит владелец с сервера), человек вошёл."""
    users.set_pin(PIN)
    assert sign_in(web, PHONE, install_pin=PIN).status_code == 302


def test_the_pin_page_needs_the_door_first(web):
    answer = web.get("/pin")
    assert answer.status_code == 302
    assert "/login" in answer.headers["Location"]


def test_the_pin_page_does_not_change_or_remove_the_installation_pin(web):
    """Со страницы /pin общий PIN не меняется и не снимается — даже с верным старым PIN.

    Раньше вошедший, знающий PIN установки (а его знает каждый, кого пустили), мог сменить его
    или снять: запереть всех остальных или открыть установку чужим. Теперь на странице нет ни
    полей, ни ветки в обработчике; PIN установки ведёт владелец на сервере (users.set_pin).
    """
    locked_in(web)
    page = text(web.get("/pin"))
    assert "Замок входа" in page and "6 цифр" in page, "страница /pin не говорит, какой длины PIN"
    for gone in ('name="old_pin"', 'name="new_pin"', "PIN установки (общий)",
                 "Сменить PIN установки", "Снять PIN установки"):
        assert gone not in page, f"на странице /pin осталось {gone!r}"

    for form in ({"old_pin": PIN, "new_pin": "246802", "do": "change"},
                 {"old_pin": PIN, "do": "clear"},
                 {"old_pin": PIN, "new_pin": "  "}):
        answer = web.post("/pin", data=form)
        assert answer.status_code == 400 and "на сервере" in text(answer), form
        assert users.pin_is_set() and users.check_pin(PIN), f"PIN установки тронут: {form}"
        assert not users.check_pin("246802"), f"PIN установки сменён: {form}"


def test_an_acquaintance_cannot_set_the_installation_pin(web):
    """Нет PIN установки — поставить его со страницы нельзя, и полей его на ней нет.

    Вошедший знакомый поставил бы общий PIN и запер бы всех остальных (в том числе
    владельца): его ставят на сервере (users.set_pin).
    """
    assert sign_in(web, PHONE).status_code == 302
    page = text(web.get("/pin"))
    assert 'name="old_pin"' not in page and 'name="new_pin"' not in page, \
        "на странице есть поля PIN установки, которого нет"
    assert "PIN установки (общий)" not in page

    for form in ({"new_pin": PIN}, {"new_pin": PIN, "do": "set"},
                 {"new_pin": PIN, "do": "change", "old_pin": ""},
                 {"old_pin": PIN, "do": "clear"}):
        answer = web.post("/pin", data=form)
        assert answer.status_code == 400, form
        assert "на сервере" in text(answer), "отказ без объяснения"
        assert not users.pin_is_set(), f"PIN установки поставлен со страницы: {form}"


def test_the_pin_page_does_not_tell_a_right_installation_pin_from_a_wrong_one(web):
    """Старый PIN установки на /pin ничего не проверяет: ответ один и тот же, счёт отказов не растёт.

    Иначе страница осталась бы подсказкой для подбора общего PIN (верный и неверный старый PIN
    отвечали бы по-разному), а её предел — обходным путём к нему.
    """
    locked_in(web)
    answers = {text(web.post("/pin", data={"old_pin": old, "new_pin": "246802", "do": "change"}))
               for old in ("000000", PIN)}
    assert len(answers) == 1, "ответ зависит от того, верен ли старый PIN установки"

    for _ in range(8):
        assert web.post("/pin", data={"old_pin": "000000", "do": "clear"}).status_code == 400
    assert enter(web, pin=PIN).status_code == 200, "страница /pin засчитала отказы как подбор"
    assert users.check_pin(PIN)


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
    """«1», «1234», «12345», «abcd» — не PIN: отказ с объяснением, а не «поставлен».

    Четырёхзначный PIN проверяется нарочно: при 20 отказах в 15 минут он подбирается
    за дни, поэтому замком больше не считается.
    """
    for bad in ("1", "1234", "12345", "abcd"):
        with pytest.raises(ValueError) as why:
            users.set_pin(bad)
        assert str(why.value), f"отказ на PIN {bad!r} без объяснения"
        assert not users.pin_is_set(), f"PIN {bad!r} всё-таки поставлен"


def test_removing_the_installation_pin_on_the_server_returns_the_personal_pin_door(web):
    """Владелец снял PIN установки на сервере (users.set_pin('')) — поля на входе нет, остаётся личный PIN."""
    locked_in(web)
    web.post("/logout")
    users.set_pin("")                                     # так снимает замок владелец, не страница
    assert not users.pin_is_set()

    assert 'name="pin"' not in text(web.get("/login"))
    assert enter(web).status_code == 200                  # шаг 1 — без PIN установки
    assert web.get("/").status_code == 302                # одного номера мало
    assert second(web).status_code == 302                 # личный PIN тот, что придумали при входе
    assert web.get("/").status_code == 200


def test_the_accounts_page_points_to_the_lock(web):
    """С экрана «Кабинеты» до замка один шаг: иначе PIN негде сменить."""
    sign_in(web, PHONE)
    page = text(web.get("/accounts"))
    assert "Замок входа (PIN)" in page
    assert 'href="/pin"' in page


def test_the_pin_never_comes_back_to_the_page(web):
    """Введённый PIN не возвращается в разметку — ни при ошибке, ни при удаче."""
    locked_in(web)

    wrong = text(web.post("/pin", data={"old_pin": "000000", "new_pin": "111111"}))
    assert "000000" not in wrong and "111111" not in wrong

    page = text(web.get("/pin"))
    assert PIN not in page and MY_PIN not in page


# ---------- PIN на диске ----------

def test_the_pin_is_not_stored_in_the_clear(web, tmp_path):
    """В файле замка нет самого PIN — только соль и хэш с числом повторов.

    Файл лежит НА ТОМЕ ДАННЫХ (рядом с базами), а не в корне установки: в контейнере
    корень — эфемерный слой образа, и PIN, записанный туда, исчезал бы при каждом
    пересоздании контейнера (Dockerfile, DEPLOY.md).
    """
    users.set_pin(PIN)
    path = os.path.join(str(tmp_path), "data", "pin.json")
    assert users.pin_path() == path, "PIN снова оказался вне тома данных"
    assert os.path.exists(path)

    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    assert PIN not in raw, "PIN лежит в файле открытым текстом"
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
    bad_number = text(enter(web, phone="12"))
    stranger = text(enter(web, phone=STRANGER))

    assert stranger == bad_number or _complaint(stranger) == _complaint(bad_number)
    assert _complaint(stranger), "отказ без объяснения — человек не поймёт, что не так"
    assert web.get("/").status_code == 302


def test_a_number_in_the_list_gets_in(web, monkeypatch):
    """Разрешённый номер пускают, и в каком бы написании он ни был в конфиге."""
    allowed(monkeypatch, ["8 999 000 00 51"])          # список
    assert sign_in(web, "+7 999 000 00 51").status_code == 302

    web.post("/logout")
    allowed(monkeypatch, "79990000051, +79990000099")  # строка через запятую
    assert sign_in(web, "79990000051").status_code == 302


def test_an_empty_list_lets_everyone_in(web, monkeypatch):
    """Пустой список — это «как раньше», а не «никому нельзя»."""
    for value in (None, "", [], "   ", "не телефон"):
        allowed(monkeypatch, value)
        answer = sign_in(web, STRANGER)
        assert answer.status_code == 302, f"users.allowed={value!r} закрыл вход всем"
        web.post("/logout")


# ---------- вход по ссылке: секрет в самой ссылке ----------
#
# Закладка «Передать вход» работает на сайте сети: куки «Моей корзины» у неё нет, PIN
# она не знает и знать не может. Поэтому адрес, который она открывает
# (?phone=…&vhod=…), закрыт секретом САМОГО рабочего места — параметром t. Здесь
# стерегутся обе половины: без секрета не пускает, с верным — пускает.

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


def test_a_link_with_a_number_but_no_secret_does_not_open_the_workspace(web):
    """?phone=…&vhod=… без t — обычный безлогинный запрос, а не вход.

    Раньше этот адрес открывал рабочее место по одному номеру: кто знал номер,
    получал чеки, адрес и куки кабинетов сетей. Так ходила старая закладка, и
    отличать её от чужого перебора было нечем.
    """
    workspace()

    page = web.get(link())
    assert page.status_code == 302, "адрес с одним номером открыл чужое рабочее место"
    assert "/login" in page.headers["Location"], "человека не отправили ко входу"

    # Запрос данных получает отказ кодом, а не перевод: fetch послушно пошёл бы за
    # переводом и принял страницу входа за ответ.
    answer = web.get(link(), headers={"Accept": "application/json"})
    assert answer.status_code == 401
    assert answer.get_json()["ok"] is False


def test_a_link_with_a_foreign_or_broken_secret_is_refused(web):
    """Чужой секрет и выдуманный не пускают: у каждого рабочего места свой."""
    own = workspace(PHONE)
    foreign = workspace(STRANGER)
    assert foreign != own

    for name, secret in (("чужой", foreign), ("выдуманный", "poddelka")):
        answer = web.get(link(secret))
        assert answer.status_code == 302, f"{name} секрет открыл рабочее место"
        assert "/login" in answer.headers["Location"]


def test_the_right_secret_in_the_link_opens_the_workspace(web):
    """Верный секрет — и рабочее место открыто без куки и без входа по номеру.

    Оба груза, которыми ходят закладки, проверяются нарочно: «Передать вход» едет с
    vhod, а закладка чеков — с keys, и закрыть надо обе дороги, а не одну.
    """
    secret = workspace()

    for trigger in ("vhod", "keys"):
        answer = web.get("/accounts?" + urllib.parse.urlencode(
            {"phone": PHONE, trigger: "1", "t": secret}))
        assert answer.status_code == 200, f"верный секрет ссылки не пустил ({trigger})"
        assert "/login" not in answer.headers.get("Location", "")
        assert users.display(PHONE) in answer.data.decode("utf-8")


def test_bookmarks_carry_a_clean_address_and_the_direct_link_still_needs_the_secret(web):
    """Закладка ходит кукой — секрета в адресе нет; прямая ссылка без t закрыта.

    Закладки (docs/hand.src.js, docs/grab.src.js) открывают приложение обычным
    переходом в браузере, где человек уже вошёл: кука (SameSite=Lax) уезжает сама,
    phone= закладка не шлёт. Секрет t в её адресе не нужен — и, попав туда, он был
    бы выдан в историю браузера и на чужую страницу, ничего не защитив. А прямой
    адрес ?phone=…&vhod=… без t по-прежнему не открывает рабочее место: механизм
    остался, но через закладки больше не выдаётся.
    """
    sign_in(web, PHONE)
    secret = workspace()

    pages = {"кабинет": text(web.get("/cabinet?store=magnit")),
             "чеки": text(web.get("/receipts"))}
    for name, page in pages.items():
        assert secret not in page, f"секрет ссылки ({name}) снова попал в закладку"
        assert "%3Ft%3D" not in page, f"в адресе закладки ({name}) снова параметр t"
    assert "%2Fcabinet" in pages["кабинет"], "закладка кабинета не ведёт на /cabinet"
    assert "%2Freceipts" in pages["чеки"], "закладка чеков не ведёт на /receipts"

    # Прямая ссылка с номером: без t — отказ, с верным t — пускает. Куки у клиента
    # здесь уже нет: вышли, как выходит человек, закрывший приложение.
    web.post("/logout")
    direct = "/accounts?" + urllib.parse.urlencode({"phone": PHONE, "vhod": "1"})
    refused = web.get(direct)
    assert refused.status_code == 302, "ссылка с одним номером открыла рабочее место"
    assert "/login" in refused.headers["Location"]
    opened = web.get(direct + "&t=" + secret)
    assert opened.status_code == 200, "верный секрет перестал пускать по прямой ссылке"

