"""Метка «цену видели» без самой цены — остаток старого сбоя, гасим при обновлении базы.

19.10.2026 у Самоката стёрли цены, оставив price_seen: большую часть вернули чтением
карточек, но 26 строк остались с меткой и пустой ценой — и проверка базы каждое утро
ругалась именно на них. Правило триггера keep_seen_price: законная чистка гасит price и
price_seen ВМЕСТЕ.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import store  # noqa: E402


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return tmp_path / "catalog.db"


def test_marker_without_price_is_cleared_on_upgrade(tmp_path, monkeypatch):
    path = _fresh(tmp_path, monkeypatch)
    with store.connect() as conn:
        conn.execute("INSERT INTO chain_products (chain, sku, name, price, price_seen,"
                     " first_seen, last_seen, active) VALUES"
                     " ('samokat','s1','Батон', NULL, '2026-10-19T19:51:10',"
                     " '2026-10-19T19:51:10','2026-10-19T19:51:10',1)")
        conn.execute("INSERT INTO chain_products (chain, sku, name, price, price_seen,"
                     " first_seen, last_seen, active) VALUES"
                     " ('samokat','s2','Хлеб', 49.9, '2026-10-19T19:51:10',"
                     " '2026-10-19T19:51:10','2026-10-19T19:51:10',1)")
        conn.execute("PRAGMA user_version = 7")     # как у базы до этой правки
        conn.commit()

    store.init()                                    # прогоняет шаг 8

    with store.connect() as conn:
        empty = conn.execute("SELECT price_seen FROM chain_products WHERE sku='s1'").fetchone()
        priced = conn.execute("SELECT price, price_seen FROM chain_products WHERE sku='s2'"
                              ).fetchone()
    assert empty["price_seen"] is None, "метка без цены должна гаснуть"
    assert priced["price"] == 49.9 and priced["price_seen"] is not None, \
        "у строки с ценой метку не трогаем"


def test_the_guard_does_not_touch_a_real_price(tmp_path, monkeypatch):
    """Вторая часть правила: строку с ценой обновление базы не меняет."""
    path = _fresh(tmp_path, monkeypatch)
    store.init()
    with store.connect() as conn:
        conn.execute("INSERT INTO chain_products (chain, sku, name, price, price_seen,"
                     " first_seen, last_seen, active) VALUES"
                     " ('lenta','l1','Молоко', 89.0, '2026-10-21T02:13:00',"
                     " '2026-10-21T02:13:00','2026-10-21T02:13:00',1)")
        conn.commit()
    store.init()
    with store.connect() as conn:
        row = conn.execute("SELECT price, price_seen FROM chain_products WHERE sku='l1'").fetchone()
    assert row["price"] == 89.0 and row["price_seen"] is not None
