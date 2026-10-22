"""Наблюдения приложения за ценами — в историю цен единой базы.

ЗАЧЕМ. Приложение, пока человек собирает корзину, видит цены в его сети: они ложатся в
его личную базу (`store_prices`: сеть, артикул сети, цена, когда). Для сетей, куда сервер
не пускают (Пятёрочка, Самокат), это ЕДИНСТВЕННЫЕ наблюдения цены — и до сих пор они
оставались в базе человека, не попадая в общую историю.

ЗАМЕР 20.10.2026, база владельца: metro 417 цен / 149 артикулов, dixy 400/144,
magnit 297/104, lenta 224/101, vkusvill 161/53, **pyaterochka 101/86** — у Пятёрочки
сервер не видит ничего, и эти 101 наблюдение бесценны для «обычной цены».

Как это работает: личная база → (сеть, артикул, цена, время) → `price_history` общей базы,
только если такой артикул в общей базе уже есть. Повтор ничего не дублирует: ключ
наблюдения — (сеть, артикул, время).

    python -m app.catalog.observations            перенести наблюдения всех рабочих мест
    python -m app.catalog.observations --dry-run  только посчитать
"""
from __future__ import annotations

import argparse
import sqlite3

from app import users
from app.catalog import store

# Сколько наблюдений за раз берём из одной личной базы. Цены копятся годами, а переносить
# их пачками безопаснее, чем одной огромной записью.
BATCH = 5_000


def _personal_rows(path: str) -> list[tuple[str, str, float, str]]:
    """(сеть, артикул, цена, когда) из личной базы: только то, что видно в витринах."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT s.code AS chain, p.sku AS sku, sp.price AS price, sp.fetched_at AS at
            FROM store_prices sp
            JOIN store_products p ON p.id = sp.store_product_id
            JOIN stores s ON s.id = p.store_id
            WHERE sp.price IS NOT NULL AND sp.price > 0
              AND p.sku IS NOT NULL AND p.sku <> ''
              AND sp.fetched_at IS NOT NULL
            ORDER BY sp.fetched_at
            LIMIT ?""", (BATCH,)).fetchall()
    finally:
        conn.close()
    out: list[tuple[str, str, float, str]] = []
    for row in rows:
        try:
            price = float(row["price"])
        except (TypeError, ValueError):
            continue
        if price <= 0 or price > 1_000_000:
            continue
        out.append((str(row["chain"]), str(row["sku"]), price, str(row["at"])))
    return out


def mirror(phones: list[str] | None = None, dry_run: bool = False) -> dict:
    """Перенести наблюдения из личных баз в историю цен. Возвращает счёт по сетям.

    ДЕМО-РАБОЧЕЕ МЕСТО ПРОПУСКАЕТСЯ. Его цены — выдуманные (замер 20.10.2026: у демо
    2 576 «наблюдений» по ВкусВиллу на 12 артикулов, и они пошли бы в общую историю как
    настоящие). Настоящее рабочее место — то, у которого номер телефона; демо живёт под
    именем, телефоном не являющимся.
    """
    known = store.known_pairs()
    tally: dict[str, int] = {}
    skipped = 0
    for phone in (phones or users.list_workspaces()):
        if users.normalize_phone(phone) == users.DEMO:
            continue
        path = users.db_path_for(phone)
        try:
            rows = _personal_rows(path)
        except Exception:  # noqa: BLE001 — битая личная база не должна останавливать остальные
            continue
        for chain, sku, price, at in rows:
            if (chain, sku) not in known:
                skipped += 1
                continue
            if dry_run:
                tally[chain] = tally.get(chain, 0) + 1
                continue
            if store.record_observation(chain, sku, price, at):
                tally[chain] = tally.get(chain, 0) + 1
    return {"chains": tally, "skipped_unknown": skipped}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Наблюдения приложения — в историю цен")
    parser.add_argument("--dry-run", action="store_true", help="только посчитать")
    parser.add_argument("--phone", action="append", help="только это рабочее место")
    args = parser.parse_args(argv)

    answer = mirror(phones=args.phone, dry_run=args.dry_run)
    total = sum(answer["chains"].values())
    for chain, count in sorted(answer["chains"].items(), key=lambda kv: -kv[1]):
        print(f"  {chain:<12} {count}")
    print(f"итого {'посчитано' if args.dry_run else 'перенесено'}: {total}"
          f" | пропущено (артикула нет в общей базе): {answer['skipped_unknown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
