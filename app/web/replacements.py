"""Предложения для замены в магазинах выбранного варианта корзины."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from app import cartrun, db, freshness, location
from app.catalog import store
from app.catalog.model import placeholder_sku


def _words(text: str) -> str:
    text = re.sub(r"\bяйцо\b", "яйца", text.casefold().replace("ё", "е"))
    text = re.sub(r"\bc([012])\b", r"с\1", text)
    return re.sub(r"(\d+)\s*шт\b", r"\1 шт", text)


def _observations(codes, cutoff, sku=None):
    """Последнее наблюдение по артикулу. Корзина покупателя приоритетнее витрины."""
    marks = ",".join("?" for _ in codes)
    point = location.point_key()
    address = location.address() or ""
    moved = location.moved_at() or ""
    observations = {}
    with store.connect() as catalog:
        for code in codes:
            branch = store.point_of(address, code)
            if not branch:
                continue
            rows = catalog.execute(
                "SELECT cp.chain, cp.sku, cp.name AS raw_name, cp.photo_digest AS image, i.name, i.id AS mid,"
                " i.brand, i.weight_g, i.unit, i.category, o.price, o.in_stock, o.seen_at AS fetched_at"
                " FROM chain_prices o JOIN chain_products cp ON cp.chain=o.chain AND cp.sku=o.sku"
                " JOIN catalog_items i ON i.id=cp.item_id"
                " WHERE o.chain=? AND o.point=? AND cp.active=1 AND o.seen_at>=?"
                + (" AND cp.sku=?" if sku else ""), (code, branch, cutoff, *((sku,) if sku else ())))
            observations.update({(code, r["sku"]): dict(r) for r in rows})
    # Снимок без координат годится, если он пришёл из личной корзины после выбора адреса.
    rows = db.query(
        "SELECT s.code AS chain, sp.sku, sp.raw_name, p.name, p.id AS pid, p.master_id AS mid,"
        " p.brand, p.weight_g, p.unit, p.category, v.price, v.in_stock, v.fetched_at, v.source"
        " FROM store_prices v JOIN store_products sp ON sp.id=v.store_product_id"
        " JOIN stores s ON s.id=sp.store_id JOIN product_mapping m ON m.store_product_id=sp.id"
        " JOIN products p ON p.id=m.product_id"
        f" WHERE s.code IN ({marks}) AND p.active=1 AND m.confirmed=1"
        " AND v.fetched_at>=? AND (v.point_key=? OR (v.point_key IS NULL AND v.source='cart'))"
        " AND COALESCE(v.source,'')!='fallback' AND COALESCE(v.mode,'delivery')='delivery'"
        + (" AND sp.sku=?" if sku else "") +
        " ORDER BY v.fetched_at, CASE WHEN v.source='cart' THEN 1 ELSE 0 END, v.id",
        (*codes, max(cutoff, moved), point, *((sku,) if sku else ())))
    for row in rows:
        key = row["chain"], row["sku"]
        previous = observations.get(key, {})
        if row["fetched_at"] < previous.get("fetched_at", ""):
            continue
        observed = {**previous, **dict(row)}
        observed["name"] = previous.get("name") or row["raw_name"] or row["name"]
        observations[key] = observed
    missing = {}
    for code in codes:
        run = cartrun.get(code)
        stamp = run.finished_at if run else None
        if stamp and stamp >= max(moved, cutoff):
            missing[code] = {_words(str(m["name"])).strip(): stamp for m in run.missing or ()
                             if m.get("name") and (m.get("gone") or "нет в наличии" in str(m.get("why", "")).lower())}
    for (code, _), observation in observations.items():
        unavailable = missing.get(code, {})
        if not any(unavailable.get(_words(name).strip(), "") >= observation["fetched_at"]
                   for name in (observation["name"], observation["raw_name"]) if name):
            yield observation


def _norm_brand(brand: str | None) -> str:
    return re.sub(r"[^0-9a-zа-я]+", " ", (brand or "").casefold().replace("ё", "е")).strip()


def _trusted_brands(bought_ids):
    """Доверенные бренды покупателя из его истории покупок (общие и по категориям)."""
    if not bought_ids:
        return set(), {}
    marks = ",".join("?" for _ in bought_ids)
    rows = db.query(
        f"SELECT brand, category FROM products WHERE id IN ({marks}) AND brand IS NOT NULL AND brand != ''",
        tuple(bought_ids),
    )
    overall = set()
    by_category = {}
    for row in rows:
        brand = _norm_brand(row["brand"])
        if not brand:
            continue
        overall.add(brand)
        cat = (row["category"] or "").casefold().replace("ё", "е").strip()
        if cat:
            by_category.setdefault(cat, set()).add(brand)
    return overall, by_category


def _target_reference(exclude: int):
    """Ориентир по цене, весу и категории исходного товара (чтобы не подсовывать дешёвый СТМ вместо привычного сегмента)."""
    if not exclude or exclude < 0:
        return None, None, None
    prod = db.query("SELECT category, weight_g, unit FROM products WHERE id=?", (exclude,))
    cat = (prod[0]["category"] or "").casefold().replace("ё", "е").strip() if prod else None
    weight_g = prod[0]["weight_g"] if prod else None
    hist = db.query(
        "SELECT unit_price FROM purchase_history WHERE product_id=? AND unit_price > 0 ORDER BY date DESC, id DESC LIMIT 1",
        (exclude,),
    )
    if hist:
        return cat, weight_g, float(hist[0]["unit_price"])
    live = db.query(
        "SELECT AVG(v.price) AS avg_p FROM store_prices v"
        " JOIN product_mapping m ON m.store_product_id=v.store_product_id"
        " WHERE m.product_id=? AND m.confirmed=1 AND v.in_stock=1 AND v.price > 0",
        (exclude,),
    )
    ref_price = float(live[0]["avg_p"]) if live and live[0]["avg_p"] else None
    return cat, weight_g, ref_price


def _norm_unit_price(price: float, weight_g, unit: str) -> float:
    if unit == "kg":
        return price
    if weight_g and weight_g >= 30:
        return (price / float(weight_g)) * 1000.0
    return price


def search(query, codes, unit, exclude, bought_ids, bought_masters, *, sku=None, selected=None, observed_stores=None):
    store._ready()
    cutoff = (datetime.now() - timedelta(hours=freshness.max_age_hours())).isoformat(timespec="seconds")
    tokens = _words(query).split()
    people = db.query("SELECT id,master_id FROM products WHERE active=1 AND master_id IS NOT NULL ORDER BY id")
    personal_ids = {}
    with store.connect() as catalog:
        aliases = dict(catalog.execute("SELECT old_id,new_id FROM item_aliases"))
        def canonical(mid):
            return (store.resolve_alias(mid, catalog) or mid) if mid in aliases else mid
        for person in people:
            pid, mid = person["id"], canonical(person["master_id"])
            if mid not in personal_ids or pid in bought_ids:
                personal_ids[mid] = pid
        bought_masters = {canonical(mid) for mid in bought_masters}
        excluded_master = canonical(-exclude) if exclude < 0 else next(
            (canonical(r["master_id"]) for r in people if r["id"] == exclude), None)
        selected_master = canonical(-selected) if selected and selected < 0 else None
    trusted_brands, trusted_by_cat = _trusted_brands(bought_ids)
    target_cat, target_weight, target_price = _target_reference(exclude)
    results = {}
    for r in _observations(codes, cutoff, sku):
        code = r["chain"]
        if observed_stores is not None:
            observed_stores.add(code)
        if not r["in_stock"] or not r["price"] or r["price"] <= 0 or placeholder_sku(r["sku"], code):
            continue
        mid = r["mid"]
        if mid in aliases:
            mid = store.resolve_alias(mid) or mid
        pid = personal_ids.get(mid) or r.get("pid") or -mid
        product_unit = "kg" if r["unit"] == "kg" else "pcs"
        if pid == exclude or (mid and mid == excluded_master) or product_unit != unit:
            continue
        if selected and selected != pid and (not selected_master or selected_master != mid):
            continue
        hay = _words(" ".join(filter(None, (r["name"], r["raw_name"], r["brand"]))))
        if not all(re.search(r"(?<!\d)" + t + r"(?!\d)", hay) if t.isdecimal() else t in hay for t in tokens):
            continue
        row = results.setdefault(pid, {
            "product": {"id": pid, "name": r["name"], "brand": r["brand"], "unit": product_unit,
                        "category": r["category"], "weight_g": r["weight_g"], "active": True,
                        "photo": r.get("image"), "previouslyBought": pid in bought_ids or mid in bought_masters},
            "prices": {}, "small": "", "available": True})
        if not row["product"]["photo"] and r.get("image"):
            row["product"]["photo"] = r["image"]
        offers = row["prices"]
        if code not in offers or r["price"] < offers[code]["price"]:
            offers[code] = dict(store_code=code, sku=r["sku"], raw_name=r["raw_name"], price=r["price"],
                                unit=product_unit, weight_g=r["weight_g"], price_per_kg=None,
                                in_stock=True, fetched_at=r["fetched_at"], source="live")

    def _sort_key(row):
        prod = row["product"]
        min_price = min(p["price"] for p in row["prices"].values())
        cand_brand = _norm_brand(prod.get("brand"))
        cat_key = target_cat or (prod.get("category") or "").casefold().replace("ё", "е").strip()
        if prod["previouslyBought"]:
            row["trustTier"] = "bought"
            row["trustLabel"] = "Вы покупали"
            return (0, 0, 0.0, min_price, prod["name"], prod["id"])
        if cand_brand and (cand_brand in trusted_by_cat.get(cat_key, ()) or cand_brand in trusted_brands):
            in_cat = 0 if cand_brand in trusted_by_cat.get(cat_key, ()) else 1
            row["trustTier"] = "brand"
            row["trustLabel"] = "Ваш бренд"
            return (1, in_cat, 0.0, min_price, prod["name"], prod["id"])
        if target_price and target_price > 0:
            ref_u = _norm_unit_price(target_price, target_weight, unit)
            cand_u = _norm_unit_price(min_price, prod.get("weight_g"), prod["unit"])
            ratio = abs(cand_u - ref_u) / ref_u if ref_u > 0 else 9.0
            if ratio <= 0.25:
                row["trustTier"] = "price_band"
                row["trustLabel"] = "В вашей цене"
                return (2, 0, ratio, min_price, prod["name"], prod["id"])
        row["trustTier"] = "cheaper"
        row["trustLabel"] = ""
        return (3, 0, 0.0, min_price, prod["name"], prod["id"])

    for row in results.values():
        row["chainsCount"] = len(row["prices"])
    return sorted(results.values(), key=_sort_key)[:30]


def materialize(row, code):
    """Перенести подтверждённый артикул и его цену в рабочее место перед заменой."""
    product, offer = row["product"], row["prices"][code]
    point = location.point_key()
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        pid = product["id"]
        if pid < 0:
            mid = -pid
            existing = conn.execute("SELECT id FROM products WHERE master_id=? ORDER BY active DESC,id LIMIT 1",
                                    (mid,)).fetchone()
            if existing:
                pid = existing["id"]
                conn.execute("UPDATE products SET active=1 WHERE id=?", (pid,))
            else:
                pid = conn.execute("INSERT INTO products(name,brand,weight_g,unit,category,active,master_id,master_method,master_at)"
                                   " VALUES (?,?,?,?,?,1,?,'catalog',CURRENT_TIMESTAMP)",
                                   (product["name"], product["brand"], product["weight_g"], product["unit"],
                                    product["category"], mid)).lastrowid
        sid = conn.execute("SELECT id FROM stores WHERE code=?", (code,)).fetchone()[0]
        conn.execute("INSERT INTO store_products(store_id,sku,raw_name,weight_g,unit) VALUES(?,?,?,?,?)"
                     " ON CONFLICT(store_id,sku) DO NOTHING",
                     (sid, offer["sku"], offer["raw_name"], offer["weight_g"], offer["unit"]))
        spid = conn.execute("SELECT id FROM store_products WHERE store_id=? AND sku=?", (sid,offer["sku"])).fetchone()[0]
        conn.execute("INSERT INTO product_mapping(product_id,store_product_id,confirmed,method,seen_name,confirmed_at)"
                     " VALUES(?,?,1,'master',?,CURRENT_TIMESTAMP) ON CONFLICT(product_id,store_product_id) DO UPDATE SET confirmed=1",
                     (pid, spid, offer["raw_name"]))
        if not conn.execute("SELECT 1 FROM store_prices WHERE store_product_id=? AND fetched_at=?"
                            " AND price=? AND in_stock=1 AND (point_key IS ? OR source='cart')",
                            (spid, offer["fetched_at"], offer["price"], point)).fetchone():
            conn.execute("INSERT INTO store_prices(store_product_id,price,in_stock,fetched_at,source,point_key)"
                         " VALUES(?,?,1,?,'live',?)", (spid, offer["price"], offer["fetched_at"], point))
        conn.commit()
    product["id"] = pid
    return True
