"""Цены из корзины телефона — в единую базу.

Цель (решение владельца 18.10.2026): наличие и цены приложение получает, формируя
корзину в фоне в каждой подключённой сети. Наличие из отчёта уже бралось
(note_device_stock), а цена — нет: у Перекрёстка, Vprok и Монетки обход отдаёт только
названия, и в базе на 194 тысячи строк нет ни одной цены. Корзина владельца — то место,
где цена этих сетей вообще видна.
"""
from __future__ import annotations

import pytest

from app import phonecart
from app.catalog import refresh, store


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


def _row(base, chain="perekrestok", sku="12345"):
    with base.connect() as conn:
        conn.execute(
            "insert into chain_products (chain, sku, name, active, first_seen, last_seen)"
            " values (?,?,?,'1',?,?)", (chain, sku, "Сыр Российский 200 г",
                                        store.now(), store.now()))
        conn.commit()
    return base.products_of_chain(chain)[0]


def test_a_price_from_the_cart_lands_in_the_base(base):
    _row(base, sku="12345")

    changed = refresh.note_device_prices("perekrestok", [
        {"sku": "12345", "ok": True, "price": 199.9}])

    assert changed == 1
    row = base.products_of_chain("perekrestok")[0]
    assert row["price"] == 199.9
    assert row["price_seen"], "видно, когда цену видели"


def test_a_position_that_did_not_land_brings_no_price(base):
    """У не легшей позиции цена может быть ценой другой упаковки — её не берём."""
    _row(base, sku="777")

    assert refresh.note_device_prices("perekrestok",
                                      [{"sku": "777", "ok": False, "price": 10.5}]) == 0
    assert base.products_of_chain("perekrestok")[0]["price"] is None


def test_a_zero_or_missing_price_changes_nothing(base):
    _row(base, sku="888")

    assert refresh.note_device_prices("perekrestok", [
        {"sku": "888", "ok": True, "price": 0},
        {"sku": "888", "ok": True},
        {"sku": "888", "ok": True, "price": "дорого"},
    ]) == 0
    assert base.products_of_chain("perekrestok")[0]["price"] is None


def test_a_placeholder_sku_gets_no_price(base):
    """У заглушки цены сети нет: по ней товар в корзину не кладут."""
    with base.connect() as conn:
        conn.execute("insert into chain_products (chain, sku, name, active, first_seen,"
                     " last_seen) values ('perekrestok','perekrestok-1','Заглушка','1',?,?)",
                     (store.now(), store.now()))
        conn.commit()

    assert refresh.note_device_prices("perekrestok",
                                      [{"sku": "perekrestok-1", "ok": True, "price": 50}]) == 0


def test_the_report_keeps_the_price_of_each_position(base, monkeypatch):
    """Отчёт телефона доносит цену позиции до базы — дорога целиком, через accept_report."""
    _row(base, sku="555")
    saved = {}
    monkeypatch.setattr(phonecart.repo, "set_setting", lambda key, value: saved.update({key: value}))
    monkeypatch.setattr(phonecart, "_to_row", lambda report: None)

    answer = phonecart.accept_report({
        "store": "perekrestok",
        "items": [{"name": "Сыр Российский 200 г", "sku": "555", "ok": True, "price": 199.9}],
    })

    assert answer["ok"]
    assert base.products_of_chain("perekrestok")[0]["price"] == 199.9


def test_the_report_answers_without_price_when_the_app_does_not_send_it(base, monkeypatch):
    """Старые сборки приложения цену не шлют — отчёт обязан приниматься и без неё."""
    _row(base, sku="556")
    monkeypatch.setattr(phonecart.repo, "set_setting", lambda key, value: None)
    monkeypatch.setattr(phonecart, "_to_row", lambda report: None)

    answer = phonecart.accept_report({
        "store": "perekrestok",
        "items": [{"name": "Сыр Российский 200 г", "sku": "556", "ok": True}],
    })

    assert answer["landed"] == 1
    assert base.products_of_chain("perekrestok")[0]["price"] is None


def test_a_name_from_a_report_is_cleaned_like_a_card_name(base, monkeypatch):
    """Отчёт тоже приносит текст карточки — и мимо чистки в базу попадал мусор.

    Замер 20.10.2026: у Пятёрочки 25 строк с «-22% 4,95 Томатная паста … 205 00 ₽» в
    имени. Они пришли ОТЧЁТОМ (инструменты кладут туда текст карточки целиком), а чистил
    имена только приём карточек.
    """
    monkeypatch.setattr(phonecart.repo, "set_setting", lambda key, value: None)
    monkeypatch.setattr(phonecart, "_to_row", lambda report: None)

    phonecart.accept_report({
        "store": "pyaterochka",
        "items": [{"name": "-22% 4,95 Томатная паста Помидорка 250мл 250 мл 205 00 ₽",
                   "sku": "84652", "url": "https://5ka.ru/product/tomatnaya-pasta--84652/",
                   "ok": True, "price": 159.9}],
    })

    row = base.products_of_chain("pyaterochka")[0]
    assert row["name"] == "Томатная паста Помидорка 250мл 250 мл", row["name"]


def test_price_from_card_url_of_a_not_landed_line_goes_to_the_shop_row(tmp_path, monkeypatch):
    """Цена не легшей позиции пишется СТРОКЕ СЕТИ с артикулом из адреса карточки.

    Замер 21.10.2026: телефон открыл карточку Papia (другая линейка), цена 139.99 осталась
    в отчёте при ok=false. Приписать её нашему товару нельзя, а строке сети с тем артикулом,
    чью карточку читали, — можно и полезно: для Пятёрочки и Самоката это единственные цены.
    """
    from app.catalog import refresh, store

    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    with store.connect() as conn:
        conn.execute("INSERT INTO chain_products (chain, sku, name, first_seen, last_seen,"
                     " active) VALUES ('pyaterochka','4359341','Сушка','2026-10-21','2026-10-21',1),"
                     " ('pyaterochka','7777777','Другое','2026-10-21','2026-10-21',1)")
        conn.commit()

    items = [
        # не легло, но карточку видели: артикул берётся из адреса
        {"ok": False, "sku": "", "price": 139.99, "in_stock": False,
         "url": "https://5ka.ru/product/sushka-samokat--4359341/"},
        # не легло и адреса нет — цене некуда лечь
        {"ok": False, "sku": "", "price": 999.0, "url": ""},
    ]
    changed = refresh.note_device_prices("pyaterochka", items)

    with store.connect() as conn:
        row = conn.execute("SELECT price, price_seen, in_stock FROM chain_products"
                           " WHERE chain='pyaterochka' AND sku='4359341'").fetchone()
        other = conn.execute("SELECT price FROM chain_products"
                             " WHERE chain='pyaterochka' AND sku='7777777'").fetchone()
    assert changed == 1, changed
    assert row["price"] == 139.99 and row["price_seen"], dict(row)
    assert row["in_stock"] == 0, "наличие с карточки («нет в наличии») — тому же артикулу"
    assert other["price"] is None, "чужим строкам цену не раздаём"



def test_dixy_catalog_style_url_gives_the_sku():
    """Адрес карточки Дикси из нашего же серверного поиска: /catalog/<раздел>/<подраздел>/<sku>/.

    Замер 21.10.2026: storefind отдаёт такие ссылки, а правило знало только /product/…, и
    цена с карточки Дикси не находила свою строку в базе.
    """
    from app.catalog import fromurl

    assert fromurl.sku_of(
        "dixy", "https://dixy.ru/catalog/molochnye-produkty-yaytsa/moloko/2000328870/"
    ) == "2000328870"
    # прежняя форма тоже должна работать
    assert fromurl.sku_of("dixy", "https://dixy.ru/product/moloko-parmalat-2000328870/") \
        == "2000328870"



def test_card_price_revives_a_row_the_crawl_had_closed(tmp_path, monkeypatch):
    """Строка, погашенная обходом (active=0), оживает: человек видел карточку сегодня.

    Замер 21.10.2026: у Магнита строки 8000104060 и 1000443710 лежали погашенными с 16.09,
    и цена с телефона не доезжала — `set_prices` пишет только `active=1`. Если человек
    открыл карточку, товар у сети есть, и строка должна жить.
    """
    from app.catalog import refresh, store

    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    with store.connect() as conn:
        conn.execute("INSERT INTO chain_products (chain, sku, name, price, first_seen,"
                     " last_seen, active) VALUES ('magnit','8000104060','Молоко neo',"
                     " 209.99, '2026-09-16', '2026-09-16', 0)")
        conn.commit()

    changed = refresh.note_device_prices("magnit", [
        {"ok": False, "sku": "", "price": 119.99, "in_stock": True,
         "url": "https://magnit.ru/product/8000104060-neo_high_protein"}])

    with store.connect() as conn:
        row = conn.execute("SELECT price, price_seen, in_stock, active FROM chain_products"
                           " WHERE chain='magnit' AND sku='8000104060'").fetchone()
    assert changed == 1, changed
    assert row["price"] == 119.99 and row["price_seen"], dict(row)
    assert row["in_stock"] == 1
    assert row["active"] == 1, "строка должна ожить: человек видел её сегодня"



def test_learn_takes_the_shop_name_not_ours(tmp_path, monkeypatch):
    """В каталог идёт имя СЕТИ с карточки (`found_name`), а не наше название позиции.

    Иначе база уверится, что сеть называет товар так же, как мы: у Дикси «Балийский
    цветок» против нашего «Bali Flower» — ровно этот случай (21.10.2026).
    """
    from app.catalog import refresh, store

    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()

    created = refresh.learn_device_rows("dixy", [{
        "sku": "2000642072",
        "name": "наше название из наряда",
        "found_name": "Молоко Neo High Protein, 0,5%, безлактозное (имя сети)",
        "price": 189.0,
    }])

    with store.connect() as conn:
        row = conn.execute("SELECT name, price FROM chain_products WHERE chain='dixy'"
                           " AND sku='2000642072'").fetchone()
    assert created == 1, created
    assert "имя сети" in row["name"], row["name"]
    assert "наше название" not in row["name"], "наше название в каталог сети не попадает"


def test_landed_position_also_revives_a_closed_row(tmp_path, monkeypatch):
    """Легшая позиция тоже оживляет погашенную строку: товар у сети есть.

    Симметрия с дорогой карточек: `set_prices` пишет только `active=1`, поэтому закрытая
    обходом строка теряла цену даже когда человек положил товар в корзину сети.
    """
    from app.catalog import refresh, store

    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    with store.connect() as conn:
        conn.execute("INSERT INTO chain_products (chain, sku, name, price, first_seen,"
                     " last_seen, active) VALUES ('pyaterochka','4359341','Сушка',"
                     " 199.0, '2026-09-16', '2026-09-16', 0)")
        conn.commit()

    changed = refresh.note_device_prices("pyaterochka", [
        {"ok": True, "sku": "4359341", "price": 205.5, "url": ""}])

    with store.connect() as conn:
        row = conn.execute("SELECT price, price_seen, active FROM chain_products"
                           " WHERE chain='pyaterochka' AND sku='4359341'").fetchone()
    assert changed == 1, changed
    assert row["price"] == 205.5 and row["price_seen"], dict(row)
    assert row["active"] == 1, "легшая позиция доказывает, что товар есть"
