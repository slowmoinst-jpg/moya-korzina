"""Доступ к данным. Единственный слой, через который UI и модули ходят в SQLite."""
from __future__ import annotations

from datetime import datetime

from app.db import get_conn, init_db  # noqa: F401  (init_db реэкспортируется для UI)
from app.models import Card, Offer, Product, Store


def NOW() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- products ----------
def _product(r) -> Product:
    return Product(r["id"], r["name"], r["brand"], r["weight_g"], r["unit"], r["category"], bool(r["active"]))


def list_products(active_only: bool = True) -> list[Product]:
    sql = "SELECT * FROM products" + (" WHERE active = 1" if active_only else "") + " ORDER BY name"
    with get_conn() as c:
        return [_product(r) for r in c.execute(sql)]


def get_product(product_id: int) -> Product | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return _product(r) if r else None


def upsert_product(p: Product) -> int:
    with get_conn() as c:
        if p.id:
            c.execute(
                "UPDATE products SET name=?, brand=?, weight_g=?, unit=?, category=?, active=? WHERE id=?",
                (p.name, p.brand, p.weight_g, p.unit, p.category, int(p.active), p.id),
            )
            c.commit()
            return p.id
        cur = c.execute(
            "INSERT INTO products (name, brand, weight_g, unit, category, active) VALUES (?,?,?,?,?,?)",
            (p.name, p.brand, p.weight_g, p.unit, p.category, int(p.active)),
        )
        c.commit()
        return cur.lastrowid


def find_product_by_name(name: str) -> Product | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM products WHERE lower(name) = lower(?)", (name,)).fetchone()
    return _product(r) if r else None


# ---------- stores ----------
def _store(r) -> Store:
    return Store(r["id"], r["code"], r["name"], r["delivery_fee"], r["free_delivery_from"],
                 r["min_order"], r["connector_type"])


def list_stores() -> list[Store]:
    with get_conn() as c:
        return [_store(r) for r in c.execute("SELECT * FROM stores ORDER BY id")]


def get_store(code_or_id: str | int) -> Store | None:
    field = "code" if isinstance(code_or_id, str) else "id"
    with get_conn() as c:
        r = c.execute("SELECT * FROM stores WHERE " + field + " = ?", (code_or_id,)).fetchone()
    return _store(r) if r else None


# ---------- cards & offers ----------
def list_cards() -> list[Card]:
    with get_conn() as c:
        return [Card(r["id"], r["bank"], r["name"]) for r in c.execute("SELECT * FROM cards ORDER BY bank, name")]


def get_card(card_id: int) -> Card | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    return Card(r["id"], r["bank"], r["name"]) if r else None


def upsert_card(card: Card) -> int:
    with get_conn() as c:
        if card.id:
            c.execute("UPDATE cards SET bank=?, name=? WHERE id=?", (card.bank, card.name, card.id))
            c.commit()
            return card.id
        cur = c.execute("INSERT INTO cards (bank, name) VALUES (?,?)", (card.bank, card.name))
        c.commit()
        return cur.lastrowid


def delete_card(card_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM cards WHERE id=?", (card_id,))
        c.commit()


def _offer(r) -> Offer:
    return Offer(r["id"], r["card_id"], r["store_id"], r["percent"], r["cap_rub"], r["min_check_rub"],
                 r["valid_from"], r["valid_to"], bool(r["requires_activation"]), bool(r["activated"]), r["cap_used"])


def list_offers(store_id: int | None = None) -> list[Offer]:
    sql = "SELECT * FROM offers" + (" WHERE store_id = ?" if store_id else "") + " ORDER BY percent DESC"
    with get_conn() as c:
        return [_offer(r) for r in c.execute(sql, (store_id,) if store_id else ())]


def offers_for_store(store_id: int, day: str | None = None) -> list[Offer]:
    """Действующие акции магазина: учтены срок, активация и остаток лимита."""
    return [o for o in list_offers(store_id) if o.is_valid_on(day) and o.cap_left > 0]


def upsert_offer(o: Offer) -> int:
    vals = (o.card_id, o.store_id, o.percent, o.cap_rub, o.min_check_rub, o.valid_from, o.valid_to,
            int(o.requires_activation), int(o.activated), o.cap_used)
    with get_conn() as c:
        if o.id:
            c.execute("UPDATE offers SET card_id=?, store_id=?, percent=?, cap_rub=?, min_check_rub=?,"
                      " valid_from=?, valid_to=?, requires_activation=?, activated=?, cap_used=? WHERE id=?",
                      vals + (o.id,))
            c.commit()
            return o.id
        cur = c.execute("INSERT INTO offers (card_id, store_id, percent, cap_rub, min_check_rub, valid_from,"
                        " valid_to, requires_activation, activated, cap_used) VALUES (?,?,?,?,?,?,?,?,?,?)", vals)
        c.commit()
        return cur.lastrowid


def delete_offer(offer_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM offers WHERE id=?", (offer_id,))
        c.commit()


# ---------- store_products / mapping / prices ----------
def upsert_store_product(store_id: int, sku: str, raw_name: str, weight_g: float | None = None,
                         unit: str | None = None, url: str | None = None) -> int:
    with get_conn() as c:
        c.execute("INSERT INTO store_products (store_id, sku, raw_name, weight_g, unit, url) VALUES (?,?,?,?,?,?)"
                  " ON CONFLICT(store_id, sku) DO UPDATE SET raw_name=excluded.raw_name,"
                  " weight_g=excluded.weight_g, unit=excluded.unit, url=excluded.url",
                  (store_id, sku, raw_name, weight_g, unit, url))
        c.commit()
        return c.execute("SELECT id FROM store_products WHERE store_id=? AND sku=?", (store_id, sku)).fetchone()["id"]


def confirm_mapping(product_id: int, store_product_id: int, confirmed: bool = True) -> None:
    with get_conn() as c:
        c.execute("INSERT INTO product_mapping (product_id, store_product_id, confirmed, confirmed_at)"
                  " VALUES (?,?,?,?) ON CONFLICT(product_id, store_product_id)"
                  " DO UPDATE SET confirmed=excluded.confirmed, confirmed_at=excluded.confirmed_at",
                  (product_id, store_product_id, int(confirmed), NOW() if confirmed else None))
        c.commit()


def drop_mapping(product_id: int, store_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM product_mapping WHERE product_id=? AND store_product_id IN"
                  " (SELECT id FROM store_products WHERE store_id=?)", (product_id, store_id))
        c.commit()


def confirmed_mapping(product_id: int, store_id: int) -> dict | None:
    """Подтверждённое сопоставление эталона в магазине или None."""
    with get_conn() as c:
        r = c.execute(
            "SELECT sp.* FROM product_mapping m JOIN store_products sp ON sp.id = m.store_product_id"
            " WHERE m.product_id=? AND sp.store_id=? AND m.confirmed=1 LIMIT 1", (product_id, store_id)).fetchone()
    return dict(r) if r else None


def mapping_matrix() -> dict[tuple[int, int], bool]:
    """(product_id, store_id) -> confirmed. Для экрана Номенклатура."""
    with get_conn() as c:
        rows = c.execute("SELECT m.product_id, sp.store_id, MAX(m.confirmed) AS confirmed FROM product_mapping m"
                         " JOIN store_products sp ON sp.id = m.store_product_id"
                         " GROUP BY m.product_id, sp.store_id").fetchall()
    return {(r["product_id"], r["store_id"]): bool(r["confirmed"]) for r in rows}


def save_price(store_product_id: int, price: float, price_per_kg: float | None = None,
               in_stock: bool = True, fetched_at: str | None = None) -> None:
    """Снимок цены — только вставка, история не перезаписывается."""
    with get_conn() as c:
        c.execute("INSERT INTO store_prices (store_product_id, price, price_per_kg, in_stock, fetched_at)"
                  " VALUES (?,?,?,?,?)", (store_product_id, price, price_per_kg, int(in_stock), fetched_at or NOW()))
        c.commit()


def latest_price(store_product_id: int) -> dict | None:
    with get_conn() as c:
        r = c.execute("SELECT * FROM store_prices WHERE store_product_id=? ORDER BY fetched_at DESC, id DESC LIMIT 1",
                      (store_product_id,)).fetchone()
    return dict(r) if r else None


def latest_price_for(product_id: int, store_id: int) -> dict | None:
    """Актуальная цена эталона в магазине по подтверждённому сопоставлению."""
    sp = confirmed_mapping(product_id, store_id)
    return latest_price(sp["id"]) if sp else None


def price_history(product_id: int, store_id: int) -> list[dict]:
    sp = confirmed_mapping(product_id, store_id)
    if not sp:
        return []
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM store_prices WHERE store_product_id=? ORDER BY fetched_at", (sp["id"],))]


# ---------- baskets ----------
def create_basket(name: str, source: str = "manual") -> int:
    with get_conn() as c:
        cur = c.execute("INSERT INTO baskets (name, created_at, source) VALUES (?,?,?)", (name, NOW(), source))
        c.commit()
        return cur.lastrowid


def list_baskets() -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM baskets ORDER BY created_at DESC")]


def set_basket_item(basket_id: int, product_id: int, qty: float) -> None:
    with get_conn() as c:
        if qty <= 0:
            c.execute("DELETE FROM basket_items WHERE basket_id=? AND product_id=?", (basket_id, product_id))
        else:
            c.execute("INSERT INTO basket_items (basket_id, product_id, qty) VALUES (?,?,?)"
                      " ON CONFLICT(basket_id, product_id) DO UPDATE SET qty=excluded.qty",
                      (basket_id, product_id, qty))
        c.commit()


def basket_items(basket_id: int) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT bi.product_id, bi.qty, p.name, p.unit, p.weight_g, p.brand FROM basket_items bi"
            " JOIN products p ON p.id = bi.product_id WHERE bi.basket_id=? ORDER BY p.name", (basket_id,))]


def clear_basket(basket_id: int) -> None:
    with get_conn() as c:
        c.execute("DELETE FROM basket_items WHERE basket_id=?", (basket_id,))
        c.commit()


# ---------- purchase history ----------
def add_history_row(date: str, store_id: int | None, product_id: int | None, raw_name: str,
                    qty: float, unit_price: float, total: float) -> int:
    with get_conn() as c:
        cur = c.execute("INSERT INTO purchase_history (date, store_id, product_id, raw_name, qty, unit_price, total)"
                        " VALUES (?,?,?,?,?,?,?)", (date, store_id, product_id, raw_name, qty, unit_price, total))
        c.commit()
        return cur.lastrowid


def list_history(date_from: str | None = None, date_to: str | None = None,
                 store_id: int | None = None) -> list[dict]:
    sql = ("SELECT h.*, s.name AS store_name, s.code AS store_code,"
           " p.name AS product_name, p.unit AS unit FROM purchase_history h"
           " LEFT JOIN stores s ON s.id = h.store_id LEFT JOIN products p ON p.id = h.product_id WHERE 1=1")
    params: list = []
    if date_from:
        sql += " AND h.date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND h.date <= ?"
        params.append(date_to)
    if store_id:
        sql += " AND h.store_id = ?"
        params.append(store_id)
    with get_conn() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY h.date DESC, h.id", tuple(params))]


# ---------- variants ----------
def save_variant(basket_id: int, total: float, baseline: float, savings_rub: float, savings_pct: float,
                 lines: list[tuple]) -> int:
    """lines: (store_id, card_id, product_id, qty, price, discount)"""
    with get_conn() as c:
        cur = c.execute("INSERT INTO variants (basket_id, created_at, total, baseline, savings_rub, savings_pct)"
                        " VALUES (?,?,?,?,?,?)", (basket_id, NOW(), total, baseline, savings_rub, savings_pct))
        vid = cur.lastrowid
        c.executemany("INSERT INTO variant_lines (variant_id, store_id, card_id, product_id, qty, price, discount)"
                      " VALUES (?,?,?,?,?,?,?)", [(vid,) + tuple(ln) for ln in lines])
        c.commit()
        return vid


def list_variants(basket_id: int) -> list[dict]:
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM variants WHERE basket_id=? ORDER BY created_at DESC, total", (basket_id,))]
