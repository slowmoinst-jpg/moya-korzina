"""Прототип уточнённого исправления (только для прогона тестов, рабочая копия не меняется).

Загрузка: PYTHONPATH=<эта папка> python -m pytest -p fixplug_refined ...

1. Только «＋» из каталога (refresh.adopt без product_id — явный выбор человека):
   строка проходит и без фасовки, если обе стороны без фасовки и не kg, слова равны
   выбранному названию, ВСЕ числа названий (кроме числа штук) совпадают и _agrees
   согласен. Штрихкод по-прежнему решает сам (исходный _row_ok идёт первым).
2. Метка master после adopt — по правилу link: «words?», если не подтверждено ни
   одной сети.
phonecart._link и фон (adopt с product_id, link_products) правила не получают.
"""
from __future__ import annotations

import contextvars
import os
import re
import sys

WT = "."
if WT not in sys.path:
    sys.path.insert(0, WT)

from app.catalog import link, match, refresh, store  # noqa: E402

_PICKED: contextvars.ContextVar[bool] = contextvars.ContextVar("picked", default=False)
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_ORIG_ROW_OK = link._row_ok


def numbers(row: match.Row) -> list[str]:
    """Числа названия без числа штук: «Pampers 4 46 шт» → ['4'], «Яйцо С1 10 шт» → ['1']."""
    nums = [n.replace(",", ".") for n in _NUM.findall(match.glue(row.name).lower())]
    if row.packs > 1 and str(row.packs) in nums:
        nums.remove(str(row.packs))
    return sorted(nums)


def row_ok(row: dict, ev) -> bool:
    if _ORIG_ROW_OK(row, ev):
        return True
    if not _PICKED.get() or not ev.person:
        return False
    if link._trusted(row.get("barcode")) and (ev.codes or ev.anchor_codes):
        return False                                   # штрихкод решил выше
    cand = ev.prepared.get(int(row["id"]))
    if cand is None or cand.weight_g or cand.unit == "kg" or not link._agrees(ev.person, cand):
        return False
    return any(m.words == cand.words and not m.weight_g and m.unit != "kg"
               and numbers(m) == numbers(cand) for m in ev.person)


link._row_ok = row_ok


def adopt(item_id: int, product_id: int | None = None) -> int:
    from app import repo
    from app.matcher.normalize import sold_by_weight
    from app.models import Product

    data = store.item(item_id)
    if not data:
        raise KeyError(f"нет единого товара {item_id}")
    rows = data.get("chains") or []
    by_weight = any((r.get("unit") or "").lower() == "kg" or sold_by_weight(r.get("name") or "")
                    for r in rows) or sold_by_weight(data.get("name") or "")
    unit = "kg" if by_weight and not data.get("weight_g") else "pcs"
    picked = product_id is None
    if product_id is None:
        existing = repo.find_product_by_name(data["name"])
        product_id = existing.id if existing else repo.upsert_product(Product(
            id=None, name=data["name"], brand=data.get("brand"), barcode=data.get("barcode"),
            weight_g=data.get("weight_g"), unit=unit if unit in ("pcs", "kg") else "pcs",
            category=data.get("category")))
    product = repo.get_product(product_id)
    if product is not None:
        token = _PICKED.set(picked)
        try:
            with store.connect() as conn:
                confirmed, _ = link.materialize(product, item_id, True, conn=conn)
        finally:
            _PICKED.reset(token)
        if repo.master_of(product_id) is None:
            human = any(link._real(m["store_code"], m["sku"])
                        for m in link._human(repo.product_mappings(product_id)))
            repo.set_master(product_id, item_id, "words" if (confirmed or human) else "words?")
    return product_id


if os.environ.get("FIX_ADOPT", "1") == "1":
    refresh.adopt = adopt
