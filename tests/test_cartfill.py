"""Пачка «корзины — в кабинеты магазинов»: те же кнопки «Результата», только разом.

Стережётся то, что пачка обязана делать ровно как кнопка, и то, чего кнопка не
делала никогда, а пачка могла бы: положить что-то молча, в чужой кабинет или
дважды. Браузера и сети здесь нет — передача подменена на уровне cart.deliver, а
пускатель, отметка о ходе и выбор варианта работают настоящие.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import cartfill, handover, repo, users  # noqa: E402
from app.models import Product  # noqa: E402
from app.shopbrowser import cart  # noqa: E402
from app.shopbrowser import store as shopstore  # noqa: E402

PHONE = "79990000077"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Рабочее место с корзиной «молоко + сыр», знакомой Магниту и Пятёрочке."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.open_workspace(PHONE)
    milk = repo.upsert_product(Product(id=None, name="Молоко Простоквашино 930 мл", unit="pcs"))
    cheese = repo.upsert_product(Product(id=None, name="Сыр Российский 45%", unit="kg"))
    for code, price_milk, price_cheese in (("magnit", 89.0, 240.0), ("pyaterochka", 95.0, 260.0)):
        shop = repo.get_store(code)
        milk_sp = repo.upsert_store_product(shop.id, f"{code}-1", "Молоко Простоквашино 930 мл",
                                            url=f"https://{code}.example/1")
        cheese_sp = repo.upsert_store_product(shop.id, f"{code}-2", "Сыр Российский 45%",
                                              url=f"https://{code}.example/2")
        repo.confirm_mapping(milk, milk_sp, confirmed=True)
        repo.confirm_mapping(cheese, cheese_sp, confirmed=True)
        repo.save_price(milk_sp, price_milk)
        repo.save_price(cheese_sp, price_cheese)
    basket_id = repo.create_basket("Неделя 39")
    repo.set_basket_item(basket_id, milk, 2.0)
    repo.set_basket_item(basket_id, cheese, 0.7)
    users.deactivate()
    yield basket_id
    users.deactivate()


@pytest.fixture
def delivered(monkeypatch):
    """Передача без браузера: всё, что в наряде, «легло». Запоминает, что клали."""
    calls: list[tuple[str, int]] = []

    def deliver(chain, phone, plan):
        calls.append((chain, len(plan.lines)))
        items = [cart._row(line, True, "", "") for line in plan.lines]
        return cart._done(chain, [line.sku for line in plan.lines], [], items, "")

    monkeypatch.setattr(cart, "deliver", deliver)
    monkeypatch.setattr(cart.driver, "close", lambda chain, phone: None)
    return calls


def save_login(chain: str = "magnit") -> None:
    """Вход — как его сохраняет «Кабинет»: состояние плюс отметка о подключении."""
    from app import store_accounts

    users.open_workspace(PHONE)
    shopstore.save(chain, {"cookies": [{"name": "mg_at", "value": "ключ"}], "origins": []})
    store_accounts.mark_connected(chain)
    users.deactivate()


def test_without_a_saved_login_nothing_is_put_anywhere(home, delivered):
    """Класть некуда, пока человек не вошёл: корзина гостя живёт до закрытия вкладки."""
    report = "\n".join(cartfill.run([PHONE]))

    assert delivered == []
    assert "вход не сохранён" in report


def test_with_a_saved_login_the_cart_is_filled_the_way_the_button_does(home, delivered):
    save_login()

    report = "\n".join(cartfill.run([PHONE]))

    assert delivered == [("magnit", 2)], "Магнит — через тот же пускатель, обе позиции"
    assert "легло 2 из 2" in report
    users.open_workspace(PHONE)
    assert cart.progress("magnit")["finished_at"], "отметка о ходе закрыта, как у кнопки"
    assert not cart.running("magnit")


def test_a_chain_that_cannot_take_a_cart_says_why_and_is_not_touched(home, delivered, monkeypatch):
    """Пятёрочка не пускает наш сервер по адресу — пачка говорит это, а не пытается."""
    save_login("pyaterochka")
    monkeypatch.setattr(cartfill, "preferred_variant", lambda basket_id: _only(basket_id, "pyaterochka"))

    report = "\n".join(cartfill.run([PHONE]))

    assert delivered == []
    assert "в кабинет не положить" in report and "адрес" in report


def test_a_dry_run_neither_fills_nor_asks_for_links(home, delivered, monkeypatch):
    save_login()
    monkeypatch.setattr(handover, "for_store", lambda *a, **kw: pytest.fail("dry-run пошёл в сеть"))

    report = "\n".join(cartfill.run([PHONE], dry_run=True))

    assert delivered == []
    assert "положу в корзину 2 поз." in report


def test_a_link_chain_gets_its_link_reported(home, delivered, monkeypatch):
    """Лента и ВкусВилл принимают корзину ссылкой — её и надо донести до человека."""
    monkeypatch.setattr(cartfill, "preferred_variant", lambda basket_id: _only(basket_id, "lenta"))
    monkeypatch.setattr(handover, "for_store", lambda code, lines: handover.Handover(
        store_code=code, kind=handover.LINK, link="https://lenta.com/basket/?share_id=abc"))

    report = "\n".join(cartfill.run([PHONE]))

    assert "https://lenta.com/basket/?share_id=abc" in report
    assert delivered == []


def test_a_handover_already_going_is_not_started_twice(home, delivered):
    """Вторая передача положила бы всё в корзину второй раз."""
    save_login()
    users.open_workspace(PHONE)
    cart._progress("magnit", started_at="2026-09-22T23:00:00", finished_at=None,
                   total=2, at=1, done=0, now="Молоко", items=[])
    users.deactivate()

    report = "\n".join(cartfill.run([PHONE]))

    assert delivered == []
    assert "уже идёт передача" in report


def test_the_demo_is_never_sent_to_real_cabinets(home, delivered, monkeypatch):
    save_login()
    users.open_workspace(users.DEMO)
    users.deactivate()

    report = cartfill.run()

    assert all(not line.startswith(users.DEMO) for line in report)
    assert delivered == [("magnit", 2)]


def test_a_workspace_without_a_basket_says_where_to_make_one(home, delivered):
    """Корзину заводит нажатие на «Корзине» — пачка говорит это и какие входы готовы."""
    save_login()
    users.open_workspace(PHONE)
    repo.delete_basket(repo.list_baskets()[0]["id"])
    users.deactivate()

    report = "\n".join(cartfill.run([PHONE]))

    assert "корзин нет" in report and "«Корзина»" in report
    assert "входы сохранены: magnit" in report
    assert delivered == []


def test_the_phone_is_masked_in_the_report(home, delivered):
    first = cartfill.run([PHONE], dry_run=True)[0]

    assert PHONE not in first and first.startswith("79***0077")


def test_the_launcher_waits_when_asked_and_keeps_its_bookkeeping(home, delivered):
    """wait=True — для пачки: тот же путь, что у фонового потока, но на месте."""
    save_login()
    users.open_workspace(PHONE)
    from app import cartplan

    plan = cartplan.build("magnit", _only(0, "magnit").stores[0].lines, force=True)

    assert cart.start("magnit", PHONE, plan, wait=True) is True
    users.open_workspace(PHONE)
    assert cart.progress("magnit")["finished_at"]
    assert delivered == [("magnit", 2)]


def _only(basket_id: int, code: str):
    """Вариант из одной сети — чтобы проверить сеть, которую расчёт сам бы не выбрал."""
    from app.models import StoreBreakdown, Variant, VariantLine

    users.open_workspace(PHONE)
    shop = repo.get_store(code)
    lines = [VariantLine(store_code=code, store_name=shop.name, card_id=None, card_name=None,
                         product_id=int(item["product_id"]), product_name=item["name"],
                         qty=float(item["qty"]), price=100.0)
             for item in repo.basket_items(repo.list_baskets()[0]["id"])]
    part = StoreBreakdown(store_code=code, store_name=shop.name, subtotal=200.0, delivery=0.0,
                          discount=0.0, total=200.0, lines=lines)
    return Variant(stores=[part], total=200.0, baseline=250.0)
