"""Вклад телефона в единую базу виден в проверке базы.

ЗАМЕР 21.10.2026: телефон — единственный источник товаров у Пятёрочки и Самоката, за
сутки он дал 107 новых строк Самоката, 4 Пятёрочки, 3 Магнита. Но посчитать это можно
было только запросом руками: проверка базы знала лишь «что и когда телефон приносил»
(отметки), а не сколько НОВЫХ строк от него пришло.

Считаем по newest: строка впервые увидена в окне, а обхода этой сети в том же окне не
было (crawl_runs) — так «принёс телефон» не путается с ночным обходом.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import store  # noqa: E402


def _row(chain: str, sku: str, first_seen: str, item_id: int | None = None) -> None:
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO chain_products (chain, sku, name, first_seen, last_seen, active)"
            " VALUES (?,?,?,?,?,1)", (chain, sku, f"Товар {sku}", first_seen, first_seen))
        conn.commit()


def test_phone_rows_are_counted_and_crawled_ones_are_not(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    now = datetime.now()
    recent = (now - timedelta(hours=2)).isoformat(timespec="microseconds")
    old = (now - timedelta(days=3)).isoformat(timespec="microseconds")

    _row("samokat", "s1", recent)          # телефон принёс
    _row("samokat", "s2", recent)
    _row("pyaterochka", "p1", recent)      # телефон принёс
    _row("lenta", "l1", recent)            # ночной обход — не телефон
    _row("samokat", "s3", old)             # старое, не в окне

    with store.connect() as conn:          # обход Ленты был в том же окне
        conn.execute("INSERT INTO crawl_runs (chain, started_at, status, finished_at)"
                     " VALUES ('lenta', ?, 'ok', ?)", (recent, recent))
        conn.commit()

    brought = store.phone_new_rows(hours=24)

    assert brought.get("samokat") == 2, brought
    assert brought.get("pyaterochka") == 1, brought
    assert "lenta" not in brought, "строки ночного обхода — не вклад телефона"
