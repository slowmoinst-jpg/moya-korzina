"""«Где дешевле» сама запускает обновление цен корзины (автопилот 25.10.2026, W2).

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Знакомый владельца входит с телефона, указывает адрес, набирает
корзину и открывает «Где дешевле». Цены у его адреса раньше приносил только дозор (раз в 15
минут), и он видел пустой экран и уходил. Теперь экран сам запускает обновление цен и честно
пишет, что цены узнаются; пришли — показывает расчёт. Каждое правило стережётся отдельно:

- запуск: когда в корзине есть товары, это не демо, цены нужны (расчёта нет или у лучшего
  варианта есть непокрытые позиции) и по корзине ничего не идёт;
- нагрузка на сети: не чаще раза в 10 минут, пока набор товаров тот же, — товара может просто
  не быть в продаже, и гонять сети каждым открытием экрана нельзя;
- слова на экране: «Узнаём цены…» и перезагрузка раз в 5 секунд, пока работа идёт; одна
  строка словами, если она кончилась ошибкой; ничего лишнего, если кончилась хорошо;
- расчёт обновляется, когда работа кончилась позже него (кэш «Результата» живёт 10 минут);
- товар, добавленный на «Корзине» (поиск, привычное), тоже заводит работу — цены начинают
  приходить, пока человек ещё выбирает.

В сеть тесты не ходят: _start_job подменён записью вызовов, а где важна настоящая работа,
подменён либо её поток (_run_job), либо только сети (matcher.refresh_prices).

ЧЕГО ЭТИ ТЕСТЫ НЕ ЗНАЮТ. Как строка и перезагрузка выглядят на телефоне и не мешает ли
перезагрузка раз в 5 секунд тому, кто листает экран: это смотрят глазами.
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

PHONE = "79990000096"
REFRESH = '<meta http-equiv="refresh" content="5">'
FAILED_LINE = "Не удалось обновить цены — показаны последние известные"
EMPTY_LINE = "Пока не хватает цен, чтобы посчитать"


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временных базах — как в tests/test_phone_newcomer.py."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app import location
    from app.web import create_app
    from app.web.screens import basket as basket_screen
    from app.web.screens import result

    result._CALC.clear()
    basket_screen._JOBS.clear()
    users.open_workspace(PHONE)
    location.save_address("г Москва, ул Тверская, 4")
    users.deactivate()

    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    result._CALC.clear()
    basket_screen._JOBS.clear()
    users.deactivate()


@pytest.fixture
def starts(monkeypatch):
    """Подмена _start_job: помнит, какие работы просили завести, и ничего не запускает."""
    from app.web.screens import basket as basket_screen

    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(basket_screen, "_start_job",
                        lambda basket_id, kind, items: calls.append((int(basket_id), kind)))
    return calls


def enter(client):
    from conftest import sign_in

    return sign_in(client, PHONE)


def workspace() -> None:
    users.open_workspace(PHONE)


def text(answer) -> str:
    return answer.data.decode("utf-8")


def add_product(name: str) -> int:
    return repo.upsert_product(Product(id=None, name=name, unit="pcs"))


def price(product_id: int, store_code: str, value: float) -> None:
    store = repo.get_store(store_code)
    sp_id = repo.upsert_store_product(store.id, f"sku-{store_code}-{product_id}", f"товар {product_id}")
    repo.confirm_mapping(product_id, sp_id, confirmed=True)
    repo.save_price(sp_id, value)


def unpriced_basket() -> tuple[int, int]:
    """Корзина из одного товара, цены которого нет нигде: расчёт пуст. (корзина, товар)"""
    milk = add_product("Молоко 930 мл")
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, milk, 2.0)
    return basket_id, milk


def priced_basket() -> tuple[int, int]:
    """Тот же товар, но цена в Магните есть: расчёт полный."""
    basket_id, milk = unpriced_basket()
    price(milk, "magnit", 40.0)
    return basket_id, milk


def half_priced_basket() -> tuple[int, int]:
    """Молоко с ценой и хлеб без неё: расчёт есть, но у лучшего варианта хлеба нет."""
    basket_id, milk = priced_basket()
    repo.set_basket_item(basket_id, add_product("Хлеб Бородинский"), 1.0)
    return basket_id, milk


def put_job(basket_id: int, status: str, *, ago: float = 0.0, pids=(), what: str = "",
            done: int = 0, total: int = 1) -> None:
    """Положить запись работы по корзине такой, какой её оставил бы _start_job.

    Рабочее место должно быть открыто: ключ записи — путь к базе человека.
    """
    from app.web.screens import basket as basket_screen

    now = time.time()
    basket_screen._JOBS[basket_screen._job_key(basket_id)] = {
        "kind": "fresh", "status": status, "done": done, "total": total, "what": what,
        "note": "", "pids": tuple(pids), "started": now - ago - 5,
        "finished": 0.0 if status == "running" else now - ago}


def demo_basket() -> tuple[int, int]:
    """Корзина в общей витрине, как в tests/test_phone_newcomer.py: нужная, но без цен."""
    from app.web.screens.basket import LAST_OPEN

    users.open_workspace(users.DEMO)
    basket_id, milk = unpriced_basket()
    repo.set_setting(LAST_OPEN, str(basket_id))
    users.deactivate()
    return basket_id, milk


# ---------- запуск ----------
def test_an_empty_calculation_starts_the_price_job(web, starts):
    """Вариантов расчёта нет — цены нужны, и экран заводит обновление."""
    enter(web)
    workspace()
    basket_id, _ = unpriced_basket()
    users.deactivate()

    page = text(web.get("/phone/cheaper"))

    assert starts == [(basket_id, "fresh")]
    assert EMPTY_LINE in page, "работы в записях нет (подмена), экран говорит как прежде"


def test_a_half_priced_basket_starts_the_price_job(web, starts):
    """Расчёт есть, но у лучшего варианта остались непокрытые позиции — цены всё ещё нужны."""
    enter(web)
    workspace()
    basket_id, _ = half_priced_basket()
    users.deactivate()

    text(web.get("/phone/cheaper"))

    assert starts == [(basket_id, "fresh")]


def test_a_complete_calculation_starts_nothing(web, starts):
    enter(web)
    workspace()
    priced_basket()
    users.deactivate()

    page = text(web.get("/phone/cheaper"))

    assert starts == []
    assert "Всё в Магните" in page


def test_an_empty_basket_starts_nothing(web, starts):
    enter(web)
    workspace()
    repo.create_basket("Неделя")
    users.deactivate()

    text(web.get("/phone/cheaper"))

    assert starts == []


def test_nothing_starts_while_the_job_runs(web, starts):
    enter(web)
    workspace()
    basket_id, milk = unpriced_basket()
    put_job(basket_id, "running", pids=(milk,), what="Магнит", done=1, total=5)
    users.deactivate()

    text(web.get("/phone/cheaper"))

    assert starts == []


def test_the_demo_starts_nothing(web, starts):
    """Демо — общая витрина: цены в неё приносит дозор, а своей работы по общей базе нет."""
    web.post("/demo")
    demo_basket()
    page = text(web.get("/phone/cheaper"))

    assert "Это демо — смотреть можно, менять нельзя." in page
    assert EMPTY_LINE in page, "корзина без цен, и в настоящем рабочем месте работа завелась бы"
    assert starts == []

    # Контроль: та же корзина в настоящем рабочем месте работу заводит.
    web.post("/logout")
    enter(web)
    workspace()
    basket_id, _ = unpriced_basket()
    users.deactivate()
    text(web.get("/phone/cheaper"))
    assert starts == [(basket_id, "fresh")]


def test_start_prices_refuses_the_demo_by_itself(web, starts):
    """Второй замок: запись в демо закрыта ещё в app/web/auth.py, но запуск не полагается на него."""
    from flask import g

    from app.web.phone.cheaper import start_prices

    items = [{"product_id": 1, "qty": 1.0}]
    with web.application.test_request_context("/phone/basket"):
        g.phone = users.DEMO
        start_prices(7, items)
    assert starts == []

    with web.application.test_request_context("/phone/basket"):
        g.phone = PHONE
        start_prices(7, [])
        start_prices(7, items)
    assert starts == [(7, "fresh")], "пустую корзину не обновляем, настоящую — да"


# ---------- не чаще раза в 10 минут ----------
@pytest.mark.parametrize("status", ["ok", "failed"])
def test_the_same_goods_are_not_asked_again_within_ten_minutes(web, starts, status):
    """Прошло меньше 10 минут и набор товаров тот же — сети не трогаем, даже если цен нет."""
    enter(web)
    workspace()
    basket_id, milk = unpriced_basket()
    put_job(basket_id, status, ago=60, pids=(milk,))
    users.deactivate()

    for _ in range(3):
        text(web.get("/phone/cheaper"))

    assert starts == []


def test_changed_goods_start_the_job_again(web, starts):
    """С прошлой работы в корзину добавили товар (набор другой) — цены нужны снова."""
    enter(web)
    workspace()
    basket_id, milk = unpriced_basket()
    put_job(basket_id, "ok", ago=60, pids=(milk + 100,))
    users.deactivate()

    text(web.get("/phone/cheaper"))

    assert starts == [(basket_id, "fresh")]


def test_a_job_without_the_goods_remembered_counts_as_other_goods(web, starts):
    """Запись, где набор товаров не записан (старая или чужая), не доказывает, что набор тот же."""
    enter(web)
    workspace()
    basket_id, _ = unpriced_basket()
    put_job(basket_id, "ok", ago=60)
    users.deactivate()

    text(web.get("/phone/cheaper"))

    assert starts == [(basket_id, "fresh")]


def test_after_ten_minutes_the_same_goods_start_the_job_again(web, starts):
    from app.web.phone.cheaper import PRICES_AGAIN_SEC

    assert PRICES_AGAIN_SEC == 600
    enter(web)
    workspace()
    basket_id, milk = unpriced_basket()
    put_job(basket_id, "ok", ago=PRICES_AGAIN_SEC + 5, pids=(milk,))
    users.deactivate()

    text(web.get("/phone/cheaper"))

    assert starts == [(basket_id, "fresh")]


# ---------- слова на экране ----------
@pytest.mark.parametrize("make", [unpriced_basket, priced_basket])
def test_a_running_job_is_said_in_words_and_the_screen_reloads_itself(web, starts, make):
    enter(web)
    workspace()
    basket_id, milk = make()
    put_job(basket_id, "running", pids=(milk,), what="Магнит", done=1, total=5)
    users.deactivate()

    page = text(web.get("/phone/cheaper"))

    assert "Узнаём цены у вашего адреса: Магнит (1 из 5). Экран обновится сам." in page
    assert REFRESH in page and page.index(REFRESH) < page.index("</head>")
    assert FAILED_LINE not in page


def test_a_running_job_replaces_the_empty_text(web, starts):
    """Расчёта ещё нет, но цены узнаются — «загляните чуть позже» было бы лишним."""
    enter(web)
    workspace()
    basket_id, milk = unpriced_basket()
    put_job(basket_id, "running", pids=(milk,), what="Лента", done=0, total=11)
    users.deactivate()

    page = text(web.get("/phone/cheaper"))

    assert "Узнаём цены у вашего адреса: Лента (0 из 11)." in page
    assert EMPTY_LINE not in page


def test_the_line_drops_the_store_when_it_is_not_known_yet(web, starts):
    enter(web)
    workspace()
    basket_id, milk = unpriced_basket()
    put_job(basket_id, "running", pids=(milk,), what="", done=0, total=3)
    users.deactivate()

    page = text(web.get("/phone/cheaper"))

    assert "Узнаём цены у вашего адреса (0 из 3). Экран обновится сам." in page
    assert "адреса:" not in page


def test_a_job_running_too_long_stops_reloading_the_screen(web, starts):
    """Зависшая работа не перезагружает экран вечно: строка словами, без перезагрузки."""
    from app.web.phone.cheaper import PRICES_SLOW_SEC

    enter(web)
    workspace()
    basket_id, milk = unpriced_basket()
    put_job(basket_id, "running", ago=PRICES_SLOW_SEC + 60, pids=(milk,), what="Лента",
            done=2, total=5)
    users.deactivate()

    page = text(web.get("/phone/cheaper"))

    assert "Цены собираются дольше обычного: Лента (2 из 5). Загляните позже." in page
    assert "http-equiv" not in page and EMPTY_LINE not in page


@pytest.mark.parametrize("make", [unpriced_basket, priced_basket])
def test_a_finished_job_does_not_reload_the_screen(web, starts, make):
    enter(web)
    workspace()
    basket_id, milk = make()
    put_job(basket_id, "ok", ago=30, pids=(milk,))
    users.deactivate()

    page = text(web.get("/phone/cheaper"))

    assert "http-equiv" not in page
    assert "Узнаём цены" not in page and FAILED_LINE not in page


@pytest.mark.parametrize("make", [unpriced_basket, priced_basket])
def test_a_failed_job_is_said_in_one_line_without_reload(web, starts, make):
    enter(web)
    workspace()
    basket_id, milk = make()
    put_job(basket_id, "failed", ago=30, pids=(milk,))
    users.deactivate()

    page = text(web.get("/phone/cheaper"))

    assert FAILED_LINE in page
    assert "http-equiv" not in page and "Узнаём цены" not in page


def test_no_job_means_no_line_and_no_reload(web, starts):
    enter(web)
    workspace()
    priced_basket()
    users.deactivate()

    page = text(web.get("/phone/cheaper"))

    assert "http-equiv" not in page
    assert "Узнаём цены" not in page and FAILED_LINE not in page


# ---------- настоящая работа ----------
def wait_for(done, seconds: float = 10.0) -> None:
    deadline = time.time() + seconds
    while not done() and time.time() < deadline:
        time.sleep(0.01)


def test_the_real_job_remembers_the_goods_and_is_not_started_twice(web, monkeypatch):
    """_start_job пишет в запись набор товаров, а повторное открытие экрана второй поток не заводит."""
    from app.web.screens import basket as basket_screen

    ran: list[tuple] = []
    monkeypatch.setattr(basket_screen, "_run_job", lambda *args: ran.append(args))
    enter(web)
    workspace()
    basket_id, milk = unpriced_basket()
    users.deactivate()

    first = text(web.get("/phone/cheaper"))
    second = text(web.get("/phone/cheaper"))

    wait_for(lambda: ran)
    assert len(ran) == 1, "второе открытие экрана работу не заводит: первая ещё идёт"
    assert REFRESH in first and REFRESH in second
    workspace()
    job = basket_screen.job_of(basket_id)
    assert job["status"] == "running" and job["kind"] == "fresh"
    assert job["pids"] == (milk,)


def test_prices_that_came_are_shown_without_waiting_for_the_cache(web, monkeypatch):
    """Расчёт «Результата» живёт 10 минут; кончилась работа позже него — экран считает заново."""
    from app.web.screens import basket as basket_screen

    monkeypatch.setattr(basket_screen, "_run_job", lambda *args: None)
    enter(web)
    workspace()
    basket_id, milk = unpriced_basket()
    users.deactivate()

    page = text(web.get("/phone/cheaper"))
    assert "Узнаём цены у вашего адреса" in page and "Всё в Магните" not in page

    workspace()
    price(milk, "magnit", 40.0)                    # сеть ответила
    time.sleep(0.05)                               # конец работы строго позже расчёта
    basket_screen._mark(basket_screen._job_key(basket_id), status="ok", finished=time.time())
    users.deactivate()

    page = text(web.get("/phone/cheaper"))
    assert "Всё в Магните" in page
    assert "Узнаём цены" not in page and "http-equiv" not in page


def test_the_whole_way_from_an_empty_screen_to_the_calculation(web, monkeypatch):
    """Настоящая работа от начала до конца; подменены только сети (matcher.refresh_prices).

    Поток, запись о ходе, строка с названием сети и пересчёт расчёта после конца работы —
    настоящие: так видно, что части склеены, а не только каждая сама по себе.
    """
    import threading

    from app.web.screens import basket as basket_screen

    gate = threading.Event()

    def asked(product_ids, store_codes, progress=None, **kwargs):
        progress(0, 2, "magnit")                   # сеть, которую спрашивают сейчас
        assert gate.wait(10), "тест не отпустил работу"
        price(product_ids[0], "magnit", 40.0)      # поток работы сидит в базе человека
        progress(1, 2, "lenta")
        return {"updated": 1, "errors": []}

    monkeypatch.setattr("app.matcher.refresh_prices", asked)
    enter(web)
    workspace()
    basket_id, _ = unpriced_basket()
    users.deactivate()

    try:
        text(web.get("/phone/cheaper"))            # заводит работу
        workspace()
        wait_for(lambda: (basket_screen.job_of(basket_id) or {}).get("what") == "Магнит")
        users.deactivate()
        during = text(web.get("/phone/cheaper"))
    finally:
        gate.set()
    assert "Узнаём цены у вашего адреса: Магнит (0 из 2). Экран обновится сам." in during
    assert REFRESH in during and "Всё в Магните" not in during

    workspace()
    wait_for(lambda: basket_screen.job_of(basket_id)["status"] != "running")
    assert basket_screen.job_of(basket_id)["status"] == "ok"
    users.deactivate()
    after = text(web.get("/phone/cheaper"))
    assert "Всё в Магните" in after
    assert REFRESH not in after and "Узнаём цены" not in after


# ---------- товар, добавленный на «Корзине» ----------
def test_adding_from_the_search_starts_the_job(web, starts):
    """Первый товар новичка заводит и корзину, и работу: цены идут, пока он выбирает дальше."""
    enter(web)
    workspace()
    cucumbers = add_product("Огурцы гладкие")
    users.deactivate()

    answer = web.post("/phone/basket", data={"do": f"add:{cucumbers}", "q": "огурц"})

    assert answer.status_code == 303
    workspace()
    (basket,) = repo.list_baskets()
    assert starts == [(int(basket["id"]), "fresh")]


def test_adding_a_catalog_good_from_the_search_starts_the_job(web, starts):
    """Новичку ищется в основном единый каталог сетей: «В корзину» оттуда тоже заводит работу."""
    from app.catalog import refresh, store
    from app.catalog.model import ChainProduct

    enter(web)
    workspace()
    store.init()
    store.upsert_products("lenta", [ChainProduct("80424", "Молоко Простоквашино 2,5% 930 мл",
                                                 price=89.99)])
    store.upsert_products("vkusvill", [ChainProduct("36296", "Молоко Простоквашино 2,5% 930 мл",
                                                    price=100.0)])
    refresh.match_all()
    item_id = int(store.search_items("простоквашино")[0]["id"])
    users.deactivate()

    answer = web.post("/phone/basket", data={"do": f"catalog:{item_id}", "q": "простоквашино"})

    assert answer.status_code == 303
    workspace()
    (basket,) = repo.list_baskets()
    assert starts == [(int(basket["id"]), "fresh")]


def test_every_added_good_asks_for_the_job(web, starts):
    """Второй товар — снова просьба о работе (если первая ещё идёт, _start_job вторую не заведёт)."""
    enter(web)
    workspace()
    first, second = add_product("Огурцы гладкие"), add_product("Помидоры")
    users.deactivate()

    web.post("/phone/basket", data={"do": f"add:{first}"})
    web.post("/phone/basket", data={"do": f"add:{second}"})

    workspace()
    (basket,) = repo.list_baskets()
    assert starts == [(int(basket["id"]), "fresh")] * 2


def test_adding_a_good_that_is_already_there_does_not_ask_again(web, starts):
    """Набор товаров не изменился, а прошлая работа свежая — повторное «добавить» сети не трогает."""
    enter(web)
    workspace()
    basket_id, milk = unpriced_basket()
    put_job(basket_id, "ok", ago=60, pids=(milk,))
    users.deactivate()

    answer = web.post("/phone/basket", data={"do": f"add:{milk}"})

    assert answer.status_code == 303
    assert starts == []
    workspace()
    assert {i["product_id"]: i["qty"] for i in repo.basket_items(basket_id)} == {milk: 3.0}


def test_a_new_good_asks_again_even_right_after_a_job(web, starts):
    enter(web)
    workspace()
    basket_id, milk = unpriced_basket()
    put_job(basket_id, "ok", ago=60, pids=(milk,))
    bread = add_product("Хлеб Бородинский")
    users.deactivate()

    web.post("/phone/basket", data={"do": f"add:{bread}"})

    assert starts == [(basket_id, "fresh")]


def test_plus_and_minus_do_not_start_the_job(web, starts):
    """Набор товаров от «+» и «−» не меняется, а количество на цены не влияет."""
    enter(web)
    workspace()
    basket_id, milk = unpriced_basket()
    users.deactivate()

    web.post("/phone/basket", data={"do": f"plus:{milk}"})
    web.post("/phone/basket", data={"do": f"minus:{milk}"})

    assert starts == []


def test_a_job_that_cannot_start_breaks_neither_the_basket_nor_the_screen(web, monkeypatch):
    """Фоновые цены — приятное дополнение: сбой запуска не должен терять добавленный товар."""
    from app.web.screens import basket as basket_screen

    def broken(basket_id, kind, items):
        raise RuntimeError("поток не завёлся")

    monkeypatch.setattr(basket_screen, "_start_job", broken)
    enter(web)
    workspace()
    cucumbers = add_product("Огурцы гладкие")
    users.deactivate()

    answer = web.post("/phone/basket", data={"do": f"add:{cucumbers}"})

    assert answer.status_code == 303
    workspace()
    (basket,) = repo.list_baskets()
    assert [i["product_id"] for i in repo.basket_items(int(basket["id"]))] == [cucumbers]
    users.deactivate()
    answer = web.get("/phone/cheaper")
    assert answer.status_code == 200 and EMPTY_LINE in text(answer)


def test_a_good_that_is_not_in_the_catalog_starts_nothing(web, starts):
    enter(web)

    answer = web.post("/phone/basket", data={"do": "add:999999"})

    assert answer.status_code == 303
    assert starts == []


def test_adding_in_the_demo_starts_nothing(web, starts):
    web.post("/demo")
    _, milk = demo_basket()

    answer = web.post("/phone/basket", data={"do": f"add:{milk}"})

    assert answer.status_code == 303 and "demo=1" in answer.headers["Location"]
    assert starts == []


def test_without_address_prices_do_not_start_and_prompt_is_shown(web, starts):
    """Без адреса ни добавление товара, ни открытие «Где дешевле» работу не заводят, а на экране просьба."""
    from app import location

    enter(web)
    workspace()
    location.save_address(None)
    milk = add_product("Молоко без адреса")
    users.deactivate()

    # 1. Добавление товара на «Корзине» не заводит работу
    web.post("/phone/basket", data={"do": f"add:{milk}"})
    assert starts == [], "без адреса добавление товара не должно запускать работу"

    # 2. Открытие «Где дешевле» работу не заводит
    answer = web.get("/phone/cheaper")
    assert starts == [], "без адреса открытие «Где дешевле» не должно запускать работу"

    page = text(answer)
    # 3. На экране просьба указать адрес
    assert "Укажите адрес доставки — от него зависят цены и наличие" in page
    assert 'href="/phone/settings#address"' in page
    assert "у вашего адреса" not in page
    assert "по вашему адресу" not in page


def test_calculation_timestamp_is_taken_before_calculate(monkeypatch):
    """Отметка времени расчёта берётся ДО вызова service.calculate, а не после."""
    from app import service
    from app.web.screens import result

    start_time = 1000.0
    calc_time = 1050.0

    current = [start_time]

    def mock_time():
        return current[0]

    def mock_calculate(basket_id, refresh, progress=None):
        # Во время расчёта время уходит вперёд (например, расчёт длился 50 с)
        current[0] = calc_time
        return [], None

    monkeypatch.setattr(time, "time", mock_time)
    monkeypatch.setattr(service, "calculate", mock_calculate)
    monkeypatch.setattr(service, "effortless_variants", lambda b: ([], None))
    monkeypatch.setattr(service, "doubts_summary", lambda b: {"rows": [], "products": 0, "total": 0})
    monkeypatch.setattr(service, "receipt_priced", lambda b: False)

    bundle = result._bundle(12345, again=True)
    assert bundle["at"] == start_time
    assert bundle["at"] != calc_time
