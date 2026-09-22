"""Подсказки адреса на главной: маршрут, поведение и границы.

ЗАЧЕМ ЭТО ВООБЩЕ. Адрес — единственное, что задаёт цены и наличие во всех сетях,
и вводится он руками. Опечатка здесь ничем себя не выдаёт: сеть не разберёт адрес,
цены придут пустыми, и человек решит, что сломано приложение.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import geo, users  # noqa: E402

PHONE = "79990000077"


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    users.deactivate()


def enter(client):
    return client.post("/login", data={"phone": PHONE, "next": "/"})


def test_the_address_field_suggests_as_you_type(web, monkeypatch):
    """Маршрут отдаёт готовые строки того же вида, что показывает подсказчик."""
    monkeypatch.setattr(geo, "suggest",
                        lambda q, limit=5: [f"Москва, {q} 42", f"Москва, {q} 44"])

    enter(web)
    body = web.get("/address/suggest?q=ленинский").get_json()

    assert body["items"][0] == "Москва, ленинский 42"
    assert len(body["items"]) == 2


def test_a_broken_suggester_does_not_break_the_screen(web, monkeypatch):
    """Служба подсказок не ответила — пустой список, а не ошибка.

    Подсказка это помощь, а не шаг: без неё человек допишет адрес сам, как
    дописывал раньше. Красная плашка здесь напугала бы на ровном месте.
    """
    def broken(_q, limit=5):
        raise RuntimeError("DaData не ответила")

    monkeypatch.setattr(geo, "suggest", broken)

    enter(web)
    answer = web.get("/address/suggest?q=ленинский")

    assert answer.status_code == 200
    assert answer.get_json() == {"items": []}


def test_suggestions_are_not_open_to_strangers(web):
    """Маршрут закрыт тем же сторожем, что и экраны: он ходит в чужую службу."""
    answer = web.get("/address/suggest?q=ленинский", follow_redirects=False)

    assert answer.status_code in (302, 401, 403), "подсказками можно доить чужую службу"


def test_nothing_is_filled_in_behind_the_person(web):
    """Поле НЕ дописывается само, пока человек не выбрал строку.

    Молча подставленный адрес человек не заметит и уедет по чужому: это не
    удобство, а подмена.
    """
    enter(web)
    page = web.get("/").data.decode("utf-8")

    assert 'id="addr-list"' in page, "списка подсказок нет"
    assert "НЕ РЕШАЕТ ЗА ЧЕЛОВЕКА" in page, "нет записи о границе — её сотрут при первой уборке"
    assert "autocomplete=\"off\"" in page, "браузер подставит свой старый адрес поверх подсказок"


def test_the_list_lies_over_the_page_and_not_inside_it(web):
    """Список лежит НАД страницей.

    Раздвигающий страницу список уводит кнопку «Сохранить адрес» под клавиатуру
    телефона ровно тогда, когда человек до неё тянется.
    """
    enter(web)
    page = web.get("/").data.decode("utf-8")

    assert ".sugg{" in page and "position:absolute" in page
    assert "min-height:44px" in page, "в строку мельче сорока четырёх точек пальцем не попасть"
