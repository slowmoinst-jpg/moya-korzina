"""Кабинеты на телефоне: первый запуск, истёкший вход, отключение, ход сборки.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ (пункт 1 владельца, зона Z6 плана
docs/plans/mobilnaya-versiya-2026-10-21.md):

- первый запуск: пока ни одна сеть не подключена, экран объясняет словами, зачем
  подключать, а не показывает пустоту; сетей нет вовсе — одна короткая строка;
- «вход истёк» называется по факту (cartrun._login вернул «again») и рядом стоит
  время последней попытки из строки сборки, если оно там есть;
- «Отключить вход» стирает сохранённые куки и отметку подключения на сервере, а
  экран честно говорит, что WebView телефона живёт своей жизнью;
- идущая сборка показывает «собираю N из M» и оценку «осталось ~K с» по
  прошедшему времени; оценить нечем — только счётчик;
- замолчавший телефон — «сборка прервана» с датой последнего пульса, а свежая
  строка без пульса — ещё «идёт», ей дан допуск.

СЕТИ ЗДЕСЬ НАСТОЯЩИЕ, ПОДМЕНЯЮТСЯ ТОЛЬКО ФУНКЦИИ: _login, live и отметки в базе.
Так тест не зависит от того, что сегодня лежит в сохранённых входах.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import cartrun, repo, store_accounts, users  # noqa: E402
from app.models import Product  # noqa: E402
from app.shopbrowser import store as shopstore  # noqa: E402

PHONE = "79990000094"


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


def enter(client):
    return client.post("/login", data={"phone": PHONE, "next": "/"})


def workspace() -> None:
    users.open_workspace(PHONE)


def text(answer) -> str:
    return answer.data.decode("utf-8")


def basket_with_milk() -> int:
    """Корзина, по которой считается отпечаток. Сети в ней не нужны."""
    milk = repo.upsert_product(Product(id=None, name="Молоко 930 мл"))
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, milk, 2.0)
    return basket_id


def store_row(basket_id: int, **fields) -> cartrun.Row:
    """Строка сборки этой корзины с отпечатком и дорогой телефона."""
    values = dict(code="magnit", name="Магнит", road=cartrun.PHONE,
                  state=cartrun.GOING, by=cartrun.DEVICE, basket_id=basket_id,
                  stamp=cartrun.stamp_of(basket_id))
    values.update(fields)
    return cartrun.Row(**values)


def raw_rows(rows: dict[str, dict]) -> None:
    """Положить строки в базу как есть: save() поставил бы свежий пульс."""
    repo.set_setting(cartrun.KEY, json.dumps(rows, ensure_ascii=False))


# ---------- первый запуск ----------

def test_the_first_run_says_why_to_connect_at_least_one_store(web):
    enter(web)
    page = text(web.get("/phone/settings"))

    assert "Пока не подключён ни один магазин" in page
    assert "соберёт корзину в его кабинете само" in page
    assert "Подключить" in page, "кнопки подключения остаются рядом"


def test_no_stores_at_all_is_a_short_line_not_an_empty_page(web, monkeypatch):
    monkeypatch.setattr(repo, "list_stores", lambda: [])
    enter(web)
    page = text(web.get("/phone/settings"))

    assert "Магазины не настроены" in page
    assert "Осталось подключить" not in page
    assert "Магнит" not in page


# ---------- «вход истёк» по факту ----------

def test_a_lost_login_is_named_with_the_last_attempt(web, monkeypatch):
    """cartrun._login вернул «again» — значит вход был и кончился, а не «не входили»."""
    enter(web)
    workspace()
    cartrun.save(cartrun.Row(code="magnit", name="Магнит", state=cartrun.LOGIN,
                             login="again", finished_at="2026-10-21T02:10:00"))
    users.deactivate()
    monkeypatch.setattr(cartrun, "_login",
                        lambda code: "again" if code == "magnit" else "connect")

    page = text(web.get("/phone/settings"))

    assert "вход истёк — войти снова" in page
    assert "последняя попытка 21.10.2026 02:10" in page
    assert "Войти снова" in page


# ---------- «Отключить вход» ----------

def test_disconnecting_a_store_erases_the_saved_login_and_the_mark(web):
    enter(web)
    workspace()
    shopstore.save("magnit", {"cookies": [{"name": "mg_at", "value": "ключ"}], "origins": []})
    store_accounts.mark_connected("magnit")
    users.deactivate()

    page = text(web.get("/phone/settings"))
    assert "Отключить вход" in page and 'name="store" value="magnit"' in page

    answer = web.post("/phone/settings/disconnect", data={"store": "magnit"})
    assert answer.status_code == 303
    assert "saved=disconnected" in answer.headers["Location"]
    assert "store=magnit" in answer.headers["Location"]

    page = text(web.get(answer.headers["Location"]))
    assert "Вход в «Магнит» отключён" in page
    assert "пока не выйдете в её окне" in page

    workspace()
    assert store_accounts.connection("magnit").connected is False
    assert shopstore.load("magnit") is None, "сохранённого входа больше нет"
    assert cartrun._login("magnit") == "connect", "сеть снова «не подключена»"
    users.deactivate()


def test_disconnecting_an_unknown_store_changes_nothing(web):
    enter(web)
    answer = web.post("/phone/settings/disconnect", data={"store": "чужой"})

    assert answer.status_code == 303
    assert "saved=" not in answer.headers["Location"]


# ---------- ход сборки и оценка ----------

def test_the_going_row_counts_and_estimates_the_rest(web, monkeypatch):
    enter(web)
    workspace()
    basket_id = basket_with_milk()
    started = (datetime.now() - timedelta(seconds=20)).isoformat(timespec="seconds")
    cartrun.save(store_row(basket_id, planned=10, at=2, started_at=started))
    users.deactivate()
    monkeypatch.setattr(cartrun, "live",
                        lambda row: {"at": 2, "total": 10, "now": "кладу 3 из 10: Сыр"})

    html = web.get("/phone/basket/runs").get_json()["html"]

    assert "собираю 2 из 10" in html
    assert "кладу 3 из 10: Сыр" in html
    assert "осталось ~" in html, "прошло 20 с, легло 2 из 10 — темп виден"


def test_without_a_start_time_the_row_only_counts(web, monkeypatch):
    """Оценить нечем — «собираю N из M» без выдуманных секунд."""
    enter(web)
    workspace()
    basket_id = basket_with_milk()
    cartrun.save(store_row(basket_id, planned=10, at=2, started_at=None))
    users.deactivate()
    monkeypatch.setattr(cartrun, "live", lambda row: {"at": 2, "total": 10, "now": ""})

    html = web.get("/phone/basket/runs").get_json()["html"]

    assert "собираю 2 из 10" in html
    assert "осталось" not in html


# ---------- «прервано» ----------

def test_a_silent_phone_is_interrupted_with_the_last_pulse(web):
    enter(web)
    workspace()
    basket_id = basket_with_milk()
    started = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
    cartrun.save(store_row(basket_id, planned=5, at=2, started_at=started))
    data = cartrun._all()
    data["magnit"]["beat"] = time.time() - cartrun.PHONE_STALE - 5
    raw_rows(data)
    users.deactivate()

    html = web.get("/phone/basket/runs").get_json()["html"]

    assert "сборка прервана" in html
    assert "телефон закрылся или ушёл" in html
    assert "последний пульс" in html
    assert "собираю" not in html, "молчащий телефон не выдаётся за идущий"


def test_a_fresh_row_without_a_pulse_is_still_going(web):
    """Свежая сборка успевает записать начало до первого пульса — это ещё «идёт»."""
    enter(web)
    workspace()
    basket_id = basket_with_milk()
    started = (datetime.now() - timedelta(seconds=3)).isoformat(timespec="seconds")
    raw_rows({"magnit": store_row(basket_id, planned=5, at=0,
                                  started_at=started).as_dict()})
    users.deactivate()

    html = web.get("/phone/basket/runs").get_json()["html"]

    assert "собираю" in html
    assert "прерван" not in html
