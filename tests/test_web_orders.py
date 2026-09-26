"""Экран «Заказы»: заказы из чеков, отложенные чеки других магазинов, повтор заказа."""
from __future__ import annotations

import pytest

from app import repo, users
from app.importers.bundle import store_receipts
from app.importers.ofd_pdf import Receipt, ReceiptRow

PHONE = "79990000002"
PATH = "/orders"


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    client = application.test_client()
    client.post("/login", data={"phone": PHONE, "next": PATH})
    yield client
    users.deactivate()


def seed():
    """Три чека: Пятёрочка (из ФНС), Магнит (вручную) и аптека — она заказом не станет."""
    from app.db import init_db

    users.activate(PHONE)
    init_db()

    def cheque(key, seller, day, rows):
        return {"key": key, "receipt": Receipt(date=day, store_name=seller, total=sum(r[2] for r in rows),
                                               rows=[ReceiptRow(raw_name=n, qty=q, unit_price=p / q, total=p)
                                                     for n, q, p in rows])}

    store_receipts([cheque("k-5ka", 'ООО "Агроторг"', "2026-09-20",
                           [("Молоко Простоквашино 2,5% 930 мл", 2, 168.0), ("Хлеб Бородинский", 1, 89.0)])],
                   source="lkdr")
    store_receipts([cheque("k-mgn", "Магнит", "2026-08-11", [("Гречка Мистраль 900 г", 1, 129.0)]),
                    cheque("k-apt", "Аптека «Ригла»", "2026-09-21", [("Бинт", 1, 60.0)])],
                   source="file")
    users.deactivate()


def test_orders_are_listed_by_month_with_their_source(web):
    seed()
    page = web.get(PATH).data.decode("utf-8")

    assert "Пятёрочка · 2 товара" in page
    assert "Чек из ФНС" in page
    assert "Магнит · 1 товар" in page and "Чек добавлен вручную" in page
    assert page.index("Сентябрь") < page.index("Август")
    assert "Загрузить чеки из ФНС" in page and "Добавить чек вручную" in page


def test_other_store_cheques_are_shown_as_not_added(web):
    seed()
    page = web.get(PATH).data.decode("utf-8")

    assert "Не добавлены" in page
    assert "1 чек из других магазинов" in page
    assert "Аптека «Ригла»" in page
    assert "Бинт" not in page, "чек аптеки не стал заказом"


def test_order_card_and_repeat_into_basket(web):
    seed()
    card = web.get(PATH + "?key=k-5ka").data.decode("utf-8")
    assert "Добавить все товары в корзину" in card and "Хлеб" in card

    answer = web.post(PATH, data={"do": "repeat", "key": "k-5ka"})
    assert answer.status_code == 303 and "/basket?id=" in answer.headers["Location"]

    users.activate(PHONE)
    try:
        basket_id = int(answer.headers["Location"].rsplit("=", 1)[1])
        items = repo.basket_items(basket_id)
    finally:
        users.deactivate()
    assert len(items) == 2 and sorted(float(i["qty"]) for i in items) == [1.0, 2.0]


def test_empty_orders_say_what_to_do(web):
    page = web.get(PATH).data.decode("utf-8")
    assert "Заказов пока нет" in page


def test_fns_button_leads_to_connect_when_not_connected(web):
    answer = web.post(PATH, data={"do": "sync"})
    assert answer.status_code == 303 and answer.headers["Location"].endswith("/receipts")
