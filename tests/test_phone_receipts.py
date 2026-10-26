"""«Мои чеки» на телефоне: статус кабинета, числа истории, одна загрузка на человека.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ (пункт 3 владельца, зона Z5 плана
docs/plans/mobilnaya-versiya-2026-10-21.md):

- экран закрыт без входа, как и остальные телефонные;
- «не подключено» объясняет капчу и зовёт вставлять ключ, а не показывает пустоту;
- «подключено» называет номер кабинета, время последней проверки и чем она кончилась,
  и рядом стоят числа истории: чеки, позиции, сумма и то, что ещё не загружено;
- идущая загрузка видна словами и полосой, а страница сама себя перезапрашивает;
- нажатие «Загрузить сейчас» запускает ОДНУ загрузку на человека: второе нажатие,
  пока первая идёт, второй не заводит (общий ход дела с /receipts);
- ключ из закладки приезжает в адресе, принимается и тут же пропадает из адреса;
- отказ кабинета виден словами на странице, а не 500;
- строка «Мои чеки (ФНС)» в настройках ведёт на телефонный экран, а не на /receipts.

СЕТИ И КАБИНЕТ ЗДЕСЬ НЕ НАСТОЯЩИЕ: подменяются is_connected, connect_with_keys,
_start_sync и _job_of. Живого кабинета у теста нет и быть не может — вход в него
проходит человек (капча и код из СМС). Числа истории — настоящие, из временной базы.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import repo, users  # noqa: E402
from app.fns.client import CabinetError  # noqa: E402
from app.models import Product  # noqa: E402
from app.web.phone import receipts as phone_receipts  # noqa: E402
from app.web.screens import receipts as web_receipts  # noqa: E402

PHONE = "79990000105"
PATH = "/phone/receipts"


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временных базах — как в tests/test_phone_screens.py."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    from app.web.screens import result

    result._CALC.clear()
    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    result._CALC.clear()
    users.deactivate()


@pytest.fixture(autouse=True)
def clean_jobs():
    """Ход загрузки живёт в памяти процесса — между тестами он не должен протекать."""
    web_receipts._JOBS.clear()
    yield
    web_receipts._JOBS.clear()


def enter(client):
    from conftest import sign_in

    return sign_in(client, PHONE)


def workspace() -> None:
    users.open_workspace(PHONE)


def text(answer) -> str:
    return answer.data.decode("utf-8")


def connected_cabinet(last: dict | None = None, synced: str = "2026-10-21T02:14:05") -> None:
    """Кабинет подключён: пропуск, номер, время проверки и её итог — в настройках базы."""
    workspace()
    repo.set_setting("fns.token", "пропуск")
    repo.set_setting("fns.phone", PHONE)
    repo.set_setting("fns.last_sync_at", synced)
    if last is not None:
        repo.set_setting("fns.last_result", json.dumps(last, ensure_ascii=False))
    users.deactivate()


def seed_history() -> None:
    """Один чек и две позиции — ровно то, что экран должен назвать числами."""
    workspace()
    store = repo.get_store("pyaterochka")
    milk = repo.upsert_product(Product(id=None, name="Молоко 930 мл"))
    repo.add_history_row("2026-10-01", store.id, milk, "МОЛОКО 930 МЛ", 2.0, 50.0, 100.0,
                         receipt_key="k1")
    repo.add_history_row("2026-10-02", store.id, milk, "МОЛОКО 930 МЛ", 1.0, 60.0, 60.0,
                         receipt_key="k1")
    repo.mark_receipt_imported("k1", "2026-10-01", "Пятёрочка", 160.0, rows=2)
    users.deactivate()


def packed_keys(token: str = "a.b.c") -> str:
    """Ключ в том виде, в каком его кладёт в адрес закладка: base64url без «=»."""
    raw = json.dumps({"source": "lkdr-keys", "token": token, "refresh": "r"})
    return base64.b64encode(raw.encode()).decode().replace("+", "-").replace("/", "_").rstrip("=")


# ---------- вход ----------
def test_the_phone_receipts_screen_needs_login(web):
    answer = web.get(PATH)

    assert answer.status_code == 302
    assert "/login" in answer.headers["Location"]


# ---------- состояния кабинета ----------
def test_a_cabinet_that_is_not_connected_says_how_to_connect(web, monkeypatch):
    """Отказ объясняется словами: капча и ключ из закладки, а не пустая страница."""
    monkeypatch.setattr("app.fns.sync.is_connected", lambda: False)
    enter(web)

    page = text(web.get(PATH))

    assert "Мои чеки" in page
    assert "не подключён" in page
    assert "капчу" in page and "закладк" in page
    assert "Вставить ключ" in page                    # главное действие снизу
    assert "Загрузить сейчас" not in page             # загружать нечего
    assert "Полная версия" in page and 'href="/receipts"' in page


def test_the_screen_offers_the_fns_cabinet_window_and_a_hint_outside_the_app(web, monkeypatch):
    """Кнопка входа в кабинет есть всегда; без моста рядом — «откройте приложение»."""
    monkeypatch.setattr("app.fns.sync.is_connected", lambda: False)
    enter(web)

    page = text(web.get(PATH))

    assert "Войти в кабинет ФНС" in page
    assert "data-open-fns" in page
    assert "Откройте приложение" in page
    assert 'id="keys"' in page, "рядом с подсказкой нет поля ключа"


def test_inside_the_app_the_hint_is_hidden_and_the_page_is_phone(web, monkeypatch):
    """UA приложения — подпись «в приложении», подсказка спрятана, кнопка на месте."""
    monkeypatch.setattr("app.fns.sync.is_connected", lambda: False)
    enter(web)

    page = text(web.get(PATH, headers={"User-Agent": "Mozilla/5.0 KorzinaApp/1.16"}))

    assert 'data-device="phone"' in page
    assert re.search(r'id="fns-hint"[^>]*hidden', page), "в приложении подсказка не спрятана"
    assert "Войти в кабинет ФНС" in page


def test_a_connected_cabinet_names_its_phone_and_the_numbers_of_history(web, monkeypatch):
    """Числа истории — из summary: чеки, позиции, сумма; итог проверки — из кабинета."""
    monkeypatch.setattr("app.fns.sync.is_connected", lambda: True)
    seed_history()
    connected_cabinet({"receipts": 5, "rows": 20, "pending": 3, "seen": 40})
    enter(web)

    page = text(web.get(PATH))

    assert "подключён" in page and users.display(PHONE) in page
    assert "Последняя проверка" in page and "2026-10-21 02:14" in page
    assert "Загружено 5 чеков" in page and "20 позиций" in page
    assert "Чеков" in page and "Позиций" in page and "На сумму" in page
    assert "Загружено 1 чек, 2 позиции" in page        # headline истории
    assert "Загрузить сейчас" in page and "Отключить" in page


def test_a_running_load_is_named_and_the_page_refreshes_itself(web, monkeypatch):
    """Застывшее «7 из 20» и работа — разное; страница обновляется сама, как на /receipts."""
    monkeypatch.setattr("app.fns.sync.is_connected", lambda: True)
    connected_cabinet()
    job = web_receipts._Job()
    job.stage, job.done, job.total = "fiscal", 7, 20
    monkeypatch.setattr(phone_receipts, "_job_of", lambda phone: job)
    enter(web)

    answer = web.get(PATH)
    page = text(answer)

    assert "Беру позиции: 7 из 20" in page
    assert "Идёт" in page and "обновляется сама" in page
    assert answer.headers.get("Refresh") == str(phone_receipts.REFRESH_SEC)
    assert "Загрузка идёт" in page                    # кнопка не предлагает вторую


def test_a_failed_load_shows_the_cabinet_answer(web, monkeypatch):
    """Ошибка потока — словами на странице, а не белый экран и не 500."""
    monkeypatch.setattr("app.fns.sync.is_connected", lambda: True)
    connected_cabinet()
    job = web_receipts._Job()
    job.error = "Кабинет перестал узнавать ключ — подключите его заново."
    monkeypatch.setattr(phone_receipts, "_job_of", lambda phone: job)
    enter(web)

    page = text(web.get(PATH))

    assert "Кабинет ответил: Кабинет перестал узнавать ключ" in page


# ---------- «Загрузить сейчас» ----------
def test_sync_starts_one_load_and_the_second_press_does_not_start_another(web, monkeypatch):
    """Одна загрузка на человека: ход дела общий с /receipts, второй поток не заводится."""
    starts: list[str] = []
    running: dict = {}

    def start(phone: str) -> bool:
        starts.append(phone)
        running["job"] = web_receipts._Job()          # загрузка пошла и ещё идёт
        return True

    monkeypatch.setattr("app.fns.sync.is_connected", lambda: True)
    monkeypatch.setattr(phone_receipts, "_start_sync", start)
    monkeypatch.setattr(phone_receipts, "_job_of", lambda phone: running.get("job"))
    enter(web)

    answer = web.post(PATH, data={"do": "sync"})
    assert answer.status_code == 303
    assert answer.headers["Location"] == PATH
    assert starts == [PHONE]

    page = text(web.post(PATH, data={"do": "sync"}, follow_redirects=True))

    assert len(starts) == 1, "второе нажатие запустило вторую загрузку"
    assert "Загрузка уже идёт" in page


def test_sync_without_a_cabinet_explains_instead_of_starting(web, monkeypatch):
    starts: list[str] = []
    monkeypatch.setattr("app.fns.sync.is_connected", lambda: False)
    monkeypatch.setattr(phone_receipts, "_start_sync", lambda phone: starts.append(phone) or True)
    enter(web)

    page = text(web.post(PATH, data={"do": "sync"}, follow_redirects=True))

    assert starts == []
    assert "Кабинет ФНС не подключён" in page


# ---------- ключ из закладки ----------
def test_the_key_from_the_bookmarklet_is_taken_and_wiped_from_the_address(web, monkeypatch):
    """Ключ приезжает в адресе — принимаем и тут же переводим на чистый /phone/receipts."""
    got: dict = {}
    starts: list[str] = []
    monkeypatch.setattr("app.fns.sync.connect_with_keys",
                        lambda text, phone=None: got.update(text=text, phone=phone) or {"seen": 3})
    monkeypatch.setattr(phone_receipts, "_start_sync", lambda phone: starts.append(phone) or True)
    enter(web)

    answer = web.get(f"{PATH}?keys={packed_keys()}")
    assert answer.status_code == 303
    assert answer.headers["Location"] == PATH, "ключ остался в адресе"
    assert json.loads(got["text"])["token"] == "a.b.c"
    assert got["phone"] == PHONE
    assert starts == [PHONE], "после подключения чеки должны забираться сразу"

    page = text(web.get(PATH))
    assert "Кабинет подключён ключом из закладки" in page and "чеки в нём видны" in page


def test_a_broken_key_is_explained_on_the_page_not_by_a_500(web, monkeypatch):
    from app.fns import sync as cabinet

    def refuses(text, phone=None):
        raise CabinetError("кабинет не узнал этот ключ")

    monkeypatch.setattr(cabinet, "connect_with_keys", refuses)
    enter(web)

    answer = web.get(f"{PATH}?keys={packed_keys()}", follow_redirects=True)

    assert answer.status_code == 200
    assert answer.request.path == PATH, "ключ остался в адресе"
    page = text(answer)
    assert "закладки не подошёл" in page and "кабинет не узнал этот ключ" in page


def test_a_cabinet_error_on_a_pasted_key_is_shown_in_words(web, monkeypatch):
    """Ключ, вставленный руками, кабинет не принял — человек видит ответ кабинета."""
    from app.fns import sync as cabinet

    def refuses(text, phone=None):
        raise CabinetError("кабинет не узнал этот ключ")

    monkeypatch.setattr(cabinet, "connect_with_keys", refuses)
    enter(web)

    answer = web.post(PATH, data={"do": "keys", "keys": "a.b.c"}, follow_redirects=True)

    assert answer.status_code == 200
    page = text(answer)
    assert "Кабинет ответил: кабинет не узнал этот ключ" in page


# ---------- отключение ----------
def test_disconnect_forgets_the_key_but_keeps_the_receipts(web):
    connected_cabinet()
    enter(web)

    page = text(web.post(PATH, data={"do": "disconnect"}, follow_redirects=True))

    assert "Кабинет отключён" in page and "остались в истории" in page
    workspace()
    from app.fns import sync as cabinet

    assert not cabinet.is_connected()
    users.deactivate()


# ---------- демо ----------
def test_demo_does_not_get_a_cabinet(web):
    """Демо — общая витрина: чужой ключ от кабинета в ней увидели бы все.

    С 25.10.2026 запись в демо закрыта на самой двери (app/web/auth.py): форма уходит назад
    переводом 303 с demo=1 и до экрана не доходит, а экран, куда вернулись, говорит про демо.
    Referer здесь — тот, что браузер присылает из формы этого экрана.
    """
    web.post("/demo")

    page = text(web.get(PATH))
    assert "Демо — общая витрина" in page

    answer = web.post(PATH, data={"do": "sync"}, headers={"Referer": "http://localhost" + PATH})
    assert answer.status_code == 303
    assert answer.headers["Location"] == f"{PATH}?demo=1"
    page = text(web.get(answer.headers["Location"]))
    assert "Ключ от настоящего кабинета ФНС сюда класть нельзя" in page
    assert "Это демо — смотреть можно, менять нельзя." in page

    users.open_workspace(users.DEMO)
    assert not repo.get_setting("fns.token"), "ключ кабинета лёг в общее демо"
    users.deactivate()


# ---------- настройки ведут сюда ----------
def test_the_settings_row_leads_to_the_phone_screen(web):
    enter(web)

    page = text(web.get("/phone/settings"))

    assert 'href="/phone/receipts"' in page
    assert 'href="/receipts"' not in page
