"""Сборка корзин: одна кнопка — корзина в каждой подключённой сети, строка на сеть.

Стережётся то, ради чего сборка написана, и то, чего она не должна делать никогда:
положить корзину дважды, выдать наш расчёт за итог сети, промолчать о сети, куда
корзина не уехала, или ломиться в сеть, которая отказала. Браузера и сети здесь нет:
передача подменена на уровне cart.deliver, ссылка — на уровне handover.for_store,
а пускатель, наряд, отметка о ходе и строки работают настоящие.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import replace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import carttotal, cartrun, handover, homeexit, repo, store_accounts, users  # noqa: E402
from app.models import Product  # noqa: E402
from app.shopbrowser import cart, signals  # noqa: E402
from app.shopbrowser import store as shopstore  # noqa: E402

PHONE = "79990000088"
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
METRO_HASH = "5502aed9f4501b12da05daf6f5347e7b"

# Артикулы METRO — числа из образца ответа её корзины (tests/fixtures/metro_basket.json):
# запись METRO принимает только числовой артикул.
SKUS = {"magnit": ("100001", "100002"), "lenta": ("101", "102"),
        "vkusvill": ("201", "202"), "pyaterochka": ("hist-1", "hist-2"),
        "metro": ("117189", "204411")}
PRICES = {"magnit": (89.0, 45.0), "lenta": (92.0, 48.0), "vkusvill": (99.0, 52.0),
          "pyaterochka": (95.0, 47.0), "metro": (85.0, 44.0)}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Рабочее место с корзиной «молоко ×2 + хлеб», знакомой пяти сетям."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    # Пауза домашнего выхода живёт файлом; в тестах — во временной папке, а не в data/.
    monkeypatch.setattr(homeexit, "STATE_DIR", str(tmp_path / "pace"))
    # Проверка цен перед передачей спрашивает сеть — здесь сети нет.
    monkeypatch.setattr("app.matcher.refresh_prices", lambda ids, stores: None)
    users.open_workspace(PHONE)
    milk = repo.upsert_product(Product(id=None, name="Молоко Простоквашино 930 мл", unit="pcs"))
    bread = repo.upsert_product(Product(id=None, name="Хлеб Бородинский", unit="pcs"))
    for code, (sku_milk, sku_bread) in SKUS.items():
        shop = repo.get_store(code)
        price_milk, price_bread = PRICES[code]
        milk_sp = repo.upsert_store_product(shop.id, sku_milk, "Молоко Простоквашино 930 мл",
                                            url=f"https://{code}.example/{sku_milk}")
        bread_sp = repo.upsert_store_product(shop.id, sku_bread, "Хлеб Бородинский",
                                             url=f"https://{code}.example/{sku_bread}")
        repo.confirm_mapping(milk, milk_sp, confirmed=True)
        repo.confirm_mapping(bread, bread_sp, confirmed=True)
        repo.save_price(milk_sp, price_milk)
        repo.save_price(bread_sp, price_bread)
    basket_id = repo.create_basket("Неделя 42")
    repo.set_basket_item(basket_id, milk, 2.0)
    repo.set_basket_item(basket_id, bread, 1.0)
    yield basket_id
    users.deactivate()


@pytest.fixture
def roads(monkeypatch):
    """Дороги без сети: передача кладёт всё, ссылку сеть выдаёт, окна для итога нет.

    Запоминает, кто и в каком потоке работал: «по потоку на сеть» проверяется
    именами потоков, а не верой.
    """
    calls: dict[str, list] = {"deliver": [], "link": [], "read": [], "close": []}

    def deliver(chain, phone, plan):
        calls["deliver"].append((chain, [ln.sku for ln in plan.lines],
                                 threading.current_thread().name))
        items = [cart._row(line, True, "", "") for line in plan.lines]
        return cart._done(chain, [ln.sku for ln in plan.lines], [], items, "")

    def for_store(code, lines):
        calls["link"].append((code, threading.current_thread().name))
        return handover.Handover(store_code=code, kind=handover.LINK,
                                 link=f"https://{code}.example/cart/share-1",
                                 note=handover.NOTE_BY_STORE.get(code, ""))

    def read(chain, phone):
        calls["read"].append(chain)
        return None, "в тесте окна магазина нет"

    monkeypatch.setattr(cart, "deliver", deliver)
    monkeypatch.setattr(cart.driver, "close", lambda chain, phone: calls["close"].append(chain))
    monkeypatch.setattr(handover, "for_store", for_store)
    monkeypatch.setattr(carttotal, "read", read)
    return calls


def save_login(chain: str = "magnit", cookies=None) -> None:
    """Вход — как его сохраняет «Кабинет»: состояние плюс отметка о подключении."""
    shopstore.save(chain, {"cookies": cookies if cookies is not None
                           else [{"name": "mg_at", "value": "ключ"}], "origins": []})
    store_accounts.mark_connected(chain)


def by_code(rows) -> dict:
    return {row.code: row for row in rows}


# ---------- одна кнопка, поток на сеть ----------

def test_one_press_fills_every_network_it_can_each_in_its_own_thread(home, roads):
    save_login()

    cartrun.start(PHONE, home, wait=True)
    rows = by_code(cartrun.rows())

    assert [c[0] for c in roads["deliver"]] == ["magnit"], "в кабинет кладём только туда, где умеем"
    assert roads["deliver"][0][2] == "cartrun-magnit", "у Магнита свой поток"
    assert {code for code, _ in roads["link"]} == {"vkusvill"}
    assert {thread for _, thread in roads["link"]} == {"cartrun-vkusvill"}
    assert sorted(roads["deliver"][0][1]) == ["100001", "100002"], "вся корзина, не доля варианта"

    magnit = rows["magnit"]
    assert magnit.state == cartrun.DONE and magnit.planned == 2 and len(magnit.landed) == 2
    assert magnit.by == cartrun.SERVER
    assert rows["vkusvill"].state == cartrun.DONE
    assert rows["vkusvill"].link == "https://vkusvill.example/cart/share-1"
    assert rows["lenta"].road == cartrun.PHONE
    assert rows["lenta"].state == cartrun.CANT


def test_networks_really_go_in_parallel(home, roads, monkeypatch):
    """Магнит и ВкусВилл встречаются у барьера: по очереди они бы не встретились никогда."""
    save_login()
    meet = threading.Barrier(2, timeout=10)
    real_deliver, real_link = cart.deliver, handover.for_store

    def deliver(chain, phone, plan):
        meet.wait()
        return real_deliver(chain, phone, plan)

    def for_store(code, lines):
        if code == "vkusvill":
            meet.wait()
        return real_link(code, lines)

    monkeypatch.setattr(cart, "deliver", deliver)
    monkeypatch.setattr(handover, "for_store", for_store)

    cartrun.start(PHONE, home, wait=True)

    rows = by_code(cartrun.rows())
    assert rows["magnit"].state == cartrun.DONE, rows["magnit"].note
    assert rows["vkusvill"].state == cartrun.DONE, rows["vkusvill"].note


def test_every_network_gets_a_row_with_its_reason(home, roads):
    """Сеть, куда корзина не уехала, — тоже строка, и причина в ней словами."""
    cartrun.start(PHONE, home, wait=True)
    rows = by_code(cartrun.rows())

    assert set(rows) == set(cartrun.networks()), "строка на каждую сеть, без пропусков"
    pyaterochka = rows["pyaterochka"]
    assert pyaterochka.state == cartrun.CANT and pyaterochka.road == cartrun.PHONE
    assert "адрес" in pyaterochka.note, "причина — адрес сервера, а не «не умеем»"
    assert rows["metro"].state == cartrun.CANT and "токен" in rows["metro"].note
    assert rows["dixy"].state == cartrun.CANT and rows["samokat"].state == cartrun.CANT


def test_a_network_without_a_login_offers_to_connect(home, roads):
    cartrun.start(PHONE, home, wait=True)

    magnit = cartrun.get("magnit")
    assert roads["deliver"] == [], "без входа класть некуда"
    assert magnit.state == cartrun.LOGIN and magnit.login == "connect"


def test_magnit_without_its_login_cookie_asks_to_log_in_again(home, roads):
    """С 16.10.2026 вход в Магнит не сохраняется (нет куки mg_at) — строка так и говорит."""
    save_login(cookies=[{"name": "mg_udi", "value": "устройство"}])

    cartrun.start(PHONE, home, wait=True)

    magnit = cartrun.get("magnit")
    assert roads["deliver"] == []
    assert magnit.state == cartrun.LOGIN and magnit.login == "again"
    assert "войдите заново" in magnit.note


def test_the_shop_window_is_closed_after_the_handover_and_not_raised_for_nothing(
        home, roads, monkeypatch):
    """Окно передачи закрывается, как у пачки; где окна не было, браузер не будится."""
    save_login()
    cartrun.start(PHONE, home, wait=True)
    assert roads["close"] == ["magnit"]

    monkeypatch.setattr(cart, "deliver", lambda chain, phone, plan: cart._done(
        chain, [], [], [], "Вход в эту сеть не сохранён.", stop="nologin"))
    cartrun.start(PHONE, home, only="magnit", again=True, wait=True)

    assert roads["close"] == ["magnit"], "закрытие подняло бы Chromium ради пустоты"
    assert cartrun.get("magnit").state == cartrun.LOGIN


def test_a_login_the_network_no_longer_accepts_becomes_log_in_again(home, roads, monkeypatch):
    save_login()

    def stale(chain, phone, plan):
        return cart._done(chain, [], [], [], "Сеть больше не считает вас вошедшим — "
                          "сохранённый вход устарел. Откройте кабинет и войдите заново.",
                          stop="login")

    monkeypatch.setattr(cart, "deliver", stale)
    cartrun.start(PHONE, home, wait=True)

    magnit = cartrun.get("magnit")
    assert magnit.state == cartrun.LOGIN and magnit.login == "again"


# ---------- второй раз в ту же сеть ----------

def test_a_second_press_does_not_fill_the_same_cart_twice(home, roads):
    save_login()
    cartrun.start(PHONE, home, wait=True)
    first = cartrun.get("magnit")

    cartrun.start(PHONE, home, wait=True)

    assert len(roads["deliver"]) == 1, "сеть кладёт поверх лежащего — второй заход удвоил бы её"
    assert len(roads["link"]) == 1, "ссылка на ту же корзину не просится заново"
    kept = cartrun.get("magnit")
    assert kept.state == cartrun.DONE and kept.landed == first.landed, \
        "повторное нажатие не стирает ответ, полученный в первый раз"


def test_again_after_clearing_the_cart_fills_it_once_more(home, roads):
    save_login()
    cartrun.start(PHONE, home, wait=True)

    cartrun.start(PHONE, home, only="magnit", again=True, wait=True)

    assert [c[0] for c in roads["deliver"]] == ["magnit", "magnit"]
    assert cartrun.get("magnit").state == cartrun.DONE


def test_a_network_already_being_filled_gets_no_second_thread(home, roads):
    save_login()
    cartrun.save(cartrun.Row(code="magnit", name="Магнит", road=cartrun.BROWSER,
                             state=cartrun.GOING, basket_id=home, started_at="2026-10-17T14:00:00"))

    cartrun.start(PHONE, home, wait=True)

    assert roads["deliver"] == []
    assert cartrun.get("magnit").state == cartrun.GOING


def test_a_row_that_died_with_the_server_does_not_say_going_forever(home, roads, monkeypatch):
    row = cartrun.save(cartrun.Row(code="magnit", name="Магнит", road=cartrun.BROWSER,
                                   state=cartrun.GOING, basket_id=home))
    data = json.loads(repo.get_setting(cartrun.KEY))
    data["magnit"]["beat"] = time.time() - cart.STALE_AFTER - 5
    repo.set_setting(cartrun.KEY, json.dumps(data, ensure_ascii=False))

    shown = by_code(cartrun.rows())["magnit"]

    assert row.state == cartrun.GOING and shown.state == cartrun.FAILED
    assert "оборвалась" in shown.note


def test_a_changed_basket_gets_a_new_link(home, roads):
    cartrun.start(PHONE, home, wait=True)
    milk = repo.basket_items(home)[0]["product_id"]
    repo.set_basket_item(home, milk, 3.0)

    cartrun.start(PHONE, home, wait=True)

    assert [code for code, _ in roads["link"]].count("vkusvill") == 2


# ---------- отказ сети и темп ----------

def test_a_refusal_is_crawlblocked_a_refused_row_and_a_pause_for_the_home_address(
        home, roads, monkeypatch):
    save_login()

    def forbidden(chain, phone, plan):
        return cart._done(chain, [], [], [], signals.FORBIDDEN_NOTE, stop="forbidden")

    monkeypatch.setattr(cart, "deliver", forbidden)
    monkeypatch.setattr(homeexit, "configured", lambda chain: "socks5://203.0.113.7:1080")

    cartrun.start(PHONE, home, wait=True)

    magnit = cartrun.get("magnit")
    assert magnit.state == cartrun.REFUSED
    assert magnit.note.startswith("Сеть отказала")
    assert homeexit.paused_until("magnit"), "отказ через дом — пауза на весь адрес владельца"

    calls: list = []
    monkeypatch.setattr(cart, "deliver", lambda *a: calls.append(a))
    cartrun.start(PHONE, home, wait=True)

    assert calls == [], "в паузе сборка в эту сеть не идёт"
    assert cartrun.get("magnit").state == cartrun.REFUSED
    assert "домашний выход" in cartrun.get("magnit").note


def test_a_check_page_is_a_refusal_too_but_a_network_down_is_not(home, roads, monkeypatch):
    save_login()
    monkeypatch.setattr(homeexit, "configured", lambda chain: None)
    monkeypatch.setattr(cart, "deliver", lambda chain, phone, plan: cart._done(
        chain, [], [], [], "Сеть встретила проверкой «я не робот».", stop="check"))
    cartrun.start(PHONE, home, wait=True)
    assert cartrun.get("magnit").state == cartrun.REFUSED

    monkeypatch.setattr(cart, "deliver", lambda chain, phone, plan: cart._done(
        chain, [], [], [], "Передача оборвалась.", stop="broken"))
    cartrun.start(PHONE, home, only="magnit", again=True, wait=True)
    assert cartrun.get("magnit").state == cartrun.FAILED


def test_the_handover_tells_programs_why_it_stopped(home, monkeypatch):
    """Код остановки рядом со словами: сборка отличает отказ сети от просьбы войти."""
    import app.location as client_place
    from app import cartplan

    monkeypatch.setattr(cart.driver, "open_store", lambda *a, **kw: None)
    monkeypatch.setattr(client_place, "for_store", lambda code: None)
    save_login()
    plan = cartplan.build("magnit", cartrun._lines("magnit", home), force=True)

    monkeypatch.setattr(cart.driver, "look", lambda *a, **kw: {
        "logged_in": None, "guarded": True, "guard": "forbidden"})
    cart.deliver("magnit", PHONE, plan)
    assert cart.progress("magnit")["stop"] == "forbidden"

    monkeypatch.setattr(cart.driver, "look", lambda *a, **kw: {
        "logged_in": False, "guarded": False})
    cart.deliver("magnit", PHONE, plan)
    assert cart.progress("magnit")["stop"] == "login"


def test_a_network_error_code_says_whether_the_door_was_closed():
    class Answer:
        def __init__(self, status):
            self.status_code = status

    class Failure(Exception):
        def __init__(self, status):
            super().__init__(status)
            self.response = Answer(status)

    assert cart._refusal_of(Failure(403)) == "refused"
    assert cart._refusal_of(Failure(429)) == "refused"
    assert cart._refusal_of(Failure(502)) == "error"
    assert cart._refusal_of(ValueError("нет хеша")) == "error"


# ---------- итог: чей он ----------

def test_the_link_total_is_ours_and_says_when_the_networks_will_show(home, roads):
    cartrun.start(PHONE, home, wait=True)

    vkusvill = cartrun.get("vkusvill")
    assert vkusvill.total.source == carttotal.OURS
    assert vkusvill.total.sum == pytest.approx(2 * 99.0 + 52.0)
    assert "итог сети появится, когда откроете ссылку под своим входом" in vkusvill.note.lower()
    assert vkusvill.total.discount is None and vkusvill.total.delivery is None, \
        "скидку и доставку сети мы не знаем — и не пишем"


def test_an_unread_network_total_is_said_out_loud_and_ours_stands_apart(home, roads):
    save_login()

    cartrun.start(PHONE, home, wait=True)

    magnit = cartrun.get("magnit")
    assert roads["read"] == ["magnit"]
    assert magnit.total.source == carttotal.OURS, "не прочиталось — значит не сетевой"
    assert magnit.total.sum == pytest.approx(2 * 89.0 + 45.0)
    assert "Итог сети не прочитан" in magnit.note


def test_magnit_total_is_read_from_its_cart_page_after_the_handover(home, roads, monkeypatch):
    """Сборка зовёт чтение итога по окну передачи и подписывает его сетевым."""
    save_login()
    page = CartPage(cart_text=fixture("magnit_cart_page.txt"))
    monkeypatch.setattr(carttotal, "read", REAL_READ)
    monkeypatch.setattr(cart.driver, "run", lambda chain, phone, job, **kw: job(page))
    monkeypatch.setattr(cart.driver, "_settle", lambda page, wait=0.8: None)

    cartrun.start(PHONE, home, wait=True)

    magnit = cartrun.get("magnit")
    assert magnit.total.source == carttotal.NETWORK
    assert magnit.total.sum == pytest.approx(387.96)
    assert magnit.total.discount == pytest.approx(100.0)
    assert magnit.total.delivery == 0.0


def test_metro_total_comes_from_the_networks_own_answer(home, monkeypatch):
    """METRO по токену: итог — total_cost её корзины, тем же запросом, что проверка."""
    import app.location as client_place
    from app.connectors import metro, metro_cart

    metro_abilities = store_accounts.ABILITIES["metro"]
    monkeypatch.setitem(store_accounts.ABILITIES, "metro", replace(
        metro_abilities, gives=metro_abilities.gives + (store_accounts.CART,)))
    monkeypatch.setattr(metro_cart, "writable", lambda: True)
    monkeypatch.setattr(metro, "_store_id", lambda loc: "16")
    monkeypatch.setattr(client_place, "for_store", lambda code: None)
    save_login("metro", cookies=[{"name": metro_cart.HASH_COOKIE, "value": METRO_HASH,
                                  "domain": "api.metro-cc.ru"}])
    answer = json.loads(fixture("metro_basket.json"))
    empty = {"success": True, "data": {"user_hash": METRO_HASH, "articles": [],
                                       "unavailable_articles": [], "total_cost": 0}}
    sent: list[str] = []

    def request(method, url, params=None, json=None, timeout=None, headers=None):
        sent.append(method)
        return Reply(answer if "POST" in sent else empty)

    monkeypatch.setattr(metro_cart.requests, "request", request)
    monkeypatch.setattr(handover, "for_store", lambda code, lines: handover.Handover(
        store_code=code, kind=handover.LINK, link=None, note="сеть молчит"))

    cartrun.start(PHONE, home, wait=True)

    row = cartrun.get("metro")
    assert sent == ["GET", "POST", "GET"], "итог — из ответа проверки, а не лишним запросом"
    assert row.state == cartrun.DONE and len(row.landed) == 2
    assert row.total.source == carttotal.NETWORK
    assert row.total.sum == pytest.approx(348.8)
    assert row.total.discount == pytest.approx(41.2)
    assert row.total.minimal == pytest.approx(1500.0)
    assert row.total.count == 2


# ---------- отчёт с телефона ----------

def test_a_phone_report_lands_in_the_same_row_without_rework(home):
    """Исполнитель на телефоне отчитывается той же формой — строка Пятёрочки его примет."""
    row = cartrun.accept({
        "store": "pyaterochka", "basket": home,
        "landed": ["Молоко Простоквашино 930 мл"],
        "missing": [{"name": "Хлеб Бородинский", "why": "нет в наличии"}],
        "total": {"sum": 140.5, "source": "network", "discount": 12, "delivery": 0},
        "token": "чужое поле не берётся"})

    stored = cartrun.get("pyaterochka")
    assert row.state == cartrun.DONE and stored.by == cartrun.DEVICE
    assert stored.total.source == carttotal.NETWORK and stored.total.sum == 140.5
    assert stored.missing == [{"name": "Хлеб Бородинский", "why": "нет в наличии", "gone": True}]
    assert "token" not in json.dumps(json.loads(repo.get_setting(cartrun.KEY)))


def test_a_press_does_not_wipe_what_the_phone_reported(home, roads):
    """Сервер в Пятёрочку не кладёт — но и отчёт телефона словами «не кладём» не стирает."""
    from app.web.screens import carts

    cartrun.accept({"store": "pyaterochka", "basket": home, "landed": ["Молоко"],
                    "total": {"sum": 100.0, "source": "network"}})

    cartrun.start(PHONE, home, wait=True)

    row = cartrun.get("pyaterochka")
    assert row.state == cartrun.DONE and row.by == cartrun.DEVICE
    assert carts._button(row) is None, "кнопка «ещё раз» у сети телефона кончилась бы отказом"


def test_a_report_without_a_declared_source_is_not_called_the_networks(home):
    cartrun.accept({"store": "samokat", "landed": ["Молоко"], "total": {"sum": 99.0}})
    assert cartrun.get("samokat").total.source == carttotal.OURS


def test_a_report_for_an_unknown_network_is_refused(home):
    with pytest.raises(ValueError):
        cartrun.accept({"store": "ozon", "landed": ["Молоко"]})


# ---------- итог со страницы корзины: разбор ----------

def fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def test_the_cart_page_total_is_read_by_its_labels():
    total = carttotal.parse(fixture("magnit_cart_page.txt"))

    assert total.source == carttotal.NETWORK
    assert total.sum == pytest.approx(387.96)
    assert total.goods == pytest.approx(487.96)
    assert total.discount == pytest.approx(100.0)
    assert total.delivery == 0.0, "«Бесплатно» — это ноль, а не «не сказано»"
    assert total.count == 3


def test_item_badges_and_prices_are_not_taken_for_the_order():
    """«Скидка 15 %» у позиции и цена рядом — не скидка заказа; без «Итого» итога нет."""
    tiles = ("Корзина\nСыр Российский 45% 200 г\nСкидка 15 %\n239,98 ₽\n"
             "Молоко\nСкидка\n−20%\n89,99 ₽\nВ корзину")
    assert carttotal.parse(tiles) is None
    with_total = tiles + "\nИтого\n329,97 ₽"
    assert carttotal.parse(with_total).discount is None


def test_an_empty_cart_page_is_recognised():
    assert carttotal.empty_cart("Корзина\nВ корзине пока пусто\nПерейти в каталог")
    assert carttotal.parse("Корзина\nВ корзине пока пусто") is None


def test_money_reads_russian_amounts_and_nothing_else():
    assert carttotal.money("−1 234,56 ₽") == -1234.56
    assert carttotal.money("Итого 1234.5 руб.") == 1234.5
    assert carttotal.money("2 шт") is None
    assert carttotal.money("4,8 · 9738 отзывов") is None


def test_a_stored_total_without_a_declared_source_is_ours():
    assert carttotal.Total.of({"sum": 100}).source == carttotal.OURS
    assert carttotal.Total.of({"sum": 100, "source": "network"}).source == carttotal.NETWORK
    assert carttotal.Total.of({"sum": -5}) is None
    assert carttotal.Total.of({"sum": "много"}) is None


def test_metro_answer_becomes_a_network_total():
    from app.connectors import metro_cart

    basket = metro_cart._basket(json.loads(fixture("metro_basket.json")))
    total = carttotal.from_metro(basket)

    assert total.source == carttotal.NETWORK and total.sum == pytest.approx(348.8)
    assert total.discount == pytest.approx(41.2) and total.count == 2


# ---------- итог со страницы корзины: окно ----------

REAL_READ = carttotal.read


class Reply:
    def __init__(self, payload: dict) -> None:
        self.payload, self.text, self.status_code = payload, "", 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class Node:
    def __init__(self, page) -> None:
        self.page = page

    def click(self, timeout=None) -> None:
        self.page.where = "cart"


class CartPage:
    """Страница сети: на карточке кнопка корзины скрыта, на главной видна.

    Ровно как у Магнита (docs/design/store-apis.md): на карточке узел кнопки есть,
    видимых ноль, поэтому чтение итога идёт на главную и нажимает её там.
    """

    def __init__(self, cart_text: str, button_on_card: bool = False) -> None:
        self.where = "card"
        self.cart_text = cart_text
        self.button_on_card = button_on_card
        self.opened: list[str] = []

    def evaluate(self, script, arg=None):
        if script is carttotal._OPEN_JS:
            visible = self.where == "home" or (self.where == "card" and self.button_on_card)
            return "Перейти в корзину Корзина" if visible else None
        if script is carttotal._LABELS_JS:
            return []
        return None

    def query_selector(self, selector):
        return Node(self) if selector == f"[{carttotal.MARK}]" else None

    def goto(self, url, **kw):
        self.opened.append(url)
        self.where = "home"

    def inner_text(self, _selector):
        if self.where == "cart":
            return self.cart_text
        return "Магнит Каталог Акции Молоко 89,99 ₽ В корзину"

    def wait_for_load_state(self, *a, **kw):
        return None


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setattr(cart.driver, "_settle", lambda page, wait=0.8: None)
    monkeypatch.setattr(carttotal, "WAIT_LIMIT", 0)


def test_the_cart_is_opened_by_its_accessible_name_from_the_main_page(window):
    page = CartPage(fixture("magnit_cart_page.txt"))

    total, why = carttotal.read_page(page, "magnit")

    assert why == "" and total.sum == pytest.approx(387.96)
    assert page.opened == ["https://magnit.ru/"], "скрытую кнопку на карточке не жмём"


def test_a_cart_page_without_a_total_says_so_and_adds_nothing_up(window):
    page = CartPage("Корзина\nМолоко\n89,99 ₽\nХлеб\n45,00 ₽\nПерейти к оформлению",
                    button_on_card=True)

    total, why = carttotal.read_page(page, "magnit")

    assert total is None, "сумма видимых цен — не итог сети"
    assert "Итого" in why


def test_only_networks_with_a_cart_page_are_read():
    total, why = carttotal.read("lenta", PHONE)
    assert total is None and "не читает" in why


# ---------- экран ----------

@pytest.fixture
def web(home, monkeypatch):
    """Экран на том же рабочем месте: вход по номеру, корзина уже собрана."""
    from app.web import create_app

    application = create_app()
    application.config.update(TESTING=True)
    client = application.test_client()
    client.post("/login", data={"phone": PHONE, "next": "/"})
    real = cartrun.start
    # Экран пускает сборку фоном; здесь ждём потоки, чтобы проверить итог.
    monkeypatch.setattr(cartrun, "start", lambda *a, **kw: real(*a, **{**kw, "wait": True}))
    yield client
    users.open_workspace(PHONE)


def test_the_result_screen_has_one_button_for_every_network(web, home):
    page = web.get(f"/result?basket={home}").data.decode("utf-8")

    assert "Собрать во всех подключённых" in page
    assert 'action="/carts"' in page


def test_the_screen_shows_where_the_cart_will_go_before_the_press(web, home):
    page = web.get(f"/carts?basket={home}").data.decode("utf-8")

    for name in ("Магнит", "Лента", "ВкусВилл", "Пятёрочка", "METRO"):
        assert name in page
    assert "Подключить" in page and 'href="/accounts"' in page
    assert "не допускают по его адресу" in page, "Пятёрочка: причина словами"
    assert "уедет по нажатию" in page


def test_the_button_fills_the_carts_and_the_rows_tell_the_outcome(web, home, roads):
    users.open_workspace(PHONE)
    save_login()

    answer = web.post("/carts", data={"basket": home, "do": "all"})

    assert answer.status_code == 302
    assert answer.headers["Location"].endswith(f"/carts?basket={home}")
    page = web.get(f"/carts?basket={home}").data.decode("utf-8")
    assert "Легло <b>2 из 2</b>" in page
    assert "наш расчёт" in page and "Итог сети не прочитан" in page
    assert "https://vkusvill.example/cart/share-1" in page
    assert "Корзину в сети очистил — положить ещё раз" in page


def test_a_login_made_after_the_press_is_seen_by_the_screen(web, home, roads):
    """«Нужен вход» — условие, а не ответ: вошёл на «Кабинетах» — строка это видит."""
    web.post("/carts", data={"basket": home, "do": "all"})
    users.open_workspace(PHONE)
    assert cartrun.get("magnit").state == cartrun.LOGIN

    save_login()
    page = web.get(f"/carts?basket={home}").data.decode("utf-8")

    assert "Подключить" not in page and "Войти заново" not in page
    assert "Собрать во всех подключённых" in page and "Магнит (2 поз.)" in page


def test_the_progress_answer_is_small(web, home):
    users.open_workspace(PHONE)
    cartrun.save(cartrun.Row(code="magnit", name="Магнит", road=cartrun.LINK,
                             state=cartrun.GOING, planned=2, now="кладу творог", basket_id=home))

    got = web.get("/carts?state=1").get_json()

    assert got["ok"] is True and got["going"] is True
    magnit = next(r for r in got["rows"] if r["code"] == "magnit")
    assert magnit["going"] is True and magnit["now"] == "кладу творог"


def test_a_phone_report_through_the_door_reaches_the_screen(web, home):
    answer = web.post("/api/carts/report", json={
        "store": "pyaterochka", "basket": home, "landed": ["Молоко Простоквашино 930 мл"],
        "total": {"sum": 140.5, "source": "network"}})

    assert answer.status_code == 200 and answer.get_json()["ok"] is True
    page = web.get(f"/carts?basket={home}").data.decode("utf-8")
    assert "посчитала сеть" in page and "прочитал телефон" in page


def test_the_screen_never_offers_to_order_or_pay(web, home, roads):
    users.open_workspace(PHONE)
    save_login()
    web.post("/carts", data={"basket": home, "do": "all"})

    page = web.get(f"/carts?basket={home}").data.decode("utf-8").lower()

    assert "оформить заказ" not in page and "оплатить" not in page
