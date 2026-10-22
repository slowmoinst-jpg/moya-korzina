"""Точка снимка цены: миграция старой базы, запись и выбор цены нужной точки.

ЗАЧЕМ. У Ленты, Магнита и METRO цена своя в каждой точке, а снимок её не помнил:
цена одного адреса выглядела как цена другого. Теперь у снимка есть point_key
(«широта,долгота» места человека) и mode (доставка/самовывоз), а свежесть
(freshness.price_for) сначала ищет снимок этой точки и только затем общий —
снимок ЧУЖОЙ точки не подставляется никогда.

Работа на временной БД: app.config.db_path подменяется, рабочая data/basket.db
не затрагивается.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import config, freshness, repo                    # noqa: E402
from app.db import init_db                                 # noqa: E402
from app.models import PriceSnapshot, Product              # noqa: E402


def _ago(minutes: float) -> str:
    """Отметка времени в прошлом — свежесть считает от «сейчас», а не от даты в файле."""
    return (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")



@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "point.db")
    monkeypatch.setattr(config, "db_path", lambda: path)
    init_db()
    assert config.db_path() != os.path.join(ROOT, "data", "basket.db")
    return path


def _priced(chain: str = "vkusvill", sku: str = "SKU-1") -> tuple:
    """(магазин, эталон, строка сети) с подтверждённым сопоставлением."""
    store = repo.get_store(chain)
    pid = repo.upsert_product(Product(None, "Молоко 1 л", weight_g=1000.0, unit="pcs"))
    sp = repo.upsert_store_product(store.id, sku, "Молоко 1 л", weight_g=1000.0, unit="pcs")
    repo.confirm_mapping(pid, sp, confirmed=True)
    return store, pid, sp


# ---------- миграция ----------
def test_an_old_base_gets_the_point_columns(tmp_path, monkeypatch):
    """База прежней версии: init_db досыпает point_key/mode, индекс и не теряет снимки."""
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.executescript(
        "CREATE TABLE store_prices ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " store_product_id INTEGER NOT NULL,"
        " price REAL NOT NULL,"
        " price_per_kg REAL,"
        " in_stock INTEGER NOT NULL DEFAULT 1,"
        " fetched_at TEXT NOT NULL,"
        " source TEXT);"
        "INSERT INTO store_prices (store_product_id, price, in_stock, fetched_at)"
        " VALUES (1, 99.0, 1, '2026-10-01T10:00:00');")
    raw.commit()
    raw.close()

    monkeypatch.setattr(config, "db_path", lambda: path)
    init_db()

    with sqlite3.connect(path) as conn:
        have = {row[1] for row in conn.execute("PRAGMA table_info(store_prices)")}
        index = conn.execute("SELECT name FROM sqlite_master WHERE type='index'"
                             " AND name='idx_prices_point'").fetchone()
        price = conn.execute("SELECT price FROM store_prices").fetchone()[0]

    assert {"point_key", "mode"} <= have, have
    assert index is not None, "индекс по point_key не создан"
    assert price == 99.0, "старый снимок не тронут"


# ---------- запись ----------
def test_save_price_remembers_the_point_and_the_mode(db):
    store, pid, sp = _priced()

    repo.save_price(sp, 89.0, point_key="55.75700,37.61700", mode="delivery")

    snap = repo.latest_price(sp)
    assert snap["point_key"] == "55.75700,37.61700"
    assert snap["mode"] == "delivery"
    # latest_price_for отдаёт к снимку единицу/фасовку/название — вместе с точкой.
    attached = repo.latest_price_for(pid, store.id)
    assert attached["point_key"] == "55.75700,37.61700"
    assert attached["mode"] == "delivery"


def test_a_snapshot_without_a_point_stays_general(db):
    _, _, sp = _priced()

    repo.save_price(sp, 89.0)

    snap = repo.latest_price(sp)
    assert snap["point_key"] is None
    assert snap["mode"] is None


def test_last_price_moment_is_the_newest_live_snapshot(db):
    """Подпись «цена от HH:MM» — по живым снимкам, справочная из CSV не в счёт."""
    store, _, sp = _priced()
    old, live = _ago(120), _ago(60)

    repo.save_price(sp, 89.0, fetched_at=old)
    repo.save_price(sp, 90.0, fetched_at=live)
    repo.save_price(sp, 1.0, fetched_at=_ago(1), source=freshness.REFERENCE)

    assert repo.last_price_moment(store.id) == live


def test_last_price_moment_sees_only_the_asked_point_products_and_live_prices(db):
    """«Витрина у адреса» — это снимки ЭТИХ товаров у ЭТОЙ точки, и только живые.

    Снимок без точки — общий, чужой точки — другого магазина, справочный из CSV —
    не с полки. Ни один из них не должен выдаваться за витрину у адреса.
    """
    store, milk, milk_sp = _priced()
    bread = repo.upsert_product(Product(None, "Хлеб Бородинский", weight_g=400.0, unit="pcs"))
    bread_sp = repo.upsert_store_product(store.id, "SKU-2", "Хлеб Бородинский",
                                         weight_g=400.0, unit="pcs")
    repo.confirm_mapping(bread, bread_sp, confirmed=True)

    point = "55.75000,37.60000"
    other = "59.93000,30.31000"
    milk_point, milk_general, bread_general = _ago(30), _ago(10), _ago(5)
    milk_other = _ago(0.5)
    repo.save_price(milk_sp, 90.0, point_key=point, fetched_at=milk_point)
    repo.save_price(milk_sp, 80.0, fetched_at=milk_general)               # без точки
    repo.save_price(bread_sp, 50.0, fetched_at=bread_general)             # без точки, свежее
    repo.save_price(milk_sp, 1.0, point_key=point, fetched_at=_ago(1),
                    source=freshness.REFERENCE)                            # каталог у точки
    repo.save_price(bread_sp, 1.0, fetched_at=_ago(1),
                    source=freshness.REFERENCE)                            # каталог без точки
    repo.save_price(milk_sp, 95.0, point_key=other, fetched_at=milk_other)

    # Без аргументов — прежняя форма: общие (без точки) живые снимки сети.
    assert repo.last_price_moment(store.id) == bread_general
    # Товары корзины: хлеб не в счёте, даже если его общий снимок свежее.
    assert repo.last_price_moment(store.id, product_ids=[milk]) == milk_general
    # Точка: ни общего снимка, ни каталога, ни чужой точки.
    assert repo.last_price_moment(store.id, point_key=point) == milk_point
    assert repo.last_price_moment(store.id, product_ids=[milk], point_key=point) == milk_point
    assert repo.last_price_moment(store.id, point_key=other) == milk_other
    # Товаров нет — и момента нет, а не «любой снимок сети».
    assert repo.last_price_moment(store.id, product_ids=[], point_key=point) is None


# ---------- свежесть ----------
def test_freshness_prefers_the_snapshot_of_the_asked_point(db):
    store, pid, sp = _priced()
    repo.save_price(sp, 100.0, fetched_at=_ago(10))                 # общий снимок
    repo.save_price(sp, 90.0, point_key="55.75000,37.60000", fetched_at=_ago(5))

    snap = freshness.price_for(pid, store.id, point_key="55.75000,37.60000")

    assert snap["price"] == 90.0


def test_freshness_falls_back_to_the_general_snapshot(db):
    """У этой точки своего снимка нет — берётся общий, а не чужой."""
    store, pid, sp = _priced()
    repo.save_price(sp, 100.0, fetched_at=_ago(10))
    repo.save_price(sp, 90.0, point_key="55.75000,37.60000", fetched_at=_ago(5))

    snap = freshness.price_for(pid, store.id, point_key="59.93000,30.31000")

    assert snap["price"] == 100.0


def test_a_snapshot_of_another_point_is_not_substituted(db):
    """Только чужая точка и нет общего снимка — цены нет вовсе, а не чужая цена."""
    store, pid, sp = _priced()
    repo.save_price(sp, 90.0, point_key="55.75000,37.60000")

    assert freshness.price_for(pid, store.id, point_key="59.93000,30.31000") is None

    repo.save_price(sp, 100.0)                                      # теперь есть общий
    assert freshness.price_for(pid, store.id, point_key="59.93000,30.31000")["price"] == 100.0


def test_without_a_point_the_old_behaviour_is_kept(db):
    store, pid, sp = _priced()
    repo.save_price(sp, 90.0, point_key="55.75000,37.60000")

    assert freshness.price_for(pid, store.id)["price"] == 90.0


# ---------- матчер заполняет точку ----------
def test_refresh_records_the_point_of_the_persons_place(db, monkeypatch):
    """Живое обновление пишет точку человека и вид цены в снимок."""
    from app import location
    from app.matcher import matcher

    # Лента берёт место по адресу, в сеть при этом не ходит (в отличие от Магнита).
    repo.set_setting(location.KEY_ADDRESS, "Москва, Ходынский бульвар 4")
    repo.set_setting(location.KEY_POINT, "55.75700,37.61700")
    _, pid, sp = _priced("lenta", "L-1")

    class Conn:
        def get_prices(self, skus):
            return [PriceSnapshot("lenta", sku, 123.0) for sku in skus]

    monkeypatch.setattr(matcher, "_get_connector", lambda code, location=None: Conn())

    result = matcher.refresh_prices([pid], ["lenta"])

    assert result["updated"] == 1
    snap = repo.latest_price(sp)
    assert snap["point_key"] == "55.75700,37.61700"
    assert snap["mode"] == "delivery"


def test_without_a_place_the_snapshot_has_no_point(db, monkeypatch):
    """Сеть, которая цены по адресу не считает, пишет общий снимок без точки."""
    from app.matcher import matcher

    _, pid, sp = _priced("vkusvill", "V-1")

    class Conn:
        def get_prices(self, skus):
            return [PriceSnapshot("vkusvill", sku, 77.0) for sku in skus]

    monkeypatch.setattr(matcher, "_get_connector", lambda code, location=None: Conn())

    matcher.refresh_prices([pid], ["vkusvill"])

    snap = repo.latest_price(sp)
    assert snap["point_key"] is None
    assert snap["mode"] is None
