"""«Из привычного» на телефонной корзине (пункт 5 владельца, план Z2).

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Веб давно предлагает привычное над списком: «часто берёте» и
«пора докупить» (app/web/screens/basket.py, _regulars и _due). На телефоне этого не
было, и корзину набирали с нуля. Здесь стережётся телефонная половина:

- история покупок даёт блок «Из привычного» с названием товара и причиной;
- то, что уже лежит в корзине, второй раз не предлагается;
- «+» кладёт шаг — штуку для штучного и 100 г для весового — и возвращает к строке;
- без истории блока нет, а корзина работает как раньше;
- блок виден и на пустой корзине, а «+» заводит её сам: именно тогда корзину
  набирают с нуля;
- сохранение адреса с телефона ставит очередь цен (location.save_address →
  catalog.store.enqueue_address), а не только пишет строку в базу.

ЧЕГО ЭТИ ТЕСТЫ НЕ ЗНАЮТ. Как блок выглядит на телефоне и попадает ли палец в «+»:
это смотрят глазами по макету, а не test_client'ом.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import repo, users  # noqa: E402
from app.models import Product  # noqa: E402

PHONE = "79990000072"


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


def add_product(name: str, unit: str = "pcs") -> int:
    return repo.upsert_product(Product(id=None, name=name, unit=unit))


def seed_history(product_id: int, days_ago: tuple[int, ...],
                 store_code: str = "pyaterochka", qty: float = 1.0) -> None:
    """Покупки товара в эти дни — по ним app/baskets.py считает «часто» и «пора»."""
    store = repo.get_store(store_code)
    for days in days_ago:
        day = (date.today() - timedelta(days=days)).isoformat()
        repo.add_history_row(day, store.id, product_id, f"ТОВАР {product_id}", qty,
                             100.0, 100.0 * qty)


def habits_block(page: str) -> str:
    """Кусок HTML от начала блока привычного до его конца — без списка товаров."""
    start = page.index('id="habits"')
    return page[start:page.index("</section>", start)]


def latest_items() -> dict[int, float]:
    basket = repo.list_baskets()[0]
    return {int(i["product_id"]): float(i["qty"]) for i in repo.basket_items(int(basket["id"]))}


# ---------- блок и его содержимое ----------
def test_history_brings_the_usual_into_the_phone_basket(web):
    enter(web)
    workspace()
    milk = add_product("Молоко 930 мл")
    cheese = add_product("Сыр Российский")
    bread = add_product("Хлеб Бородинский")
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, bread, 1.0)
    seed_history(cheese, (30, 23))   # цикл 7 дней, последний раз 23 дня назад — пора
    seed_history(milk, (8, 1))        # брали недавно и не раз — часто

    page = text(web.get("/phone/basket"))

    assert "Из привычного" in page
    block = habits_block(page)
    assert "Молоко 930 мл" in block and "часто берёте" in block
    assert "Сыр Российский" in block and "пора докупить" in block


def test_a_good_already_in_the_basket_is_not_offered_again(web):
    enter(web)
    workspace()
    milk = add_product("Молоко 930 мл")
    kefir = add_product("Кефир 1%")
    bread = add_product("Хлеб Бородинский")
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, milk, 2.0)
    repo.set_basket_item(basket_id, bread, 1.0)
    seed_history(milk, (8, 1))
    seed_history(kefir, (8, 1))

    page = text(web.get("/phone/basket"))
    block = habits_block(page)

    assert "Кефир 1%" in block, "чего в корзине нет — то и предлагаем"
    assert "Молоко 930 мл" not in block, "оно уже лежит в списке выше"
    assert "Молоко 930 мл" in page


def test_a_product_in_both_lists_is_shown_once(web):
    """Товар, который и частый, и просроченный, — одна строка с причиной «пора»."""
    enter(web)
    workspace()
    cheese = add_product("Сыр Российский")
    repo.create_basket("Неделя")
    seed_history(cheese, (30, 23))

    page = text(web.get("/phone/basket"))
    block = habits_block(page)

    assert block.count('value="add:') == 1, "дедупликация по pid, а не по списку"
    assert block.count('<span class="name">Сыр Российский') == 1, "одна строка товара"
    assert "пора докупить" in block
    assert "часто берёте" not in block, "просроченное важнее частого — причина одна"


# ---------- «+» из привычного ----------
def test_add_puts_one_piece_and_returns_to_the_row(web):
    enter(web)
    workspace()
    milk = add_product("Молоко 930 мл")
    bread = add_product("Хлеб Бородинский")
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, milk, 2.0)

    answer = web.post("/phone/basket", data={"do": f"add:{bread}"})
    assert answer.status_code == 303
    assert answer.headers["Location"].endswith(f"/phone/basket#it-{bread}")
    workspace()
    assert latest_items()[bread] == 1.0

    web.post("/phone/basket", data={"do": f"add:{bread}"})
    workspace()
    assert latest_items()[bread] == 2.0, "повторное нажатие прибавляет шаг, а не кладёт один"

    web.post("/phone/basket", data={"do": f"add:{milk}"})
    workspace()
    assert latest_items()[milk] == 3.0, "у лежащего в корзине — текущее количество плюс шаг"


def test_add_weighs_a_hundred_grams(web):
    """Весовой товар шагает на 100 г: килограмм за нажатие стоил бы впятеро дороже."""
    enter(web)
    workspace()
    cheese = add_product("Сыр Российский", unit="kg")
    bread = add_product("Хлеб Бородинский")
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, bread, 1.0)

    web.post("/phone/basket", data={"do": f"add:{cheese}"})
    workspace()
    assert latest_items()[cheese] == pytest.approx(0.1)

    web.post("/phone/basket", data={"do": f"add:{cheese}"})
    workspace()
    assert latest_items()[cheese] == pytest.approx(0.2)


def test_add_of_an_unknown_product_does_not_create_a_basket(web):
    """Несуществующий pid: 303 и никакой пустой корзины «на всякий случай»."""
    enter(web)
    workspace()

    answer = web.post("/phone/basket", data={"do": "add:999999"})

    assert answer.status_code == 303
    workspace()
    assert repo.list_baskets() == []


def test_habits_stop_at_the_shown_limit(web):
    """HABITS_SHOWN — потолок подсказки: это не каталог истории."""
    from app.web.phone.basket import HABITS_SHOWN

    enter(web)
    workspace()
    for number in range(HABITS_SHOWN + 3):
        product = add_product(f"Товар {number}")
        seed_history(product, (8, 1))

    page = text(web.get("/phone/basket"))
    block = habits_block(page)

    assert block.count('value="add:') == HABITS_SHOWN


# ---------- без истории и без корзины ----------
def test_without_history_there_is_no_block_and_the_basket_still_works(web):
    enter(web)
    workspace()
    bread = add_product("Хлеб Бородинский")
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, bread, 1.0)

    page = text(web.get("/phone/basket"))
    assert "Из привычного" not in page
    assert "Хлеб Бородинский" in page and "1 шт" in page

    web.post("/phone/basket", data={"do": f"plus:{bread}"})
    workspace()
    assert latest_items()[bread] == 2.0, "«+» у товара на месте"


def test_an_empty_basket_still_offers_the_usual(web):
    """История есть, корзины нет: «Из привычного» видно, а «+» заводит корзину."""
    enter(web)
    workspace()
    milk = add_product("Молоко 930 мл")
    seed_history(milk, (8, 1))

    page = text(web.get("/phone/basket"))
    assert "Корзина пуста" in page
    assert "История заказов" in page
    block = habits_block(page)
    assert "Молоко 930 мл" in block and "часто берёте" in block

    answer = web.post("/phone/basket", data={"do": f"add:{milk}"})

    assert answer.status_code == 303
    assert answer.headers["Location"].endswith(f"/phone/basket#it-{milk}")
    workspace()
    baskets = repo.list_baskets()
    assert len(baskets) == 1, "«+» из привычного заводит корзину"
    assert latest_items()[milk] == 1.0


def test_an_existing_but_empty_basket_still_offers_the_usual(web):
    """Пустая корзина — тот же случай «набираю с нуля»: блок нужен и здесь."""
    enter(web)
    workspace()
    milk = add_product("Молоко 930 мл")
    seed_history(milk, (8, 1))
    repo.create_basket("Пустая")

    page = text(web.get("/phone/basket"))

    assert "Корзина пуста" in page
    assert "Молоко 930 мл" in habits_block(page)


def test_an_empty_basket_without_history_has_no_block(web):
    enter(web)
    workspace()
    repo.create_basket("Пустая")

    page = text(web.get("/phone/basket"))

    assert "Корзина пуста" in page
    assert "Из привычного" not in page


# ---------- адрес ставит очередь цен ----------
def test_saving_an_address_enqueues_the_price_run(web, monkeypatch):
    """Сохранение адреса с телефона — это не только строка в базе.

    location.save_address обязан поставить адрес в очередь загрузки цен
    (app/catalog/store.py, enqueue_address); иначе после смены адреса цены
    остались бы от прежнего места. Все соседние тесты подменяют
    refresh_after_move пустышкой — здесь проверяется сама цепочка.
    """
    from app import location
    from app.web.screens import basket as basket_screen

    monkeypatch.setattr(basket_screen, "refresh_after_move", lambda: None)
    queued = []
    monkeypatch.setattr("app.catalog.store.enqueue_address",
                        lambda address: queued.append(address) or True)
    enter(web)

    answer = web.post("/phone/settings",
                      data={"address": "Москва, Ленинский проспект, 42"})

    assert answer.status_code == 303
    assert queued == ["Москва, Ленинский проспект, 42"]
    workspace()
    assert location.address() == "Москва, Ленинский проспект, 42"
