"""Экран «Мои чеки» на новом интерфейсе: четыре пути загрузки и ни одного потерянного.

Этот экран — первая опора приложения: пока чеков нет, считать нечего. Поэтому
проверки здесь не про вёрстку, а про то, чем экран отличается от Streamlit-версии
и где новый интерфейс может тихо соврать.

ГЛАВНОЕ, ЧТО ЗДЕСЬ СТОРОЖИТСЯ.

  Ключ проверки из кабинета не должен уехать ни в куку, ни в адрес: он живёт в
  скрытом поле одной формы. Кука ходит на сервер с каждым запросом и лежит на
  диске браузера — для ключа, живущего минуту, это на три порядка больше мест,
  чем нужно.

  Долгая загрузка не должна вешать страницу молча. Пока она идёт, страница
  возвращается с заголовком Refresh и показывает, сколько чеков уже взято; когда
  кончилась — числами говорит, чем.

  Фоновая загрузка обязана писать в базу ТОГО ЖЕ человека. Новый поток приходит с
  чистым контекстом (contextvars), и без явного открытия рабочего места чеки ушли
  бы в общую базу из config.yaml — тихо и правдоподобно.
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, receipts as history, users  # noqa: E402
from app.fns.client import CabinetError, CaptchaRequired  # noqa: E402
from app.web.screens import receipts as screen  # noqa: E402

PHONE = "79990000001"
PATH = "/receipts"


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временных базах — как в tests/test_web.py.

    config.db_path не подменяется нарочно: подмена вернула бы одну базу всем и
    отключила бы выбор базы по человеку, а здесь он и проверяется.
    """
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    users.deactivate()


@pytest.fixture(autouse=True)
def clean_jobs():
    """Ход загрузки живёт в памяти процесса — между тестами он не должен протекать."""
    screen._JOBS.clear()
    yield
    screen._JOBS.clear()


def enter(client, phone=PHONE):
    return client.post("/login", data={"phone": phone, "next": PATH})


def page_of(client):
    return client.get(PATH).data.decode("utf-8")


def inside(client, phone=PHONE):
    """Открыть базу этого человека в самом тесте: после запроса она снята с потока."""
    users.open_workspace(phone)


def wait_for(job, seconds: float = 10.0) -> None:
    """Дождаться конца фоновой загрузки. Ждём состояние, а не время.

    Сон на глазок делает тест либо медленным, либо шатким — а шаткий тест про
    потоки хуже отсутствующего: его начинают перезапускать, не читая.
    """
    deadline = time.monotonic() + seconds
    while job.running and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not job.running, "фоновая загрузка не кончилась за отведённое время"


def receipt(key: str, day: str, brand: str, items: list[tuple[str, int, int]]) -> dict:
    """Чек в том виде, в каком его кладёт закладка: опись снаружи, позиции внутри."""
    return {"key": key, "date": f"{day}T19:41:00", "store": brand,
            "total": sum(price * qty for _, qty, price in items),
            "fiscalData": {"key": key, "dateTime": f"{day}T19:41:00",
                           "retailPlace": "Торговая точка",
                           "totalSum": sum(price * qty for _, qty, price in items),
                           "items": [{"name": name, "quantity": qty, "price": price,
                                      "sum": price * qty} for name, qty, price in items]}}


BUNDLE = json.dumps({
    "source": "lkdr", "takenAt": "2026-09-17T20:05:39.742Z", "failed": [],
    "receipts": [
        receipt("k1", "2026-08-16", "Пятёрочка",
                [("Молоко 3.2% 900 мл", 2, 10990), ("Хлеб Бородинский 400 г", 1, 5450)]),
        receipt("k2", "2026-08-20", "ВкусВилл", [("Сыр Страчателла 200 г", 1, 32900)]),
    ],
    "catalogue": [{"key": "k1", "date": "2026-08-16", "store": "Пятёрочка", "total": 27430},
                  {"key": "k2", "date": "2026-08-20", "store": "ВкусВилл", "total": 32900}],
}, ensure_ascii=False)

PASTED = """Ваш заказ №12345678 от 15.09.2026
Молоко Простоквашино 2,5% 930 мл      2 шт × 149,00 ₽ = 298,00 ₽
Огурцы короткоплодные                 0,4 кг × 215 ₽    86,00 ₽
Хлеб Бородинский нарезка 270 г        1 шт              94,00 ₽
Итого: 478,00 ₽"""

DONE = {"receipts": 2, "rows": 3, "total": 602.3, "skipped": 0, "products_created": 3,
        "period": ("2026-08-16", "2026-08-20"), "failed": [], "pending": 0, "seen": 2,
        "no_data": 0, "synced_at": "2026-09-17T12:00:00"}


# ---------- экран стоит и говорит правду ----------
def test_the_screen_opens_and_says_what_is_loaded(web):
    enter(web)
    page = page_of(web)

    assert "Покупок пока нет ни одной" in page
    assert "Кабинет ФНС" in page and "не подключён" in page


def test_a_cabinet_that_is_not_connected_explains_the_captcha(web):
    """Отказ объясняется словами: кнопка «получить код», которая не работает, — обман."""
    enter(web)
    page = page_of(web)

    assert "капчу" in page
    assert "Подключить кабинет" in page          # ключ из закладки — главный путь
    assert "Код по СМС из приложения" in page    # и запасной, с честной оговоркой


def test_the_bookmarklet_carries_the_address_of_this_screen(web):
    """Закладка должна вести ключ на ЭТОТ экран, иначе он приедет в никуда."""
    enter(web)
    page = page_of(web)

    assert "javascript:" in page, "закладка не собрана — python tools/build_bookmarklet.py"
    assert "__APP_URL__" not in page, "заглушка адреса осталась незаполненной"
    assert "%2Freceipts" in page


# ---------- ключ проверки: скрытое поле, а не кука и не адрес ----------
def test_the_challenge_key_lives_in_the_form_and_nowhere_else(web, monkeypatch):
    """Ключ проверки уходит в скрытое поле формы; в куке и в адресе его быть не должно.

    Кука ездит на сервер с каждым запросом и лежит на диске браузера. Ключ живёт
    минуту и нужен ровно одной форме — значит, и место ему одно.
    """
    monkeypatch.setattr("app.fns.sync.request_code",
                        lambda phone: {"challenge_token": "ЧТ-12345", "sent_to": "+7 999 ***-45-67",
                                       "expires_in": 120})
    enter(web)
    answer = web.post(PATH, data={"do": "code", "phone": "+7 999 123-45-67"})
    body = answer.data.decode("utf-8")

    assert answer.status_code == 200, "шаг с кодом отвечает страницей, а не переводом"
    assert 'name="challenge" value="ЧТ-12345"' in body
    assert "Код отправлен" in body and "+7 999 ***-45-67" in body

    cookie = answer.headers.get("Set-Cookie") or ""
    assert "ЧТ-12345" not in cookie, "ключ проверки уехал в куку"
    for fossil in web.get(PATH).headers.values():
        assert "ЧТ-12345" not in str(fossil)


def test_the_cabinet_is_quoted_word_for_word(web, monkeypatch):
    """Живого входа отсюда не было ни разу — ответ кабинета показывается как есть."""
    def refuses(phone):
        raise CabinetError("Кабинет ответил: registration.sms.verification.not.expired")

    monkeypatch.setattr("app.fns.sync.request_code", refuses)
    enter(web)
    page = web.post(PATH, data={"do": "code", "phone": "+7 999 123-45-67"},
                    follow_redirects=True).data.decode("utf-8")

    assert "registration.sms.verification.not.expired" in page


def test_the_captcha_sends_the_person_to_the_bookmarklet(web, monkeypatch):
    def captcha(phone):
        raise CaptchaRequired("empty.captcha")

    monkeypatch.setattr("app.fns.sync.request_code", captcha)
    enter(web)
    page = web.post(PATH, data={"do": "code", "phone": "+7 999 123-45-67"},
                    follow_redirects=True).data.decode("utf-8")

    assert "капчу" in page and "закладки" in page


def test_the_code_from_the_sms_never_reaches_the_address(web, monkeypatch):
    """Код проверен — экран переводит на чистый адрес: ни кода, ни ключа в строке."""
    seen = {}
    monkeypatch.setattr("app.fns.sync.confirm_code",
                        lambda token, phone, code: seen.update(token=token, code=code))
    monkeypatch.setattr("app.fns.sync.sync", lambda on_step=None, **kw: dict(DONE))
    enter(web)
    answer = web.post(PATH, data={"do": "confirm", "challenge": "ЧТ-12345",
                                  "phone": PHONE, "code": "123456"})

    assert seen == {"token": "ЧТ-12345", "code": "123456"}
    assert answer.status_code == 303
    assert answer.headers["Location"] == PATH


def test_a_short_code_is_refused_without_losing_the_challenge(web, monkeypatch):
    """Опечатка не должна стоить человеку нового кода: ключ проверки остаётся в форме."""
    monkeypatch.setattr("app.fns.sync.confirm_code",
                        lambda *a: pytest.fail("кабинет не должен спрашиваться о пустом коде"))
    enter(web)
    body = web.post(PATH, data={"do": "confirm", "challenge": "ЧТ-12345",
                                "phone": PHONE, "code": "12"}).data.decode("utf-8")

    assert "Введите код из СМС" in body
    assert 'name="challenge" value="ЧТ-12345"' in body


# ---------- ключ из закладки ----------
def test_the_key_from_the_bookmarklet_is_taken_and_wiped_from_the_address(web, monkeypatch):
    """Ключ приезжает в адресе — забираем и тут же переводим на чистый адрес.

    В адресной строке и в истории браузера ключу доступа делать нечего.
    """
    import base64

    keys = json.dumps({"source": "lkdr-keys", "token": "a.b.c", "refresh": "r"})
    packed = base64.b64encode(keys.encode()).decode().replace("+", "-").replace("/", "_").rstrip("=")
    got = {}
    monkeypatch.setattr("app.fns.sync.connect_with_keys",
                        lambda text, phone=None: got.update(text=text, phone=phone) or {"seen": 3})
    monkeypatch.setattr("app.fns.sync.sync", lambda on_step=None, **kw: dict(DONE))
    enter(web)
    answer = web.get(f"{PATH}?keys={packed}")

    assert answer.status_code == 303
    assert answer.headers["Location"] == PATH, "ключ остался в адресе"
    assert json.loads(got["text"])["token"] == "a.b.c"
    assert got["phone"] == PHONE
    wait_for(screen._job_of(PHONE))


def test_a_broken_key_from_the_address_is_explained_not_swallowed(web):
    enter(web)
    page = web.get(f"{PATH}?keys=не-база64-!!!", follow_redirects=True).data.decode("utf-8")

    assert "закладки не подошёл" in page


# ---------- долгая загрузка ----------
def test_a_long_load_shows_its_progress_and_then_how_it_ended(web, monkeypatch):
    """Страница не висит молча: пока идёт работа — числа и заголовок Refresh.

    Именно это заменило полосу Streamlit: обычная страница не может ждать две
    минуты, а человек не должен гадать, работает приложение или умерло.
    """
    gate = threading.Event()

    def slow(on_step=None, **kw):
        on_step("fiscal", 7, 20)
        assert gate.wait(10), "тест не отпустил загрузку"
        return dict(DONE)

    monkeypatch.setattr("app.fns.sync.is_connected", lambda: True)
    monkeypatch.setattr("app.fns.sync.sync", slow)
    enter(web)
    web.post(PATH, data={"do": "sync"})

    going = web.get(PATH)
    assert going.headers.get("Refresh") == str(screen.REFRESH_SEC)
    assert "7 из 20" in going.data.decode("utf-8")

    gate.set()
    wait_for(screen._job_of(PHONE))

    ended = web.get(PATH)
    assert "Refresh" not in ended.headers, "страница обновляется сама после конца работы"
    body = ended.data.decode("utf-8")
    assert "Загружено 2 чека" in body and "3 позиции" in body


def test_a_cabinet_gone_quiet_is_named_not_hidden():
    """Застывшее «640 из 1632» и работающая загрузка выглядят одинаково.

    Разницу между «отвечают медленно» и «всё встало» человек по неподвижному числу
    не увидит, поэтому молчание кабинета считается и называется вслух.
    """
    job = screen._Job()
    job.news -= 120

    assert job.silent >= 120
    assert job.running and "Читаю опись" in job.saying


def test_the_second_press_does_not_start_a_second_load(web, monkeypatch):
    gate = threading.Event()
    starts = []

    def slow(on_step=None, **kw):
        starts.append(1)
        assert gate.wait(10)
        return dict(DONE)

    monkeypatch.setattr("app.fns.sync.is_connected", lambda: True)
    monkeypatch.setattr("app.fns.sync.sync", slow)
    enter(web)
    web.post(PATH, data={"do": "sync"})
    page = web.post(PATH, data={"do": "sync"}, follow_redirects=True).data.decode("utf-8")

    assert "Загрузка уже идёт" in page
    gate.set()
    wait_for(screen._job_of(PHONE))
    assert len(starts) == 1


def test_the_background_load_writes_into_the_workspace_of_that_person(web, monkeypatch):
    """Главная ловушка потока: contextvars в нём чистые, база выбирается заново.

    Не открыть рабочее место в самом потоке — значит записать чеки в общую базу из
    config.yaml. На экране это выглядит как «загрузилось, но ничего не появилось».
    """
    where = {}
    monkeypatch.setattr("app.fns.sync.is_connected", lambda: True)
    monkeypatch.setattr("app.fns.sync.sync",
                        lambda on_step=None, **kw: where.update(db=config.db_override()) or dict(DONE))
    enter(web)
    web.post(PATH, data={"do": "sync"})
    wait_for(screen._job_of(PHONE))

    assert where["db"] == users.db_path_for(PHONE)
    assert config.db_override() is None, "поток не снял базу за собой"


def test_a_cabinet_error_in_the_background_reaches_the_person(web, monkeypatch):
    def breaks(on_step=None, **kw):
        raise CabinetError("Кабинет перестал узнавать ключ — подключите его заново.")

    monkeypatch.setattr("app.fns.sync.is_connected", lambda: True)
    monkeypatch.setattr("app.fns.sync.sync", breaks)
    enter(web)
    web.post(PATH, data={"do": "sync"})
    wait_for(screen._job_of(PHONE))

    assert "перестал узнавать ключ" in page_of(web)


def test_a_connected_cabinet_says_when_it_last_looked_and_how_it_went(web):
    """Состояние подключения — четвёртая обязанность экрана: когда и чем кончилось."""
    enter(web)
    inside(web)
    from app import repo

    repo.set_setting("fns.token", "пропуск")
    repo.set_setting("fns.phone", PHONE)
    repo.set_setting("fns.last_sync_at", "2026-09-17T12:33:05")
    repo.set_setting("fns.last_result", '{"receipts": 5, "rows": 20, "pending": 3, "seen": 40}')
    users.deactivate()

    page = page_of(web)
    assert "подключён" in page and "+7 999 000-00-01" in page
    assert "2026-09-17 12:33" in page and "Загружено 5 чеков" in page
    assert "Проверить новые чеки" in page and "Отключить кабинет" in page


def test_disconnecting_forgets_the_key_but_keeps_the_receipts(web):
    enter(web)
    inside(web)
    from app import repo

    repo.set_setting("fns.token", "пропуск")
    users.deactivate()

    page = web.post(PATH, data={"do": "disconnect"},
                    follow_redirects=True).data.decode("utf-8")
    assert "Кабинет отключён" in page and "остались в истории" in page

    inside(web)
    from app.fns import sync as cabinet

    assert not cabinet.is_connected()


def test_asking_for_receipts_without_a_cabinet_says_so(web):
    enter(web)
    page = web.post(PATH, data={"do": "sync"}, follow_redirects=True).data.decode("utf-8")

    assert "Кабинет не подключён" in page


# ---------- запасные пути ----------
def test_a_bundle_file_lands_in_the_history_and_is_reported_in_numbers(web):
    """Файл пакета из закладки: чеки в истории, отчёт числами, а не словом «готово»."""
    enter(web)
    page = web.post(PATH, data={"do": "file", "store": "",
                                "file": (io.BytesIO(BUNDLE.encode("utf-8")), "cheki.json")},
                    content_type="multipart/form-data",
                    follow_redirects=True).data.decode("utf-8")

    assert "Загружено 2 чека" in page and "3 позиции" in page

    inside(web)
    assert history.loaded()["receipts"] == 2


def test_the_same_file_twice_does_not_double_the_history(web):
    """Повторная загрузка — норма, а не ошибка: закладку нажимают ещё раз."""
    enter(web)
    for _ in range(2):
        page = web.post(PATH, data={"do": "file", "store": "",
                                    "file": (io.BytesIO(BUNDLE.encode("utf-8")), "cheki.json")},
                        content_type="multipart/form-data",
                        follow_redirects=True).data.decode("utf-8")

    assert "Пропущено 2 чека" in page
    inside(web)
    assert history.loaded()["receipts"] == 2


def test_a_receipt_file_of_another_kind_goes_through_the_same_door(web):
    """PDF, таблица и письмо разбираются тем же импортёром — поле для файла одно."""
    enter(web)
    page = web.post(PATH, data={"do": "file", "store": "pyaterochka",
                                "file": (io.BytesIO(PASTED.encode("utf-8")), "zakaz.txt")},
                    content_type="multipart/form-data",
                    follow_redirects=True).data.decode("utf-8")

    assert "Загружено 1 чек" in page
    inside(web)
    assert history.loaded()["rows"] == 3


def test_a_file_we_do_not_read_is_refused_with_words(web):
    enter(web)
    page = web.post(PATH, data={"do": "file", "store": "",
                                "file": (io.BytesIO(b"PK\x03\x04"), "arhiv.zip")},
                    content_type="multipart/form-data",
                    follow_redirects=True).data.decode("utf-8")

    assert "не читает" in page and ".pdf" in page


def test_pasted_text_is_shown_before_it_is_saved(web):
    """Сначала показать, потом сохранять: ошибку разбора человек увидит до истории."""
    enter(web)
    body = web.post(PATH, data={"do": "preview", "text": PASTED, "store": ""}).data.decode("utf-8")

    assert "Понял <b>3 позиции</b>" in body, "числа склоняем и в отчёте о своей работе"
    assert "Молоко Простоквашино" in body
    inside(web)
    assert history.loaded()["rows"] == 0, "предпросмотр не должен ничего записывать"


def test_the_shown_text_survives_the_round_trip_unbroken(web):
    """Показанный чек возвращается в поле слово в слово — на этом держится второй шаг.

    «Разобрать» и «Добавить» — две отправки одной формы, и сохраняется ровно то, что
    человек видит. Скрытым полем чек нести нельзя: переводы строк в значении атрибута
    живут по своим правилам, и чек приехал бы слипшимся в одну строку.
    """
    import html as html_tools
    import re

    enter(web)
    body = web.post(PATH, data={"do": "preview", "text": PASTED, "store": ""}).data.decode("utf-8")
    inside_field = re.search(r'<textarea name="text".*?>(.*?)</textarea>', body, re.S)

    assert inside_field, "поле с чеком пропало со страницы разбора"
    assert html_tools.unescape(inside_field.group(1)) == PASTED


def test_the_shown_receipt_is_saved_by_the_next_press(web):
    enter(web)
    web.post(PATH, data={"do": "preview", "text": PASTED, "store": ""})
    page = web.post(PATH, data={"do": "save", "text": PASTED, "store": ""},
                    follow_redirects=True).data.decode("utf-8")

    assert "Добавлено 3 позиции" in page
    inside(web)
    assert history.loaded()["rows"] == 3


def test_text_without_prices_is_explained_not_silently_dropped(web):
    enter(web)
    body = web.post(PATH, data={"do": "preview", "text": "просто слова без цен",
                                "store": ""}).data.decode("utf-8")

    assert "Ни одной строки с товаром и ценой" in body


# ---------- демо ----------
def test_demo_does_not_get_a_cabinet(web):
    """Демо — общая витрина: чужой ключ от кабинета в ней увидели бы все."""
    web.post("/demo")
    page = web.post(PATH, data={"do": "keys", "keys": "a.b.c"},
                    follow_redirects=True).data.decode("utf-8")

    assert "Демо — общая витрина" in page


def test_an_unknown_action_does_not_break_the_screen(web):
    enter(web)
    answer = web.post(PATH, data={"do": "приделать-крылья"})

    assert answer.status_code == 303
    assert web.get(PATH).status_code == 200
