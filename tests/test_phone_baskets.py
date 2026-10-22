"""Корзины на телефоне: после «Повторить» человек возвращается к прежней.

ЗАЧЕМ ЭТОТ СТОРОЖ. Живой прогон на эмуляторе 21.10.2026: человек нажал
«Повторить» в «Истории заказов», создалась новая корзина, она стала «текущей»
(самая свежая), а прежняя — с четырьмя товарами — пропала с экрана. У телефона
не было списка корзин, а `_basket()` брал только `repo.list_baskets()[0]`. В вебе
«открытая» корзина — это настройка `basket:last`, и телефон её игнорировал:
телефон и компьютер могли показывать разные корзины.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Экран «Корзины» со всеми корзинами; «Открыть» пишет
`basket:last` и возвращает в корзину; `_basket()` читает ту же настройку, а если
она указывает на удалённую или несуществующую корзину — берёт самую свежую;
ссылка «Корзины · N» появляется только тогда, когда корзин больше одной.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import repo, users  # noqa: E402

PHONE = "79990000079"


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


def basket_at(monkeypatch, name: str, moment: str) -> int:
    """Корзина с заданным временем создания: «самая свежая» должна быть однозначной."""
    monkeypatch.setattr(repo, "NOW", lambda: moment)
    return repo.create_basket(name)


def test_the_baskets_screen_lists_every_basket(web, monkeypatch):
    """Список всех корзин: имя каждой и «Открыть» у той, которую можно открыть."""
    enter(web)
    workspace()
    basket_at(monkeypatch, "Неделя", "2026-10-20T09:00:00")
    basket_at(monkeypatch, "Из заказа 11 октября, Пятёрочка", "2026-10-21T09:00:00")

    answer = web.get("/phone/baskets")

    assert answer.status_code == 200
    page = text(answer)
    assert "Неделя" in page
    assert "Из заказа 11 октября, Пятёрочка" in page
    assert "Открыть" in page
    assert "0 товаров" in page
    assert "открыта" in page


def test_opening_a_basket_remembers_it_for_the_phone_and_the_web(web, monkeypatch):
    """«Открыть» пишет basket:last — ту же настройку, что читает веб-корзина."""
    from app.models import Product
    from app.web.screens.basket import LAST_OPEN

    enter(web)
    workspace()
    old = basket_at(monkeypatch, "Прежняя", "2026-10-20T09:00:00")
    milk = repo.upsert_product(Product(id=None, name="Молоко 930 мл", unit="pcs"))
    repo.set_basket_item(old, milk, 1.0)
    basket_at(monkeypatch, "Из заказа", "2026-10-21T09:00:00")

    answer = web.post(f"/phone/baskets/{old}")

    assert answer.status_code == 303
    assert answer.headers["Location"].endswith("/phone/basket")
    workspace()
    assert repo.get_setting(LAST_OPEN) == str(old)
    # Человек после «Открыть» видит прежнюю корзину, а не свежую.
    page = text(web.get("/phone/basket"))
    assert "Прежняя" in page
    assert "Молоко 930 мл" in page
    assert "Из заказа" not in page


def test_the_phone_opens_the_basket_the_web_has_open(web, monkeypatch):
    """_basket() слушает basket:last; удалённая или чужая — откат к самой свежей."""
    from app.web.phone.common import _basket
    from app.web.screens.basket import LAST_OPEN

    enter(web)
    workspace()
    old = basket_at(monkeypatch, "Прежняя", "2026-10-20T09:00:00")
    fresh = basket_at(monkeypatch, "Из заказа", "2026-10-21T09:00:00")

    repo.set_setting(LAST_OPEN, str(old))
    assert int(_basket()["id"]) == old

    repo.delete_basket(old)
    assert int(_basket()["id"]) == fresh

    repo.set_setting(LAST_OPEN, "999999")
    assert int(_basket()["id"]) == fresh


def test_opening_a_missing_basket_returns_to_the_list(web, monkeypatch):
    """Корзины уже нет — не пишем basket:last и возвращаемся к списку, а не в пустоту."""
    from app.web.screens.basket import LAST_OPEN

    enter(web)
    workspace()
    current = basket_at(monkeypatch, "Прежняя", "2026-10-20T09:00:00")
    repo.set_setting(LAST_OPEN, str(current))

    answer = web.post("/phone/baskets/999999")

    assert answer.status_code == 303
    assert answer.headers["Location"].endswith("/phone/baskets")
    workspace()
    assert repo.get_setting(LAST_OPEN) == str(current)


def test_the_link_to_all_baskets_appears_only_when_there_is_a_choice(web, monkeypatch):
    """Одна корзина — ссылки нет; их две — «Корзины · 2» ведёт на /phone/baskets."""
    enter(web)
    workspace()
    basket_at(monkeypatch, "Одна", "2026-10-20T09:00:00")

    page = text(web.get("/phone/basket"))
    assert "Корзины" not in page
    assert "/phone/baskets" not in page

    workspace()                                # запрос снял базу с потока, как в бою
    basket_at(monkeypatch, "Вторая", "2026-10-21T09:00:00")

    page = text(web.get("/phone/basket"))
    assert "Корзины" in page
    assert "/phone/baskets" in page
