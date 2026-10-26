"""Витрина «Посмотреть демо» (app/demo.py): налив, версия, цены «чека из Ленты», акции, защита.

Каталог здесь крошечный и временный, как в tests/test_catalog.py: store.path подменён на
файл в tmp_path, товары кладёт store.upsert_products, склеивает refresh.match_all. Рабочие
места тоже во временной папке (users.USERS_DIR), поэтому настоящие data/catalog.db и
data/users/demo тесты не открывают. Сеть закрыта tests/conftest.py: витрина собирается без
неё, а если ей когда-нибудь понадобится сеть, тест упадёт, а не соберёт витрину на живых
запросах.
"""
from __future__ import annotations

import csv
import os
import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime, timedelta

import pytest

from app import config, demo, location, repo, service, users
from app.catalog import refresh, store
from app.catalog.model import ChainProduct as P
from app.db import get_conn, init_db, query
from app.models import Product

PERSON = "79990000050"          # вымышленный номер: личное рабочее место для проверок защиты

MILK = "Молоко ПРОСТОКВАШИНО 2,5% 930мл"
MILK_FATTER = "Молоко ПРОСТОКВАШИНО 3,2% 930мл"   # не в списке: от соседа его отличает только процент
BUTTER = "Масло сливочное ВКУСНОТЕЕВО 82,5% 200г"
PASTA = "Макароны MAKFA спирали 400г"
CHEESE = "Сыр Российский 45% 200г"
BANANAS = "Бананы"
FLOUR = "Мука пшеничная MAKFA 1кг"
CAVIAR = "Икра чёрная паюсная 100г"           # в списке есть, в каталоге нет

# название -> {сеть: (цена, в наличии)}. У ВкусВилла наличие не сообщается (None).
CATALOG = {
    MILK: {"lenta": (89.99, True), "magnit": (79.99, True), "vkusvill": (99.0, None)},
    MILK_FATTER: {"magnit": (84.0, True)},
    BUTTER: {"magnit": (229.0, True), "dixy": (199.0, True), "vkusvill": (259.0, None)},
    PASTA: {"lenta": (None, True), "magnit": (55.0, True), "dixy": (49.9, True)},
    CHEESE: {"lenta": (None, None), "magnit": (None, None)},
    BANANAS: {"lenta": (139.9, True), "magnit": (129.9, True)},
    FLOUR: {"lenta": (62.0, True), "magnit": (58.0, True)},
}
LIST = [(MILK, "2"), (BUTTER, "1"), (PASTA, "1"), (CHEESE, "1"), (BANANAS, "1,5"),
        (FLOUR, "1"), (CAVIAR, "1")]


# ---------- заготовки ----------
@pytest.fixture(autouse=True)
def catalog(tmp_path, monkeypatch):
    """Каталог — во временном файле у КАЖДОГО теста: наливка кладёт адрес в очередь каталога,
    и тест, забывший про подмену, записал бы его в настоящий data/catalog.db."""
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    yield


@pytest.fixture(autouse=True)
def workplaces(tmp_path, monkeypatch):
    """Рабочие места в стороне от живых data/users — тоже у каждого теста."""
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    yield
    users.deactivate()


@pytest.fixture
def showcase(tmp_path, monkeypatch):
    """Открытое рабочее место demo, крошечный каталог и список, который по нему читается."""
    fill_catalog()
    write_list(tmp_path, monkeypatch, LIST)
    users.open_workspace(users.DEMO)


def fill_catalog() -> None:
    """Цены сняты «через минуту после теста»: не раньше адреса витрины и не старше срока свежести.

    Адрес витрины пишется при наливе с отметкой смены (address.changed_at, секундная точность), а
    freshness.before_move гасит цену адресной сети, снятую до этой отметки. Снятые «сейчас» цены
    каталога иногда попадали в ту же секунду, иногда на секунду раньше — и тест мигал. На сервере
    так и есть по-настоящему: цены ночного обхода старше адреса, и сразу после наливки их заменяет
    дозор цен (app/pricewatch.py).
    """
    seen = (datetime.now() + timedelta(minutes=1)).isoformat(timespec="microseconds")
    sku = 0
    for name, chains in CATALOG.items():
        for chain, (price, in_stock) in chains.items():
            sku += 1
            store.upsert_products(chain, [P(str(sku), name, price=price, in_stock=in_stock,
                                            unit="kg" if name == BANANAS else None)], seen_at=seen)
    refresh.match_all()


def write_list(tmp_path, monkeypatch, rows) -> None:
    """Список витрины во временном файле: десятичная запятая пишется в кавычках, как в Excel."""
    file = tmp_path / "demo_basket.csv"
    with open(file, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["name", "qty", "note"])
        writer.writerows((name, qty, "") for name, qty in rows)
    monkeypatch.setattr(demo, "LIST_FILE", str(file))


def item_id(name: str) -> int:
    with closing(store.connect()) as conn:
        return int(conn.execute("SELECT id FROM catalog_items WHERE name = ?", (name,)).fetchone()["id"])


def ids() -> dict[str, int]:
    return {s.code: s.id for s in repo.list_stores()}


def lines() -> dict[str, float]:
    """Строки единственной корзины: название -> количество."""
    (basket,) = repo.list_baskets()
    return {r["name"]: r["qty"] for r in repo.basket_items(basket["id"])}


def receipt() -> dict[str, dict]:
    return {h["raw_name"]: h for h in repo.list_history()}


def snapshot() -> dict[str, list]:
    """Всё содержимое рабочего места. Две равные выемки — между ними ничего не менялось."""
    return {t: sorted((tuple(r) for r in query(f"SELECT * FROM {t}")), key=repr)
            for t in demo.CONTENT_TABLES}


def count(table: str) -> int:
    return query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]


def queued() -> list[tuple[str, str]]:
    with closing(store.connect()) as conn:
        return [(r["address"], r["status"])
                for r in conn.execute("SELECT address, status FROM address_queue")]


# ---------- налив ----------
def test_the_first_visit_builds_the_whole_showcase(showcase):
    today = date.today()

    assert demo.ensure() is True
    assert users.current() == users.DEMO, "рабочее место ensure не закрывает: это дело вызывающего"

    # корзина — одна, «из истории», строки списка с количествами; товара без записи в каталоге нет
    (basket,) = repo.list_baskets()
    assert basket["name"] == f"Неделя (чек из Ленты, {today:%d.%m.%Y})"
    assert basket["source"] == "history"
    assert lines() == {MILK: 2.0, BUTTER: 1.0, PASTA: 1.0, CHEESE: 1.0, BANANAS: 1.5, FLOUR: 1.0}
    # весовой остаётся весовым, и дробное количество не округляется до штуки
    units = {p.name: p.unit for p in repo.list_products()}
    assert units[BANANAS] == "kg" and units[MILK] == "pcs"

    # товар лёг как «＋» из каталога: связи с сетями и цены в базе человека — их читает «Где дешевле»
    milk = repo.find_product_by_name(MILK)
    seen = {code: repo.latest_price_for(milk.id, sid)["price"]
            for code, sid in ids().items() if repo.confirmed_mapping(milk.id, sid)}
    assert seen == {"lenta": 89.99, "magnit": 79.99, "vkusvill": 99.0}

    # чек — из Ленты, на сегодня, по одной строке на товар с ценой
    history = receipt()
    assert set(history) == {MILK, BUTTER, PASTA, BANANAS, FLOUR}
    assert {(h["store_code"], h["date"]) for h in history.values()} == {("lenta", today.isoformat())}

    # карты и акции из data/seed_offers.csv — все, что там есть
    with open(os.path.join(config.ROOT, "data", "seed_offers.csv"), encoding="utf-8-sig",
              newline="") as fh:
        seeded = list(csv.DictReader(fh))
    assert len(repo.list_offers()) == len(seeded)
    assert len(repo.list_cards()) == len({(r["bank"], r["card"]) for r in seeded})

    # адрес — строкой и в очереди на загрузку цен; версия проставлена
    assert location.address() == demo.ADDRESS
    assert queued() == [(demo.ADDRESS, "waiting")]
    assert repo.get_setting(demo.VERSION_KEY) == demo.DEMO_VERSION


def test_a_second_visit_changes_nothing(showcase):
    assert demo.ensure() is True
    before = snapshot()

    assert demo.ensure() is False

    assert snapshot() == before, "повторное нажатие ничего не стирает и не задваивает"
    assert len(repo.list_baskets()) == 1 and count("purchase_history") == 5


def test_the_goods_are_checked_along_with_the_version(showcase):
    """Версия совпала, а товаров нет (витрину кто-то опустошил) — это не «налито»."""
    demo.ensure()
    demo._wipe()
    repo.set_setting(demo.VERSION_KEY, demo.DEMO_VERSION)

    assert demo.ensure() is True
    assert len(lines()) == 6


def test_a_new_version_rebuilds_and_leaves_nothing_old(showcase, monkeypatch):
    demo.ensure()
    first = lines()
    # Остатки прежней версии и следы посетителей — хотя бы по строке в каждой таблице, у которой
    # есть внешние ключи: стирание идёт детьми вперёд, и неверный порядок упал бы здесь.
    junk = repo.upsert_product(Product(id=None, name="Остаток прежней версии"))
    junk_basket = repo.create_basket("Корзина прежней версии")
    repo.set_basket_item(junk_basket, junk, 3)
    repo.set_setting("остаток", "1")
    card = repo.list_cards()[0].id
    lenta = ids()["lenta"]
    with get_conn() as conn:
        variant = conn.execute(
            "INSERT INTO variants (basket_id, created_at, total, baseline, savings_rub, savings_pct)"
            " VALUES (?,?,?,?,?,?)", (junk_basket, "2026-10-01T10:00:00", 90.0, 100.0, 10.0, 10.0)
        ).lastrowid
        conn.execute("INSERT INTO variant_lines (variant_id, store_id, card_id, product_id, qty,"
                     " price, discount) VALUES (?,?,?,?,?,?,?)",
                     (variant, lenta, card, junk, 1, 90.0, 0.0))
        conn.execute("INSERT INTO link_rejects (product_id, store_id, sku, at) VALUES (?,?,?,?)",
                     (junk, lenta, "1", "2026-10-01T10:00:00"))
        conn.execute("INSERT INTO receipts (key, date, store, total, rows, source, seen_at, imported_at)"
                     " VALUES (?,?,?,?,?,?,?,?)",
                     ("k1", "2026-10-01", "lenta", 100.0, 1, "file", "2026-10-01", "2026-10-01"))
    stores_before = [(s.id, s.code) for s in repo.list_stores()]
    monkeypatch.setattr(demo, "DEMO_VERSION", "следующая")

    assert demo.ensure() is True

    assert "Остаток прежней версии" not in {p.name for p in repo.list_products()}
    assert [b["name"].startswith("Неделя (чек из Ленты") for b in repo.list_baskets()] == [True]
    assert lines() == first, "витрина собрана заново, а не дополнена"
    assert repo.get_setting("остаток") is None
    for table in ("variants", "variant_lines", "link_rejects", "receipts"):
        assert count(table) == 0, table
    assert repo.get_setting(demo.VERSION_KEY) == "следующая"
    assert location.address() == demo.ADDRESS, "адрес лежит в настройках: стирание его уносит, наливка возвращает"
    assert [(s.id, s.code) for s in repo.list_stores()] == stores_before, "справочник сетей общий и цел"


def test_the_wipe_covers_every_table_of_a_workspace():
    """Появилась новая таблица в схеме — стирание про неё не знает, и её строки пережили бы пересборку."""
    users.open_workspace(users.DEMO)
    tables = {r["name"] for r in query(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}

    assert tables - {"stores"} == set(demo.CONTENT_TABLES)


# ---------- цены «чека из Ленты» ----------
def test_the_receipt_takes_lenta_prices_then_the_dearest_other(showcase):
    demo.ensure()

    paid = {name: (h["qty"], h["unit_price"], h["total"]) for name, h in receipt().items()}

    assert paid == {
        MILK: (2.0, 89.99, 179.98),       # цена Ленты, а не дешёвая 79,99 и не дорогая 99
        BUTTER: (1.0, 259.0, 259.0),      # Ленты в этом товаре нет — самая высокая у остальных
        PASTA: (1.0, 55.0, 55.0),         # строка Ленты есть, цены в ней нет — то же самое
        BANANAS: (1.5, 139.9, 209.85),    # на вес: количество дробное
        FLOUR: (1.0, 62.0, 62.0),
    }
    assert CHEESE not in paid, "цены нет ни у кого — строку чека не выдумываем"
    assert CHEESE in lines(), "но в корзине товар остаётся"


def test_the_comparison_prices_every_line_of_the_receipt(showcase):
    """Ради этого витрина и собирается из единой базы: у позиции чека есть цена хотя бы в одной сети.

    Прежний вымышленный набор давал «7 из 16 без цены» и вариант дороже чека. Здесь проверяется
    весь путь: связи и снимки цен легли в базу человека, а «Раньше вы платили» — это сам чек.
    """
    demo.ensure()
    (basket,) = repo.list_baskets()

    priced = {ln.name for ln in service.build_basket_lines(basket["id"]) if ln.prices}
    variants, baseline = service.calculate(basket["id"], refresh=False)

    assert priced == set(receipt()), "цены нет только у сыра, которого нет ни в одной сети с ценой"
    assert baseline == service.baseline_total(basket["id"]) == round(
        sum(h["total"] for h in repo.list_history()), 2)
    assert variants, "расчёт что-то предложил"


def test_lenta_rows_in_stock_come_first_then_the_cheaper():
    """Соседние фасовки в одном товаре: берётся та, что в наличии, и из них дешевле."""
    rows = [{"chain": "lenta", "price": 120.0, "in_stock": 0},
            {"chain": "lenta", "price": 140.0, "in_stock": 1},
            {"chain": "lenta", "price": 130.0, "in_stock": 1},
            {"chain": "magnit", "price": 99.0, "in_stock": 1}]

    assert demo._paid(rows) == 130.0


def test_a_chain_that_is_silent_about_stock_counts_as_in_stock():
    """ВкусВилл и Дикси о наличии молчат (None): молчание не «нет в наличии»."""
    rows = [{"chain": "lenta", "price": 150.0, "in_stock": None},
            {"chain": "lenta", "price": 120.0, "in_stock": 0}]

    assert demo._paid(rows) == 150.0


@pytest.mark.parametrize("rows, expected", [
    ([{"chain": "magnit", "price": 70.0}, {"chain": "dixy", "price": 90.0},
      {"chain": "lenta", "price": None}], 90.0),
    ([{"chain": "magnit", "price": 70.0}, {"chain": "dixy", "price": 0}], 70.0),
    ([{"chain": "lenta", "price": None}, {"chain": "magnit", "price": 0}], None),
    ([], None),
])
def test_without_a_lenta_price_the_dearest_other_is_taken(rows, expected):
    assert demo._paid(rows) == expected


# ---------- акции ----------
def test_the_offers_are_live_for_a_month_ahead(showcase):
    today = date.today()
    demo.ensure()

    first, last = demo.offer_window(today)
    offers = repo.list_offers()

    assert offers and {(o.valid_from, o.valid_to) for o in offers} == {(first, last)}
    assert all(o.is_valid_on(today.isoformat()) for o in offers)
    # и для расчёта «Где дешевле», а не только на бумаге
    for store_id in {o.store_id for o in offers}:
        assert repo.offers_for_store(store_id), store_id


@pytest.mark.parametrize("today, window", [
    (date(2026, 10, 25), ("2026-10-01", "2026-11-30")),
    (date(2026, 11, 30), ("2026-11-01", "2026-12-31")),
    (date(2026, 12, 1), ("2026-12-01", "2027-01-31")),
    (date(2026, 12, 31), ("2026-12-01", "2027-01-31")),
    (date(2027, 1, 31), ("2027-01-01", "2027-02-28")),
    (date(2027, 12, 31), ("2027-12-01", "2028-01-31")),
    (date(2028, 1, 15), ("2028-01-01", "2028-02-29")),
])
def test_the_offer_window_runs_to_the_end_of_next_month(today, window):
    first, last = demo.offer_window(today)

    assert (first, last) == window
    assert first <= today.isoformat() <= last and last[:7] > today.isoformat()[:7]


def test_a_december_build_keeps_the_offers_alive_into_january(showcase, monkeypatch):
    class December31(date):
        @classmethod
        def today(cls):
            return cls(2026, 12, 31)

    monkeypatch.setattr(demo, "date", December31)

    assert demo.ensure() is True

    offers = repo.list_offers()
    assert {(o.valid_from, o.valid_to) for o in offers} == {("2026-12-01", "2027-01-31")}
    assert all(o.is_valid_on("2026-12-31") and o.is_valid_on("2027-01-31")
               and not o.is_valid_on("2027-02-01") for o in offers)
    assert {h["date"] for h in repo.list_history()} == {"2026-12-31"}
    assert next(iter(repo.list_baskets()))["name"] == "Неделя (чек из Ленты, 31.12.2026)"


# ---------- запасной набор ----------
def test_an_empty_catalog_gives_the_invented_set():
    users.open_workspace(users.DEMO)
    seed = demo._seed()

    assert demo.ensure() is True

    assert [b["name"] for b in repo.list_baskets()] == [seed.BASKET_NAME]
    assert {h["date"] for h in repo.list_history()} == {seed.RECEIPT_DATE}
    assert location.address() == demo.ADDRESS
    # карты и акции у запасного набора тоже с живым сроком, а не со сентябрьским из CSV
    first, last = demo.offer_window(date.today())
    assert repo.list_offers() and {(o.valid_from, o.valid_to) for o in repo.list_offers()} == {(first, last)}


@pytest.mark.parametrize("found, from_catalog", [(3, True), (2, False)])
def test_fewer_goods_than_the_minimum_fall_back(showcase, tmp_path, monkeypatch, found, from_catalog):
    monkeypatch.setattr(demo, "MIN_GOODS", 3)
    write_list(tmp_path, monkeypatch, [(MILK, "1"), (BUTTER, "1"), (FLOUR, "1")][:found]
               + [(CAVIAR, "1")])
    seed = demo._seed()

    assert demo.ensure() is True

    names = [b["name"] for b in repo.list_baskets()]
    if from_catalog:
        assert len(names) == 1 and names[0].startswith("Неделя (чек из Ленты")
    else:
        # недоложенная витрина стёрта целиком: от неё не остаётся ни корзины, ни строк чека
        assert names == [seed.BASKET_NAME]
        assert {h["date"] for h in repo.list_history()} == {seed.RECEIPT_DATE}


# ---------- сбои ----------
def test_a_failing_address_queue_does_not_stop_the_build(showcase, monkeypatch):
    def locked(address):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "enqueue_address", locked)

    assert demo.ensure() is True
    assert location.address() == demo.ADDRESS and len(lines()) == 6


def test_a_geocoder_that_is_down_never_escapes(showcase, monkeypatch, caplog):
    real = location.save_address

    def down(value):
        raise OSError("геокодер недоступен")

    monkeypatch.setattr(location, "save_address", down)

    assert demo.ensure() is False, "посетитель в любом случае должен попасть в демо"
    assert "витрина не собралась" in caplog.text
    assert repo.get_setting(demo.VERSION_KEY) is None, "оборванная сборка не должна выглядеть законченной"

    monkeypatch.setattr(location, "save_address", real)
    assert demo.ensure() is True, "связь вернулась — следующий посетитель получит витрину"
    assert len(lines()) == 6


def test_a_catalog_that_does_not_open_gives_the_invented_set(showcase, monkeypatch):
    def broken():
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(store, "init", broken)

    assert demo.ensure() is True
    assert [b["name"] for b in repo.list_baskets()] == [demo._seed().BASKET_NAME]
    assert location.address() == demo.ADDRESS


def test_one_failing_row_does_not_sink_the_rest(showcase, monkeypatch):
    monkeypatch.setattr(demo, "MIN_GOODS", 3)
    bad, real = item_id(BUTTER), refresh.adopt

    def adopt(number, product_id=None):
        if number == bad:
            raise RuntimeError("связь не легла")
        return real(number, product_id)

    monkeypatch.setattr(refresh, "adopt", adopt)

    assert demo.ensure() is True
    assert set(lines()) == {MILK, PASTA, CHEESE, BANANAS, FLOUR}
    assert BUTTER not in receipt()


def test_an_unreadable_list_keeps_the_previous_showcase(showcase, tmp_path, monkeypatch):
    demo.ensure()
    before = snapshot()
    monkeypatch.setattr(demo, "LIST_FILE", str(tmp_path / "нет такого списка.csv"))
    monkeypatch.setattr(demo, "DEMO_VERSION", "следующая")

    assert demo.ensure() is False

    assert snapshot() == before, "список читается до стирания: не прочитался — витрина как была"


# ---------- защита чужих рабочих мест ----------
def test_a_personal_workspace_is_never_wiped():
    users.open_workspace(PERSON)
    mine = repo.upsert_product(Product(id=None, name="Мой товар"))
    basket = repo.create_basket("Моя корзина")
    repo.set_basket_item(basket, mine, 2.0)
    repo.add_history_row("2026-10-01", None, mine, "Мой товар", 1.0, 10.0, 10.0)
    repo.set_setting("address", "Тестовый адрес 1")
    before = snapshot()

    assert demo.ensure() is False

    assert snapshot() == before
    assert repo.get_setting(demo.VERSION_KEY) is None
    assert users.current() == PERSON


def test_the_shared_base_is_never_wiped(tmp_path, monkeypatch):
    """Ни одно рабочее место не открыто — работает общая база из config.yaml, а не демо."""
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "shared.db"))
    users.deactivate()
    init_db()
    repo.set_basket_item(repo.create_basket("Общая корзина"),
                         repo.upsert_product(Product(id=None, name="Общий товар")), 1.0)
    before = snapshot()

    assert users.current() is None
    assert demo.ensure() is False

    assert snapshot() == before


# ---------- одновременные посетители ----------
def test_two_visitors_at_once_build_it_once(showcase):
    results: list[bool] = []
    start = threading.Barrier(2)

    def visitor():
        users.activate(users.DEMO)          # контекст потока свой: рабочее место открывают в каждом
        start.wait()
        results.append(demo.ensure())

    threads = [threading.Thread(target=visitor) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)

    assert sorted(results) == [False, True], "второй ждёт замок и видит готовую витрину"
    assert len(repo.list_baskets()) == 1 and len(lines()) == 6


# ---------- поиск товара списка ----------
def test_a_name_is_found_exactly_first_and_by_words_otherwise(showcase):
    assert demo._find(MILK) == item_id(MILK)
    # поиск не видит чисел и из двух молок выбрал бы известное большему числу сетей;
    # точное название различает и проценты, и фасовки
    assert demo._find(MILK_FATTER) == item_id(MILK_FATTER) != item_id(MILK)
    # сеть чуть переписала название: ищется по тем же словам, что попадают в norm
    assert demo._find("Молоко ПРОСТОКВАШИНО 2,5%, без змж, 900мл") == item_id(MILK)
    assert demo._find(CAVIAR) is None


# ---------- сам список ----------
def test_the_shipped_list_is_well_formed():
    with open(demo.LIST_FILE, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    names = [r["name"].strip() for r in rows]

    assert len(rows) >= demo.MIN_GOODS
    assert all(names) and len(set(names)) == len(names), "пустых названий и повторов нет"
    assert all(float(r["qty"].replace(",", ".")) > 0 for r in rows)
    # Образ копирует app/ целиком, а из data/ — только перечисленные файлы: список в data/
    # на сервер не доехал бы, и демо молча вернулось бы к вымышленному чеку.
    assert os.path.dirname(os.path.abspath(demo.LIST_FILE)) == os.path.dirname(os.path.abspath(demo.__file__))
