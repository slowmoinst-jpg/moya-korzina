"""Проверка «доехали ли цена и наличие из отчёта телефона».

21.10.2026 цена терялась на стороне приложения (в отчёт не уходила), и проверять это
приходилось запросом руками. Теперь есть команда `python -m app.catalog.devicecheck`,
и она должна отличать «телефон принёс строки с ценой» от «принёс только названия».
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import devicecheck, store  # noqa: E402


def _row(chain: str, sku: str, name: str, first_seen: str, price=None,
         price_seen=None, in_stock=None) -> None:
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO chain_products (chain, sku, name, price, price_seen, in_stock,"
            " first_seen, last_seen, active) VALUES (?,?,?,?,?,?,?,?,1)",
            (chain, sku, name, price, price_seen, in_stock, first_seen, first_seen))
        conn.commit()


def test_counts_price_and_stock_per_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    now = datetime.now()
    recent = (now - timedelta(hours=1)).isoformat(timespec="microseconds")
    old = (now - timedelta(days=2)).isoformat(timespec="microseconds")

    _row("samokat", "s1", "Молоко 1 л", recent, price=189.0, price_seen=recent, in_stock=1)
    _row("samokat", "s2", "Хлеб", recent)                      # без цены и наличия
    _row("pyaterochka", "p1", "Сушка", recent, in_stock=0)     # наличие «нет» — тоже данные
    _row("lenta", "l9", "Чужое", recent, price=10.0)           # не телефонная сеть
    _row("samokat", "s3", "Старое", old, price=5.0)            # вне окна

    rows = devicecheck.recent_rows(hours=3)
    stat = devicecheck.summarize(rows)

    assert stat["samokat"] == {"rows": 2, "priced": 1, "stocked": 1}, stat
    assert stat["pyaterochka"] == {"rows": 1, "priced": 0, "stocked": 1}, stat
    assert "lenta" not in stat, "ночной обход — не вклад телефона"


def test_render_says_when_there_is_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()

    text = devicecheck.render(devicecheck.recent_rows(hours=3), hours=3)

    assert "не принёс ни одной новой строки" in text, text


def test_updates_count_old_rows_whose_price_changed(tmp_path, monkeypatch):
    """Обновление цены у СТАРОЙ строки видно отдельно от «новых строк».

    Замер 21.10.2026: телефон открыл карточку известного товара и обновил цену
    (magnit:8000104060 → 119.99), но команда считала только `first_seen` и показывала
    «с ценой 3» — как будто ничего не произошло.
    """
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    now = datetime.now()
    recent = (now - timedelta(hours=1)).isoformat(timespec="microseconds")
    long_ago = (now - timedelta(days=30)).isoformat(timespec="microseconds")

    # старая строка, но цена обновлена час назад — это и есть работа второй дороги
    _row("magnit", "8000104060", "Молоко neo", long_ago, price=119.99, price_seen=recent)
    # старая строка без обновления — не считается
    _row("magnit", "1000443710", "Papia", long_ago, price=349.99, price_seen=None)

    fresh = devicecheck.recent_rows(hours=3)
    updates = devicecheck.updated_rows(hours=3)

    assert fresh == [], "новых строк нет"
    assert len(updates) == 1 and updates[0]["sku"] == "8000104060", updates
    text = devicecheck.render(fresh, 3, updates)
    assert "ЦЕН ОБНОВЛЕНО за 3 ч: 1" in text, text

