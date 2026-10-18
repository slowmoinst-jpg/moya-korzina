"""Плагин pytest: подменить чистку item_last вариантом из ITEMLAST_VARIANT.

asis — ничего не менять; literal — исправление находки дословно (в
_keep_last_records после вставки); age3 — «старше 3 пересборок»; tail — чистка
в конце match_all по спящим строкам. Рабочую копию не трогает: подмена в памяти.
"""
from __future__ import annotations

import os


def pytest_configure(config):
    variant = os.environ.get("ITEMLAST_VARIANT", "asis")
    if variant == "asis":
        return
    from app.catalog import refresh, store

    if variant == "literal":
        orig = refresh._keep_last_records

        def patched(conn, kept, stamp):
            orig(conn, kept, stamp)
            conn.execute("DELETE FROM item_last WHERE NOT EXISTS (SELECT 1 FROM chain_products p"
                         " WHERE p.prev_item_id = item_last.id AND p.item_id IS NULL)"
                         " AND NOT EXISTS (SELECT 1 FROM item_aliases a WHERE a.old_id = item_last.id)")
        refresh._keep_last_records = patched
    elif variant == "age3":
        orig = refresh._keep_last_records

        def patched(conn, kept, stamp):
            orig(conn, kept, stamp)
            runs = [r[0] for r in conn.execute("SELECT started_at FROM match_runs ORDER BY id DESC LIMIT 4")]
            if len(runs) > 3:
                conn.execute("DELETE FROM item_last WHERE since < ?", (runs[-1],))
        refresh._keep_last_records = patched
    elif variant == "tail":
        orig_all = refresh.match_all

        def patched_all(progress=None):
            out = orig_all(progress)
            with store.connect() as conn:
                conn.execute("DELETE FROM item_last WHERE NOT EXISTS (SELECT 1 FROM chain_products p"
                             " WHERE p.prev_item_id = item_last.id AND p.item_id IS NULL)")
                conn.commit()
            return out
        refresh.match_all = patched_all
    else:
        raise SystemExit(f"неизвестный вариант {variant}")
    print(f"\n[fixplug] item_last: вариант {variant}")
