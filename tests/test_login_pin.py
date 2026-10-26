"""Вход «номер + личный PIN», страница /pin и демо только для чтения (25.10.2026).

ЧТО ЗДЕСЬ ОХРАНЯЕТСЯ. Владелец раздаёт приложение знакомым. При входе по одному номеру
знакомый, знающий номер владельца, открывал его чеки, адрес, входы в магазины и ключ ФНС.
Теперь у каждого рабочего места свой PIN, и каждая ветка этого замка стережётся отдельно:

- вход в два шага: новый номер придумывает PIN, номер с PIN вводит его, старое место без PIN
  тоже придумывает; шаг 1 не заводит рабочее место; шаг 2 нельзя прислать мимо шага 1;
- пределы попыток: неверные личные PIN считаются, ни шаг 1, ни верный PIN счёт не обнуляют;
- PIN нигде не лежит открытым текстом: ни на диске, ни в куке, ни в странице, ни в журнале;
- старая кука места без PIN уводит на /pin, пока PIN не придуман;
- страница /pin: придумать и сменить личный PIN (со старым, с пределом);
- демо одно на всех и только для чтения: форма уходит назад с demo=1, JSON и api.* получают
  403, API устройства демо не пускает, а выйти из демо и нажать «Посмотреть демо» можно.

PIN установки, список разрешённых номеров и вход по ссылке с секретом стережёт
tests/test_login_secret.py: здесь они только там, где задевают личный PIN. Дыры, найденные
ревью безопасности и закрытые 26.10.2026 (отпечаток PIN в куке, демо и GET, слабые PIN, пауза
перед сверкой, PIN установки не со страницы), — в tests/test_login_hardening.py.

ЧЕГО ЭТИ ТЕСТЫ НЕ ЗНАЮТ. Как страницы выглядят на ширине 360 и 390 и увеличивает ли Safari
страницу при фокусе на поле: проверяется только разметка (шрифт 16 px, числовая клавиатура),
а не телефон в руке. И сколько времени хэш PBKDF2 займёт на живом сервере: набор гоняет его
с облегчённым числом повторов (боевое число проверяет только
test_a_workspace_pin_is_stored_hashed_and_checked).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import urllib.parse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import api, config, location, repo, users  # noqa: E402
from conftest import sign_in  # noqa: E402

PHONE = "79990000071"
OTHER = "79990000072"
MY_PIN = "735190"          # необычный нарочно: поиск «PIN лежит открытым текстом» не должен находить его случайно
WRONG = "918273"           # заведомо неверный
NEW_PIN = "590264"         # не 654321: цифры подряд теперь отклоняются (users.check_new_pin)
DEMO_REFUSAL = "Это демо — изменения не сохраняются"
RESTART = "Начните вход заново"
TOO_MANY = "Слишком много попыток, подождите 15 минут"
FULL_ITERATIONS = users.PIN_ITERATIONS     # боевое число повторов; набор облегчает его на время теста


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временном корне, счётчики попыток обнулены, витрину демо никто не собирает."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    # Хэш PBKDF2 в боевых 200 000 повторов занимает ~80 мс, а входов в этом файле сотни.
    # Число повторов лежит в самой записи PIN, и проверка идёт по нему же.
    monkeypatch.setattr(users, "PIN_ITERATIONS", 1_000)

    from app import demo as showcase
    from app.web import create_app
    from app.web import login as web_login

    # Счётчики попыток живут в памяти процесса и пережили бы тест.
    monkeypatch.setattr(web_login, "_FAILS", {})
    monkeypatch.setattr(web_login, "_INSTALL_FAILS", [])
    # Настоящая сборка витрины ходит в единую базу сетей и долго; здесь важна только дверь.
    monkeypatch.setattr(showcase, "ensure", lambda: False)

    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    users.deactivate()


def text(answer) -> str:
    return answer.data.decode("utf-8")


def step1(web, phone: str = PHONE, **kwargs):
    """Шаг 1 входа: номер."""
    return web.post("/login", data={"phone": phone, "next": "/"}, **kwargs)


def step2(web, pin: str = MY_PIN, repeat: str | None = None, phone: str = PHONE, **kwargs):
    """Шаг 2 входа: личный PIN (и повтор, если придумывается)."""
    data = {"step": "2", "phone": phone, "next": "/", "my_pin": pin}
    if repeat is not None:
        data["my_pin2"] = repeat
    return web.post("/login", data=data, **kwargs)


def has_a_pin(phone: str = PHONE, pin: str = MY_PIN) -> None:
    """Рабочее место с личным PIN — как после первого входа; сессий нет."""
    users.set_workspace_pin(phone, pin)


def legacy(web, phone: str = PHONE) -> None:
    """Вошедший по живой старой куке в место, заведённое ДО личных PIN: PIN у места нет."""
    users.open_workspace(phone)
    users.deactivate()
    with web.session_transaction() as sess:
        sess.clear()                  # в старой куке были только номер и время: отпечатка PIN нет
        sess["phone"] = phone
        sess["since"] = "2026-10-20T10:00:00"


def allowed(monkeypatch, value) -> None:
    """Подставить users.allowed так, как его прочитает app.config.get (config.yaml не правится)."""
    real = config.get

    def fake(path, default=None):
        return value if path == "users.allowed" else real(path, default)

    monkeypatch.setattr(config, "get", fake)


# ---------- модель: PIN рабочего места (app/users.py) ----------

def test_a_new_pin_must_be_six_digits_or_more():
    for good in ("482915", "  482915  ", "73519048261593720481"):
        assert users.check_new_pin(good) == good.strip()

    for bad, word in (("", "шести"), (None, "шести"), ("12345", "шести"),
                      ("abc123", "цифр"), ("12 3456", "цифр"), ("١٢٣٤٥٦", "цифр")):
        with pytest.raises(ValueError) as why:
            users.check_new_pin(bad)
        assert word in str(why.value), f"PIN {bad!r}: отказ без объяснения"


def test_a_workspace_pin_is_stored_hashed_and_checked(web, monkeypatch):
    # Здесь нужно боевое число повторов: проверяется, что в запись пишется именно оно.
    monkeypatch.setattr(users, "PIN_ITERATIONS", FULL_ITERATIONS)
    assert FULL_ITERATIONS >= 200_000, "повторов PBKDF2 меньше, чем обещано в документации"

    assert users.set_workspace_pin(PHONE, MY_PIN) is True
    assert users.exists(PHONE) and users.workspace_pin_is_set(PHONE)
    assert users.check_workspace_pin(PHONE, MY_PIN)
    assert users.check_workspace_pin(PHONE, f"  {MY_PIN} "), "пробелы по краям — не часть PIN"
    for wrong in ("", WRONG, MY_PIN[:-1], MY_PIN + "0", None):
        assert not users.check_workspace_pin(PHONE, wrong), f"подошёл PIN {wrong!r}"

    users.open_workspace(PHONE)
    raw = repo.get_setting(users.LOGIN_PIN_KEY)
    users.deactivate()
    record = json.loads(raw)
    assert MY_PIN not in raw, "PIN лежит в настройках открытым текстом"
    assert record["salt"] and record["hash"]
    assert record["iterations"] == FULL_ITERATIONS

    # Тот же PIN у другого места — другая соль и другой хэш: по базам не видно, у кого PIN совпал.
    users.set_workspace_pin(OTHER, MY_PIN)
    users.open_workspace(OTHER)
    other = json.loads(repo.get_setting(users.LOGIN_PIN_KEY))
    users.deactivate()
    assert other["salt"] != record["salt"] and other["hash"] != record["hash"]


def test_a_workspace_pin_is_not_overwritten_by_a_second_creator(web):
    """keep_existing: двое с одним новым номером — второй не затирает PIN первого."""
    users.set_workspace_pin(PHONE, MY_PIN)
    assert users.set_workspace_pin(PHONE, NEW_PIN, keep_existing=True) is False
    assert users.check_workspace_pin(PHONE, MY_PIN) and not users.check_workspace_pin(PHONE, NEW_PIN)

    assert users.set_workspace_pin(PHONE, NEW_PIN) is True        # смена, когда старый PIN проверен
    assert users.check_workspace_pin(PHONE, NEW_PIN) and not users.check_workspace_pin(PHONE, MY_PIN)

    with pytest.raises(ValueError):
        users.set_workspace_pin(PHONE, "12345")                   # короткий не ставится и тут
    with pytest.raises(ValueError):
        users.set_workspace_pin("12", MY_PIN)                     # и «не номер» тоже


def test_asking_about_a_pin_does_not_create_the_workspace(web):
    """Вопрос «есть ли PIN» — это шаг 1, и папки он заводить не вправе."""
    assert users.workspace_pin_is_set(PHONE) is False
    assert users.check_workspace_pin(PHONE, MY_PIN) is False
    users.clear_workspace_pin(PHONE)
    assert users.workspace_pin_is_set("12") is False
    assert not os.path.exists(users.workspace_dir(PHONE)), "вопрос о PIN завёл рабочее место"


def test_a_broken_pin_record_counts_as_set_and_matches_nobody(web):
    """Битую запись не принять за «PIN нет»: иначе первый пришедший придумал бы свой поверх."""
    has_a_pin()
    for broken in ("{не json", "[]", '{"salt": "zz", "hash": "00"}'):
        users.open_workspace(PHONE)
        repo.set_setting(users.LOGIN_PIN_KEY, broken)
        users.deactivate()

        assert users.workspace_pin_is_set(PHONE), broken
        assert not users.check_workspace_pin(PHONE, MY_PIN), broken
        assert users.set_workspace_pin(PHONE, NEW_PIN, keep_existing=True) is False, broken

    users.clear_workspace_pin(PHONE)                              # сбрасывает только сервер
    assert not users.workspace_pin_is_set(PHONE)
    assert users.set_workspace_pin(PHONE, NEW_PIN, keep_existing=True) is True


def test_reading_a_pin_leaves_the_current_workspace_as_it_was(web):
    users.open_workspace(OTHER)
    before = config.db_override()
    users.set_workspace_pin(PHONE, MY_PIN)
    users.check_workspace_pin(PHONE, MY_PIN)
    users.workspace_pin_is_set(PHONE)
    assert config.db_override() == before and users.current() == OTHER
    users.deactivate()


def test_demo_is_not_a_number_to_type():
    for typed in ("demo", "Demo", "  DEMO  "):
        phone, why = users.resolve_login(typed)
        assert phone is None and why == users.NOT_A_PHONE, typed


@pytest.mark.parametrize("raw, expected", [
    ("/basket", "/basket"),
    ("/phone/basket?q=1&x=2", "/phone/basket?q=1&x=2"),
    ("https://evil.example", "/"),
    ("//evil.example", "/"),
    ("/\\evil.example", "/"),            # браузер читает «/\» как «//»
    ("/\t/evil.example", "/"),           # табуляцию из адреса выбрасывают
    ("/\n/evil.example", "/"),
    ("basket", "/"),
    ("", "/"),
    (None, "/"),
])
def test_next_keeps_only_a_path_inside_the_site(raw, expected):
    from app.web.login import _safe_next

    assert _safe_next(raw) == expected


# ---------- вход в два шага ----------

def test_step_one_does_not_create_the_workspace(web):
    """Шаг 1 PIN только спрашивает: случайные номера не наполняют диск пустыми базами."""
    answer = step1(web)
    assert answer.status_code == 200
    assert not users.exists(PHONE)
    assert not os.path.exists(users.workspace_dir(PHONE)), "шаг 1 завёл папку рабочего места"
    assert web.get("/").status_code == 302, "шаг 1 открыл рабочее место"


def test_a_new_number_invents_a_pin(web):
    first = step1(web)
    page = text(first)
    assert first.status_code == 200 and "Придумайте PIN" in page
    assert 'name="my_pin"' in page and 'name="my_pin2"' in page
    assert users.display(PHONE) in page, "страница не говорит, чей это номер"

    answer = step2(web, MY_PIN, MY_PIN)
    assert answer.status_code == 302 and answer.headers["Location"] == "/"
    assert users.exists(PHONE) and users.check_workspace_pin(PHONE, MY_PIN)
    assert web.get("/").status_code == 200

    # И теперь вход требует этот PIN: одного номера мало.
    web.post("/logout")
    assert "Введите PIN" in text(step1(web))
    assert web.get("/").status_code == 302


def test_inventing_a_pin_refuses_a_mismatch_a_short_and_a_non_digit_pin(web):
    step1(web)
    for pin, repeat, words in ((MY_PIN, WRONG, users.PIN_MISMATCH), ("12345", "12345", "шести"),
                               ("abcdef", "abcdef", "цифр"), ("", "", "шести")):
        answer = step2(web, pin, repeat)
        page = text(answer)
        assert answer.status_code == 400, pin
        assert words in page, f"PIN {pin!r}: отказ без объяснения"
        assert "Придумайте PIN" in page, "после отказа не вернули на «придумайте»"
        assert not users.workspace_pin_is_set(PHONE) and not users.exists(PHONE)

    # Это не догадки: PIN ещё только придумывают, и счёт отказов не растёт.
    for _ in range(8):
        assert step2(web, "12345", "12345").status_code == 400
    assert step2(web, MY_PIN, MY_PIN).status_code == 302


def test_a_number_with_a_pin_asks_for_it(web):
    has_a_pin()
    first = step1(web)
    page = text(first)
    assert first.status_code == 200 and "Введите PIN" in page
    assert 'name="my_pin2"' not in page, "у номера с PIN просят повтор — придумать поверх нельзя"
    assert 'autocomplete="current-password"' in page

    wrong = step2(web, WRONG)
    assert wrong.status_code == 400 and "Неверный PIN" in text(wrong)
    assert "Введите PIN" in text(wrong), "после неверного PIN не вернули на шаг 2"
    assert web.get("/").status_code == 302

    right = step2(web, MY_PIN)
    assert right.status_code == 302 and right.headers["Location"] == "/"
    assert web.get("/").status_code == 200


def test_an_old_workspace_without_a_pin_invents_one(web):
    """Место, заведённое до личных PIN (так у владельца): первый вход после выкладки придумывает."""
    users.open_workspace(PHONE)
    users.deactivate()
    assert users.exists(PHONE) and not users.workspace_pin_is_set(PHONE)

    assert "Придумайте PIN" in text(step1(web))
    assert step2(web, MY_PIN, MY_PIN).status_code == 302
    assert users.check_workspace_pin(PHONE, MY_PIN)


def test_the_hidden_form_cannot_turn_entering_into_inventing(web):
    """Номер с PIN: присланный «PIN и повтор» не затирает PIN, а проверяется как ввод."""
    has_a_pin()
    step1(web)
    answer = step2(web, NEW_PIN, NEW_PIN)
    assert answer.status_code == 400 and "Неверный PIN" in text(answer)
    assert users.check_workspace_pin(PHONE, MY_PIN) and not users.check_workspace_pin(PHONE, NEW_PIN)


def test_a_pin_invented_between_the_steps_is_entered_not_overwritten(web):
    """Двое пришли с одним новым номером; первый успел придумать PIN до шага 2 второго."""
    step1(web)
    users.set_workspace_pin(PHONE, NEW_PIN)

    answer = step2(web, MY_PIN, MY_PIN)
    page = text(answer)
    assert answer.status_code == 400 and "Неверный PIN" in page and "Введите PIN" in page
    assert 'name="my_pin2"' not in page
    assert users.check_workspace_pin(PHONE, NEW_PIN) and not users.check_workspace_pin(PHONE, MY_PIN)
    assert web.get("/").status_code == 302

    assert step2(web, NEW_PIN).status_code == 302          # шаг 1 ещё действует
    assert web.get("/").status_code == 200


def test_the_loser_of_the_race_to_invent_a_pin_is_asked_to_enter_it(web, monkeypatch):
    """Гонка: PIN появился уже ПОСЛЕ того, как сервер решил «придумывать» (set_workspace_pin
    с keep_existing возвращает False). Второй не затирает чужой PIN и не получает отказа-догадки."""
    step1(web)
    users.set_workspace_pin(PHONE, NEW_PIN)
    real = users.workspace_pin_is_set
    monkeypatch.setattr(users, "workspace_pin_is_set", lambda phone: False)

    answer = step2(web, MY_PIN, MY_PIN)
    page = text(answer)
    assert answer.status_code == 400 and "уже придумали" in page and "Введите PIN" in page
    assert 'name="my_pin2"' not in page
    assert users.check_workspace_pin(PHONE, NEW_PIN) and not users.check_workspace_pin(PHONE, MY_PIN)
    assert web.get("/").status_code == 302

    monkeypatch.setattr(users, "workspace_pin_is_set", real)
    for _ in range(4):                                     # и счёт отказов проигравшему не писали
        assert step2(web, WRONG).status_code == 400
    assert step2(web, NEW_PIN).status_code == 302


def test_step_two_cannot_be_sent_without_step_one(web):
    """Шаг 2 без шага 1 не проходит: ни придумать PIN, ни ввести, и ничего не заводится."""
    answer = step2(web, MY_PIN, MY_PIN)
    assert answer.status_code == 400 and RESTART in text(answer)
    assert not users.exists(PHONE)

    # Шаг 1 ДРУГОГО номера шаг 2 этого не открывает.
    step1(web, OTHER)
    answer = step2(web, MY_PIN, MY_PIN)
    assert answer.status_code == 400 and RESTART in text(answer)
    assert not users.exists(PHONE) and not users.exists(OTHER)
    assert web.get("/").status_code == 302

    # И у номера с PIN верный PIN без шага 1 тоже ничего не открывает.
    has_a_pin()
    web.post("/logout")                                    # кука шага 1 стёрта
    answer = step2(web, MY_PIN)
    assert answer.status_code == 400 and RESTART in text(answer)
    assert web.get("/").status_code == 302

    # Это не догадки: счёт отказов не тронут.
    for _ in range(8):
        assert step2(web, MY_PIN).status_code == 400
    assert step1(web).status_code == 200


def test_step_two_cannot_skip_the_installation_pin(web):
    has_a_pin()
    users.set_pin("432100")                                # общий замок установки
    answer = step2(web, MY_PIN)                            # шаг 1 (а с ним PIN установки) пропущен
    assert answer.status_code == 400 and RESTART in text(answer)
    assert step1(web).status_code == 400                   # без PIN установки шаг 1 не пускает
    assert step2(web, MY_PIN).status_code == 400
    assert web.get("/").status_code == 302

    assert sign_in(web, PHONE, pin=MY_PIN, install_pin="432100").status_code == 302
    assert web.get("/").status_code == 200


def test_step_one_expires_after_ten_minutes(web):
    step1(web)
    with web.session_transaction() as sess:
        sess["login_at"] = time.time() - 11 * 60
    answer = step2(web, MY_PIN, MY_PIN)
    assert answer.status_code == 400 and RESTART in text(answer)
    assert not users.exists(PHONE)
    for _ in range(8):                                     # и это не догадка
        assert step2(web, MY_PIN, MY_PIN).status_code == 400

    step1(web)
    with web.session_transaction() as sess:
        sess["login_at"] = time.time() - 9 * 60
    assert step2(web, MY_PIN, MY_PIN).status_code == 302, "шаг 1 стареет раньше срока"


def test_a_number_dropped_from_the_list_between_the_steps_is_refused(web, monkeypatch):
    step1(web)
    allowed(monkeypatch, "+7 999 000-00-99")               # список сменился за эти минуты
    answer = step2(web, MY_PIN, MY_PIN)
    assert answer.status_code == 400 and users.NOT_A_PHONE in text(answer)
    assert not users.exists(PHONE)


def test_demo_cannot_be_typed_into_the_form_or_forged_into_step_two(web):
    for typed in ("demo", "Demo"):
        answer = step1(web, typed)
        assert answer.status_code == 400 and "номер" in text(answer).lower()
    answer = step2(web, MY_PIN, MY_PIN, phone="demo")
    assert answer.status_code == 400
    assert not users.exists(users.DEMO), "демо заведено формой входа"


def test_a_login_starts_the_catalog_linking_only_when_it_succeeds(web, monkeypatch):
    from app.catalog import link

    calls: list[str] = []
    monkeypatch.setattr(link, "link_soon", lambda phone, sync=False: calls.append(phone) or False)
    step1(web)
    step2(web, "12345", "12345")
    assert calls == []
    step2(web, MY_PIN, MY_PIN)
    assert calls == [PHONE]


def test_an_unsafe_next_is_dropped_on_both_steps(web):
    page = text(web.post("/login", data={"phone": PHONE, "next": "https://evil.example"}))
    assert "evil.example" not in page
    answer = web.post("/login", data={"step": "2", "phone": PHONE, "next": "//evil.example",
                                      "my_pin": MY_PIN, "my_pin2": MY_PIN})
    assert answer.status_code == 302 and answer.headers["Location"] == "/"


def test_the_pin_fields_are_numeric_password_fields_with_a_big_font(web):
    """Safari увеличивает страницу при фокусе на поле мельче 16 px; PIN вводится цифрами."""
    page = text(step1(web))
    for name in ("my_pin", "my_pin2"):
        field = re.search(rf'<input[^>]*name="{name}"[^>]*>', page, re.S).group(0)
        assert 'type="password"' in field and 'inputmode="numeric"' in field, field
        assert 'autocomplete="new-password"' in field and 'class="pinf"' in field, field
    assert re.search(r"\.pinf\{[^}]*font-size:\s*16px", page), "шрифт полей меньше 16 px"

    has_a_pin()
    web.post("/logout")
    field = re.search(r'<input[^>]*name="my_pin"[^>]*>', text(step1(web)), re.S).group(0)
    assert 'autocomplete="current-password"' in field and 'inputmode="numeric"' in field

    assert 'name="pin"' not in text(web.get("/login")), "поле PIN установки показано без замка"
    users.set_pin("432100")
    field = re.search(r'<input[^>]*name="pin"[^>]*>', text(web.get("/login")), re.S).group(0)
    assert 'type="password"' in field and 'class="pinf"' in field


# ---------- пределы попыток на втором шаге ----------

def test_wrong_personal_pins_are_counted_by_the_address_limit(web):
    has_a_pin()
    step1(web)
    for _ in range(5):
        assert step2(web, WRONG).status_code == 400, "предел сработал раньше пятого отказа"

    sixth = step2(web, MY_PIN)                             # даже верный
    assert sixth.status_code == 429 and TOO_MANY in text(sixth)
    assert web.get("/").status_code == 302, "отказ пропустил внутрь"
    assert step1(web).status_code == 429, "шаг 1 с закрытого адреса не закрыт"

    # С другого адреса счёт свой.
    other = {"environ_base": {"REMOTE_ADDR": "10.0.0.9"}}
    assert step1(web, **other).status_code == 200
    assert step2(web, MY_PIN, **other).status_code == 302


def test_step_one_does_not_reset_the_failures(web):
    """Четыре догадки, верный шаг 1, ещё одна — пятая: иначе шаг 1 подарил бы новые попытки."""
    has_a_pin()
    step1(web)
    for _ in range(4):
        assert step2(web, WRONG).status_code == 400
    assert step1(web).status_code == 200
    assert step2(web, WRONG).status_code == 400
    assert step2(web, MY_PIN).status_code == 429, "шаг 1 обнулил счёт отказов"


def test_a_right_personal_pin_does_not_reset_the_failures(web):
    """Удачный вход счёт адреса не обнуляет: окно в 15 минут истекает само.

    Раньше верный PIN обнулял счёт, и перебор чужого PIN шёл пачками по четыре догадки с
    входом в своё место между ними (tests/test_login_hardening.py — тот же приём на двух номерах).
    """
    has_a_pin()
    step1(web)
    for _ in range(4):
        step2(web, WRONG)
    assert step2(web, MY_PIN).status_code == 302
    web.post("/logout")

    step1(web)
    assert step2(web, WRONG).status_code == 400            # пятая догадка за окно — ещё проверяется
    assert step2(web, MY_PIN).status_code == 429, "верный вход обнулил счёт отказов адреса"


def test_the_second_step_pauses_on_the_installation_limit_and_still_checks(web, monkeypatch):
    """20 отказов на установке: пауза и на шаге 2, а верный PIN проходит (владельца не запирают)."""
    from app.web import login as web_login

    sleeps: list[float] = []
    monkeypatch.setattr(web_login.time, "sleep", lambda seconds: sleeps.append(seconds))
    has_a_pin()
    step1(web)
    for i in range(web_login.INSTALL_FAIL_LIMIT):
        answer = step2(web, WRONG, environ_base={"REMOTE_ADDR": f"10.1.0.{i}"})
        assert answer.status_code == 400
    assert sleeps == [], "пауза раньше двадцатого отказа"

    answer = step2(web, MY_PIN, environ_base={"REMOTE_ADDR": "10.1.1.1"})
    assert answer.status_code == 302, "общий предел запер владельца снаружи"
    assert sleeps == [web_login.INSTALL_PAUSE_SEC]


# ---------- PIN нигде не лежит открытым текстом ----------

def test_the_pin_is_nowhere_in_the_clear(web, tmp_path, caplog):
    """Ни в странице, ни в заголовках и куке, ни в журнале, ни в файлах на диске."""
    from flask.sessions import SecureCookieSessionInterface

    caplog.set_level(logging.DEBUG)
    serializer = SecureCookieSessionInterface().get_signing_serializer(web.application)
    cookie = web.application.config["SESSION_COOKIE_NAME"]

    def session() -> dict:
        return dict(serializer.loads(web.get_cookie(cookie).value))

    answers = [step1(web)]
    assert set(session()) == {"login_phone", "login_at"}, "в куке шага 1 лишнее"
    answers += [step2(web, MY_PIN, WRONG), step2(web, MY_PIN, MY_PIN)]
    assert set(session()) <= {"phone", "since", "pin_stamp", "_permanent"}, "в куке входа лишнее"
    assert session()["phone"] == PHONE

    web.post("/logout")
    answers += [step1(web), step2(web, "111222"), step2(web, MY_PIN)]
    answers += [web.get("/pin"),
                web.post("/pin", data={"do": "my", "my_old": "333444", "my_pin": "555666",
                                       "my_pin2": "555666"}),
                web.post("/pin", data={"do": "my", "my_old": MY_PIN, "my_pin": "777888",
                                       "my_pin2": "777888"}),
                web.get("/pin?saved=my_changed")]

    secrets = [MY_PIN, WRONG, "111222", "333444", "555666", "777888"]
    for answer in answers:
        blob = answer.data.decode("utf-8") + "\n".join(f"{k}: {v}" for k, v in answer.headers)
        for secret in secrets:
            assert secret not in blob, f"PIN {secret} в ответе {answer.status_code}"
    for secret in secrets:
        assert secret not in caplog.text, f"PIN {secret} попал в журнал"

    for folder, _, names in os.walk(tmp_path):
        for name in names:
            path = os.path.join(folder, name)
            with open(path, "rb") as fh:
                data = fh.read()
            for secret in secrets:
                assert secret.encode() not in data, f"PIN {secret} лежит в файле {path}"


# ---------- старая кука места без PIN ----------

def test_an_old_cookie_without_a_pin_is_sent_to_invent_one(web):
    legacy(web)
    for path in ("/", "/basket", "/accounts", "/receipts", "/phone/basket"):
        answer = web.get(path)
        assert answer.status_code == 302 and "/pin" in answer.headers["Location"], path
    assert web.get("/basket").headers["Location"] == "/pin?next=/basket"

    # Запрос данных получает отказ кодом, а не перевод: fetch принял бы страницу за ответ.
    answer = web.get("/basket", headers={"Accept": "application/json"})
    assert answer.status_code == 401 and answer.get_json()["ok"] is False

    # Форма тоже: старый вход не пишет ничего, пока PIN не придуман.
    answer = web.post("/address", data={"address": "Москва, Тверская 1"})
    assert answer.status_code == 302 and "/pin" in answer.headers["Location"]

    # Что остаётся открытым: страница PIN, статика, дверь API и выход.
    assert web.get("/pin").status_code == 200
    assert web.get("/static/korzina.css").status_code == 200
    assert web.get("/api/health").status_code == 200
    assert web.post("/logout").status_code == 302
    assert web.get("/").status_code == 302 and "/login" in web.get("/").headers["Location"]


def test_a_place_with_a_pin_is_not_sent_to_the_pin_page_and_neither_is_the_demo(web):
    """Место с PIN на /pin не уводят: старая кука без отпечатка идёт на вход, а не внутрь и не на /pin
    (tests/test_login_hardening.py); вошедший по PIN и демо — внутрь."""
    has_a_pin()
    assert sign_in(web, PHONE, pin=MY_PIN).status_code == 302
    assert web.get("/").status_code == 200, "место с PIN уведено на /pin"
    web.post("/logout")

    has_a_pin(OTHER)
    legacy(web, OTHER)
    answer = web.get("/")
    assert answer.status_code == 302 and "/login" in answer.headers["Location"]
    assert "/pin" not in answer.headers["Location"]
    web.post("/logout")

    web.post("/demo")
    assert web.get("/").status_code == 200, "демо уведено на /pin"


def test_a_cookie_revoked_by_a_pin_change_stays_dead_after_the_pin_is_cleared(web):
    """Кука под прежним PIN не оживает, когда PIN потом сняли на сервере (ревью 26.10.2026).

    Иначе её сохранивший пришёл бы на /pin и придумал PIN первым — то есть забрал место.
    """
    sign_in(web, PHONE, pin=MY_PIN)
    users.set_workspace_pin(PHONE, NEW_PIN)        # PIN сменили на другом устройстве: кука отозвана
    users.clear_workspace_pin(PHONE)               # владелец снял PIN на сервере
    answer = web.get("/")
    assert answer.status_code == 302 and answer.headers["Location"].startswith("/login")
    assert web.post("/pin", data={"do": "my", "my_pin": MY_PIN, "my_pin2": MY_PIN}).status_code == 302
    assert not users.workspace_pin_is_set(PHONE), "по отозванной куке придумали PIN"


def test_inventing_the_pin_on_the_pin_page_lets_the_old_cookie_in(web):
    legacy(web)
    page = text(web.get("/pin?next=/basket"))
    assert "Придумать PIN" in page and 'name="my_old"' not in page
    assert 'name="next" value="/basket"' in page

    for pin, repeat, words in ((MY_PIN, WRONG, users.PIN_MISMATCH), ("12345", "12345", "шести"),
                               ("abcdef", "abcdef", "цифр")):
        answer = web.post("/pin", data={"do": "my", "my_pin": pin, "my_pin2": repeat,
                                        "next": "/basket"})
        assert answer.status_code == 400 and words in text(answer), pin
        assert not users.workspace_pin_is_set(PHONE)
        assert web.get("/").status_code == 302, "кука открылась без PIN"

    answer = web.post("/pin", data={"do": "my", "my_pin": MY_PIN, "my_pin2": MY_PIN,
                                    "next": "/basket"})
    assert answer.status_code == 302 and answer.headers["Location"] == "/basket"
    assert users.check_workspace_pin(PHONE, MY_PIN)
    assert web.get("/").status_code == 200

    # Чужой адрес в next не пролезает.
    legacy(web, OTHER)
    answer = web.post("/pin", data={"do": "my", "my_pin": MY_PIN, "my_pin2": MY_PIN,
                                    "next": "https://evil.example"})
    assert answer.status_code == 302 and answer.headers["Location"] == "/"


def test_the_link_door_still_opens_a_workspace_without_a_pin(web):
    """Ссылка со своим секретом — отдельная дверь: PIN у закладки спросить нечем."""
    users.open_workspace(PHONE)
    secret = users.link_secret(PHONE)
    users.deactivate()
    assert not users.workspace_pin_is_set(PHONE)
    answer = web.get("/accounts?" + urllib.parse.urlencode({"phone": PHONE, "vhod": "1", "t": secret}))
    assert answer.status_code == 200, "ссылка с верным секретом уведена на /pin"


# ---------- страница /pin: личный PIN ----------

def test_the_pin_page_changes_the_personal_pin_with_the_old_one(web):
    sign_in(web, PHONE, pin=MY_PIN)
    page = text(web.get("/pin"))
    assert "Сменить PIN" in page and 'name="my_old"' in page and "6 цифр" in page

    for form, words in (({"my_old": WRONG, "my_pin": NEW_PIN, "my_pin2": NEW_PIN}, "Неверный PIN"),
                        ({"my_pin": NEW_PIN, "my_pin2": NEW_PIN}, "Неверный PIN"),   # «придумать» поверх
                        ({"my_old": MY_PIN, "my_pin": NEW_PIN, "my_pin2": WRONG}, users.PIN_MISMATCH),
                        ({"my_old": MY_PIN, "my_pin": "12345", "my_pin2": "12345"}, "шести"),
                        ({"my_old": MY_PIN, "my_pin": "abcdef", "my_pin2": "abcdef"}, "цифр")):
        answer = web.post("/pin", data={"do": "my", **form})
        assert answer.status_code == 400 and words in text(answer), form
        assert users.check_workspace_pin(PHONE, MY_PIN), f"PIN сменился: {form}"

    answer = web.post("/pin", data={"do": "my", "my_old": MY_PIN, "my_pin": NEW_PIN,
                                    "my_pin2": NEW_PIN})
    assert answer.status_code == 302 and "saved=my_changed" in answer.headers["Location"]
    assert users.check_workspace_pin(PHONE, NEW_PIN) and not users.check_workspace_pin(PHONE, MY_PIN)
    assert "PIN сменён" in text(web.get("/pin?saved=my_changed"))

    web.post("/logout")
    step1(web)
    assert step2(web, MY_PIN).status_code == 400, "прежний PIN подошёл после смены"
    assert step2(web, NEW_PIN).status_code == 302


def test_guessing_the_old_personal_pin_on_the_pin_page_is_limited(web):
    sign_in(web, PHONE, pin=MY_PIN)
    guess = {"do": "my", "my_old": WRONG, "my_pin": NEW_PIN, "my_pin2": NEW_PIN}
    for _ in range(5):
        assert web.post("/pin", data=guess).status_code == 400, "предел сработал раньше пятого отказа"

    sixth = web.post("/pin", data={**guess, "my_old": MY_PIN})   # даже верный старый
    assert sixth.status_code == 429 and TOO_MANY in text(sixth)
    assert users.check_workspace_pin(PHONE, MY_PIN), "отказ всё-таки сменил PIN"


def test_the_pin_page_is_compact_and_the_fields_are_big_enough(web):
    sign_in(web, PHONE, pin=MY_PIN)
    page = text(web.get("/pin"))
    rule = re.search(r"\.pinf\{([^}]*)\}", page).group(1)
    assert int(re.search(r"font-size:\s*(\d+)px", rule).group(1)) >= 16, "шрифт полей меньше 16 px"
    assert 40 <= int(re.search(r"height:\s*(\d+)px", rule).group(1)) <= 44, "строка не 40–44 px"
    for name in ("my_old", "my_pin", "my_pin2"):
        field = re.search(rf'<input[^>]*name="{name}"[^>]*>', page, re.S).group(0)
        assert 'type="password"' in field and 'inputmode="numeric"' in field, field
        assert 'class="pinf"' in field, field


# ---------- демо только для чтения ----------

def test_the_demo_button_signs_in_and_fills_the_showcase(web, monkeypatch):
    from app import demo as showcase

    seen: list[str | None] = []
    monkeypatch.setattr(showcase, "ensure", lambda: seen.append(users.current()) or False)
    answer = web.post("/demo")
    assert answer.status_code == 302 and answer.headers["Location"] == "/"
    assert seen == [users.DEMO], "витрину звали не при открытом рабочем месте demo"
    assert web.get("/").status_code == 200

    # Упавшее наполнение не мешает войти: пустая витрина лучше отказа.
    def boom():
        raise RuntimeError("единая база не открылась")

    monkeypatch.setattr(showcase, "ensure", boom)
    web.post("/logout")
    assert web.post("/demo").status_code == 302
    assert web.get("/").status_code == 200


def test_the_demo_reads_every_screen(web):
    from app.web.views import SCREENS

    web.post("/demo")
    for screen in SCREENS:
        assert web.get(screen.path).status_code == 200, screen.path
        assert web.head(screen.path).status_code == 200, screen.path


def test_a_form_in_the_demo_goes_back_with_the_demo_flag(web):
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
    assert post(Referer="http://[").headers["Location"] == "/?demo=1"          # не адрес
    # Флаг не двоится, если форма и так пришла со страницы с demo=1.
    assert post(Referer="http://localhost/phone/basket?demo=1").headers["Location"] == "/phone/basket?demo=1"

    users.open_workspace(users.DEMO)
    assert location.address() == before, "адрес в общем демо изменился"
    users.deactivate()


def test_a_json_request_in_the_demo_gets_403_and_changes_nothing(web):
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
    # По сессии: дверь api.* закрыта для вошедшего в демо.
    web.post("/demo")
    for path in ("/api/device/cartplan", "/api/device/cartreport", "/api/device/connected",
                 "/api/prices"):
        answer = web.post(path, json={"store": "dixy"})
        assert answer.status_code == 403, path
        assert answer.get_json() == {"ok": False, "error": DEMO_REFUSAL}, path

    # По замку: секрет демо виден каждому, кто открыл его «Кабинеты», — и он не открывает дверь.
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

    # Контроль: настоящее рабочее место с верным секретом дверь пускает.
    answer = anonymous.post("/api/device/connected", json={
        "workplace": PHONE, "secret": own_secret, "store": "dixy", "connected": True})
    assert answer.status_code == 200 and answer.get_json()["ok"] is True


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
    web.post("/demo")
    assert web.post("/demo").status_code == 302          # кнопка работает и из самого демо
    assert web.post("/logout").status_code == 302
    assert web.get("/").status_code == 302

    web.post("/demo")
    # Из демо можно начать настоящий вход: шаги 1 и 2 демо-сторож не режет.
    assert step1(web).status_code == 200
    assert step2(web, MY_PIN, MY_PIN).status_code == 302
    assert users.exists(PHONE)

    # И запись снова работает: кука демо заменена кукой настоящего рабочего места.
    web.post("/address", data={"address": "Москва, Ходынский бульвар 4"})
    assert "Ходынский" in text(web.get("/"))

    web.post("/logout")
    assert web.post("/demo").status_code == 302          # и кнопка нажимается снова
    assert web.get("/").status_code == 200


def test_the_pin_page_of_the_demo_has_no_forms_and_cannot_be_posted(web):
    web.post("/demo")
    page = text(web.get("/pin"))
    assert "Это демо" in page and 'name="my_pin"' not in page

    answer = web.post("/pin", data={"do": "my", "my_pin": MY_PIN, "my_pin2": MY_PIN})
    assert answer.status_code == 303
    assert not users.workspace_pin_is_set(users.DEMO), "у общего демо появился PIN"
