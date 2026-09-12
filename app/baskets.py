"""Сборка корзины из истории покупок.

Два способа, оба из раздела 5.3 спецификации:

- «как в прошлый раз» — копия последней покупки: берём самый свежий чек и переносим
  его позиции с теми же количествами;
- «по среднему за месяц» — сколько товара семья берёт за месяц в среднем. Считаем по числу
  месяцев, в которых вообще были покупки, а не по календарю: два чека в одном месяце дают
  сумму за месяц, а не среднее двух.

Штучные округляем до целого — «полторы пачки» в корзине бессмысленны. Весовые оставляем
дробными с точностью до грамма.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from app import repo

LAST = "history"
AVERAGE = "average"


def _round_qty(qty: float, unit: str | None) -> float:
    if (unit or "pcs") == "kg":
        return round(qty, 3)
    return float(round(qty))


def last_purchase_items(store_id: int | None = None) -> tuple[str | None, list[dict]]:
    """Позиции самой свежей покупки: (дата, строки). Строки — product_id, qty, unit, name."""
    rows = [r for r in repo.list_history(store_id=store_id) if r.get("product_id")]
    if not rows:
        return None, []
    last_date = max(str(r["date"]) for r in rows)

    merged: dict[int, dict] = {}
    for row in rows:
        if str(row["date"]) != last_date:
            continue
        pid = int(row["product_id"])
        item = merged.setdefault(pid, {"product_id": pid, "qty": 0.0,
                                       "unit": row.get("unit") or "pcs",
                                       "name": row.get("product_name") or row.get("raw_name")})
        item["qty"] += float(row.get("qty") or 0)
    for item in merged.values():
        item["qty"] = _round_qty(item["qty"], item["unit"])
    return last_date, [i for i in merged.values() if i["qty"] > 0]


def average_month_items(store_id: int | None = None) -> tuple[int, list[dict]]:
    """Среднемесячное количество по каждому товару: (число месяцев, строки)."""
    rows = [r for r in repo.list_history(store_id=store_id) if r.get("product_id")]
    if not rows:
        return 0, []

    months: set[str] = set()
    totals: dict[int, dict] = defaultdict(lambda: {"qty": 0.0, "unit": "pcs", "name": ""})
    for row in rows:
        date = str(row.get("date") or "")
        if len(date) >= 7:
            months.add(date[:7])
        pid = int(row["product_id"])
        entry = totals[pid]
        entry["qty"] += float(row.get("qty") or 0)
        entry["unit"] = row.get("unit") or "pcs"
        entry["name"] = row.get("product_name") or row.get("raw_name")

    count = max(1, len(months))
    items = []
    for pid, entry in totals.items():
        qty = _round_qty(entry["qty"] / count, entry["unit"])
        if qty > 0:
            items.append({"product_id": pid, "qty": qty, "unit": entry["unit"], "name": entry["name"]})
    return len(months), items


def build_from_history(kind: str, name: str | None = None, store_id: int | None = None) -> dict:
    """Создаёт корзину из истории. kind: 'history' — как в прошлый раз, 'average' — по среднему.

    Возвращает {'basket_id', 'items', 'months', 'date', 'skipped'}.
    """
    if kind == AVERAGE:
        months, items = average_month_items(store_id)
        date = None
        title = name or f"По среднему за месяц ({datetime.now():%d.%m.%Y})"
    else:
        kind = LAST
        date, items = last_purchase_items(store_id)
        months = 0
        title = name or (f"Как в прошлый раз ({date})" if date else "Как в прошлый раз")

    if not items:
        return {"basket_id": None, "items": [], "months": months, "date": date, "skipped": 0}

    known = {p.id for p in repo.list_products(active_only=False)}
    usable = [i for i in items if i["product_id"] in known]

    basket_id = repo.create_basket(title, source=kind)
    for item in usable:
        repo.set_basket_item(basket_id, item["product_id"], float(item["qty"]))
    return {
        "basket_id": basket_id,
        "items": usable,
        "months": months,
        "date": date,
        "skipped": len(items) - len(usable),
    }


def regular_purchases(min_times: int = 2) -> list[dict]:
    """Товары, которые покупают регулярно: встречались минимум в min_times разных покупках."""
    rows = [r for r in repo.list_history() if r.get("product_id")]
    seen: dict[int, set[str]] = defaultdict(set)
    names: dict[int, str] = {}
    for row in rows:
        pid = int(row["product_id"])
        seen[pid].add(str(row.get("date") or ""))
        names[pid] = row.get("product_name") or row.get("raw_name") or ""
    return [{"product_id": pid, "name": names[pid], "times": len(dates)}
            for pid, dates in seen.items() if len(dates) >= min_times]


def missing_regulars(basket_id: int, min_times: int = 2) -> list[dict]:
    """Регулярные покупки, которых нет в этой корзине (раздел 5.3 спецификации)."""
    in_basket = {int(i["product_id"]) for i in repo.basket_items(basket_id)}
    return [r for r in regular_purchases(min_times) if r["product_id"] not in in_basket]
