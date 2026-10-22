"""Проверка: доехали ли до единой базы цена и наличие, которые принёс ТЕЛЕФОН.

ЗАЧЕМ (21.10.2026). Цена из отчёта телефона раньше терялась на стороне приложения: у
Пятёрочки, Самоката и Дикси телефон просто жмёт «В корзину», ответов корзины нет, и в
отчёте не было `price` — сервер получал `price = NULL`. Приёмка при этом работала
(`accept_report` → `refresh.note_device_prices`/`note_device_stock`), и вопрос был только
в том, что отчёт не нёс числа.

Теперь телефон присылает `price`/`in_stock`/`url`, и нужна быстрая проверка «доехало или
нет» без чтения логов: строки, которые появились или обновились за последние часы, и у них
есть ли цена, дата цены, наличие.

Запуск на сервере:
    docker exec -i -w /app korzina-jobs python -m app.catalog.devicecheck
    docker exec -i -w /app korzina-jobs python -m app.catalog.devicecheck --hours 6
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta

from app.catalog import store

# Сети, которые сервер сам не обходит и где телефон — единственный источник данных.
PHONE_CHAINS = ("pyaterochka", "samokat", "magnit", "dixy")


def recent_rows(hours: float = 3.0, chains: tuple[str, ...] = PHONE_CHAINS) -> list[dict]:
    """Строки этих сетей, впервые увиденные в окне: что телефон принёс и что с ними."""
    store.init()
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="microseconds")
    placeholders = ",".join("?" * len(chains))
    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT chain, sku, name, price, price_seen, in_stock, first_seen, last_seen"
            " FROM chain_products WHERE chain IN (" + placeholders + ")"
            "   AND first_seen > ? ORDER BY chain, first_seen", (*chains, since)).fetchall()
    return [dict(row) for row in rows]


def updated_rows(hours: float = 3.0, chains: tuple[str, ...] = PHONE_CHAINS) -> list[dict]:
    """СТАРЫЕ строки, у которых в окне обновилась цена (price_seen), — не новые.

    ЗАЧЕМ (21.10.2026, просьба мобильной части). Считая только `first_seen`, команда не
    видела главного результата второй дороги: телефон открывает карточку уже известного
    товара и обновляет ему цену и наличие — строка при этом «старая». На прогоне 09:26 UTC
    так обновились magnit:8000104060 (119.99) и magnit:1000193728 (349.99), а счётчик
    «с ценой» не шелохнулся и выглядел как «не работает».
    """
    store.init()
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="microseconds")
    placeholders = ",".join("?" * len(chains))
    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT chain, sku, name, price, price_seen, in_stock, first_seen"
            " FROM chain_products WHERE chain IN (" + placeholders + ")"
            "   AND price_seen > ? AND first_seen <= ?"
            " ORDER BY price_seen DESC", (*chains, since, since)).fetchall()
    return [dict(row) for row in rows]


def summarize(rows: list[dict]) -> dict[str, dict[str, int]]:
    """По каждой сети: сколько строк, у скольких есть цена, у скольких наличие."""
    out: dict[str, dict[str, int]] = {}
    for row in rows:
        chain = str(row.get("chain"))
        stat = out.setdefault(chain, {"rows": 0, "priced": 0, "stocked": 0})
        stat["rows"] += 1
        if row.get("price") is not None:
            stat["priced"] += 1
        if row.get("in_stock") is not None:
            stat["stocked"] += 1
    return out


def render(rows: list[dict], hours: float, updates: list[dict] | None = None) -> str:
    updates = list(updates or [])
    if not rows and not updates:
        return f"за {hours:g} ч телефон не принёс ни одной новой строки и не обновил ни одной цены"
    lines: list[str] = []
    if rows:
        lines.append(f"строк, принесённых телефоном за {hours:g} ч: {len(rows)}")
        for chain, stat in sorted(summarize(rows).items()):
            lines.append(f"  {chain:<12} строк {stat['rows']:>4}"
                         f" | с ценой {stat['priced']:>4}"
                         f" | с наличием {stat['stocked']:>4}")
        lines.append("")
        lines.append("  примеры новых:")
        for row in rows[:10]:
            price = "—" if row.get("price") is None else f"{row['price']:.2f}"
            seen = str(row.get("price_seen") or "")[:19] or "—"
            stock = "—" if row.get("in_stock") is None else ("есть" if row["in_stock"] else "нет")
            lines.append(f"  {row['chain']:<12} {str(row['sku'])[:16]:<16} {price:>8}"
                         f" цена увидена {seen} | наличие {stock} | {str(row['name'])[:38]}")
    if updates:
        if lines:
            lines.append("")
        lines.append(f"ЦЕН ОБНОВЛЕНО за {hours:g} ч: {len(updates)} "
                     "(источник — сеть ИЛИ телефон; у обходимых сетей это в основном обход)")
        for row in updates[:10]:
            price = "—" if row.get("price") is None else f"{row['price']:.2f}"
            seen = str(row.get("price_seen") or "")[:19]
            lines.append(f"  {row['chain']:<12} {str(row['sku'])[:16]:<16} {price:>8}"
                         f" {seen} | {str(row['name'])[:40]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Что телефон принёс в базу и с какими числами")
    parser.add_argument("--hours", type=float, default=3.0, help="окно в часах")
    args = parser.parse_args(argv)
    print(render(recent_rows(args.hours), args.hours, updated_rows(args.hours)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



