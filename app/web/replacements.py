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
    for row in results.values():
        row["chainsCount"] = len(row["prices"])
    return sorted(results.values(), key=lambda r: (not r["product"]["previouslyBought"],
                  min(p["price"] for p in r["prices"].values()), r["product"]["name"], r["product"]["id"]))[:30]


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
