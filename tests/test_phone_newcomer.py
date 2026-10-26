"""Новый человек на телефоне: корзина, которую можно набрать с нуля (автопилот 25.10.2026).

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Знакомый открыл сайт в Safari или Chrome на телефоне. Истории
покупок у него нет, адреса нет, и раньше корзина была тупиком: «повторите заказ из
истории», которого нет. Теперь путь такой, и каждый его шаг стережётся отдельно:

- телефон в обычном браузере попадает с главной на телефонные экраны, а полная версия
  (/?web=1) и браузер компьютера живут как жили; «наше приложение» (on_phone) от этого
  не размывается — от него зависит мост к телефону;
- над корзиной стоит просьба указать адрес, пока он не сохранён;
- поиск находит свои товары и товары единого каталога сетей, «В корзину» заводит корзину
  и кладёт шаг, слово поиска остаётся в адресе — можно взять несколько товаров подряд;
- пустые экраны говорят, что делать, а не ведут к тому, чего у новичка нет;
- в демо (общая витрина) стоит строка «менять нельзя», нет поиска и «−/+», а приложение
  не получает корзину на сборку в сетях.

ЧЕГО ЭТИ ТЕСТЫ НЕ ЗНАЮТ. Как поле и найденное выглядят на ширине 360 и 390 и не
увеличивает ли Safari страницу при фокусе: это смотрят глазами на живом телефоне.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import repo, users  # noqa: E402
from app.models import Product  # noqa: E402

PHONE = "79990000094"
APP_UA = "Mozilla/5.0 (Linux; Android 14; wv) KorzinaApp/1.16"
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
ANDROID_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")
TABLET_UA = ("Mozilla/5.0 (Linux; Android 13; SM-X710) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


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
    from conftest import sign_in

    return sign_in(client, PHONE)


def workspace() -> None:
    users.open_workspace(PHONE)


def text(answer) -> str:
    return answer.data.decode("utf-8")


def add_product(name: str, unit: str = "pcs") -> int:
    return repo.upsert_product(Product(id=None, name=name, unit=unit))


def found_block(page: str) -> str:
    """Кусок HTML с найденным: от карточки до конца её формы — без списка корзины."""
    start = page.index('class="card found"')
    return page[start:page.index("</form>", start)]


def seed_catalog() -> int:
    """Единый товар «Молоко Простоквашино» в двух сетях — так его видит поиск каталога."""
    from app.catalog import refresh, store
    from app.catalog.model import ChainProduct

    store.init()
    store.upsert_products("lenta", [ChainProduct("80424", "Молоко Простоквашино 2,5% 930 мл",
                                                 price=89.99)])
    store.upsert_products("vkusvill", [ChainProduct("36296", "Молоко Простоквашино 2,5% 930 мл",
                                                    price=100.0)])
    refresh.match_all()
    return int(store.search_items("простоквашино")[0]["id"])


def baskets_with_items() -> list[tuple[str, dict[int, float]]]:
    return [(b["name"], {int(i["product_id"]): float(i["qty"])
                         for i in repo.basket_items(int(b["id"]))})
            for b in repo.list_baskets()]


# ---------- телефон в обычном браузере ----------
@pytest.mark.parametrize("ua", [IPHONE_UA, ANDROID_UA, TABLET_UA])
def test_a_phone_browser_lands_on_the_phone_screens(web, ua):
    """Знакомый открыл сайт в Safari/Chrome — ему телефонные экраны, а не веб компьютера."""
    enter(web)
    answer = web.get("/", headers={"User-Agent": ua})
    assert answer.status_code == 302
    assert answer.headers["Location"].endswith("/phone/basket")


def test_a_desktop_browser_keeps_the_web_home(web):
    enter(web)
    assert web.get("/", headers={"User-Agent": DESKTOP_UA}).status_code == 200


def test_the_full_version_stays_reachable_from_a_phone_browser(web):
    """«Полная версия» — адрес /?web=1: он жив и для браузера на телефоне."""
    enter(web)
    assert web.get("/?web=1", headers={"User-Agent": IPHONE_UA}).status_code == 200


def test_a_phone_browser_without_login_goes_to_the_login_page(web):
    answer = web.get("/", headers={"User-Agent": IPHONE_UA})
    assert answer.status_code == 302 and "/login" in answer.headers["Location"]


def test_a_phone_browser_is_not_our_app(web):
    """Мост к телефону зовётся только внутри приложения: Safari им не становится."""
    from app.web import device

    app = web.application
    with app.test_request_context("/", headers={"User-Agent": IPHONE_UA}):
        assert device.on_mobile() and not device.on_phone()
    with app.test_request_context("/", headers={"User-Agent": APP_UA}):
        assert device.on_mobile() and device.on_phone()
    with app.test_request_context("/", headers={"User-Agent": DESKTOP_UA}):
        assert not device.on_mobile() and not device.on_phone()


# ---------- адрес первым делом ----------
def test_a_newcomer_is_asked_for_the_address_until_it_is_saved(web):
    enter(web)
    page = text(web.get("/phone/basket"))
    assert "Укажите адрес доставки — от него зависят цены и наличие" in page
    assert 'href="/phone/settings#address"' in page

    workspace()
    repo.set_setting("address", "Санкт-Петербург, Невский пр., 28")
    page = text(web.get("/phone/basket"))
    assert "Укажите адрес доставки" not in page
    assert 'role="search"' in page, "поиск остаётся и с адресом"


# ---------- поиск ----------
def test_a_newcomer_sees_the_search_on_an_empty_basket(web):
    enter(web)
    page = text(web.get("/phone/basket"))
    assert 'role="search"' in page and 'name="q"' in page and 'placeholder="Найти товар"' in page
    assert 'method="get"' in page, "поиск без скрипта: обычная форма"


def test_the_search_finds_own_goods_that_are_not_in_the_basket_yet(web):
    enter(web)
    workspace()
    cucumbers = add_product("Огурцы гладкие")
    milk = add_product("Молоко 930 мл")
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, milk, 1.0)

    page = text(web.get("/phone/basket?q=огурц"))
    block = found_block(page)
    assert "Огурцы гладкие" in block and f'value="add:{cucumbers}"' in block
    assert "В корзину" in block
    assert 'name="q" value="огурц"' in block, "слово едет в форме, чтобы вернуться к найденному"

    # то, что уже лежит в корзине, найденным не предлагается: нажатие переставило бы количество
    page = text(web.get("/phone/basket?q=молоко"))
    assert 'class="card found"' not in page
    assert "ничего не нашлось" in page


def test_a_missing_word_and_a_single_letter_are_answered_in_words(web):
    enter(web)
    assert "Введите хотя бы две буквы" in text(web.get("/phone/basket?q=м"))
    assert "По запросу «ъъъъ» ничего не нашлось" in text(web.get("/phone/basket?q=ъъъъ"))


def test_add_from_the_search_puts_the_good_in_the_basket_and_keeps_the_word(web):
    enter(web)
    workspace()
    cucumbers = add_product("Огурцы гладкие")

    answer = web.post("/phone/basket", data={"do": f"add:{cucumbers}", "q": "огурц"})

    assert answer.status_code == 303
    assert answer.headers["Location"].endswith(f"/phone/basket?q=%D0%BE%D0%B3%D1%83%D1%80%D1%86#it-{cucumbers}")
    workspace()
    assert baskets_with_items() == [("Корзина", {cucumbers: 1.0})], "корзины не было — завелась"
    page = text(web.get(answer.headers["Location"]))
    assert 'id="it-%d"' % cucumbers in page, "товар теперь в списке корзины"
    assert 'class="card found"' not in page, "а из найденного ушёл"


def test_the_catalog_item_is_found_and_added_with_a_new_basket(web):
    """Товар единого каталога сетей — для новичка без истории и без своих товаров."""
    enter(web)
    workspace()
    item_id = seed_catalog()
    users.deactivate()

    page = text(web.get("/phone/basket?q=простоквашино"))
    block = found_block(page)
    assert "Молоко Простоквашино 2,5% 930 мл" in block
    assert f'value="catalog:{item_id}"' in block
    assert "в 2 сетях" in block

    answer = web.post("/phone/basket", data={"do": f"catalog:{item_id}", "q": "простоквашино"})
    assert answer.status_code == 303 and "q=" in answer.headers["Location"]
    assert "#it-" in answer.headers["Location"]

    workspace()
    ((name, items),) = baskets_with_items()
    assert name == "Корзина" and list(items.values()) == [1.0]
    product_id = next(iter(items))
    assert repo.get_product(product_id).name == "Молоко Простоквашино 2,5% 930 мл"
    stores = {s.code: s.id for s in repo.list_stores()}
    assert repo.confirmed_mapping(product_id, stores["lenta"])["sku"] == "80424", "adopt сопоставил сети"
    users.deactivate()

    web.post("/phone/basket", data={"do": f"catalog:{item_id}", "q": "простоквашино"})
    workspace()
    assert list(baskets_with_items()[0][1].values()) == [2.0], "второй раз — ещё шаг, а не сброс в единицу"


def test_a_vanished_catalog_item_does_not_break_the_basket(web):
    enter(web)
    answer = web.post("/phone/basket", data={"do": "catalog:999999", "q": "молоко"})
    assert answer.status_code == 303
    assert answer.headers["Location"].endswith("/phone/basket?q=%D0%BC%D0%BE%D0%BB%D0%BE%D0%BA%D0%BE")
    workspace()
    assert repo.list_baskets() == [], "товара нет — корзину не заводим"


def test_the_phone_search_is_the_web_suggestion(web):
    """Одна функция на два экрана: телефон находит ровно то, что подсказывает веб."""
    from app.web.screens.basket import find

    enter(web)
    workspace()
    add_product("Огурцы гладкие")
    add_product("Огурцы солёные")
    basket_id = repo.create_basket("Неделя")
    users.deactivate()

    suggested = web.get(f"/basket/suggest?q=огурц&id={basket_id}").get_json()["items"]
    workspace()
    assert suggested == find("огурц", basket_id) and len(suggested) == 2


# ---------- пустые экраны ----------
def test_empty_screens_tell_a_newcomer_what_to_do(web):
    enter(web)

    page = text(web.get("/phone/basket"))
    assert "Корзина пуста. Найдите товар поиском выше — или повторите заказ из «Истории заказов»." in page

    page = text(web.get("/phone/baskets"))
    assert "Найдите товар поиском на экране «Корзина»" in page
    assert "из привычного" not in page, "у новичка привычного нет — туда не отправляем"
    assert 'href="/phone/basket"' in page

    page = text(web.get("/phone/orders"))
    assert "Заказов пока нет" in page
    assert "найдите товар поиском на экране «Корзина»" in page


# ---------- демо ----------
def demo_basket() -> int:
    """Корзина в общей витрине: без неё нечего показывать и «−/+», и сборку в сетях.

    Открытой делаем явно: витрина при заведении сама кладёт в базу свою корзину.
    """
    from app.web.screens.basket import LAST_OPEN

    users.open_workspace(users.DEMO)
    milk = add_product("Молоко 930 мл")
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, milk, 2.0)
    repo.set_setting(LAST_OPEN, str(basket_id))
    users.deactivate()
    return basket_id


def test_the_demo_is_for_looking_only(web):
    web.post("/demo")
    demo_basket()

    page = text(web.get("/phone/basket"))

    assert "Это демо — смотреть можно, менять нельзя." in page
    assert "Войти по своему номеру" in page and 'action="/logout"' in page
    assert "Молоко 930 мл" in page, "смотреть можно"
    assert 'role="search"' not in page and "Найти товар" not in page
    assert "Укажите адрес" not in page
    assert 'value="plus:' not in page and 'value="minus:' not in page
    assert 'class="qn num"' in page, "количество на месте, кнопок нет"
    # В тексте скрипта слово data-collect есть всегда; смотрим на сам атрибут и на блок.
    assert 'data-collect="' not in page and 'id="kz-runs"' not in page,         "приложение не получает корзину демо на сборку в сетях"
    assert "Найдите товар поиском выше" not in page


def test_a_real_workspace_has_no_demo_line_and_still_collects(web):
    """Контроль к тесту выше: у настоящего рабочего места всё это есть."""
    enter(web)
    workspace()
    milk = add_product("Молоко 930 мл")
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, milk, 2.0)
    users.deactivate()

    page = text(web.get("/phone/basket"))

    assert "Это демо" not in page
    assert 'value="plus:' in page and 'role="search"' in page
    assert 'data-collect="' in page


def test_a_refused_change_makes_the_demo_line_stand_out(web):
    web.post("/demo")

    calm = text(web.get("/phone/orders"))
    loud = text(web.get("/phone/orders?demo=1"))

    assert "Это демо — смотреть можно, менять нельзя." in calm and "Это демо — смотреть можно" in loud
    assert 'class="note act warn"' in calm
    assert 'class="note act bad hot"' in loud


def test_the_demo_line_is_on_every_phone_screen_and_leads_to_a_real_login(web):
    web.post("/demo")

    for path in ("/phone/orders", "/phone/baskets"):
        assert "Это демо — смотреть можно" in text(web.get(path)), path

    answer = web.post("/logout")
    assert answer.status_code == 302 and answer.headers["Location"].endswith("/login")


def test_a_phone_browser_is_told_that_stores_connect_in_the_android_app(web):
    """В браузере «Подключить» ведёт в окно магазина на сервере — сказано, где вход проще."""
    enter(web)

    iphone = text(web.get("/phone/settings", headers={"User-Agent": IPHONE_UA}))
    assert "удобнее в приложении для Android" not in iphone
    assert "/static/moya-korzina.apk" not in iphone
    assert "В браузере вход идёт через окно магазина на нашем сервере" in iphone
    assert 'href="/cabinet?store=magnit"' in iphone, "дорога через сервер осталась"

    android = text(web.get("/phone/settings", headers={"User-Agent": ANDROID_UA}))
    assert "удобнее в приложении для Android" in android
    assert 'href="/static/moya-korzina.apk"' in android
    assert "В браузере вход идёт через окно магазина на нашем сервере" in android

    app = text(web.get("/phone/settings", headers={"User-Agent": APP_UA}))
    assert "удобнее в приложении для Android" not in app
    assert "/static/moya-korzina.apk" not in app
    assert "В браузере вход идёт через окно магазина на нашем сервере" not in app
