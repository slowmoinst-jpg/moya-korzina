"""Очистка товарных карточек, заведённых старым импортом чеков."""
from __future__ import annotations

import sqlite3

from app import chains, repo
from app.catalog.nonfood import is_receipt_product


def clean_legacy(conn: sqlite3.Connection, *, apply: bool = False) -> dict[str, int]:
    """Отвязать исключённые строки и убрать карточки без других оснований для покупки.

    Чеки остаются целиком. Карточки архивируем, сохраняя их номера для старых
    клиентов и расчётов. Выбор пользователя из каталога или в ручной корзине,
    подтверждённая покупка в сети и явная подтверждённая привязка сохраняются.
    Вызывающая сторона ведёт транзакцию.
    """
    allowed = chains.orderable()
    stores = {code: sid for sid, code in conn.execute("SELECT id, code FROM stores") if code in allowed}
    rejected_rows: list[tuple[int]] = []
    resolved_rows: list[tuple[int, int]] = []
    rejected_products: set[int] = set()
    verified_products: set[int] = set()
    for row_id, pid, raw, name, code, seller in conn.execute(
        "SELECT h.id, p.id, h.raw_name, p.name, s.code, r.store "
        "FROM purchase_history h JOIN products p ON p.id=h.product_id "
        "LEFT JOIN stores s ON s.id=h.store_id LEFT JOIN receipts r ON r.key=h.receipt_key"
    ):
        receipt_code = code or (seller if seller in stores else chains.resolve(seller))
        if receipt_code in stores and is_receipt_product(raw, name):
            verified_products.add(pid)
            if code is None:
                resolved_rows.append((stores[receipt_code], row_id))
        else:
            rejected_products.add(pid)
            rejected_rows.append((row_id,))

    explicit = {pid for (pid,) in conn.execute(
        "SELECT id FROM products WHERE master_method='catalog' "
        "UNION SELECT bi.product_id FROM basket_items bi JOIN baskets b ON b.id=bi.basket_id "
        "WHERE b.source='manual' "
        "UNION SELECT bi.fallback_product_id FROM basket_items bi JOIN baskets b ON b.id=bi.basket_id "
        "WHERE b.source='manual' AND bi.fallback_product_id IS NOT NULL"
    )}
    explicit.update(pid for pid, code in conn.execute(
        "SELECT m.product_id, s.code FROM product_mapping m "
        "JOIN store_products sp ON sp.id=m.store_product_id JOIN stores s ON s.id=sp.store_id "
        "WHERE m.confirmed=1 AND (m.method IS NULL OR m.method='order') AND " + repo.real_sku_sql()
    ) if code in stores)
    active = {pid for (pid,) in conn.execute("SELECT id FROM products WHERE active=1")}
    archived = (rejected_products & active) - verified_products - explicit
    if apply:
        conn.executemany("UPDATE purchase_history SET store_id=? WHERE id=?", resolved_rows)
        conn.executemany("UPDATE purchase_history SET product_id=NULL WHERE id=?", rejected_rows)
        conn.executemany("UPDATE products SET active=0 WHERE id=?", ((pid,) for pid in archived))
        conn.executemany("DELETE FROM basket_items WHERE product_id=?", ((pid,) for pid in archived))
        conn.executemany("UPDATE basket_items SET fallback_product_id=NULL WHERE fallback_product_id=?",
                         ((pid,) for pid in archived))
    return {"unlinked": len(rejected_rows), "archived": len(archived), "resolved": len(resolved_rows)}
