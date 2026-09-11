"""Наполнение базы демо-данными: эталоны, карты и акции, тестовый чек, тестовая корзина.

Запуск из корня проекта:
    .venv\\Scripts\\python.exe tools/seed.py
Идемпотентен: повторный запуск не плодит дубли.
"""
from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import repo  # noqa: E402
from app.db import init_db  # noqa: E402
from app.models import Card, Offer, Product  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RECEIPT_DATE = "2026-08-16"
RECEIPT_STORE = "pyaterochka"
BASKET_NAME = "Тестовая корзина (чек 16.08.2026)"


def _rows(filename: str) -> list[dict]:
    with open(os.path.join(DATA, filename), encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def seed_products() -> dict[str, int]:
    ids: dict[str, int] = {}
    for r in _rows("seed_products.csv"):
        existing = repo.find_product_by_name(r["name"])
        if existing:
            ids[r["name"]] = existing.id
            continue
        ids[r["name"]] = repo.upsert_product(Product(
            id=None,
            name=r["name"],
            brand=r["brand"] or None,
            weight_g=float(r["weight_g"]) if r["weight_g"] else None,
            unit=r["unit"],
            category=r["category"] or None,
        ))
    return ids


def seed_cards_and_offers() -> None:
    cards = {(c.bank, c.name): c.id for c in repo.list_cards()}
    stores = {s.code: s.id for s in repo.list_stores()}
    existing = {(o.card_id, o.store_id) for o in repo.list_offers()}
    for r in _rows("seed_offers.csv"):
        key = (r["bank"], r["card"])
        if key not in cards:
            cards[key] = repo.upsert_card(Card(None, r["bank"], r["card"]))
        store_id = stores.get(r["store_code"])
        if not store_id or (cards[key], store_id) in existing:
            continue
        repo.upsert_offer(Offer(
            id=None,
            card_id=cards[key],
            store_id=store_id,
            percent=float(r["percent"]),
            cap_rub=float(r["cap_rub"]),
            min_check_rub=float(r["min_check_rub"]),
            valid_from=r["valid_from"] or None,
            valid_to=r["valid_to"] or None,
            requires_activation=r["requires_activation"] == "1",
            activated=r["activated"] == "1",
            cap_used=float(r["cap_used"] or 0),
        ))
        existing.add((cards[key], store_id))


def seed_receipt(product_ids: dict[str, int]) -> float:
    """Чек из раздела 8 спецификации в purchase_history. Возвращает итог."""
    store_id = repo.get_store(RECEIPT_STORE).id
    already = repo.list_history(date_from=RECEIPT_DATE, date_to=RECEIPT_DATE, store_id=store_id)
    total = 0.0
    for r in _rows("seed_receipt.csv"):
        qty, unit_price = float(r["qty"]), float(r["unit_price"])
        line_total = round(qty * unit_price, 2)
        total += line_total
        if not already:
            repo.add_history_row(RECEIPT_DATE, store_id, product_ids.get(r["name"]),
                                 r["name"], qty, unit_price, line_total)
    return round(total, 2)


def seed_basket(product_ids: dict[str, int]) -> int:
    for b in repo.list_baskets():
        if b["name"] == BASKET_NAME:
            basket_id = b["id"]
            break
    else:
        basket_id = repo.create_basket(BASKET_NAME, source="history")
    for r in _rows("seed_receipt.csv"):
        pid = product_ids.get(r["name"])
        if pid:
            repo.set_basket_item(basket_id, pid, float(r["qty"]))
    return basket_id


def main() -> None:
    init_db()
    product_ids = seed_products()
    seed_cards_and_offers()
    receipt_total = seed_receipt(product_ids)
    basket_id = seed_basket(product_ids)
    print(f"Эталонов: {len(product_ids)}")
    print(f"Карт: {len(repo.list_cards())}, акций: {len(repo.list_offers())}")
    print(f"Чек {RECEIPT_DATE}: строк {len(repo.list_history(date_from=RECEIPT_DATE, date_to=RECEIPT_DATE))},"
          f" итог {receipt_total:.2f} (ожидается 3690.40)")
    print(f"Корзина #{basket_id}: позиций {len(repo.basket_items(basket_id))}")


if __name__ == "__main__":
    main()
