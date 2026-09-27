"""Живая проверка «закажем ли» (tools/ordercheck.py) — на подставных сетях.

Сам инструмент ходит в сети и запускается на сервере. Здесь проверяется то, что
от сети не зависит: как он считает цены и наличие, как называет дорогу корзины и
что он НИЧЕГО не пишет в базу цен и не нажимает «в корзину».
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import location, repo, users  # noqa: E402
from app.models import Location, PriceSnapshot, Product  # noqa: E402
from tools import ordercheck  # noqa: E402

PHONE = "79990000111"
CARD = "https://magnit.ru/product/1000-moloko"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Рабочее место с адресом и корзиной «молоко + сыр», опознанной в трёх сетях."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    from app.catalog import store as catalog_store

    monkeypatch.setattr(catalog_store, "path", lambda: str(tmp_path / "catalog.db"))
    users.open_workspace(PHONE)
    repo.set_setting(location.KEY_ADDRESS, "Москва, Микояна 12")
    milk = repo.upsert_product(Product(id=None, name="Молоко 930 мл", unit="pcs", weight_g=930))
    cheese = repo.upsert_product(Product(id=None, name="Сыр Российский", unit="kg"))
    basket = repo.create_basket("Неделя 40")
    repo.set_basket_item(basket, milk, 2)
    repo.set_basket_item(basket, cheese, 0.5)

    def mapped(code, pid, sku, url=None):
        store = repo.get_store(code)
        sp = repo.upsert_store_product(store.id, sku, "товар", url=url)
        repo.confirm_mapping(pid, sp, confirmed=True)

    mapped("magnit", milk, "1000", CARD)
    mapped("magnit", cheese, "1001", CARD)
    mapped("lenta", milk, "80424")
    mapped("pyaterochka", milk, f"hist-{milk}")
    users.deactivate()

    # Ни одна проверка здесь не ходит в сеть: точка и цены — подставные.
    monkeypatch.setattr(ordercheck, "_point", lambda code, address: f"точка {code}")
    monkeypatch.setattr(location, "for_store", lambda code: None)
    yield {"milk": milk, "cheese": cheese}
    users.deactivate()


def fake_prices(monkeypatch, answers: dict[str, list[PriceSnapshot]]):
    """Сеть отвечает заданными снимками; кто спросил — записано."""
    asked: list[tuple[str, list[str]]] = []

    class Fake:
        def __init__(self, code):
            self.code = code

        def get_prices(self, skus):
            asked.append((self.code, list(skus)))
            return answers.get(self.code, [])

    import app.connectors as connectors

    monkeypatch.setattr(connectors, "get_connector", lambda code, place=None: Fake(code))
    return asked


def snap(code, sku, price, in_stock=True, source=None):
    return PriceSnapshot(store_code=code, sku=sku, price=price, in_stock=in_stock, source=source)


def test_prices_and_stock_are_counted_without_saving_anything(home, monkeypatch):
    fake_prices(monkeypatch, {
        "magnit": [snap("magnit", "1000", 89.0), snap("magnit", "1001", 0.0, in_stock=False)],
        "lenta": [snap("lenta", "80424", 90.99)],
        "pyaterochka": [snap("pyaterochka", f"hist-{home['milk']}", 95.0)],
    })
    from app import service

    monkeypatch.setattr(service, "cart_link", lambda code, lines: f"https://{code}.example/cart")
    answer = ordercheck.run(PHONE, chains=["magnit", "lenta", "pyaterochka", "vkusvill"],
                            browser=False)
    by_code = {r["code"]: r for r in answer["chains"]}

    magnit = by_code["magnit"]
    assert (magnit["mapped"], magnit["live"], magnit["no_price"]) == (2, 1, 1)
    assert (magnit["in_stock"], magnit["out_of_stock"]) == (1, 1), "«нет в точке» — это наличие"
    lenta = by_code["lenta"]
    assert lenta["live"] == 1 and lenta["in_stock"] == 1
    assert lenta["ok"] is True and "https://lenta.example/cart" in lenta["order"]
    assert any("магазин в ссылке не передаётся" in p for p in lenta["problems"])
    five = by_code["pyaterochka"]
    assert five["receipts"] == 1 and five["in_stock"] is None, "Пятёрочка остатков не отдаёт"
    assert five["ok"] is None and "с телефона" in five["order"]
    vv = by_code["vkusvill"]
    assert vv["mapped"] == 0 and vv["ok"] is False

    # База цен человека не тронута: снимков нет ни одного.
    users.open_workspace(PHONE)
    with repo.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM store_prices").fetchone()[0] == 0


def test_magnit_without_a_saved_login_says_what_to_do(home, monkeypatch):
    fake_prices(monkeypatch, {})
    answer = ordercheck.run(PHONE, chains=["magnit"], browser=True)
    magnit = answer["chains"][0]
    assert magnit["ok"] is False and "вход не сохранён" in magnit["order"]


class Page:
    """Карточка Магнита: кнопка товара под заголовком. Нажатий быть не должно."""

    def __init__(self):
        self.clicked = False
        self.picked = None

    def goto(self, url, **kw):
        self.url = url

    def inner_text(self, _selector):
        return "Молоко 930 мл. Цена 89 ₽. В корзину"

    def evaluate(self, script, arg=None):
        if isinstance(arg, list) and len(arg) == 5:      # выбор кнопки (cart._PICK_JS)
            self.picked = self
            return 500
        return False                                     # «нет в наличии» в блоке покупки

    def query_selector(self, _selector):
        return self.picked

    def click(self, **kw):                               # pragma: no cover — не должно случиться
        self.clicked = True


def test_magnit_probe_reaches_the_button_and_does_not_press_it(home, monkeypatch):
    fake_prices(monkeypatch, {"magnit": [snap("magnit", "1000", 89.0), snap("magnit", "1001", 300.0)]})
    from app.shopbrowser import driver, point
    from app.shopbrowser import store as shopstore

    page = Page()
    monkeypatch.setattr(shopstore, "load", lambda chain: {"cookies": []})
    monkeypatch.setattr(point, "trouble", lambda chain, place=None: "")
    monkeypatch.setattr(point, "cookies_for", lambda chain, place=None: [])
    monkeypatch.setattr(driver, "available", lambda: True)
    monkeypatch.setattr(driver, "open_store", lambda chain, phone, state=None, url=None: None)
    monkeypatch.setattr(driver, "look", lambda chain, phone: {"guarded": False, "logged_in": True})
    monkeypatch.setattr(driver, "run", lambda chain, phone, job, timeout=0: job(page))
    monkeypatch.setattr(driver, "_settle", lambda page, wait=0.8: None)
    closed = []
    monkeypatch.setattr(driver, "close", lambda chain, phone: closed.append((chain, phone)))

    magnit = ordercheck.run(PHONE, chains=["magnit"])["chains"][0]
    assert magnit["ok"] is True and "не нажимали" in magnit["order"]
    assert page.clicked is False
    assert closed == [("magnit", ordercheck.PROBE)], "окно проверки своё и закрывается"
    assert any("кнопка «Передать»" in p for p in magnit["problems"]), \
        "без подключения кнопка на «Результате» скрыта — и это надо сказать"


def test_the_report_reads_for_a_person(home, monkeypatch):
    fake_prices(monkeypatch, {"magnit": [snap("magnit", "1000", 89.0)]})
    text = ordercheck.as_text(ordercheck.run(PHONE, chains=["magnit"], browser=False))
    assert "Адрес: Москва, Микояна 12" in text and "Корзина «Неделя 40»" in text
    assert "МАГНИТ — заказ:" in text and "Сводка:" in text


def test_no_address_means_no_check(home, monkeypatch):
    users.open_workspace(PHONE)
    repo.set_setting(location.KEY_ADDRESS, None)
    users.deactivate()
    answer = ordercheck.run(PHONE)
    assert "адрес не указан" in answer["error"]
    assert ordercheck.as_text(answer).startswith("Проверка не началась")


def test_the_point_of_magnit_names_a_shop_without_a_storefront(monkeypatch):
    """Точка Магнита «у дома мини» — назвать, что из неё не заказать."""
    from app.shopbrowser import point

    monkeypatch.setattr(location, "for_store",
                        lambda code: Location(store_id="628425", shop_type="MM_MINI"))
    monkeypatch.setattr(point, "instead", lambda chain: "")
    text = ordercheck._point("magnit", "Москва, Микояна 12")
    assert "628425" in text and "только на полке" in text
