"""Приложение на телефоне: четыре экрана, выбранные владельцем 17.10.2026.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Путь, ради которого телефонная часть затевалась
(docs/design/ekrany-telefona-2026-10-17.md): повторить прошлый заказ, увидеть его
товары в корзине, узнать, где дешевле, и нажать «Заказать». Каждый шаг — отдельный
тест, потому что ломается каждый по-своему: повтор может положить не те количества,
«−» у весового товара — снять килограмм вместо ста граммов, «Заказать» — повести в
магазин без входа, а главная приложения — не довести до телефонных экранов.

ЧЕГО ЭТИ ТЕСТЫ НЕ ЗНАЮТ. Как экраны выглядят на телефоне и попадает ли палец:
это смотрят глазами (макеты — канва «Моя корзина — приложение на телефоне»).
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import repo, users  # noqa: E402
from app.models import Product  # noqa: E402

PHONE = "79990000071"
APP_UA = "Mozilla/5.0 (Linux; Android 14; wv) KorzinaApp/1.6"
CSS = os.path.join(ROOT, "app", "web", "static", "phone.css")


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временных базах — как в tests/test_basket_mobile.py."""
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


def add_product(name: str, unit: str = "pcs") -> int:
    return repo.upsert_product(Product(id=None, name=name, unit=unit))


def price(product_id: int, store_code: str, value: float) -> None:
    store = repo.get_store(store_code)
    sp_id = repo.upsert_store_product(store.id, f"sku-{store_code}-{product_id}", f"товар {product_id}")
    repo.confirm_mapping(product_id, sp_id, confirmed=True)
    repo.save_price(sp_id, value)


def seed_order(day: str = "2026-10-11", store_code: str = "pyaterochka", count: int = 7) -> list[int]:
    """Один чек из count товаров — то, что человек повторяет."""
    store = repo.get_store(store_code)
    ids = []
    for n in range(count):
        pid = add_product(f"Товар заказа {n + 1}")
        repo.add_history_row(day, store.id, pid, f"ТОВАР {n + 1}", float(n % 2 + 1), 100.0,
                             100.0 * (n % 2 + 1))
        ids.append(pid)
    return ids


def latest_items() -> dict[int, float]:
    basket = repo.list_baskets()[0]
    return {int(i["product_id"]): float(i["qty"]) for i in repo.basket_items(int(basket["id"]))}


# ---------- вход и главная приложения ----------
def test_phone_screens_need_login(web):
    answer = web.get("/phone/basket")
    assert answer.status_code == 302
    assert "/login" in answer.headers["Location"]


def test_the_app_on_the_phone_lands_on_the_phone_screens(web):
    """Приложение открывает главную — и попадает в Корзину телефона, а не в веб."""
    enter(web)
    answer = web.get("/", headers={"User-Agent": APP_UA})
    assert answer.status_code == 302
    assert answer.headers["Location"].endswith("/phone/basket")


def test_the_full_version_stays_reachable_from_the_app(web):
    enter(web)
    assert web.get("/?web=1", headers={"User-Agent": APP_UA}).status_code == 200


def test_a_browser_keeps_the_web_home(web):
    """Компьютер и обычный браузер живут как жили: телефонная часть их не уводит."""
    enter(web)
    assert web.get("/").status_code == 200


# ---------- Корзина ----------
def test_an_empty_basket_says_what_to_do(web):
    enter(web)
    page = text(web.get("/phone/basket"))
    assert "Корзина пуста" in page
    assert "История заказов" in page
    assert "disabled" in page                 # «Где дешевле» нечего считать


def test_plus_minus_and_remove_change_the_basket(web):
    enter(web)
    workspace()
    milk = add_product("Молоко 930 мл")
    bread = add_product("Хлеб Бородинский")
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, milk, 2.0)
    repo.set_basket_item(basket_id, bread, 1.0)

    page = text(web.get("/phone/basket"))
    assert "Молоко 930 мл" in page and "2 шт" in page

    web.post("/phone/basket", data={"do": f"plus:{milk}"})
    workspace()
    assert latest_items()[milk] == 3.0

    web.post("/phone/basket", data={"do": f"minus:{bread}"})
    workspace()
    assert bread not in latest_items()          # с одной штуки «−» убирает товар

    web.post("/phone/basket", data={"do": f"remove:{milk}"})
    workspace()
    assert latest_items() == {}


def test_a_weighed_item_steps_by_a_hundred_grams(web):
    """«+» у сливы — сто граммов, а не килограмм: иначе корзина стоит впятеро дороже."""
    enter(web)
    workspace()
    plum = add_product("Слива", unit="kg")
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, plum, 0.5)

    web.post("/phone/basket", data={"do": f"plus:{plum}"})
    workspace()
    assert latest_items()[plum] == pytest.approx(0.6)


# ---------- История заказов ----------
def test_history_shows_the_first_goods_and_the_rest_on_tap(web):
    enter(web)
    workspace()
    seed_order(count=7)

    page = text(web.get("/phone/orders"))
    assert "История заказов" in page
    assert "Пятёрочка" in page and "7 товаров" in page
    assert "Ещё 2 товара" in page
    assert "Повторить" in page


def test_repeat_puts_the_order_into_the_basket(web):
    """«Повторить» — те же товары и те же количества, и заказ помечен «В корзине»."""
    from app.web.screens import orders as web_orders

    enter(web)
    workspace()
    ids = seed_order(count=3)
    key = web_orders.orders()[0]["key"]

    answer = web.post("/phone/orders", data={"key": key})
    assert answer.status_code == 303
    assert answer.headers["Location"].endswith("/phone/basket")

    workspace()
    assert latest_items() == {ids[0]: 1.0, ids[1]: 2.0, ids[2]: 1.0}
    assert "Из заказа" in repo.list_baskets()[0]["name"]
    assert "В корзине" in text(web.get("/phone/orders"))


# ---------- Где дешевле ----------
def _priced_basket(stores=("magnit", "pyaterochka")) -> None:
    milk = add_product("Молоко 930 мл")
    bread = add_product("Хлеб Бородинский")
    values = {"magnit": (40.0, 30.0), "pyaterochka": (50.0, 25.0), "vkusvill": (45.0, 28.0)}
    for code in stores:
        price(milk, code, values[code][0])
        price(bread, code, values[code][1])
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, milk, 2.0)
    repo.set_basket_item(basket_id, bread, 1.0)


def test_cheaper_answers_where_and_lists_every_place(web):
    enter(web)
    workspace()
    _priced_basket()

    page = text(web.get("/phone/cheaper"))
    assert "Где дешевле" in page
    assert "Рекомендуем" in page
    assert "Всё в Магните" in page and "Всё в Пятёрочке" in page
    assert "Сколько стоит в каждом месте" in page


def test_every_store_with_the_basket_is_a_place(web):
    """Расчёт отдаёт три лучших варианта, а владелец просил «сколько стоит в каждом месте»:
    вся корзина в каждой сети стоит в списке, даже если в тройку она не вошла."""
    enter(web)
    workspace()
    _priced_basket(stores=("magnit", "pyaterochka", "vkusvill"))

    page = text(web.get("/phone/cheaper"))
    for title in ("Всё в Магните", "Всё в Пятёрочке", "Всё во ВкусВилле"):
        assert title in page


def test_ordering_without_login_leads_to_connecting_the_store(web):
    """«Заказать» в сеть, куда человек не вошёл, ведёт подключать её, а не в никуда."""
    enter(web)
    workspace()
    _priced_basket(stores=("magnit",))

    page = text(web.get("/phone/cheaper"))
    assert "Подключить Магнит" in page

    answer = web.post("/phone/cheaper", data={"store": "magnit"})
    assert answer.status_code == 303
    assert "/phone/settings?connect=magnit" in answer.headers["Location"]


# ---------- Настройки ----------
def test_settings_list_stores_receipts_and_the_way_back_to_the_web(web):
    enter(web)
    page = text(web.get("/phone/settings"))
    assert "Осталось подключить" in page
    assert "Магнит" in page and "Подключить" in page
    assert "Мои чеки (ФНС)" in page
    assert "Полная версия приложения" in page


def test_a_lost_login_asks_to_sign_in_again(web):
    """Вход был, а сохранённого нет — «Войти снова», а не галочка «подключено»."""
    from app import store_accounts

    enter(web)
    workspace()
    store_accounts.mark_connected("magnit")

    page = text(web.get("/phone/settings"))
    assert "вход истёк" in page
    assert "Войти снова" in page


def test_settings_save_the_address(web, monkeypatch):
    from app import location

    saved = []
    monkeypatch.setattr(location, "save_address", lambda value: saved.append(value) or False)
    enter(web)
    answer = web.post("/phone/settings", data={"address": "Санкт-Петербург, Невский пр., 28"})
    assert answer.status_code == 303
    assert "saved=address" in answer.headers["Location"]
    assert saved == ["Санкт-Петербург, Невский пр., 28"]


# ---------- плотность ----------
def test_controls_are_not_bigger_than_the_text(web):
    """Слово владельца: «половина на текст, половина на элемент управления» — нельзя.

    Размер в точках тест не измерит, но поймает переименованный класс или
    возвращённые крупные кнопки: правила плотности стоят в phone.css буквально.
    """
    with open(CSS, encoding="utf-8") as fh:
        css = fh.read()
    assert ".q { width: 28px; height: 28px;" in css
    assert "height: 48px;" in css                       # главная кнопка внизу
    assert ".it { display: flex; align-items: center; gap: 6px; min-height: 40px;" in css
