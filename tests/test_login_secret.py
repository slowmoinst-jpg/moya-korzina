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
    """Вход: номер и (если задан) PIN установки."""
    data = {"phone": phone, "next": "/"}
    if pin is not None:
        data["pin"] = pin
    return client.post("/login", data=data, **kwargs)


def text(answer) -> str:
    return answer.data.decode("utf-8")


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
    assert answer.status_code == 302
    assert answer.headers["Location"] == "/"
    assert web.get("/").status_code == 200


def test_without_an_installation_pin_the_login_page_does_not_ask_for_one(web):
    """Поля PIN установки на экране нет: поле обещало бы защиту, которой не существует."""
    page = text(web.get("/login"))
    assert "Вход" in page
    assert 'name="pin"' not in page


def test_valid_new_number_signs_in_in_one_step_and_pin_page_is_404(web):
    """Годный новый номер одним POST /login получает 302 на next, следующий GET /
    открывает его место без увода на /pin, GET /pin отвечает 404.
    """
    new_phone = "79991234500"
    assert not users.exists(new_phone)
    res = web.post("/login", data={"phone": "+7 999 123-45-00", "next": "/phone/basket"})
    assert res.status_code == 302
    assert res.headers["Location"] == "/phone/basket"
    # Следующий GET / открывает его место без увода на /pin
    root = web.get("/")
    assert root.status_code == 200
    # GET /pin отвечает 404
    assert web.get("/pin").status_code == 404


@pytest.mark.parametrize("raw, expected", [
    (None, "/"),
    ("", "/"),
    ("   ", "/"),
    ("not-a-path", "/"),
    ("https://зло.example", "/"),
    ("http://зло.example", "/"),
    ("//зло.example", "/"),
    ("///зло.example", "/"),
    ("/\\зло.example", "/"),
    ("/\t/зло.example", "/"),
    ("/\n/зло.example", "/"),
    ("/", "/"),
    ("/phone/basket", "/phone/basket"),
    ("/receipts?id=12", "/receipts?id=12"),
    ("  /phone/basket  ", "/phone/basket"),
])
def test_next_keeps_only_a_path_inside_the_site(raw, expected):
    from app.web.login import _safe_next

    assert _safe_next(raw) == expected


def test_a_login_starts_the_catalog_linking_only_when_it_succeeds(web, monkeypatch):
    """Сопоставление товаров чека с каталогом зовётся при удачном входе фоном."""
    started: list[str] = []
    from app.catalog import link

    monkeypatch.setattr(link, "link_soon", lambda phone: started.append(phone))

    assert web.post("/login", data={"phone": "12"}).status_code == 400
    assert started == []

    assert web.post("/login", data={"phone": PHONE}).status_code == 302
    assert started == [PHONE]


# ---------- PIN установки поставлен ----------

def test_with_an_installation_pin_the_number_alone_does_not_open(web):
    """PIN установки задан — без него не пускают, и на экране появляется поле."""
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


def test_with_an_installation_pin_the_right_one_opens_the_workspace(web):
    """Верный PIN установки сразу открывает рабочее место."""
    users.set_pin(PIN)
    answer = enter(web, pin=PIN)
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
    """Смена номера не даёт новых попыток: счёт идёт по адресу, а не по паре."""
    users.set_pin(PIN)
    for i in range(5):
        answer = enter(web, phone=f"7999000000{i}", pin="000000")
        assert answer.status_code == 400, "предел сработал раньше пятого отказа"

    blocked = enter(web, phone="79990000099", pin=PIN)
    assert blocked.status_code == 429, "новый номер обошёл предел адреса"
    assert "Слишком много попыток, подождите 15 минут" in text(blocked)
    assert web.get("/").status_code == 302, "отказ пропустил внутрь"


def test_the_limit_counts_addresses_separately(web):
    """Предел — по адресу: другой адрес с тем же номером не закрыт."""
    users.set_pin(PIN)
    for number in ("8 (999) 000-00-51", "+7 999 000 00 51",
                   "79990000052", "79990000053", "79990000054"):
        assert enter(web, phone=number, pin="000000").status_code == 400

    # Другой адрес — не закрыт: счёт свой.
    answer = enter(web, pin=PIN, environ_base={"REMOTE_ADDR": "10.0.0.9"})
    assert answer.status_code == 302


def test_the_installation_limit_pauses_instead_of_locking_the_owner_out(web, monkeypatch):
    """Двадцать отказов со многих адресов замедляют вход, но не запирают владельца."""
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
    assert answer.status_code == 302, "общий предел запер владельца снаружи"
    assert sleeps and 1 <= sleeps[-1] <= 2, "перебор не замедлился паузой"
    assert checked_after_pause and checked_after_pause[-1] >= 1, "PIN проверили до паузы"

    check_page = web.get("/")
    assert check_page.status_code == 200, "после верного PIN внутрь не пустили"

    # Неверный PIN на том же счёте — по-прежнему отказ, а не 429: пауза не отменяет
    # проверку и не превращает её в отказ без проверки.
    web.post("/logout")
    wrong = enter(web, pin="000000", environ_base={"REMOTE_ADDR": "10.0.2.2"})
    assert wrong.status_code == 400, "общий предел выдал 429 вместо проверки PIN"
    assert "Неверный PIN" in text(wrong)


def test_the_address_limit_is_logged_with_its_count_and_without_the_phone(web, caplog):
    """Адресный предел говорит в журнал адрес и счёт — и не выдаёт номер телефона."""
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
                     environ_base={"REMOTE_ADDR": "10.0.4.1"}).status_code == 302

    messages = [record.getMessage() for record in caplog.records
                if record.name == "app.web.login"]
    assert any("10.0.4.1" in message and "20" in message for message in messages), messages
    assert all(PHONE not in message for message in messages), "номер попал в журнал"


def test_a_successful_login_does_not_reset_the_address_counter(web):
    """Удачный вход счёт адреса НЕ обнуляет: окно в 15 минут истекает само."""
    for _ in range(4):
        assert enter(web, phone="12").status_code == 400, "до предела ещё далеко"

    assert sign_in(web, PHONE).status_code == 302
    web.post("/logout")

    # Счёт остался (4): ещё один отказ — пятый, и вход с этого адреса закрыт на окно.
    assert enter(web, phone="12").status_code == 400
    assert enter(web, phone=PHONE).status_code == 429, "вход обнулил счёт адреса"


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


def test_removing_the_installation_pin_on_the_server_returns_single_step_login(web):
    """Владелец снял PIN установки на сервере (users.set_pin('')) — вход снова по одному номеру."""
    users.set_pin(PIN)
    users.set_pin("")                                     # так снимает замок владелец
    assert not users.pin_is_set()

    assert 'name="pin"' not in text(web.get("/login"))
    assert enter(web).status_code == 302
    assert web.get("/").status_code == 200


def test_the_pin_never_comes_back_to_the_page(web):
    """Введённый PIN не возвращается в разметку — ни при ошибке, ни при удаче."""
    users.set_pin(PIN)
    wrong = text(enter(web, pin="000000"))
    assert "000000" not in wrong


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
    """?phone=…&vhod=… без t — обычный безлогинный запрос, а не вход."""
    workspace()

    page = web.get(link())
    assert page.status_code == 302, "адрес с одним номером открыл чужое рабочее место"
    assert "/login" in page.headers["Location"], "человека не отправили ко входу"

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
    """Верный секрет — и рабочее место открыто без куки и без входа по номеру."""
    secret = workspace()

    for trigger in ("vhod", "keys"):
        answer = web.get("/accounts?" + urllib.parse.urlencode(
            {"phone": PHONE, trigger: "1", "t": secret}))
        assert answer.status_code == 200, f"верный секрет ссылки не пустил ({trigger})"
        assert "/login" not in answer.headers.get("Location", "")
        assert users.display(PHONE) in answer.data.decode("utf-8")


def test_bookmarks_carry_a_clean_address_and_the_direct_link_still_needs_the_secret(web):
    """Закладка ходит кукой — секрета в адресе нет; прямая ссылка без t закрыта."""
    sign_in(web, PHONE)
    secret = workspace()

    pages = {"кабинет": text(web.get("/cabinet?store=magnit")),
             "чеки": text(web.get("/receipts"))}
    for name, page in pages.items():
        assert secret not in page, f"секрет ссылки ({name}) снова попал в закладку"
        assert "%3Ft%3D" not in page, f"в адресе закладки ({name}) снова параметр t"
    assert "%2Fcabinet" in pages["кабинет"], "закладка кабинета не ведёт на /cabinet"
    assert "%2Freceipts" in pages["чеки"], "закладка чеков не ведёт на /receipts"

    web.post("/logout")
    direct = "/accounts?" + urllib.parse.urlencode({"phone": PHONE, "vhod": "1"})
    refused = web.get(direct)
    assert refused.status_code == 302, "ссылка с одним номером открыла рабочее место"
    assert "/login" in refused.headers["Location"]
    opened = web.get(direct + "&t=" + secret)
    assert opened.status_code == 200, "верный секрет перестал пускать по прямой ссылке"


# ---------- демо для просмотра: только для чтения ----------

def test_demo_is_not_a_number_to_type():
    """Слово demo в поле номера не принимается: в демо ведёт только кнопка."""
    assert users.resolve_login("demo") == (None, users.NOT_A_PHONE)
    assert users.resolve_login("DEMO") == (None, users.NOT_A_PHONE)


def test_the_demo_button_signs_in_and_fills_the_showcase(web, monkeypatch):
    """Кнопка «Посмотреть демо» открывает рабочее место demo и наливает витрину."""
    filled: list[bool] = []
    from app import demo as showcase

    monkeypatch.setattr(showcase, "ensure", lambda: filled.append(True) or True)

    answer = web.post("/demo")
    assert answer.status_code == 302 and answer.headers["Location"] == "/"
    assert filled == [True]

    main = text(web.get("/"))
    assert "Демо" in main or "demo" in main


def test_the_demo_reads_every_screen(web):
    """Демо читает все экраны без падений."""
    from app.web.views import SCREENS

    web.post("/demo")
    for screen in SCREENS:
        assert web.get(screen.path).status_code == 200, screen.path
        assert web.head(screen.path).status_code == 200, screen.path


def test_a_form_in_the_demo_goes_back_with_the_demo_flag(web):
    """Отправка формы в демо переводится назад с demo=1, запись не проходит."""
    web.post("/demo")
    users.open_workspace(users.DEMO)
    before = location.address()
    users.deactivate()

    def post(**headers):
        return web.post("/address", data={"address": "Москва, Тверская улица 1"}, headers=headers)

    answer = post(Referer="http://localhost/phone/basket?q=1")
    assert answer.status_code == 303
    assert answer.headers["Location"] == "/phone/basket?q=1&demo=1"
    assert post().headers["Location"] == "/?demo=1"
    assert post(Referer="https://evil.example/phone/basket").headers["Location"] == "/?demo=1"
    assert post(Referer="http://localhost//evil.example/x").headers["Location"] == "/?demo=1"
    assert post(Referer="http://[").headers["Location"] == "/?demo=1"
    assert post(Referer="http://localhost/phone/basket?demo=1").headers["Location"] == "/phone/basket?demo=1"

    users.open_workspace(users.DEMO)
    assert location.address() == before, "адрес в общем демо изменился"
    users.deactivate()


def test_a_json_request_in_the_demo_gets_403_and_changes_nothing(web):
    """Запрос данных в демо получает 403 JSON."""
    web.post("/demo")
    users.open_workspace(users.DEMO)
    before = location.address()
    users.deactivate()

    for kwargs in ({"json": {"address": "Москва, Тверская улица 1"}},
                   {"data": {"address": "Москва, Тверская улица 1"},
                    "headers": {"Accept": "application/json"}}):
        answer = web.post("/address", **kwargs)
        assert answer.status_code == 403, kwargs
        assert answer.get_json() == {"ok": False, "error": DEMO_REFUSAL}

    users.open_workspace(users.DEMO)
    assert location.address() == before
    users.deactivate()


def test_the_device_api_refuses_the_demo_by_session_and_by_secret(web):
    """API устройства (/api/device/*) не пускает демо ни сессией, ни секретом."""
    web.post("/demo")
    for path in ("/api/device/cartplan", "/api/device/cartreport", "/api/device/connected",
                 "/api/prices"):
        answer = web.post(path, json={"store": "dixy"})
        assert answer.status_code == 403, path
        assert answer.get_json() == {"ok": False, "error": DEMO_REFUSAL}, path

    users.open_workspace(users.DEMO)
    demo_secret = api.workplace_secret()
    users.deactivate()
    users.open_workspace(PHONE)
    own_secret = api.workplace_secret()
    users.deactivate()

    anonymous = web.application.test_client()
    answer = anonymous.post("/api/device/connected", json={
        "workplace": "demo", "secret": demo_secret, "store": "dixy", "connected": True})
    assert answer.status_code == 401
    assert answer.get_json() == {"ok": False, "error": api.DENIED}

    answer = anonymous.post("/api/device/connected", json={
        "workplace": PHONE, "secret": own_secret, "store": "dixy", "connected": True})
    assert answer.status_code == 200


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


def test_the_demo_can_be_left_and_entered_again(web):
    """Выход из демо возвращает на страницу входа, где снова можно нажать «Посмотреть демо»."""
    web.post("/demo")
    assert web.post("/demo").status_code == 302          # кнопка работает и из самого демо
    assert web.get("/").status_code == 200

    assert web.post("/logout").status_code == 302
    assert web.get("/").status_code == 302

    assert web.post("/demo").status_code == 302
    assert web.get("/").status_code == 200
