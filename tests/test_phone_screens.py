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

from app import freshness, repo, users  # noqa: E402
from app.models import Product, StoreBreakdown, Variant, VariantLine  # noqa: E402

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


# ---------- «Быстрее» и подписи свежести (Z3+4) ----------
def _one_place(code: str, name: str, amount: float, *, lines: int = 2,
               missing: int = 0) -> Variant:
    """Место «вся корзина в одной сети» — тот же вариант расчёта, только собранный руками."""
    rows = [VariantLine(store_code=code, store_name=name, card_id=None, card_name=None,
                        product_id=n, product_name=f"Товар {n}", qty=1.0,
                        price=round(amount / max(1, lines), 2))
            for n in range(lines)]
    return Variant(
        stores=[StoreBreakdown(store_code=code, store_name=name, subtotal=amount,
                               delivery=0.0, discount=0.0, total=amount, lines=rows)],
        total=amount, baseline=amount + 100.0,
        missing_products=[f"Нет {n}" for n in range(missing)])


def _two_places(first: str, second: str, amount: float) -> Variant:
    """Разбивка на две сети, где всё на месте: дешевле одного заказа — и это не «быстрее»."""
    def part(code: str, n: int, price: float) -> StoreBreakdown:
        name = code.title()
        return StoreBreakdown(
            store_code=code, store_name=name, subtotal=price, delivery=0.0, discount=0.0,
            total=price,
            lines=[VariantLine(store_code=code, store_name=name, card_id=None, card_name=None,
                               product_id=n, product_name=f"Товар {n}", qty=1.0, price=price)])

    half = round(amount / 2, 2)
    return Variant(stores=[part(first, 0, half), part(second, 1, amount - half)],
                   total=amount, baseline=amount + 100.0)


def _a_basket() -> int:
    """Текущая корзина для страницы: содержимое ей не нужно, варианты подменены."""
    return repo.create_basket("Неделя")


def _substituted(monkeypatch, *variants, positions: int = 2) -> None:
    """Подменить расчёт «Результата» готовыми вариантами: экран рисуется из них.

    Через свёрток, а не через цены в базе: подписи источника и «быстрее» решаются
    вариантами, и собирать для каждого случая настоящий расчёт значило бы проверять
    не то правило (app/web/screens/result.py, _bundle).
    """
    from app.web.screens import result

    bundle = {"variants": list(variants), "baseline": 120.0, "positions": positions,
              "from_receipts": [], "phone_places": list(variants)}
    monkeypatch.setattr(result, "_bundle", lambda basket_id, again=False: bundle)


def test_cheaper_offers_one_order_for_the_whole_basket(web):
    """Полный односетевой вариант — блок «Быстрее» рядом с ответом, без обещания срока."""
    enter(web)
    workspace()
    _priced_basket(stores=("magnit",))

    page = text(web.get("/phone/cheaper"))
    assert "Быстрее" in page
    assert "Одним заказом, всё есть" in page
    assert "без обещания срока" in page


def test_cheaper_does_not_promise_one_order_when_no_store_has_everything(web, monkeypatch):
    """Нет сети, где есть всё, — блока нет: обещать «одним заказом» не из чего."""
    enter(web)
    workspace()
    _a_basket()
    _substituted(monkeypatch, _one_place("magnit", "Магнит", 90.0, lines=1, missing=1))

    page = text(web.get("/phone/cheaper"))
    assert "Сколько стоит в каждом месте" in page
    assert "Одним заказом, всё есть" not in page


def test_the_extra_for_one_order_is_named(web, monkeypatch):
    """Разбивка дешевле — блок говорит, сколько стоит взять всё одним заказом."""
    enter(web)
    workspace()
    _a_basket()
    _substituted(monkeypatch, _two_places("magnit", "pyaterochka", 100.0),
                 _one_place("magnit", "Магнит", 110.0))

    page = text(web.get("/phone/cheaper"))
    assert "Одним заказом, всё есть" in page
    assert "дороже самого выгодного на" in page


def test_each_place_says_where_its_price_comes_from(web):
    """У каждого места — источник и свежесть: витрина у адреса или наша копия цен."""
    enter(web)
    workspace()
    _priced_basket()

    page = text(web.get("/phone/cheaper"))
    assert "витрина у адреса, " in page      # Магнит считает по адресу (location.ADDRESS_STORES)
    assert "кэш, " in page                   # Пятёрочка — только наш снимок цен


def test_the_cabinet_sum_is_signed_by_the_store_and_its_time(web, monkeypatch):
    """Цену места написала сама сеть — так и подписано, со временем её итога."""
    from datetime import datetime, timedelta

    from app import cartrun

    enter(web)
    workspace()
    basket_id = _a_basket()
    at = (datetime.now() - timedelta(minutes=30)).replace(microsecond=0)
    cartrun.save(_cabinet_row(cartrun, basket_id, cartrun.stamp_of(basket_id), at=at))
    _substituted(monkeypatch, _one_place("magnit", "Магнит", 110.0))

    page = text(web.get("/phone/cheaper"))
    assert f"корзина кабинета, {at:%d.%m %H:%M}" in page


def _cabinet_row(cartrun, basket_id: int | None, stamp: str, amount: float = 110.0,
                 at=None, code: str = "magnit", landed: int = 2, delivery=None, discount=None,
                 **fields):
    """Строка сборки: итог написала сеть, легло `landed` позиций (в корзине их две)."""
    from datetime import datetime

    from app.carttotal import NETWORK, Total

    moment = (at or datetime.now()).replace(microsecond=0).isoformat()
    return cartrun.Row(
        code=code, name=code.title(), road=cartrun.BROWSER, state=cartrun.DONE,
        basket_id=basket_id, stamp=stamp, landed=[f"Товар {n}" for n in range(landed)],
        total=Total(sum=amount, source=NETWORK, at=moment, delivery=delivery, discount=discount),
        finished_at=moment, **fields)


def _network_dearer(cartrun, monkeypatch, **fields) -> None:
    """Пятёрочка у нас дешевле Магнита (100 против 110), а сеть насчитала ей 130."""
    basket_id = _a_basket()
    cartrun.save(_cabinet_row(cartrun, basket_id, cartrun.stamp_of(basket_id), amount=130.0,
                              code="pyaterochka", **fields))
    _substituted(monkeypatch, _one_place("pyaterochka", "Пятёрочка", 100.0),
                 _one_place("magnit", "Магнит", 110.0))


def test_another_baskets_cabinet_sum_is_not_shown(web, monkeypatch):
    """Строка сборки живёт в настройке рабочего места и переживает правку корзины.

    Итог ПРЕЖНЕЙ сборки — не итог текущей корзины: подпись «корзина кабинета»
    показывается, только когда совпали basket_id и отпечаток состава.
    """
    from app import cartrun

    enter(web)
    workspace()
    basket_id = _a_basket()
    cartrun.save(_cabinet_row(cartrun, basket_id + 1, cartrun.stamp_of(basket_id)))
    _substituted(monkeypatch, _one_place("magnit", "Магнит", 110.0))

    page = text(web.get("/phone/cheaper"))
    assert "корзина кабинета" not in page


def test_a_cabinet_sum_of_another_basket_composition_is_not_shown(web, monkeypatch):
    """Та же корзина, но состав с тех пор поменялся — итог снова чужой."""
    from app import cartrun

    enter(web)
    workspace()
    basket_id = _a_basket()
    cartrun.save(_cabinet_row(cartrun, basket_id, "старый-отпечаток"))
    _substituted(monkeypatch, _one_place("magnit", "Магнит", 110.0))

    page = text(web.get("/phone/cheaper"))
    assert "корзина кабинета" not in page


def test_the_network_total_replaces_our_price_and_changes_the_advice(web, monkeypatch):
    """Сеть насчитала дороже нашего расчёта — место дорожает, и советуют другое (Магнит)."""
    from app import cartrun

    enter(web)
    workspace()
    _network_dearer(cartrun, monkeypatch)

    page = text(web.get("/phone/cheaper"))
    assert page.index("Всё в Магните") < page.index("Всё в Пятёрочке")   # крупно — Магнит
    assert "130,00" in page and "корзина кабинета" in page
    assert "«Корзина кабинета» — цену написала сама сеть" in page


def test_the_chosen_place_names_the_network_total(web, monkeypatch):
    """Выбрано место с итогом сети — под ценой итог сети, а не наши «товары и доставка»."""
    from app import cartrun

    enter(web)
    workspace()
    _network_dearer(cartrun, monkeypatch)

    page = text(web.get("/phone/cheaper?pick=0"))        # место 0 — Пятёрочка
    assert "Итог посчитала сама сеть: 130,00" in page
    assert "Товары " not in page
    # Сеть доставку не показала — о ней молчим, а не гадаем «не указана» или «бесплатно».
    assert "доставка бесплатно" not in page and "не указана" not in page


@pytest.mark.parametrize("delivery, said", [(0.0, ", доставка бесплатно"), (59.0, ", доставка 59,00")])
def test_the_chosen_place_shows_the_network_delivery_and_discount(web, monkeypatch, delivery, said):
    """Доставку и скидку называем такими, какими их написала сеть: нулевая доставка — «бесплатно»."""
    from app import cartrun

    enter(web)
    workspace()
    _network_dearer(cartrun, monkeypatch, delivery=delivery, discount=20.0)

    page = text(web.get("/phone/cheaper?pick=0"))
    assert "(скидка сети −20,00" in page and said in page


def test_an_incomplete_cabinet_cart_does_not_set_the_price(web, monkeypatch):
    """Легло одно из двух — итог сети не про всю корзину: цена наша, и сказано, почему."""
    from app import cartrun

    enter(web)
    workspace()
    _network_dearer(cartrun, monkeypatch, landed=1)

    page = text(web.get("/phone/cheaper"))
    assert page.index("Всё в Пятёрочке") < page.index("Всё в Магните")   # совет прежний
    assert "130,00" not in page and "корзина кабинета" not in page
    assert "итог сети не взят: легло 1 из 2" in page


def test_a_stale_cabinet_total_does_not_set_the_price(web, monkeypatch):
    from datetime import datetime, timedelta

    from app import cartrun

    enter(web)
    workspace()
    _network_dearer(cartrun, monkeypatch, at=datetime.now() - timedelta(days=3))

    page = text(web.get("/phone/cheaper"))
    assert "130,00" not in page
    assert "итог сети не взят: устарел" in page


def test_ordering_without_pick_follows_the_place_the_screen_recommends(web, monkeypatch):
    """Итог сети сменил совет: «Заказать» без pick — в том месте, что рекомендует экран.

    Иначе человек нажимал бы «Заказать в Магните» под советом «Магнит», а сервер искал бы
    Магнит среди мест прежнего совета (Пятёрочка) и молча возвращал на тот же экран.
    """
    from app import cartrun

    enter(web)
    workspace()
    _network_dearer(cartrun, monkeypatch)

    answer = web.post("/phone/cheaper", data={"store": "magnit"})
    assert answer.status_code == 303
    assert "/phone/settings?connect=magnit" in answer.headers["Location"]


def test_a_catalog_snapshot_does_not_make_a_showcase_at_the_address(web, monkeypatch):
    """Справочная цена из CSV — не витрина у адреса: подписи «витрина у адреса» нет."""
    enter(web)
    workspace()
    milk = add_product("Молоко 930 мл")
    store = repo.get_store("magnit")
    sp_id = repo.upsert_store_product(store.id, "sku-catalog", "Молоко 930 мл")
    repo.confirm_mapping(milk, sp_id, confirmed=True)
    repo.save_price(sp_id, 1.0, source=freshness.REFERENCE)
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, milk, 1.0)

    _substituted(monkeypatch, _one_place("magnit", "Магнит", 110.0))

    page = text(web.get("/phone/cheaper"))
    assert "витрина у адреса" not in page
    assert "свежесть неизвестна" in page


def test_a_phone_only_store_is_checked_by_the_phone(web, monkeypatch):
    """Пятёрочка, Самокат, Дикси и Перекрёсток серверу витрину не отдают — так и пишем."""
    enter(web)
    workspace()
    _a_basket()
    _substituted(monkeypatch, _one_place("pyaterochka", "Пятёрочка", 120.0))

    page = text(web.get("/phone/cheaper"))
    assert "проверяется телефоном" in page
    assert "Быстрее" in page


def test_a_place_without_a_moment_does_not_invent_freshness(web, monkeypatch):
    """Цена есть, а когда снята — не знаем: «свежесть неизвестна», а не выдуманное время."""
    enter(web)
    workspace()
    _a_basket()
    _substituted(monkeypatch, _one_place("metro", "METRO", 120.0))

    page = text(web.get("/phone/cheaper"))
    assert "свежесть неизвестна" in page


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
    Размеры — вида по мотивам Магнита, принятого владельцем 22.10.2026: счётчик 32 px,
    строка товара 56 px (с фото).
    """
    with open(CSS, encoding="utf-8") as fh:
        css = fh.read()
    assert ".q { width: 32px; height: 32px;" in css
    assert "height: 48px;" in css                       # главная кнопка внизу
    assert ".it { display: flex; align-items: center; gap: 10px; min-height: 56px;" in css


def test_a_basket_row_shows_the_chain_photo(web):
    """Фото в строке корзины — из единой базы сетей (вид 22.10.2026).

    У Пятёрочки картинок нет: фото берётся у другой сети того же единого товара.
    Товар без связи с сетями остаётся без фото, и корзина от этого не падает.
    """
    from app.catalog import store
    from app.web.phone.basket import _photos

    enter(web)
    workspace()
    milk = add_product("Молоко 930 мл")
    bread = add_product("Хлеб Бородинский")
    sp_id = repo.upsert_store_product(repo.get_store("pyaterochka").id, "4442656", "Молоко 930 мл")
    repo.confirm_mapping(milk, sp_id, confirmed=True)
    store.init()
    seen = "2026-10-22T10:00:00"
    with store.connect() as conn:
        conn.execute("INSERT INTO catalog_items (id, name, created_at, updated_at) VALUES (7, 'Молоко', ?, ?)",
                     (seen, seen))
        conn.execute("INSERT INTO chain_products (chain, sku, name, first_seen, last_seen, item_id)"
                     " VALUES ('pyaterochka', '4442656', 'Молоко', ?, ?, 7)", (seen, seen))
        conn.execute("INSERT INTO chain_products (chain, sku, name, image, first_seen, last_seen, item_id)"
                     " VALUES ('magnit', '1000', 'Молоко', 'https://img.example/m.jpg', ?, ?, 7)", (seen, seen))

    assert _photos([milk, bread]) == {milk: "https://img.example/m.jpg"}
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, milk, 1.0)
    repo.set_basket_item(basket_id, bread, 2.0)
    page = text(web.get("/phone/basket"))
    assert 'src="https://img.example/m.jpg"' in page
    assert 'aria-label="Убрать: Молоко 930 мл"' in page       # одна штука — «−» с корзинкой
    assert 'aria-label="Меньше: Хлеб Бородинский"' in page
