"""SQLite: схема (раздел 4 спецификации), подключение, инициализация, сиды."""
from __future__ import annotations

import os
import sqlite3
from typing import Iterable

from app import config

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    brand TEXT,
    weight_g REAL,
    unit TEXT NOT NULL CHECK (unit IN ('pcs', 'kg')),
    category TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    delivery_fee REAL NOT NULL DEFAULT 0,
    free_delivery_from REAL NOT NULL DEFAULT 0,
    min_order REAL NOT NULL DEFAULT 0,
    connector_type TEXT NOT NULL DEFAULT 'stub'
);

CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank TEXT NOT NULL,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    percent REAL NOT NULL,
    cap_rub REAL NOT NULL,
    min_check_rub REAL NOT NULL DEFAULT 0,
    valid_from TEXT,
    valid_to TEXT,
    requires_activation INTEGER NOT NULL DEFAULT 0,
    activated INTEGER NOT NULL DEFAULT 1,
    cap_used REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS purchase_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    store_id INTEGER REFERENCES stores(id),
    product_id INTEGER REFERENCES products(id),
    raw_name TEXT NOT NULL,
    qty REAL NOT NULL,
    unit_price REAL NOT NULL,
    total REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS store_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    sku TEXT NOT NULL,
    raw_name TEXT NOT NULL,
    weight_g REAL,
    unit TEXT,
    url TEXT,
    UNIQUE (store_id, sku)
);

CREATE TABLE IF NOT EXISTS product_mapping (
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    store_product_id INTEGER NOT NULL REFERENCES store_products(id) ON DELETE CASCADE,
    confirmed INTEGER NOT NULL DEFAULT 0,
    confirmed_at TEXT,
    PRIMARY KEY (product_id, store_product_id)
);

CREATE TABLE IF NOT EXISTS store_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_product_id INTEGER NOT NULL REFERENCES store_products(id) ON DELETE CASCADE,
    price REAL NOT NULL,
    price_per_kg REAL,
    in_stock INTEGER NOT NULL DEFAULT 1,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS baskets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'average', 'history'))
);

CREATE TABLE IF NOT EXISTS basket_items (
    basket_id INTEGER NOT NULL REFERENCES baskets(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    qty REAL NOT NULL,
    PRIMARY KEY (basket_id, product_id)
);

CREATE TABLE IF NOT EXISTS variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    basket_id INTEGER NOT NULL REFERENCES baskets(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    total REAL NOT NULL,
    baseline REAL NOT NULL,
    savings_rub REAL NOT NULL,
    savings_pct REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS variant_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    variant_id INTEGER NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    card_id INTEGER REFERENCES cards(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty REAL NOT NULL,
    price REAL NOT NULL,
    discount REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_prices_sp ON store_prices(store_product_id, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_sp_store ON store_products(store_id);
CREATE INDEX IF NOT EXISTS idx_mapping_product ON product_mapping(product_id);
CREATE INDEX IF NOT EXISTS idx_history_date ON purchase_history(date);
"""

SEED_STORES: list[tuple] = [
    # code, name, delivery_fee, free_delivery_from, min_order, connector_type
    ("magnit", "Магнит", 149.0, 2000.0, 500.0, "magnit"),
    ("vkusvill", "ВкусВилл", 99.0, 1500.0, 400.0, "vkusvill"),
    ("pyaterochka", "Пятёрочка", 199.0, 2500.0, 600.0, "stub"),
]


def get_conn() -> sqlite3.Connection:
    path = config.db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> None:
    """Создаёт схему и справочник магазинов. Идемпотентно."""
    own = conn is None
    conn = conn or get_conn()
    try:
        conn.executescript(SCHEMA_SQL)
        for row in SEED_STORES:
            conn.execute(
                "INSERT INTO stores (code, name, delivery_fee, free_delivery_from, min_order, connector_type)"
                " VALUES (?,?,?,?,?,?) ON CONFLICT(code) DO NOTHING",
                row,
            )
        conn.commit()
    finally:
        if own:
            conn.close()


def query(sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(sql, tuple(params)).fetchall()


def execute(sql: str, params: Iterable = ()) -> int:
    with get_conn() as conn:
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur.lastrowid


if __name__ == "__main__":
    init_db()
    print("DB initialized at", config.db_path())
