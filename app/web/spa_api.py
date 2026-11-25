"""JSON API единого React SPA поверх реальных SQLite-баз «Моей корзины».

Работает напрямую с боевыми таблицами `data/users/<phone>/basket.db` и единым
каталогом `data/catalog.db`, разделяя данные с `/api/device/*` (Android APK)
и фоновым воркером каталога (`korzina-jobs`).

Аутентификация:
  1. Сессионная кука Flask (`auth.current_phone()`).
  2. Подписанный токен `Authorization: Bearer <token>` (itsdangerous, 30 дней) —
     для Android WebView (`file://`), cross-origin и iframe.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import requests
from flask import Blueprint, Response, abort, current_app, has_request_context, jsonify, make_response, request, send_file, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app import address_search, cartrun, chains, config, db, geo, location, repo, store_accounts, users
from app.catalog import nonfood
from app.models import Product
from app.web import apk, auth, device, login as web_login

log = logging.getLogger(__name__)

TOKEN_SALT = "korzina-spa-v1"
TOKEN_MAX_AGE = 30 * 24 * 3600  # 30 дней

SETTING_LAST_BASKET = "basket:last"
SETTING_REPEATED = "spa.repeated"
SETTING_STORE_PERKS = "spa.store_perks"
SETTING_APPLY_BONUSES = "spa.apply_bonuses"

# Прокси тайлов карты OpenStreetMap с дисковым кэшем (7 дней)
TILE_URL = "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_UA = "moya-korzina/1.0 (adresa; owner app)"
TILE_MIN_Z, TILE_MAX_Z = 3, 18
TILE_TTL_SEC = 7 * 24 * 3600
TILE_CACHE_CONTROL = "public, max-age=604800"
TILES_PER_HOUR = 600
TILE_WINDOW_SEC = 3600
PNG_MAGIC = b"\x89PNG"
_tile_lock = threading.Lock()
_tile_hits: dict[str, tuple[float, int]] = {}

# ---------- подписанный Bearer-токен ----------

def _serializer() -> URLSafeTimedSerializer:
    secret = current_app.secret_key
    if isinstance(secret, bytes):
        secret = secret.decode("ascii", errors="ignore")
    return URLSafeTimedSerializer(str(secret or "korzina-dev"), salt=TOKEN_SALT)


def issue_token(phone: str) -> str:
    """Выпустить подписанный Bearer-токен для SPA/APK."""
    return _serializer().dumps({"p": phone})


def phone_from_bearer() -> str | None:
    """Извлечь номер телефона из заголовка Authorization: Bearer <token>."""
    header = (request.headers.get("Authorization") or "").strip()
    if not header.lower().startswith("bearer "):
        return None
    raw = header[7:].strip()
    if not raw:
        return None
    try:
        data = _serializer().loads(raw, max_age=TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("p"), str):
        return None
    return users.normalize_phone(data["p"])


def current_spa_phone() -> str | None:
    """Кто пришёл в SPA API: кука сессии или Bearer-токен."""
    return auth.current_phone() or phone_from_bearer()


def _json(payload: dict, status: int = 200):
    from app.web.api import _cors_response

    return _cors_response(make_response(jsonify(payload), status))


def _auth_payload(phone: str | None) -> dict:
    return {
        "authenticated": bool(phone),
        "phone": phone,
        "phoneLabel": users.display(phone) if phone else "",
        "isDemo": phone == users.DEMO,
        "pinRequired": users.pin_is_set(),
    }


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- сериализация реальной базы SQLite в снимок SPA ----------

def _load_json_setting(key: str, default: Any) -> Any:
    raw = repo.get_setting(key)
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, type(default)) else default
    except (TypeError, ValueError):
        return default


def _thumbnail_url(digest: str | None) -> str | None:
    if not digest:
        return None
    if has_request_context():
        return url_for("api.spa_thumbnail", digest=digest, _external=True)
    return f"/api/catalog/images/{digest}.webp"


def _product_photos(pids: list[int], titles: dict[int, str] | None = None) -> dict[int, str]:
    """Фото товаров из единой базы сетей data/catalog.db (мягкий отказ, если базы нет)."""
    pids = sorted({int(p) for p in pids if p})
    if not pids:
        return {}
    try:
        from app.catalog import store as catalog_store

        pairs = {
            pid: [(m["store_code"], str(m["sku"])) for m in repo.product_mappings(pid)]
            for pid in pids
        }
        all_skus = [p for ps in pairs.values() for p in ps]
        masters = {int(r["id"]): int(r["master_id"]) for r in
                   db.query("SELECT id,master_id FROM products WHERE master_id IS NOT NULL")}
        if not all_skus and not any(pid in masters for pid in pids):
            return {}
        conn = catalog_store.connect()
        try:
            found = {
                (r["chain"], str(r["sku"])): r
                for r in catalog_store.rows_by_skus(all_skus, conn=conn)
            }
            out: dict[int, str] = {}

            def _img(rows) -> str | None:
                for r in rows:
                    if r.get("photo_digest"):
                        return _thumbnail_url(r["photo_digest"])
                return None

            for pid, ps in pairs.items():
                own = [found[p] for p in ps if p in found]
                if titles is not None:
                    # Some feeds supply transliterated URL slugs as names.
                    title = next((r["name"] for r in own
                                  if re.search(r"[А-Яа-яЁё]", r.get("name") or "")), None)
                    if title:
                        titles[pid] = title
                url = _img(own)
                if not url:
                    ids = [r["item_id"] for r in own if r.get("item_id")]
                    if pid in masters:
                        ids.append(masters[pid])
                    for item_id in dict.fromkeys(ids):
                        url = _img(catalog_store.item_rows(item_id, conn=conn))
                        if url:
                            break
                if url:
                    out[pid] = url
            return out
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — отсутствие catalog.db не повод ронять синк
        return {}


def _prices_version() -> int:
    return int(db.query("SELECT COALESCE(MAX(id), 0) AS v FROM store_prices")[0]["v"])


def _bulk_latest_prices(product_ids: list[int] | None = None) -> dict[str, dict[str, dict]]:
    """Все свежие цены по подтверждённым связям одним SQL-запросом (или только для нужных товаров)."""
    params: list = []
    pid_filter = ""
    if product_ids is not None:
        clean_ids = sorted({int(x) for x in product_ids if x is not None})
        if not clean_ids:
            return {}
        placeholders = ",".join("?" for _ in clean_ids)
        pid_filter = f"AND m.product_id IN ({placeholders})"
        params.extend(clean_ids)
    from app import freshness
    cutoff = (datetime.now() - timedelta(hours=freshness.max_age_hours())).isoformat(timespec="seconds")
    point = location.point_key()
    rows = db.query(
        f"""
        SELECT
            m.product_id,
            s.code AS store_code,
            sp.sku,
            sp.raw_name,
            sp.unit,
            sp.weight_g,
            p.price,
            p.price_per_kg,
            p.in_stock,
            p.fetched_at,
            p.source
        FROM product_mapping m
        JOIN store_products sp ON sp.id = m.store_product_id
        JOIN stores s ON s.id = sp.store_id
        JOIN (
            SELECT v.*, ROW_NUMBER() OVER (PARTITION BY v.store_product_id ORDER BY
                (v.source IS 'cart' AND v.fetched_at>=?) DESC,
                (v.point_key IS NOT NULL AND v.fetched_at>=?) DESC, v.fetched_at DESC, v.id DESC) AS rn
            FROM store_prices v WHERE v.point_key IS NULL OR v.point_key=?
        ) p ON p.store_product_id=sp.id AND p.rn=1
        WHERE m.confirmed = 1 {pid_filter}
        ORDER BY (p.in_stock=1 AND p.price>0 AND p.fetched_at>=? AND COALESCE(p.source,'')!='fallback') DESC,
                 (p.source IS 'cart' AND p.fetched_at>=?) DESC,
                 (p.point_key IS NOT NULL AND p.fetched_at>=?) DESC,
                 CASE WHEN p.in_stock=1 AND p.fetched_at>=? THEN p.price END, p.fetched_at DESC, p.id DESC
        """, (cutoff, cutoff, point, *params, cutoff, cutoff, cutoff, cutoff)
    )
    prices: dict[str, dict[str, dict]] = {}
    for r in rows:
        pid_key = str(int(r["product_id"]))
        code = str(r["store_code"])
        by_store = prices.setdefault(pid_key, {})
        if code in by_store:
            continue
        raw_unit = str(r["unit"] or "pcs")
        unit = "kg" if raw_unit == "kg" else "pcs"
        by_store[code] = {
            "store_code": code,
            "sku": str(r["sku"] or ""),
            "raw_name": str(r["raw_name"] or ""),
            "price": float(r["price"]),
            "unit": unit,
            "weight_g": float(r["weight_g"]) if r["weight_g"] is not None else None,
            "price_per_kg": float(r["price_per_kg"]) if r["price_per_kg"] is not None else None,
            "in_stock": bool(r["in_stock"]),
            "fetched_at": str(r["fetched_at"] or _now_iso()),
            "source": "fallback" if r["source"] == "fallback" else "live",
        }
    return prices


def _serialize_connections(stores: list) -> dict[str, dict]:
    by_code = {c.store_code: c for c in store_accounts.connections()}
    raw_runs = cartrun._all()
    out: dict[str, dict] = {}
    for st in stores:
        c = by_code.get(st.code)
        row = cartrun.Row.of(raw_runs.get(st.code))
        last_ts = (
            (row.finished_at or row.started_at)
            if row
            else (c.last_seen_at if c else None)
        )
        again = bool(row and row.state == cartrun.LOGIN and not (c and c.device))
        out[st.code] = {
            "store_code": st.code,
            "connected": bool(c and c.connected and not again),
            "connectedAt": c.connected_at if c else None,
            "again": again,
            "last": str(last_ts)[:16].replace("T", " ") if last_ts else None,
            "mode": "session" if (c and c.device) else ("manual" if (c and c.connected) else None),
        }
    return out


def _quote_reason(row: cartrun.Row) -> str | None:
    if row.state == cartrun.LOGIN:
        return "login"
    if row.state == cartrun.REFUSED:
        return "refused"
    if row.state == cartrun.EMPTY:
        return "empty"
    if row.state == cartrun.CANT:
        return "cant"
    if row.state == cartrun.FAILED:
        return "lost" if row.lost else "failed"
    return None


def _serialize_quotes(stores: list, address: str | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    by_code = {c.store_code: c for c in store_accounts.connections()}
    raw_runs = cartrun._all()
    for st in stores:
        row = cartrun.Row.of(raw_runs.get(st.code))
        if not row or row.state == cartrun.IDLE:
            continue
        if row.state == cartrun.GOING:
            status = "collecting"
        elif row.state in (cartrun.DONE, cartrun.SENT):
            status = "ready"
        elif row.state in (cartrun.FAILED, cartrun.REFUSED, cartrun.LOGIN):
            status = "error"
        else:
            status = "idle"
        tot = row.total
        missing_names = [
            str(m.get("name") or "")
            for m in (row.missing or [])
            if isinstance(m, dict) and m.get("name")
        ]
        avail = len(row.landed or [])
        total_cnt = int(row.planned or (avail + len(missing_names)))
        acc = by_code.get(st.code)
        bonus_bal = float(acc.points) if (acc and acc.points is not None) else 0.0
        raw_link = str(row.link or "").strip()
        cart_link = raw_link if raw_link.startswith(("http://", "https://")) else None
        out[st.code] = {
            "store_code": st.code,
            "status": status,
            "reason": _quote_reason(row),
            "stamp": row.stamp or None,
            "updated_at": row.finished_at or row.started_at or None,
            "address": address,
            "available_count": avail,
            "total_count": total_cnt,
            "missing_names": missing_names,
            "goods_total": float(tot.goods) if (tot and tot.goods is not None) else None,
            "delivery_fee": float(tot.delivery) if (tot and tot.delivery is not None) else None,
            "service_fee": 0.0,
            "promo_discount": float(tot.discount) if (tot and tot.discount is not None) else 0.0,
            "promo_label": None,
            "bonus_balance": bonus_bal,
            "bonus_max_use": round(bonus_bal, 2),
            "store_total": float(tot.sum) if (tot and tot.source == "network") else None,
            "note": row.note or None,
            "cart_link": cart_link,
        }
    return out


def _fns_state() -> tuple[bool, str | None, str | None]:
    try:
        from app.fns import sync as cabinet

        st = cabinet.status()
        if isinstance(st, dict):
            return (
                bool(st.get("connected")),
                st.get("phone"),
                (str(st.get("last_sync_at") or "").replace("T", " ")[:16] or None),
            )
    except Exception:  # noqa: BLE001
        pass
    return False, None, None


def serialize_workspace(phone: str) -> dict:
    """Собрать полный WorkspaceSnapshot из реальной базы рабочего места."""
    stores = repo.list_stores()
    products_raw = repo.list_products(active_only=False)
    next_product_id = max((p.id for p in products_raw if p.id is not None), default=0) + 1

    cards = [{"id": int(c.id), "bank": c.bank, "name": c.name} for c in repo.list_cards() if c.id is not None]
    offers = [
        {
            "id": int(o.id),
            "card_id": int(o.card_id),
            "store_id": int(o.store_id),
            "percent": float(o.percent),
            "cap_rub": float(o.cap_rub),
            "min_check_rub": float(o.min_check_rub),
            "valid_from": o.valid_from,
            "valid_to": o.valid_to,
            "requires_activation": bool(o.requires_activation),
            "activated": bool(o.activated),
            "cap_used": float(o.cap_used),
        }
        for o in repo.list_offers()
        if o.id is not None
    ]

    baskets_raw = repo.list_baskets()
    baskets = [
        {
            "id": int(b["id"]),
            "name": str(b["name"]),
            "created_at": str(b["created_at"] or "")[:10],
            "source": b["source"] if b["source"] in ("manual", "average", "history") else "manual",
        }
        for b in baskets_raw
    ]
    next_basket_id = max((b["id"] for b in baskets), default=0) + 1

    basket_items: dict[str, list[dict]] = {}
    for b in baskets:
        bid = b["id"]
        rows = repo.basket_items(bid)
        basket_items[str(bid)] = [
            {
                "basket_id": bid,
                "product_id": int(r["product_id"]),
                "qty": float(r["qty"]),
                "preferred_store": r.get("preferred_store"),
                "fallback_product_id": (
                    int(r["fallback_product_id"]) if r.get("fallback_product_id") is not None else None
                ),
            }
            for r in rows
        ]

    basket_product_ids = {pid for items in basket_items.values() for item in items
                          for pid in (item["product_id"], item["fallback_product_id"]) if pid is not None}
    visible = [p for p in products_raw if p.id is not None and (p.active or p.id in basket_product_ids)]
    titles = repo.human_titles({p.id: p.name for p in visible if p.id in basket_product_ids})
    photos = _product_photos([p.id for p in visible], titles)
    bought_ids, _ = _bought_ids()
    products = [
        {"id": p.id, "name": p.name, "title": titles.get(p.id), "brand": p.brand, "weight_g": p.weight_g,
         "unit": "kg" if p.unit == "kg" else "pcs", "category": p.category,
         "active": bool(p.active), "photo": photos.get(p.id), "previouslyBought": p.id in bought_ids}
        for p in visible
    ]
    visible_ids = {str(p.id) for p in visible}
    prices = {pid: values for pid, values in _bulk_latest_prices().items() if pid in visible_ids}

    last_raw = repo.get_setting(SETTING_LAST_BASKET)
    last_basket_id: int | None = None
    if last_raw:
        try:
            cand = int(last_raw)
            if any(b["id"] == cand for b in baskets):
                last_basket_id = cand
        except ValueError:
            pass
    if last_basket_id is None and baskets:
        last_basket_id = baskets[0]["id"]

    store_by_id = {int(s.id): s for s in stores if s.id is not None}
    history_raw = repo.list_history(limit=600)
    habit_stores = chains.orderable()
    history = []
    for h in history_raw:
        sid = int(h["store_id"]) if h.get("store_id") is not None else 0
        st = store_by_id.get(sid)
        store_code = str(h.get("store_code") or (st.code if st else "unknown"))
        is_service = nonfood.is_service_name(h["raw_name"] or h.get("product_name"))
        is_product = nonfood.is_receipt_product(h["raw_name"], h.get("product_key"))
        history.append(
            {
                "id": int(h["id"]),
                "date": str(h["date"] or "")[:10],
                "store_id": sid,
                "store_code": store_code,
                "store_name": str(h.get("store_name") or (st.name if st else "Магазин")),
                "product_id": int(h["product_id"]) if h.get("product_id") is not None else None,
                "raw_name": str(h["raw_name"] or ""),
                "is_service": is_service,
                "recommendable": bool(h["product_id"] and store_code in habit_stores and is_product),
                "qty": float(h["qty"] or 1),
                "unit_price": float(h["unit_price"] or 0),
                "total": float(h["total"] or 0),
                "receipt_key": str(h.get("receipt_key") or f"row-{h['id']}"),
            }
        )

    receipts_raw = repo.imported_receipts()
    other_receipts = repo.other_store_receipts(limit=5)
    receipts = [
        {
            "key": str(r["key"]),
            "date": str(r.get("date") or "")[:10],
            "store": str(r.get("store") or "Магазин"),
            "total": float(r.get("total") or 0),
            "rows": int(r.get("rows") or 0),
            "source": str(r.get("source") or "paste"),
        }
        for r in receipts_raw
    ]

    addr = location.address()
    pt = location.point()
    connections = _serialize_connections(stores)
    store_quotes = _serialize_quotes(stores, addr)
    store_perks = _load_json_setting(SETTING_STORE_PERKS, {})
    apply_bonuses_raw = repo.get_setting(SETTING_APPLY_BONUSES)
    apply_bonuses = False if apply_bonuses_raw == "0" else True
    repeated = _load_json_setting(SETTING_REPEATED, None)
    if not isinstance(repeated, dict):
        repeated = None
    fns_connected, fns_phone, fns_synced_at = _fns_state()
    _, active_stamp = _active_basket_id_and_stamp()

    return {
        "phone": phone,
        "phoneLabel": users.display(phone),
        "isDemo": phone == users.DEMO,
        "address": addr,
        "point": [pt[0], pt[1]] if pt else None,
        "stores": [
            {
                "id": int(s.id),
                "code": s.code,
                "name": s.name,
                "delivery_fee": float(s.delivery_fee),
                "free_delivery_from": float(s.free_delivery_from),
                "min_order": float(s.min_order),
                "connector_type": s.connector_type,
            }
            for s in stores
            if s.id is not None
        ],
        "products": products,
        "nextProductId": next_product_id,
        "prices": prices,
        "cards": cards,
        "offers": offers,
        "baskets": baskets,
        "nextBasketId": next_basket_id,
        "basketItems": basket_items,
        "history": history,
        "receipts": receipts,
        "otherReceipts": {"count": repo.saved_receipt_count() - len(receipts), "recent": [
            {k: r[k] for k in ("key", "date", "store", "total")} for r in other_receipts]},
        "connections": connections,
        "storeQuotes": store_quotes,
        "storePerks": store_perks,
        "applyBonuses": apply_bonuses,
        "lastBasketId": last_basket_id,
        "basketStamp": active_stamp or None,
        "repeated": repeated,
        "fnsConnected": fns_connected,
        "fnsPhone": fns_phone,
        "fnsSyncedAt": fns_synced_at,
        "syncedAt": _now_iso(),
    }


# ---------- импорт товара из единого каталога data/catalog.db ----------

def _import_catalog_product(catalog_item_id: int, fallback_data: dict | None = None) -> int | None:
    """Перенести товар из data/catalog.db в личную basket.db со всеми связями и ценами."""
    store_by_code = {s.code: int(s.id) for s in repo.list_stores() if s.id is not None}
    name = str((fallback_data or {}).get("name") or "").strip()
    brand = (fallback_data or {}).get("brand")
    weight_g = (fallback_data or {}).get("weight_g")
    unit = "kg" if (fallback_data or {}).get("unit") == "kg" else "pcs"
    category = (fallback_data or {}).get("category")

    rows: list[dict] = []
    try:
        from app.catalog import store as catalog_store

        item = catalog_store.item(catalog_item_id)
        if item:
            name = str(item.get("name") or name).strip()
            brand = item.get("brand") or brand
            weight_g = item.get("weight_g") if item.get("weight_g") is not None else weight_g
            unit = "kg" if item.get("unit") == "kg" else unit
            category = item.get("category") or category
        rows = catalog_store.item_rows(catalog_item_id) or []
    except Exception:  # noqa: BLE001
        pass

    if not name:
        return None

    existing = repo.find_product_by_name(name)
    if existing and existing.id is not None:
        if not existing.active:
            if not rows:
                return None
            existing.active = True
            repo.upsert_product(existing)
        pid = int(existing.id)
    else:
        pid = repo.upsert_product(
            Product(
                id=None,
                name=name,
                brand=brand,
                weight_g=float(weight_g) if weight_g is not None else None,
                unit=unit,
                category=category,
                active=True,
            )
        )

    try:
        repo.set_master(pid, catalog_item_id, "catalog")
    except Exception:  # noqa: BLE001
        pass

    for r in rows:
        chain = str(r.get("chain") or "").strip().lower()
        sid = store_by_code.get(chain)
        sku = str(r.get("sku") or "").strip()
        if not sid or not sku:
            continue
        sp_id = repo.upsert_store_product(
            sid,
            sku,
            str(r.get("name") or name),
            weight_g=float(r["weight_g"]) if r.get("weight_g") is not None else None,
            unit=r.get("unit"),
            url=r.get("url"),
        )
        repo.confirm_mapping(pid, sp_id, confirmed=True, method="master")
        price_val = r.get("price")
        if price_val is not None:
            try:
                p_num = float(price_val)
                if p_num > 0:
                    repo.save_price(
                        sp_id,
                        p_num,
                        in_stock=bool(r.get("in_stock")),
                        fetched_at=r.get("price_seen") or r.get("first_seen"),
                        source="live",
                    )
            except (TypeError, ValueError):
                pass
    return pid


# ---------- сохранение снимка SPA обратно в SQLite ----------

def _apply_sync(body: dict) -> None:
    """Применить изменения от SPA к реальной SQLite-базе рабочего места."""
    # 1. Товары (включая новые из каталога с отрицательными временными ID)
    pid_map: dict[int, int] = {}
    existing_pids = {int(p.id) for p in repo.list_products(active_only=False) if p.id is not None}
    raw_products = body.get("products")
    if isinstance(raw_products, list):
        for p in raw_products:
            if not isinstance(p, dict):
                continue
            raw_id = p.get("id")
            if raw_id is None:
                continue
            try:
                pid_int = int(raw_id)
            except (TypeError, ValueError):
                continue
            if pid_int < 0:
                imported_id = _import_catalog_product(-pid_int, p)
                if imported_id:
                    pid_map[pid_int] = imported_id
            elif pid_int not in existing_pids and p.get("name"):
                new_id = repo.upsert_product(
                    Product(
                        id=None,
                        name=str(p["name"]).strip(),
                        brand=p.get("brand"),
                        weight_g=float(p["weight_g"]) if p.get("weight_g") is not None else None,
                        unit="kg" if p.get("unit") == "kg" else "pcs",
                        category=p.get("category"),
                        active=bool(p.get("active", True)),
                    )
                )
                pid_map[pid_int] = new_id
                existing_pids.add(new_id)

    # 2. Корзины и позиции
    raw_baskets = body.get("baskets")
    raw_items = body.get("basketItems")
    basket_id_map: dict[int, int] = {}
    if isinstance(raw_baskets, list):
        with db.get_conn() as conn:
            current_ids = {
                int(r["id"]) for r in conn.execute("SELECT id FROM baskets").fetchall()
            }
            kept_ids: set[int] = set()
            for b in raw_baskets:
                if not isinstance(b, dict):
                    continue
                try:
                    bid = int(b.get("id") or 0)
                except (TypeError, ValueError):
                    bid = 0
                name = str(b.get("name") or "Корзина").strip() or "Корзина"
                created_at = str(b.get("created_at") or _now_iso()[:10])[:19]
                source = b.get("source") if b.get("source") in ("manual", "average", "history") else "manual"
                if bid in current_ids:
                    conn.execute(
                        "UPDATE baskets SET name=?, source=? WHERE id=?",
                        (name, source, bid),
                    )
                    basket_id_map[bid] = bid
                    kept_ids.add(bid)
                elif bid > 0:
                    conn.execute(
                        "INSERT INTO baskets (id, name, created_at, source) VALUES (?,?,?,?)",
                        (bid, name, created_at, source),
                    )
                    basket_id_map[bid] = bid
                    kept_ids.add(bid)
                else:
                    cur = conn.execute(
                        "INSERT INTO baskets (name, created_at, source) VALUES (?,?,?)",
                        (name, created_at, source),
                    )
                    new_bid = int(cur.lastrowid)
                    basket_id_map[bid] = new_bid
                    kept_ids.add(new_bid)

            for obsolete in current_ids - kept_ids:
                conn.execute("DELETE FROM baskets WHERE id=?", (obsolete,))

            if isinstance(raw_items, dict):
                active_pids = {
                    int(r["id"]) for r in conn.execute("SELECT id FROM products WHERE active=1")
                }
                for key, item_list in raw_items.items():
                    try:
                        orig_bid = int(key)
                    except (TypeError, ValueError):
                        continue
                    real_bid = basket_id_map.get(orig_bid)
                    if not real_bid or real_bid not in kept_ids:
                        continue
                    previous_stores = dict(conn.execute(
                        "SELECT product_id, preferred_store FROM basket_items WHERE basket_id=?", (real_bid,)
                    ))
                    previous_ids = {pid for line in conn.execute(
                        "SELECT product_id, fallback_product_id FROM basket_items WHERE basket_id=?", (real_bid,)
                    ) for pid in line if pid is not None}
                    valid_pids = active_pids | previous_ids
                    conn.execute("DELETE FROM basket_items WHERE basket_id=?", (real_bid,))
                    if not isinstance(item_list, list):
                        continue
                    for item in item_list:
                        if not isinstance(item, dict):
                            continue
                        try:
                            orig_pid = int(item.get("product_id") or 0)
                            qty = float(item.get("qty") or 0)
                        except (TypeError, ValueError):
                            continue
                        real_pid = pid_map.get(orig_pid, orig_pid)
                        if real_pid not in valid_pids or qty <= 0:
                            continue
                        fb = item.get("fallback_product_id")
                        fb_id = int(fb) if (fb is not None and int(fb) in valid_pids) else None
                        preferred_store = item.get("preferred_store", previous_stores.get(real_pid))
                        if not isinstance(preferred_store, str) or preferred_store not in chains.orderable():
                            preferred_store = None
                        conn.execute(
                            "INSERT OR REPLACE INTO basket_items (basket_id, product_id, qty, fallback_product_id, preferred_store)"
                            " VALUES (?,?,?,?,?)",
                            (real_bid, real_pid, qty, fb_id, preferred_store),
                        )
            conn.commit()

    if "lastBasketId" in body:
        last_val = body.get("lastBasketId")
        if last_val is None:
            repo.set_setting(SETTING_LAST_BASKET, None)
        else:
            try:
                mapped = basket_id_map.get(int(last_val), int(last_val))
                repo.set_setting(SETTING_LAST_BASKET, str(mapped))
            except (TypeError, ValueError):
                pass

    if "repeated" in body:
        rep = body.get("repeated")
        repo.set_setting(
            SETTING_REPEATED,
            json.dumps(rep, ensure_ascii=False) if isinstance(rep, dict) else None,
        )

    # 3. Адрес и точка на карте
    if "address" in body:
        addr = str(body.get("address") or "").strip() or None
        location.save_address(addr)
    if "point" in body:
        pt = body.get("point")
        if isinstance(pt, (list, tuple)) and len(pt) == 2:
            location.save_point(pt[0], pt[1])
        elif pt is None:
            location.save_point(None, None)

    # 4. Подключения магазинов
    raw_conns = body.get("connections")
    if isinstance(raw_conns, dict):
        for code, st in raw_conns.items():
            if code not in store_accounts.ABILITIES or not isinstance(st, dict):
                continue
            want = bool(st.get("connected"))
            cur = store_accounts.connection(code)
            if want and not cur.connected:
                store_accounts.mark_connected(
                    code,
                    device=(st.get("mode") == "session"),
                )
            elif not want and cur.connected and (not cur.device or (
                st.get("disconnectSeen") and st["disconnectSeen"] == cur.connected_at
            )):
                store_accounts.forget(code)

    # 5. Перки/скидки/бонусы сетей и флаг списания бонусов
    if isinstance(body.get("storePerks"), dict):
        repo.set_setting(
            SETTING_STORE_PERKS,
            json.dumps(body["storePerks"], ensure_ascii=False),
        )
    if "applyBonuses" in body:
        repo.set_setting(SETTING_APPLY_BONUSES, "1" if body["applyBonuses"] else "0")

    # 6. Карты и предложения кэшбэка
    raw_cards = body.get("cards")
    raw_offers = body.get("offers")
    if isinstance(raw_cards, list) and isinstance(raw_offers, list):
        valid_store_ids = {int(s.id) for s in repo.list_stores() if s.id is not None}
        with db.get_conn() as conn:
            conn.execute("DELETE FROM offers")
            conn.execute("DELETE FROM cards")
            card_id_map: dict[int, int] = {}
            for c in raw_cards:
                if not isinstance(c, dict):
                    continue
                try:
                    cid = int(c.get("id") or 0)
                except (TypeError, ValueError):
                    cid = 0
                bank = str(c.get("bank") or "Банк").strip() or "Банк"
                name = str(c.get("name") or "Карта").strip() or "Карта"
                if cid > 0:
                    conn.execute("INSERT INTO cards (id, bank, name) VALUES (?,?,?)", (cid, bank, name))
                    card_id_map[cid] = cid
                else:
                    cur = conn.execute("INSERT INTO cards (bank, name) VALUES (?,?)", (bank, name))
                    card_id_map[cid] = int(cur.lastrowid)

            for o in raw_offers:
                if not isinstance(o, dict):
                    continue
                try:
                    orig_cid = int(o.get("card_id") or 0)
                    sid = int(o.get("store_id") or 0)
                except (TypeError, ValueError):
                    continue
                real_cid = card_id_map.get(orig_cid)
                if not real_cid or sid not in valid_store_ids:
                    continue
                conn.execute(
                    """
                    INSERT INTO offers (
                        card_id, store_id, percent, cap_rub, min_check_rub,
                        valid_from, valid_to, requires_activation, activated, cap_used
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        real_cid,
                        sid,
                        float(o.get("percent") or 0),
                        float(o.get("cap_rub") or 0),
                        float(o.get("min_check_rub") or 0),
                        o.get("valid_from"),
                        o.get("valid_to"),
                        1 if o.get("requires_activation") else 0,
                        1 if o.get("activated", True) else 0,
                        float(o.get("cap_used") or 0),
                    ),
                )
            conn.commit()


# ---------- поиск по личной базе и единому каталогу data/catalog.db ----------

def _bought_ids() -> tuple[set[int], set[int]]:
    rows = db.query("SELECT DISTINCT h.product_id, p.master_id FROM purchase_history h"
                    " JOIN products p ON p.id=h.product_id WHERE h.qty>0 AND h.total>=0")
    return ({int(r["product_id"]) for r in rows},
            {int(r["master_id"]) for r in rows if r["master_id"]})


def _search_order(item: dict, chain: str | None) -> tuple:
    prices = [p for code, p in item["prices"].items() if not chain or code == chain]
    minimum = min((p["price"] for p in prices if p["price"] > 0
                   and (not item["available"] or p["in_stock"])), default=float("inf"))
    return not item["product"]["previouslyBought"], not item["available"], minimum


def _search_products(query: str, limit: int = 30, chain: str | None = None) -> list[dict]:
    q = " ".join((query or "").split())
    if len(q) < 2:
        return []
    def normalized(s: str) -> str:
        s = re.sub(r"\bяйцо\b", "яйца", s.casefold())
        return re.sub(r"\bc([012])\b", r"с\1", s)

    words = normalized(q).split()
    bought_ids, bought_masters = _bought_ids()
    prices_map = _bulk_latest_prices()
    all_prods = repo.list_products(active_only=True)
    own_hits = [
        p for p in all_prods
        if all(word in normalized(f"{p.name} {p.brand or ''}") for word in words)
        or normalized(p.category or "") == normalized(q)
    ]
    if chain:
        own_hits = [p for p in own_hits if prices_map.get(str(p.id), {}).get(chain, {}).get("in_stock")]
    titles: dict[int, str] = {}
    results: list[dict] = []
    pending_details: dict[int, list[tuple[str,str]]] = {}

    for p in own_hits:
        if p.id is None:
            continue
        p_prices = {code: price for code, price in prices_map.get(str(int(p.id)), {}).items()
                    if price.get("source") != "fallback"}
        chains_n = sum(bool(s["in_stock"] and s["price"] > 0) for s in p_prices.values())
        small_parts = [x for x in ("кг" if p.unit == "kg" else "шт", p.brand or "") if x]
        if chain and not (p_prices.get(chain, {}).get("in_stock") and p_prices[chain]["price"] > 0):
            continue
        if chains_n:
            small_parts.append(f"в {chains_n} сет.")
        else:
            small_parts.append("нет подтверждённого предложения")
        results.append(
            {
                "product": {
                    "id": int(p.id),
                    "name": p.name,
                    "title": titles.get(int(p.id)),
                    "brand": p.brand,
                    "weight_g": p.weight_g,
                    "unit": "kg" if p.unit == "kg" else "pcs",
                    "category": p.category,
                    "active": True,
                    "photo": None,
                    "previouslyBought": p.id in bought_ids,
                },
                "prices": p_prices,
                "chainsCount": chains_n,
                "available": any(v["in_stock"] for k, v in p_prices.items() if not chain or k == chain),
                "small": " · ".join(small_parts),
            }
        )

    # Добираем из единого каталога data/catalog.db
    if limit > 0:
        try:
            from app.catalog import store as catalog_store

            conn = catalog_store.connect()
            try:
                linked = db.query("SELECT id,master_id FROM products WHERE active=1 AND master_id IS NOT NULL"
                                  " ORDER BY id")
                aliases = dict(conn.execute("SELECT old_id,new_id FROM item_aliases"))
                def canonical(mid: int) -> int:
                    return (catalog_store.resolve_alias(mid, conn) or mid) if mid in aliases else mid
                personal_ids = {}
                for record in linked:
                    mid, pid = canonical(int(record["master_id"])), int(record["id"])
                    if mid not in personal_ids or pid in bought_ids:
                        personal_ids[mid] = pid
                bought_masters = {canonical(mid) for mid in bought_masters}
                hits = catalog_store.search_items(q, limit=limit * 2, conn=conn, by_name=True,
                                                  chain=chain, purchased=bought_masters)
                candidate_hits = []
                for h in hits:
                    item_id = int(h.get("id") or 0)
                    name = str(h.get("name") or "").strip()
                    if not item_id or not name:
                        continue
                    candidate_hits.append((item_id, name, h))
                rows_by_item = catalog_store.batch_item_rows(
                    [item_id for item_id, _, _ in candidate_hits], conn=conn
                )
                for item_id, name, h in candidate_hits:
                    rows = rows_by_item.get(item_id) or []
                    cat_prices: dict[str, dict] = {}
                    photo_url: str | None = None
                    for r in rows:
                        if not photo_url:
                            photo_url = _thumbnail_url(r.get("photo_digest"))
                        store_code = str(r.get("chain") or "").strip().lower()
                        price_val = r.get("price")
                        if not store_code or price_val is None:
                            continue
                        try:
                            pv = float(price_val)
                        except (TypeError, ValueError):
                            continue
                        if pv <= 0:
                            continue
                        stock = bool(r.get("in_stock"))
                        prior = cat_prices.get(store_code)
                        if prior and (not prior["in_stock"], prior["price"]) <= (not stock, pv):
                            continue
                        cat_prices[store_code] = {
                            "store_code": store_code,
                            "sku": str(r.get("sku") or ""),
                            "raw_name": str(r.get("name") or name),
                            "price": pv,
                            "unit": "kg" if r.get("unit") == "kg" else "pcs",
                            "weight_g": float(r["weight_g"]) if r.get("weight_g") is not None else None,
                            "price_per_kg": None,
                            "in_stock": stock,
                            "fetched_at": str(r.get("price_seen") or r.get("first_seen") or ""),
                            "source": "live",
                        }
                    if chain and not cat_prices.get(chain, {}).get("in_stock"):
                        continue
                    chains_n = sum(bool(s["in_stock"]) for s in cat_prices.values())
                    unit = "kg" if h.get("unit") == "kg" else "pcs"
                    small_parts = [x for x in ("кг" if unit == "kg" else "шт", h.get("brand") or "") if x]
                    if chains_n:
                        small_parts.append(f"каталог · {chains_n} сет.")
                    else:
                        small_parts.append("цена не подтверждена")
                    pid = personal_ids.get(item_id, -item_id)
                    scoped = [r for r in rows if not chain or r["chain"] == chain]
                    pending_details[pid] = [(r["chain"], r["sku"]) for r in scoped
                        if r["chain"] in ("lenta", "vkusvill") and not r.get("detail_requested_at")
                        and (not r.get("price") or (r["chain"] == "lenta" and r.get("in_stock") is None))]
                    if not chains_n:
                        if any(r.get("in_stock") for r in scoped):
                            small_parts[-1] = "есть в наличии · цена не указана"
                        elif all(r.get("in_stock") == 0 for r in scoped):
                            small_parts[-1] = "нет в наличии"
                        else:
                            small_parts[-1] = "наличие неизвестно" if cat_prices else "цена и наличие неизвестны"
                    results.append(
                        {
                            "product": {
                                "id": pid,
                                "name": name,
                                "brand": h.get("brand"),
                                "weight_g": float(h["weight_g"]) if h.get("weight_g") is not None else None,
                                "unit": unit,
                                "category": h.get("category"),
                                "active": True,
                                "photo": photo_url,
                                "previouslyBought": item_id in bought_masters,
                            },
                            "prices": cat_prices,
                            "chainsCount": chains_n,
                            "available": any(r.get("in_stock") and (not chain or r["chain"] == chain)
                                             for r in rows),
                            "small": " · ".join(small_parts),
                        }
                    )
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — отсутствие catalog.db не ломает поиск
            pass

    results.sort(key=lambda item: _search_order(item, chain))
    visible: list[dict] = []
    names: set[str] = set()
    ids: set[int] = set()
    for row in results:
        key = row["product"]["name"].strip().casefold()
        pid = row["product"]["id"]
        if key not in names and pid not in ids:
            visible.append(row)
            names.add(key)
            ids.add(pid)
            if len(visible) == limit:
                break
    photos = _product_photos([row["product"]["id"] for row in visible if row["product"]["id"] > 0], titles)
    for row in visible:
        p = row["product"]
        if p["id"] > 0:
            p.update(photo=photos.get(p["id"]) or p.get("photo"), title=titles.get(p["id"]))
    cards = [card for row in visible[:5] for card in pending_details.get(row["product"]["id"], [])]
    if cards:
        try:
            catalog_store.request_details(cards)
        except sqlite3.OperationalError:
            pass  # A busy catalogue still serves its results. The nightly scan also finds these gaps.
    return visible


# ---------- ручной импорт чека текстом ----------

def _parse_receipt_lines(text: str) -> list[tuple[str, float, float]]:
    out: list[tuple[str, float, float]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if ";" in line or "\t" in line:
            parts = [p.strip() for p in re.split(r"[;\t]+", line) if p.strip()]
            if len(parts) >= 3:
                name = parts[0]
                try:
                    qty = max(0.01, float(parts[1].replace(",", ".")))
                    price = float(re.sub(r"[^\d.,]", "", parts[2]).replace(",", "."))
                    if name and price > 0:
                        out.append((name, qty, price))
                        continue
                except ValueError:
                    pass
            elif len(parts) == 2:
                name = parts[0]
                try:
                    price = float(re.sub(r"[^\d.,]", "", parts[1]).replace(",", "."))
                    if name and price > 0:
                        out.append((name, 1.0, price))
                        continue
                except ValueError:
                    pass
        m = re.match(r"^(.+?)\s+(?:[-—–]\s*)?(\d+(?:[.,]\d{1,2})?)\s*(?:₽|р\.?|руб\.?)?$", line, re.I)
        if m:
            name = m.group(1).strip(" -—–")
            price = float(m.group(2).replace(",", "."))
            if name and price > 0:
                out.append((name, 1.0, price))
    return out


# ---------- подсказки адреса и тайлы карты ----------

def _tile_path(z: int, x: int, y: int) -> str:
    return os.path.join(config.ROOT, "data", "tiles", str(z), str(x), f"{y}.png")


def _tile_allowed() -> bool:
    key = current_spa_phone() or request.remote_addr or "?"
    now = time.time()
    with _tile_lock:
        start, count = _tile_hits.get(key, (now, 0))
        if now - start >= TILE_WINDOW_SEC:
            start, count = now, 0
        if count >= TILES_PER_HOUR:
            _tile_hits[key] = (start, count)
            return False
        count += 1
        _tile_hits[key] = (start, count)
        return True


def _serve_tile(z: int, x: int, y: int):
    edge = 1 << z
    if not (TILE_MIN_Z <= z <= TILE_MAX_Z) or not (0 <= x < edge and 0 <= y < edge):
        return Response("нет такого тайла", status=404, mimetype="text/plain")
    path = _tile_path(z, x, y)
    data: bytes | None = None
    try:
        if os.path.exists(path) and (time.time() - os.path.getmtime(path)) <= TILE_TTL_SEC:
            with open(path, "rb") as fh:
                raw = fh.read()
                if raw.startswith(PNG_MAGIC):
                    data = raw
    except OSError:
        data = None

    if data is None:
        if not _tile_allowed():
            return Response("карта ненадолго отдыхает", status=429, mimetype="text/plain")
        try:
            r = requests.get(
                TILE_URL.format(z=z, x=x, y=y),
                headers={"User-Agent": TILE_UA, "Accept": "image/png,image/*;q=0.8"},
                timeout=8.0,
            )
            if r.status_code == 404:
                return Response("тайла нет", status=404, mimetype="text/plain")
            if r.status_code != 200 or not (r.content or b"").startswith(PNG_MAGIC):
                return Response("тайл недоступен", status=502, mimetype="text/plain")
            data = r.content
            folder = os.path.dirname(path)
            os.makedirs(folder, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".tile-", suffix=".tmp", dir=folder)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001
            return Response("тайл недоступен", status=502, mimetype="text/plain")

    resp = Response(data, mimetype="image/png")
    resp.headers["Cache-Control"] = TILE_CACHE_CONTROL
    return resp


def _active_basket_id_and_stamp() -> tuple[int | None, str]:
    """Текущая активная корзина и её отпечаток состава."""
    baskets = repo.list_baskets()
    if not baskets:
        return None, ""
    raw_last = repo.get_setting(SETTING_LAST_BASKET)
    active_id = None
    if raw_last:
        try:
            cand = int(raw_last)
            if any(int(b["id"]) == cand for b in baskets):
                active_id = cand
        except (TypeError, ValueError):
            pass
    if active_id is None:
        active_id = int(baskets[0]["id"])
    items = repo.basket_items(active_id)
    if not items:
        return active_id, ""
    return active_id, cartrun.stamp_of(active_id)


def _auto_start_server_carts(phone: str, basket_id: int | None, stamp: str) -> list[str]:
    """Параллельно запустить серверные сети (дорога LINK: Лента, ВкусВилл) при правке корзины.

    Не трогает сети телефона (Пятёрочка, Самокат, Дикси, Перекрёсток, Магнит) и не мешает
    Android-сборке: по одному daemon-потоку на сеть, только если для этого stamp ещё не собрано.
    """
    if not phone or phone == users.DEMO or basket_id is None or not stamp:
        return []
    started: list[str] = []
    for code in cartrun.networks():
        if cartrun.road(code) != cartrun.LINK:
            continue
        cur = cartrun.get(code)
        if cartrun.alive(cur):
            started.append(code)
            continue
        if (
            cur is not None
            and cur.state == cartrun.DONE
            and cur.basket_id == int(basket_id)
            and cur.stamp == stamp
        ):
            continue
        try:
            rows = cartrun.start(phone, int(basket_id), only=code)
            if any(r.code == code and r.state == cartrun.GOING for r in rows):
                started.append(code)
        except Exception as exc:  # noqa: BLE001 — фоновый старт не должен ломать /api/sync
            log.warning("автосборка %s после sync не стартовала: %s", code, exc)
    return started


# ---------- регистрация маршрутов на Blueprint api ----------

def register_routes(bp: Blueprint) -> None:
    """Повесить все маршруты React SPA на общий Blueprint /api (endpoint api.*)."""

    @bp.route("/auth/me", methods=["GET"])
    def spa_auth_me():
        phone = current_spa_phone()
        payload = {"ok": True, **_auth_payload(phone)}
        update = apk.update_for(device.app_version())
        if update and apk.fresh_path():
            payload["apkUpdate"] = {
                "version": update["version"],
                "canDownload": update["can_download"],
                "url": url_for("apk.download", _external=True),
            }
        response = _json(payload)
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.route("/auth/login", methods=["POST"])
    def spa_auth_login():
        body = request.get_json(silent=True) or {}
        typed = str(body.get("phone") or "").strip()
        pin = str(body.get("pin") or "").strip()

        if web_login.failure_counts()[0] >= web_login.FAIL_LIMIT:
            return _json({"ok": False, "error": web_login.TOO_MANY}, 429)

        if users.pin_is_set():
            attempt = web_login.reserve_attempt()
            if attempt is None:
                return _json({"ok": False, "error": web_login.TOO_MANY}, 429)
            web_login._pause_before_check()
            if not users.check_pin(pin):
                return _json({"ok": False, "error": web_login.BAD_PIN}, 401)
            web_login.release_attempt(attempt)

        phone, err = users.resolve_login(typed)
        if not phone:
            web_login.note_failure()
            return _json({"ok": False, "error": err or "Введите номер телефона"}, 400)

        users.open_workspace(phone)
        auth.sign_in(phone)
        token = issue_token(phone)
        snapshot = serialize_workspace(phone)
        try:
            from app.catalog import link

            link.link_soon(phone)
        except Exception:  # noqa: BLE001
            pass
        return _json({"ok": True, "token": token, "auth": _auth_payload(phone), "snapshot": snapshot})

    @bp.route("/auth/demo", methods=["POST"])
    def spa_auth_demo():
        users.open_workspace(users.DEMO)
        web_login._fill_demo()
        auth.sign_in(users.DEMO)
        token = issue_token(users.DEMO)
        snapshot = serialize_workspace(users.DEMO)
        return _json({"ok": True, "token": token, "auth": _auth_payload(users.DEMO), "snapshot": snapshot})

    @bp.route("/auth/logout", methods=["POST"])
    def spa_auth_logout():
        auth.sign_out()
        return _json({"ok": True})

    @bp.route("/sync", methods=["GET", "POST"])
    def spa_sync():
        phone = current_spa_phone()
        if not phone:
            return _json({"ok": False, "error": "Требуется вход"}, 401)
        users.open_workspace(phone)

        if request.method == "GET":
            return _json({"ok": True, "snapshot": serialize_workspace(phone)})

        if phone == users.DEMO:
            return _json({"ok": False, "error": auth.DEMO_READ_ONLY}, 403)

        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _json({"ok": False, "error": "Ожидается JSON-объект"}, 400)

        prev_bid, prev_stamp = _active_basket_id_and_stamp()
        _apply_sync(body)
        next_bid, next_stamp = _active_basket_id_and_stamp()
        server_started: list[str] = []
        if next_bid is not None and next_stamp and (
            next_bid != prev_bid or next_stamp != prev_stamp
        ):
            server_started = _auto_start_server_carts(phone, next_bid, next_stamp)
        return _json({
            "ok": True,
            "syncedAt": _now_iso(),
            "basketStamp": next_stamp,
            "serverStarted": server_started,
        })

    @bp.route("/runs", methods=["GET"])
    def spa_runs():
        phone = current_spa_phone()
        if not phone:
            return _json({"ok": False, "error": "Требуется вход"}, 401)
        users.open_workspace(phone)
        stores = repo.list_stores()
        addr = location.address()
        apply_bonuses_raw = repo.get_setting(SETTING_APPLY_BONUSES)
        active_bid, active_stamp = _active_basket_id_and_stamp()
        current_pv = _prices_version()
        client_pv_raw = request.args.get("pv")
        client_pv: int | None = None
        if client_pv_raw is not None and str(client_pv_raw).strip() != "":
            try:
                client_pv = int(client_pv_raw)
            except (TypeError, ValueError):
                client_pv = None

        prices_payload: dict | None
        if client_pv is not None and client_pv == current_pv:
            prices_payload = None
        else:
            raw_bid = request.args.get("basket")
            target_bid = active_bid
            if raw_bid is not None and str(raw_bid).strip() != "":
                try:
                    target_bid = int(raw_bid)
                except (TypeError, ValueError):
                    target_bid = active_bid
            if target_bid is not None:
                items = repo.basket_items(target_bid)
                pids = [int(it["product_id"]) for it in items if it.get("product_id") is not None]
                prices_payload = _bulk_latest_prices(pids)
            else:
                prices_payload = _bulk_latest_prices()

        return _json(
            {
                "ok": True,
                "storeQuotes": _serialize_quotes(stores, addr),
                "connections": _serialize_connections(stores),
                "prices": prices_payload,
                "pricesVersion": current_pv,
                "storePerks": _load_json_setting(SETTING_STORE_PERKS, {}),
                "applyBonuses": False if apply_bonuses_raw == "0" else True,
                "basketStamp": active_stamp or None,
                "syncedAt": _now_iso(),
            }
        )

    @bp.route("/search", methods=["GET"])
    def spa_search():
        phone = current_spa_phone()
        if not phone:
            return _json({"ok": False, "error": "Требуется вход"}, 401)
        users.open_workspace(phone)
        q = (request.args.get("q") or "").strip()
        return _json({"ok": True, "results": _search_products(q, limit=30, chain=request.args.get("store") or None)})

    @bp.get("/catalog/images/<digest>.webp")
    def spa_thumbnail(digest: str):
        from app.catalog.photos import path_for
        if not re.fullmatch("[0-9a-f]{64}", digest):
            abort(404)
        path = path_for(digest)
        if not path.is_file():
            abort(404)
        response = send_file(path, mimetype="image/webp", max_age=31536000, etag=digest)
        response.cache_control.immutable = True
        return response

    @bp.get("/catalog/photo/<chain>/<sku>")
    def spa_catalog_photo(chain: str, sku: str):
        if not current_spa_phone():
            return _json({"ok": False, "error": "Требуется вход"}, 401)
        from app.catalog import store
        rows = store.rows_by_skus([(chain, sku)])
        return _json({"ok": True, "photo": _thumbnail_url(rows[0].get("photo_digest")) if rows else None})

    @bp.route("/replacements", methods=["GET", "POST"])
    def spa_replacements():
        from app.web import replacements

        phone = current_spa_phone()
        if not phone:
            return _json({"ok": False, "error": "Требуется вход"}, 401)
        users.open_workspace(phone)
        args = request.args
        codes = list(dict.fromkeys(args.getlist("store")))
        unit, original = args.get("unit"), args.get("exclude", type=int)
        if (not 1 <= len(codes) <= 2 or any(code not in chains.orderable() for code in codes)
                or unit not in ("pcs", "kg") or not original):
            return _json({"ok": False, "error": "Выберите вариант корзины заново"}, 400)
        q = (args.get("q") or "").strip()[:200]
        choosing = request.method == "POST"
        sku, pid = args.get("sku"), args.get("product", type=int)
        if choosing and (len(codes) != 1 or not sku or not pid):
            return _json({"ok": False, "error": "Выберите товар из списка"}, 400)
        if not choosing and len(q) < 2:
            return _json({"ok": True, "results": []})
        observed_stores = set()
        rows = replacements.search(q, codes, unit, original, *_bought_ids(),
                                   sku=sku if choosing else None, selected=pid if choosing else None, observed_stores=observed_stores)
        for row in rows:
            row["product"]["photo"] = _thumbnail_url(row["product"]["photo"])
        if choosing:
            row = rows[0] if rows else None
            if row and replacements.materialize(row, codes[0]):
                return _json({"ok": True, "item": row})
            return _json({"ok": False, "error": "Магазин больше не подтверждает эту замену"}, 409)
        photos = _product_photos([r["product"]["id"] for r in rows if r["product"]["id"] > 0])
        for row in rows:
            product = row["product"]
            product["photo"] = product.get("photo") or photos.get(product["id"])
        return _json({"ok": True, "results": rows, "observedStores": sorted(observed_stores)})

    @bp.route("/address/suggest", methods=["GET"])
    def spa_address_suggest():
        q = (request.args.get("q") or "").strip()
        try:
            return _json({"ok": True, "items": address_search.suggest(q, limit=6)})
        except address_search.SearchUnavailable:
            return _json({"ok": False, "error": "Поиск адресов временно недоступен"}, 503)

    @bp.route("/address/reverse", methods=["GET"])
    def spa_address_reverse():
        try:
            lat = float((request.args.get("lat") or "").replace(",", "."))
            lon = float((request.args.get("lon") or "").replace(",", "."))
        except (TypeError, ValueError):
            return _json({"ok": True, "address": ""})
        try:
            addr = geo.reverse(lat, lon) or ""
        except Exception:  # noqa: BLE001
            addr = ""
        return _json({"ok": True, "address": addr})

    @bp.route("/map/tile/<int:z>/<int:x>/<int:y>.png", methods=["GET"])
    def spa_map_tile(z: int, x: int, y: int):
        return _serve_tile(z, x, y)

    @bp.route("/receipts/paste", methods=["POST"])
    def spa_receipts_paste():
        phone = current_spa_phone()
        if not phone:
            return _json({"ok": False, "error": "Требуется вход"}, 401)
        if phone == users.DEMO:
            return _json({"ok": False, "error": auth.DEMO_READ_ONLY}, 403)
        users.open_workspace(phone)
        body = request.get_json(silent=True) or {}
        store_code = str(body.get("store_code") or "").strip().lower()
        text = str(body.get("text") or "").strip()
        lines = _parse_receipt_lines(text)
        if not lines:
            return _json(
                {
                    "ok": False,
                    "error": "Не удалось распознать строки чека. Формат: «Название; кол-во; цена».",
                },
                400,
            )

        store = repo.get_store(store_code)
        if not store or not chains.accepts(store_code=store_code):
            return _json({"ok": False, "error": "Выберите сеть из списка"}, 400)

        from app.importers.bundle import store_receipts
        from app.importers.ofd_pdf import Receipt, ReceiptRow

        today = datetime.now().strftime("%Y-%m-%d")
        digest = hashlib.sha256(f"{store.code}:{today}:{text}".encode("utf-8")).hexdigest()[:16]
        rows = [ReceiptRow(name, qty, price, round(qty * price, 2)) for name, qty, price in lines]
        receipt = Receipt(today, store.name, rows, round(sum(row.total for row in rows), 2))
        result = store_receipts([{"key": f"paste-{digest}", "receipt": receipt}],
                                store_code=store.code, source="paste")
        return _json(
            {
                "ok": True,
                "imported": result["rows"],
                "total": result["total"],
                "snapshot": serialize_workspace(phone),
            }
        )

    @bp.post("/fns/device-progress")
    def spa_fns_device_progress():
        phone = current_spa_phone()
        if not phone or phone == users.DEMO:
            return _json({"ok": False}, 403 if phone else 401)
        body = request.get_json(silent=True)
        phases = ("running", "resuming", "waiting", "partial", "retry", "denied", "account", "upload", "cabinet", "done")
        if not isinstance(body, dict) or body.get("phase") not in phases or not all(
            type(body.get(k)) is int and 0 <= body[k] <= 2**31 - 1 for k in ("position", "total")
        ):
            return _json({"ok": False}, 400)
        users.open_workspace(phone)
        state = {k: body[k] for k in ("phase", "position", "total")}
        state["receivedAt"] = datetime.now().isoformat(timespec="seconds")
        repo.set_setting("fns.device_progress", json.dumps(state))
        return _json({"ok": True})

    @bp.get("/fns/status")
    def spa_fns_status():
        phone = current_spa_phone()
        if not phone:
            return _json({"ok": False, "error": "Требуется вход"}, 401)
        users.open_workspace(phone)
        from app.fns import sync as cabinet
        from app.fns import background
        running = background.running(phone)
        state = cabinet.status()
        error = repo.get_setting("fns.sync_error") or ""
        last = state.get("last_result") or {}
        return _json({"ok": True, "running": running, "message": error or (cabinet.headline(last) if last else ""),
                      "failed": bool(error), "needsConnection": bool(repo.get_setting(background.PAUSED)),
                      "lastSync": state.get("last_sync_at"),
                      "device": json.loads(repo.get_setting("fns.device_progress") or "null")})

    @bp.get("/receipts/other")
    def spa_other_receipts():
        phone = current_spa_phone()
        if not phone:
            return _json({"ok": False, "error": "Требуется вход"}, 401)
        users.open_workspace(phone)
        if key := request.args.get("key"):
            return _json({"ok": True, "lines": repo.other_receipt_lines(key)})
        offset = max(0, request.args.get("offset", default=0, type=int))
        page = repo.other_store_receipts(limit=25, offset=offset)
        return _json({"ok": True, "receipts": [
            {k: r[k] for k in ("key", "date", "store", "total")} for r in page]})

    @bp.post("/receipts/needed")
    def spa_receipts_needed():
        phone = current_spa_phone()
        if not phone or phone == users.DEMO:
            return _json({"ok": False, "error": "Войдите в свой профиль"}, 403 if phone else 401)
        users.open_workspace(phone)
        body = request.get_json(silent=True) or {}
        keys = body.get("keys") if isinstance(body, dict) else None
        if not isinstance(keys, list) or len(keys) > 50 or not all(isinstance(k, str) and 0 < len(k) <= 256 for k in keys):
            return _json({"ok": False, "error": "Неверный список чеков"}, 400)
        known = repo.imported_receipt_keys() | repo.other_receipts_with_contents()
        return _json({"ok": True, "needed": [key for key in dict.fromkeys(keys) if key not in known]})

    @bp.post("/receipts/file")
    def spa_receipts_file():
        phone = current_spa_phone()
        if not phone:
            return _json({"ok": False, "error": "Требуется вход"}, 401)
        if phone == users.DEMO:
            return _json({"ok": False, "error": auth.DEMO_READ_ONLY}, 403)
        users.open_workspace(phone)
        body = request.get_json(silent=True) or {}
        encoded = body.get("content") if isinstance(body, dict) else None
        if not isinstance(encoded, str) or not encoded or len(encoded) > 24 * 1024 * 1024:
            return _json({"ok": False, "error": "Выберите файл чека до 18 МБ"}, 400)
        try:
            data = base64.b64decode(encoded, validate=True)
            from app.importers.bundle import import_bundle
            if data.startswith(b"%PDF-"):
                from app.importers.ofd_pdf import import_receipt
                with tempfile.TemporaryDirectory() as directory:
                    path = os.path.join(directory, "receipt.pdf")
                    with open(path, "wb") as output:
                        output.write(data)
                    result = import_receipt(path)
            else:
                result = import_bundle(data.decode("utf-8-sig"))
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError, binascii.Error):
            return _json({"ok": False, "error": "Не удалось прочитать чек. Выберите фискальный JSON или PDF с позициями."}, 400)
        counts = {k: int(result[k]) for k in ("receipts", "rows", "skipped")}
        counts.update(other=len(result.get("other") or []), failed=len(result.get("failed") or []))
        if not (counts["receipts"] or counts["skipped"] or counts["other"]):
            return _json({"ok": False, "error": "В файле не найдены чеки с позициями"}, 400)
        return _json({"ok": True, **counts, "storedCount": repo.saved_receipt_count()})

    @bp.route("/fns/sync", methods=["POST"])
    def spa_fns_sync():
        phone = current_spa_phone()
        if not phone:
            return _json({"ok": False, "error": "Требуется вход"}, 401)
        if phone == users.DEMO:
            return _json({"ok": False, "error": auth.DEMO_READ_ONLY}, 403)
        users.open_workspace(phone)
        body = request.get_json(silent=True) or {}
        fns_phone = str(body.get("phone") or "").strip() or phone
        keys = str(body.get("keys") or "").strip()
        try:
            from app.fns import background
            from app.fns import sync as cabinet
            from app.fns.client import CabinetError

            if keys:
                unpacked = keys if keys.startswith("{") else cabinet.unpack_keys(keys)
                background.connect(phone, unpacked, fns_phone)
            elif not cabinet.is_connected():
                return _json(
                    {
                        "ok": False,
                        "error": "Автоимпорт не подключён. Откройте «Мои чеки» в настройках.",
                    },
                    400,
                )
            # Запускаем обновление чеков в фоне (один поток на номер)
            if repo.get_setting(background.PAUSED):
                return _json({"ok": False, "error": "Подтвердите подключение автоимпорта."}, 409)
            background.start(phone)
        except CabinetError as exc:
            return _json({"ok": False, "error": str(exc)}, 400)
        except ValueError as exc:
            return _json({"ok": False, "error": f"Некорректный ключ ФНС: {exc}"}, 400)
        except Exception:
            log.warning("Не удалось подключить автоимпорт чеков")
            return _json({"ok": False, "error": "Не удалось подключить автоимпорт. Попробуйте позже."}, 400)

        return _json({"ok": True, "snapshot": serialize_workspace(phone)})

    @bp.route("/fns/disconnect", methods=["POST"])
    def spa_fns_disconnect():
        phone = current_spa_phone()
        if not phone:
            return _json({"ok": False, "error": "Требуется вход"}, 401)
        if phone == users.DEMO:
            return _json({"ok": False, "error": auth.DEMO_READ_ONLY}, 403)
        users.open_workspace(phone)
        from app.fns import background
        background.disconnect(phone)
        return _json({"ok": True, "snapshot": serialize_workspace(phone)})
