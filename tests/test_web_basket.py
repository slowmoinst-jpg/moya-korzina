"""Раздел «Корзина» на новом интерфейсе: корзина, результат и история.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ В ПЕРВУЮ ОЧЕРЕДЬ — то, ради чего экраны и переезжали.

  АДРЕС. У корзины, у способа сборки и у отбора истории должен быть адрес: на них
  дают ссылку, к ним возвращаются кнопкой «назад» и их открывают двумя вкладками
  рядом. Проверяется не тем, что страница отвечает 200, а тем, что ДВА РАЗНЫХ
  АДРЕСА показывают разное, не мешая друг другу.

  ОТКРЫТИЕ СТРАНИЦЫ НИЧЕГО НЕ МЕНЯЕТ. Прежний экран наполнял пустую корзину по
  истории прямо при показе, а от повторного наполнения спасала отметка в
  session_state. Здесь такой отметки нет, и запись при показе означала бы:
  очистил в одной вкладке — вернулось из другой. Сторож ниже ловит именно это.

  ЧУЖОЙ РАСЧЁТ. Посчитанное лежит в памяти сервера, и ключ у него — база человека.
  Ошибиться тут дорого: расчёт Петра, показанный Ивану, выглядит как свой
  собственный, просто цифры другие, чем помнится.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import repo, users  # noqa: E402
from app.models import Product  # noqa: E402

PHONE = "79990000011"
OTHER = "79990000012"


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временных базах — как в tests/test_web.py.

    db_path здесь НЕ подменяется нарочно: подмена вернула бы одну базу всем и
    отключила бы ровно то, что проверяется в test_calculation_does_not_leak.
    """
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    from app.web.screens import result

    result._CALC.clear()          # память расчёта общая на процесс — между тестами чистим
    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    result._CALC.clear()
    users.deactivate()


def enter(client, phone: str = PHONE):
    return client.post("/login", data={"phone": phone, "next": "/"})


def workspace(phone: str = PHONE) -> None:
    """Открыть базу человека в самом тесте: между запросами её снимает teardown."""
    users.open_workspace(phone)


def add_product(name: str, unit: str = "pcs") -> int:
    return repo.upsert_product(Product(id=None, name=name, unit=unit))


def price(product_id: int, store_code: str, value: float) -> None:
    """Цена товара в сети: артикул, подтверждённая связь и снимок цены."""
    store = repo.get_store(store_code)
    sp_id = repo.upsert_store_product(store.id, f"sku-{store_code}-{product_id}", f"товар {product_id}")
    repo.confirm_mapping(product_id, sp_id, confirmed=True)
    repo.save_price(sp_id, value)


def seed_basket(name: str = "Неделя 38") -> tuple[int, int, int]:
    """Корзина из двух товаров с ценами в двух сетях. Сети без сети — Магнит и Пятёрочка.

    Ленту и ВкусВилл здесь не берём намеренно: они умеют отдавать корзину ссылкой,
    а ссылку создаёт СЕТЕВОЙ вызов. Попади они в лучший вариант — показ страницы
    ушёл бы в сеть, и тест поймал бы это падением (tests/conftest.py), но ловил бы
    он тогда не то, что проверяет.
    """
    milk = add_product("Молоко Простоквашино 930 мл")
    bread = add_product("Хлеб Бородинский")
    price(milk, "magnit", 89.0)
    price(milk, "pyaterochka", 95.0)
    price(bread, "magnit", 55.0)
    price(bread, "pyaterochka", 49.0)
    basket_id = repo.create_basket(name)
    repo.set_basket_item(basket_id, milk, 2.0)
    repo.set_basket_item(basket_id, bread, 1.0)
    return basket_id, milk, bread


def seed_history(day: str = "2026-08-16") -> int:
    milk = add_product("Молоко Простоквашино 930 мл")
    store = repo.get_store("magnit")
    repo.add_history_row(day, store.id, milk, "МОЛОКО ПРОСТОКВ 930", 2.0, 89.0, 178.0)
    return milk


def text(answer) -> str:
    return answer.data.decode("utf-8")


# ---------- корзина: адрес ----------
def test_basket_lives_at_its_own_address(web):
    """Две корзины — два адреса, и каждый показывает своё.

    Это и есть всё приобретение переезда: в Streamlit открытая корзина лежала в
    session_state, одна на всю сессию, и две вкладки рядом дрались за неё.
    """
    enter(web)
    workspace()
    first, _, _ = seed_basket("Неделя 38")
    second = repo.create_basket("Праздничная")

    assert "Неделя 38" in text(web.get(f"/basket?id={first}"))
    assert "Праздничная" in text(web.get(f"/basket?id={second}"))


def test_a_link_to_a_deleted_basket_explains_itself(web):
    """Ссылку на корзину можно переслать, а корзину за это время удалить."""
    enter(web)
    workspace()
    seed_basket()
    page = text(web.get("/basket?id=9999"))
    assert "9999" in page and "нет" in page


def test_search_in_the_basket_is_addressable(web):
    enter(web)
    workspace()
    seed_basket()
    add_product("Огурцы гладкие")
    assert "Огурцы" in text(web.get("/basket?q=огурц"))
    assert "Огурцы" not in text(web.get("/basket?q=хлеб"))


# ---------- корзина: открытие ничего не меняет ----------
def test_opening_the_basket_does_not_fill_it(web):
    """Пустая корзина остаётся пустой, сколько её ни открывай.

    Прежний экран наполнял её из истории прямо при показе. Здесь так нельзя:
    обновил вкладку — корзина снова полна, очистил в одной — вернулось из другой.
    Вместо этого пустое состояние ПРЕДЛАГАЕТ собрать, одним нажатием.
    """
    enter(web)
    workspace()
    seed_history()
    basket_id = repo.create_basket("Пустая")

    page = text(web.get(f"/basket?id={basket_id}"))
    workspace()
    assert repo.basket_items(basket_id) == []
    assert "Собрать заново" in page          # предложение есть

    web.get(f"/basket?id={basket_id}")
    workspace()
    assert repo.basket_items(basket_id) == []


def test_an_empty_basket_says_what_to_do_when_there_is_no_history(web):
    """Пустое состояние — тоже экран: два разных случая и два разных ответа."""
    enter(web)
    workspace()
    basket_id = repo.create_basket("Пустая")
    page = text(web.get(f"/basket?id={basket_id}"))
    assert "Наполнить нечем" in page
    assert "чек" in page.lower()


def test_filling_from_history_is_an_action(web):
    enter(web)
    workspace()
    milk = seed_history()
    basket_id = repo.create_basket("Пустая")

    answer = web.post("/basket", data={"basket": basket_id, "do": "fill:history"})
    assert answer.status_code == 302
    assert answer.headers["Location"].startswith("/basket")

    workspace()
    assert [int(i["product_id"]) for i in repo.basket_items(basket_id)] == [milk]


def test_a_new_basket_can_be_born_already_full(web):
    """Наполнение переехало туда, где оно и есть действие человека."""
    enter(web)
    workspace()
    seed_history()

    answer = web.post("/basket", data={"do": "new:history", "name": "Как всегда"})
    assert answer.status_code == 302
    workspace()
    fresh = repo.list_baskets()[0]
    assert fresh["name"] == "Как всегда"
    assert repo.basket_items(int(fresh["id"]))


# ---------- корзина: правка позиций ----------
def test_quantities_are_saved_by_the_form(web):
    enter(web)
    workspace()
    basket_id, milk, bread = seed_basket()

    web.post("/basket", data={"basket": basket_id, "do": "save",
                              f"qty:{milk}": "5", f"qty:{bread}": "1"})
    workspace()
    qty = {int(i["product_id"]): float(i["qty"]) for i in repo.basket_items(basket_id)}
    assert qty[milk] == 5.0


def test_removing_a_row_keeps_what_was_typed_in_the_others(web):
    """Человек правит корзину по строке; нажатие «убрать» не должно съедать ввод.

    Это не придирка: у полки набирают количества подряд, а лишнюю позицию убирают
    посреди этого. Потеря набранного здесь означала бы вводить всё заново.
    """
    enter(web)
    workspace()
    basket_id, milk, bread = seed_basket()

    web.post("/basket", data={"basket": basket_id, "do": f"drop:{bread}",
                              f"qty:{milk}": "7", f"qty:{bread}": "3"})
    workspace()
    rows = {int(i["product_id"]): float(i["qty"]) for i in repo.basket_items(basket_id)}
    assert bread not in rows
    assert rows[milk] == 7.0


def test_plus_and_minus_move_one_step(web):
    enter(web)
    workspace()
    basket_id, milk, _ = seed_basket()

    web.post("/basket", data={"basket": basket_id, "do": f"plus:{milk}", f"qty:{milk}": "2"})
    workspace()
    rows = {int(i["product_id"]): float(i["qty"]) for i in repo.basket_items(basket_id)}
    assert rows[milk] == 3.0

    web.post("/basket", data={"basket": basket_id, "do": f"minus:{milk}", f"qty:{milk}": "3"})
    workspace()
    rows = {int(i["product_id"]): float(i["qty"]) for i in repo.basket_items(basket_id)}
    assert rows[milk] == 2.0


def test_adding_and_clearing(web):
    enter(web)
    workspace()
    basket_id, milk, _ = seed_basket()
    cucumber = add_product("Огурцы гладкие")

    web.post("/basket", data={"basket": basket_id, "do": f"add:{cucumber}"})
    workspace()
    assert cucumber in {int(i["product_id"]) for i in repo.basket_items(basket_id)}

    web.post("/basket", data={"basket": basket_id, "do": "clear"})
    workspace()
    assert repo.basket_items(basket_id) == []


def test_every_action_ends_with_a_redirect(web):
    """После действия — перевод на адрес экрана, иначе обновление страницы повторит его."""
    enter(web)
    workspace()
    basket_id, milk, _ = seed_basket()
    for do in ("save", f"plus:{milk}", f"drop:{milk}", "clear"):
        answer = web.post("/basket", data={"basket": basket_id, "do": do})
        assert answer.status_code == 302, do
        assert answer.headers["Location"].startswith("/basket"), do


def test_the_cheapest_chain_is_named_in_the_row(web):
    """У позиции видно, где она дешевле и насколько — иначе цены не ответ, а таблица."""
    enter(web)
    workspace()
    basket_id, _, _ = seed_basket()
    page = text(web.get(f"/basket?id={basket_id}"))
    assert "дешевле на" in page
    assert "s-magnit" in page                # цвет сети ведёт: точка у цены


# ---------- корзина: долгая работа ----------
def test_asking_the_stores_happens_in_the_background(web, monkeypatch):
    """Опрос всех сетей — это минуты, и держать столько открытым запрос нельзя.

    Проверяется не скорость, а устройство: нажатие отвечает переводом сразу, а
    работа доживает своим потоком и сама открывает базу человека — новый поток в
    Python начинает с пустого контекста, и без этого цены легли бы в чужую базу.
    """
    import app.compare as compare
    from app.web.screens import basket as screen

    asked: list[str] = []

    def fake_query(query, **kwargs):
        asked.append(query)
        return []

    monkeypatch.setattr(compare, "compare_query", fake_query)

    enter(web)
    workspace()
    basket_id, _, _ = seed_basket()

    answer = web.post("/basket", data={"basket": basket_id, "do": "ask"})
    assert answer.status_code == 302

    workspace()
    for _ in range(100):                      # ждём фоновый поток, но не бесконечно
        job = screen.job_of(basket_id)
        if job and job["status"] != "running":
            break
        time.sleep(0.05)
    job = screen.job_of(basket_id)
    assert job and job["status"] == "ok", job
    assert len(asked) == 2                    # спросили обе позиции


def test_calculate_without_refresh_goes_straight_to_the_result(web):
    enter(web)
    workspace()
    basket_id, _, _ = seed_basket()
    answer = web.post("/basket", data={"basket": basket_id, "do": "calc"})
    assert answer.status_code == 302
    assert answer.headers["Location"] == f"/result?basket={basket_id}"


# ---------- результат ----------
def test_result_is_reachable_by_a_link_without_pressing_calculate(web):
    """В Streamlit сюда можно было попасть только нажатием: расчёт жил в session_state."""
    enter(web)
    workspace()
    basket_id, _, _ = seed_basket()

    page = text(web.get(f"/result?basket={basket_id}"))
    assert "Лучший вариант" in page
    assert "Экономия" in page


def test_result_without_a_basket_explains_itself(web):
    enter(web)
    page = text(web.get("/result"))
    assert "Сначала корзина" in page


def test_result_remembers_and_forgets_when_the_basket_changes(web, monkeypatch):
    """Тяжёлое не считается на каждое открытие — но и не показывается устаревшим.

    Прежний экран решал это тем, что считал по нажатию и клал ответ в session_state.
    Смысл сохранён: ответ живёт в памяти сервера, а ключом ему служит отпечаток
    корзины, поэтому «сбросить кэш» руками не нужно ниоткуда.
    """
    from app import service
    from app.web.screens import result as screen

    calls: list[int] = []
    real = service.calculate

    def counted(basket_id, refresh=True):
        calls.append(basket_id)
        return real(basket_id, refresh)

    monkeypatch.setattr(service, "calculate", counted)

    enter(web)
    workspace()
    basket_id, milk, _ = seed_basket()

    web.get(f"/result?basket={basket_id}")
    web.get(f"/result?basket={basket_id}")
    assert len(calls) == 1, "расчёт пошёл на каждое открытие страницы"

    workspace()
    repo.set_basket_item(basket_id, milk, 9.0)
    web.get(f"/result?basket={basket_id}")
    assert len(calls) == 2, "корзина изменилась, а показан старый расчёт"

    assert screen._CALC


def test_calculation_does_not_leak_between_people(web):
    """Расчёт Петра, показанный Ивану, выглядит как свой — просто цифры другие.

    Поэтому ключ памяти — база человека. Тест смотрит на сам ключ, а не на
    страницу: две пустые страницы выглядят одинаково, и подмену по ним не увидеть.
    """
    from app.web.screens import result as screen

    enter(web, PHONE)
    workspace(PHONE)
    mine, _, _ = seed_basket("Моя")
    web.get(f"/result?basket={mine}")
    web.post("/logout")

    enter(web, OTHER)
    workspace(OTHER)
    theirs, _, _ = seed_basket("Чужая")
    web.get(f"/result?basket={theirs}")

    bases = {key[0] for key in screen._CALC}
    assert len(bases) == 2, "расчёты двух людей легли в память под одним ключом"
    assert users.db_path_for(PHONE) in bases and users.db_path_for(OTHER) in bases


def test_the_way_to_assemble_is_in_the_address(web):
    """«Разделить» и «Один магазин» — ссылки: их сравнивают, открыв рядом."""
    enter(web)
    workspace()
    basket_id, _, _ = seed_basket()

    split = text(web.get(f"/result?basket={basket_id}&mode=split"))
    single = text(web.get(f"/result?basket={basket_id}&mode=single"))
    assert "mode=single" in split and "mode=split" in single


def seed_link_basket() -> int:
    """Корзина, которую выигрывает ВкусВилл — сеть, принимающая корзину ссылкой."""
    milk = add_product("Молоко Простоквашино 930 мл")
    price(milk, "vkusvill", 79.0)
    price(milk, "magnit", 99.0)
    basket_id = repo.create_basket("Одной ссылкой")
    repo.set_basket_item(basket_id, milk, 2.0)
    return basket_id


def test_a_chain_that_takes_the_whole_basket_gets_a_button_not_a_link(web):
    """Ссылку на корзину создаёт сетевой вызов, и каждый вызов делает НОВУЮ корзину.

    Поэтому при показе страницы в магазин не ходят. Проверка честная: сеть в
    тестах запрещена (tests/conftest.py), и живой вызов уронил бы эту страницу.
    """
    enter(web)
    workspace()
    basket_id = seed_link_basket()
    page = text(web.get(f"/result?basket={basket_id}"))
    assert "Собрать корзину в" in page


def test_a_link_from_the_store_takes_the_person_straight_there(web, monkeypatch):
    from app import handover as ho

    monkeypatch.setattr(ho, "for_store", lambda code, lines: ho.Handover(
        store_code=code, kind=ho.LINK, link="https://vkusvill.ru/cart/abc", note="Откройте ссылку"))

    enter(web)
    workspace()
    basket_id = seed_link_basket()
    answer = web.post("/result", data={"basket": basket_id, "do": "link:vkusvill"})
    assert answer.status_code == 302
    assert answer.headers["Location"] == "https://vkusvill.ru/cart/abc"


def test_a_store_that_did_not_give_a_link_leaves_the_person_with_a_list(web, monkeypatch):
    """Отказ магазина не должен оставлять человека ни с чем — и не должен врать.

    Магазин это УМЕЕТ, просто сейчас не вышло: так и говорим, а рядом кладём тот
    самый список позиций, который уже собран, — переводить на адрес экрана значило
    бы его потерять.
    """
    from app import handover as ho

    monkeypatch.setattr(ho, "for_store", lambda code, lines: ho.Handover(
        store_code=code, kind=ho.LIST, link=None, note="Соберите их сами",
        items=[ho.HandoverItem(name="Молоко Простоквашино 930 мл", qty=2.0)]))

    enter(web)
    workspace()
    basket_id = seed_link_basket()
    page = text(web.post("/result", data={"basket": basket_id, "do": "link:vkusvill"}))
    assert "не отдал ссылку" in page
    assert "Молоко Простоквашино" in page


def test_result_does_not_go_to_the_network_when_shown(web):
    """Показ страницы не должен ходить в магазины: живой запрос упал бы тестом.

    Сеть запрещена в tests/conftest.py, поэтому проверка честная — если экран
    попробует спросить у сети ссылку на корзину при показе, страница не соберётся.
    """
    enter(web)
    workspace()
    basket_id, _, _ = seed_basket()
    assert web.get(f"/result?basket={basket_id}").status_code == 200


# ---------- история ----------
def test_history_filters_live_in_the_address(web):
    enter(web)
    workspace()
    milk = seed_history("2026-08-16")
    store = repo.get_store("pyaterochka")
    repo.add_history_row("2026-09-02", store.id, milk, "МОЛОКО", 1.0, 95.0, 95.0)

    august = text(web.get("/history?from=2026-08-01&to=2026-08-31"))
    assert "16 августа 2026" in august
    assert "2 сентября 2026" not in august

    only_five = text(web.get("/history?store=pyaterochka"))
    assert "2 сентября 2026" in only_five
    assert "16 августа 2026" not in only_five


def test_history_groups_rows_into_receipts(web):
    """Чек — это день плюс магазин: отдельной сущности чека в данных пока нет."""
    enter(web)
    workspace()
    milk = seed_history("2026-08-16")
    bread = add_product("Хлеб Бородинский")
    store = repo.get_store("magnit")
    repo.add_history_row("2026-08-16", store.id, bread, "ХЛЕБ БОРОД", 1.0, 55.0, 55.0)

    page = text(web.get("/history"))
    assert page.count("16 августа 2026") == 1      # одна строка чека, а не две
    assert "2 позиции" in page
    assert str(bread) or True


def test_empty_history_explains_why_it_matters(web):
    enter(web)
    page = text(web.get("/history"))
    assert "Отсюда начинается всё остальное" in page


def test_a_filter_that_found_nothing_is_not_the_same_as_no_purchases(web):
    """Две пустоты с разными ответами: «покупок нет» и «по этому отбору ничего»."""
    enter(web)
    workspace()
    seed_history("2026-08-16")
    page = text(web.get("/history?from=2027-01-01"))
    assert "По этому отбору ничего" in page
    assert "Отсюда начинается всё остальное" not in page


def test_pasted_text_is_shown_before_it_is_saved(web):
    """Разбор чужого письма негарантирован — ошибку надо увидеть до записи в покупки."""
    enter(web)
    workspace()
    order = "Молоко Простоквашино 930 мл   2 шт x 89,00 = 178,00"

    page = text(web.post("/history", data={"do": "preview", "text": order, "store": "magnit"}))
    assert "Понял" in page
    workspace()
    assert repo.list_history() == [], "проверка разбора не должна писать в историю"

    answer = web.post("/history", data={"do": "paste", "text": order, "store": "magnit"})
    assert answer.status_code == 302
    assert "loaded=" in answer.headers["Location"]
    workspace()
    assert repo.list_history(), "подтверждённый заказ должен лечь в историю"


def test_the_address_after_import_carries_only_counters(web):
    """В строку браузера едут счётчики, а не покупки: это данные человека."""
    enter(web)
    workspace()
    answer = web.post("/history", data={"do": "paste", "store": "magnit",
                                        "text": "Хлеб Бородинский 1 шт x 55,00 = 55,00"})
    where = answer.headers["Location"]
    assert where.startswith("/history?loaded=")
    assert "Хлеб" not in where and "55" not in where.split("loaded=")[0]


def test_nothing_understood_is_not_reported_as_success(web):
    """«Загружено 0 позиций» — не успех с числом ноль, а отдельный ответ.

    Сказать «они уже в списке ниже» значило бы отправить человека искать то, чего
    там нет: чаще всего это чек-картинка, в котором текста нет вовсе.
    """
    enter(web)
    workspace()
    answer = web.post("/history", data={"do": "paste", "text": "Спасибо за покупку!"})
    assert answer.headers["Location"].startswith("/history?loaded=0")
    page = text(web.get(answer.headers["Location"]))
    assert "Файл прочитался" in page
    assert "уже в списке ниже" not in page


def test_nothing_to_parse_is_answered_with_words(web):
    enter(web)
    page = text(web.post("/history", data={"do": "preview", "text": "   "}))
    assert "Вставьте текст" in page


def test_a_file_that_was_not_chosen_is_answered_with_words(web):
    enter(web)
    page = text(web.post("/history", data={"do": "upload"}))
    assert "Выберите файл" in page


# ---------- общее ----------
def test_all_three_screens_need_a_phone(web):
    """Ни один из трёх экранов не открывает базу, пока не известно, чью."""
    for path in ("/basket", "/result", "/history"):
        answer = web.get(path)
        assert answer.status_code == 302 and "/login" in answer.headers["Location"], path


def test_all_three_screens_accept_their_own_actions(web):
    """Действия живут на адресе экрана: POST туда же, куда GET, без 405."""
    enter(web)
    for path in ("/basket", "/result", "/history"):
        assert web.post(path, data={"do": ""}).status_code in (200, 302), path
