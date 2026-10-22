"""Наблюдения приложения за ценами становятся историей единой базы.

Повод: у владельца в личной базе 101 наблюдение цены по Пятёрочке (86 артикулов) — а
сервер Пятёрочку не видит вовсе. Это единственные настоящие цены по ней.
"""
from __future__ import annotations

import sqlite3

import pytest

from app import users
from app.catalog import observations, store


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    with store.connect() as conn:
        conn.execute("insert into chain_products (chain, sku, name, active, first_seen, last_seen)"
                     " values ('pyaterochka','555','Сыр',1,?,?)",
                     (store.now(), store.now()))
        conn.commit()
    return store


def _personal(tmp_path, rows, name: str = "basket.db"):
    """Личная база с наблюдениями: (сеть, артикул, цена, когда)."""
    path = tmp_path / name
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE stores (id INTEGER PRIMARY KEY, code TEXT, name TEXT);
        CREATE TABLE store_products (id INTEGER PRIMARY KEY, store_id INT, sku TEXT);
        CREATE TABLE store_prices (store_product_id INT, price REAL, fetched_at TEXT);
    """)
    conn.execute("insert into stores (id, code, name) values (3, 'pyaterochka', 'Пятёрочка')")
    conn.execute("insert into store_products (id, store_id, sku) values (1, 3, '555')")
    conn.execute("insert into store_products (id, store_id, sku) values (2, 3, 'нет-такого')")
    for store_product_id, price, at in rows:
        conn.execute("insert into store_prices values (?,?,?)",
                     (store_product_id, price, at))
    conn.commit()
    conn.close()
    return str(path)


def test_an_observation_becomes_history(base, tmp_path, monkeypatch):
    path = _personal(tmp_path, [(1, 189.0, "2026-10-19T10:00:00"),
                                (1, 199.0, "2026-10-20T10:00:00")])
    monkeypatch.setattr(users, "list_workspaces", lambda: ["79990000001"])
    monkeypatch.setattr(users, "db_path_for", lambda phone: path)

    answer = observations.mirror()

    assert answer["chains"] == {"pyaterochka": 2}
    assert [row["price"] for row in base.price_history("pyaterochka", "555")] == [199.0, 189.0]


def test_the_same_observation_is_not_written_twice(base, tmp_path, monkeypatch):
    path = _personal(tmp_path, [(1, 189.0, "2026-10-19T10:00:00")])
    monkeypatch.setattr(users, "list_workspaces", lambda: ["79990000001"])
    monkeypatch.setattr(users, "db_path_for", lambda phone: path)

    observations.mirror()
    second = observations.mirror()

    assert second["chains"] == {}
    assert len(base.price_history("pyaterochka", "555")) == 1


def test_an_unknown_article_is_skipped(base, tmp_path, monkeypatch):
    """Цена артикула, которого в общей базе нет, не заводит товар-призрак."""
    path = _personal(tmp_path, [(2, 100.0, "2026-10-19T10:00:00")])
    monkeypatch.setattr(users, "list_workspaces", lambda: ["79990000001"])
    monkeypatch.setattr(users, "db_path_for", lambda phone: path)

    answer = observations.mirror()

    assert answer["chains"] == {} and answer["skipped_unknown"] == 1


def test_a_dry_run_changes_nothing(base, tmp_path, monkeypatch):
    path = _personal(tmp_path, [(1, 189.0, "2026-10-19T10:00:00")])
    monkeypatch.setattr(users, "list_workspaces", lambda: ["79990000001"])
    monkeypatch.setattr(users, "db_path_for", lambda phone: path)

    answer = observations.mirror(dry_run=True)

    assert answer["chains"] == {"pyaterochka": 1}
    assert base.price_history("pyaterochka", "555") == []


def test_a_broken_personal_base_does_not_stop_the_rest(base, tmp_path, monkeypatch):
    good = _personal(tmp_path, [(1, 189.0, "2026-10-19T10:00:00")])
    monkeypatch.setattr(users, "list_workspaces", lambda: ["битая", "живая"])
    monkeypatch.setattr(users, "db_path_for",
                        lambda phone: good if phone == "живая" else str(tmp_path / "нет.db"))

    answer = observations.mirror()

    assert answer["chains"] == {"pyaterochka": 1}


def test_the_demo_workplace_is_skipped(base, tmp_path, monkeypatch):
    """Цены демо выдуманы: в общую историю они попасть не должны.

    Замер 20.10.2026: у демо 2 576 «наблюдений» по ВкусВиллу на 12 артикулов — как
    настоящие они испортили бы «обычную цену».
    """
    demo = _personal(tmp_path, [(1, 999.0, "2026-10-19T10:00:00")], name="demo.db")
    real = _personal(tmp_path, [(1, 189.0, "2026-10-19T10:00:00")], name="real.db")

    monkeypatch.setattr(users, "list_workspaces", lambda: ["demo", "79990000001"])
    monkeypatch.setattr(users, "db_path_for",
                        lambda phone: demo if phone == "demo" else real)

    answer = observations.mirror()

    assert answer["chains"] == {"pyaterochka": 1}, "только настоящее рабочее место"
    assert [row["price"] for row in base.price_history("pyaterochka", "555")] == [189.0]


def test_usual_price_works_from_app_observations(base, tmp_path, monkeypatch):
    """Главный смысл: по телефонной сети появляется обычная цена."""
    path = _personal(tmp_path, [(1, 180.0, "2026-10-10T10:00:00"),
                                (1, 190.0, "2026-10-15T10:00:00"),
                                (1, 230.0, "2026-10-20T10:00:00")])
    monkeypatch.setattr(users, "list_workspaces", lambda: ["79990000001"])
    monkeypatch.setattr(users, "db_path_for", lambda phone: path)

    observations.mirror()

    assert base.usual_price("pyaterochka", "555") == 190.0
    assert base.usual_trusted("pyaterochka", "555") is True
