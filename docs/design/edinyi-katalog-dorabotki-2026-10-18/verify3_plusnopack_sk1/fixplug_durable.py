"""Прототип уточнённого исправления, вариант «прочный» (только для прогона тестов).

Загрузка: PYTHONPATH=<эта папка> python -m pytest -p fixplug_durable ...

Правила link не меняются. Только refresh.adopt без product_id («＋» из каталога —
явный выбор человека): после link.materialize(strong=True) в каждой сети, где
подтверждённого артикула так и нет (нет выбора человека, нет отказа), ищется строка,
которая и есть выбранная запись каталога: обе стороны без фасовки и не kg, слова равны
выбранному названию, все числа названий, кроме числа штук, совпадают, _agrees согласен,
настоящий штрихкод не спорит. Её подтверждают как выбор человека (confirm_mapping без
method, со снимком названия): следующий проход link её не снимет.
Метка master — по правилу link: «words?», если не подтверждено ни одной сети.
"""
from __future__ import annotations

import re
import sys

WT = "."
if WT not in sys.path:
    sys.path.insert(0, WT)

from app.catalog import link, match, refresh, store  # noqa: E402

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def numbers(row: match.Row) -> list[str]:
    nums = [n.replace(",", ".") for n in _NUM.findall(match.glue(row.name).lower())]
    if row.packs > 1 and str(row.packs) in nums:
        nums.remove(str(row.packs))
    return sorted(nums)


def picked_listing(person: list[match.Row], row: dict, own_code: str | None) -> bool:
    code = link._trusted(row.get("barcode"))
    if code and own_code and code != own_code:
        return False
    cand = link._prep(row)
    if cand.weight_g or cand.unit == "kg" or not person or not link._agrees(person, cand):
        return False
    return any(m.words == cand.words and not m.weight_g and m.unit != "kg"
               and numbers(m) == numbers(cand) for m in person)


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
    if product is None:
        return product_id
    with store.connect() as conn:
        confirmed, pending = link.materialize(product, item_id, True, conn=conn)
        if picked:
            person = link._rows_of_person(product, repo.raw_names(product_id))
            own_code = link._trusted(product.barcode)
            shops = {s.code: s for s in repo.list_stores()}
            item_rows = [r for r in store.item_rows(item_id, conn) if link._real(r["chain"], r["sku"])]
            for chain in list(pending):
                shop = shops.get(chain)
                if shop is None or repo.chain_rejected(product_id, shop.id):
                    continue
                fits = [r for r in item_rows if r["chain"] == chain
                        and picked_listing(person, r, own_code)]
                if not fits:
                    continue
                fits.sort(key=lambda r: (r.get("name") != data["name"], str(r["sku"])))
                row = fits[0]
                sp_id = repo.upsert_store_product(shop.id, str(row["sku"]), row.get("name") or product.name,
                                                  weight_g=row.get("weight_g"), unit=row.get("unit"),
                                                  url=row.get("url"), ean=row.get("barcode"),
                                                  keep_known=True)
                repo.confirm_mapping(product_id, sp_id, confirmed=True)     # выбор человека
                repo.drop_base_candidates(product_id, shop.id, keep=sp_id)
                link._price(sp_id, row)
                confirmed[chain] = str(row["sku"])
                pending.pop(chain, None)
    if repo.master_of(product_id) is None:
        human = any(link._real(m["store_code"], m["sku"])
                    for m in link._human(repo.product_mappings(product_id)))
        repo.set_master(product_id, item_id, "words" if (confirmed or human) else "words?")
    return product_id


refresh.adopt = adopt
