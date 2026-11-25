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

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import datetime
from typing import Any

import requests
from flask import Blueprint, Response, current_app, jsonify, make_response, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app import cartrun, config, db, geo, location, repo, store_accounts, users
from app.models import Card, Offer, Product
from app.web import auth, login as web_login

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

PHOTON_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)


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


def _cors(resp):
    origin = request.headers.get("Origin")
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
    else:
        resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, Accept"
    resp.headers["Access-Control-Max-Age"] = "600"
    resp.headers["Vary"] = "Origin"
    return resp


def _json(payload: dict, status: int = 200):
    return _cors(make_response(jsonify(payload), status))


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


def _product_photos(pids: list[int]) -> dict[int, str]:
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
        if not all_skus:
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
                    url = str(r.get("image") or "")
                    if url.startswith(("https://", "http://")):
                        return url
                return None

            for pid, ps in pairs.items():
                own = [found[p] for p in ps if p in found]
                url = _img(own)
                if not url:
                    for item_id in dict.fromkeys(r["item_id"] for r in own if r.get("item_id")):
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
    row = db.one("SELECT COALESCE(MAX(id), 0) AS v FROM store_prices")
    return int(row["v"] or 0) if row else 0


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
            SELECT store_product_id, MAX(id) AS max_id
            FROM store_prices
            GROUP BY store_product_id
        ) latest ON latest.store_product_id = sp.id
        JOIN store_prices p ON p.id = latest.max_id
        WHERE m.confirmed = 1 {pid_filter}
        ORDER BY p.fetched_at DESC
        """,
        tuple(params),
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
    photos = _product_photos([int(p.id) for p in products_raw if p.id is not None])
    products = [
        {
            "id": int(p.id),
            "name": p.name,
            "brand": p.brand,
            "weight_g": p.weight_g,
            "unit": "kg" if p.unit == "kg" else "pcs",
            "category": p.category,
            "active": bool(p.active),
            "photo": photos.get(int(p.id)) if p.id is not None else None,
        }
        for p in products_raw
        if p.id is not None
    ]
    next_product_id = max((p["id"] for p in products), default=0) + 1

    prices = _bulk_latest_prices()

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
                "fallback_product_id": (
                    int(r["fallback_product_id"]) if r.get("fallback_product_id") is not None else None
                ),
            }
            for r in rows
        ]

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
    history = []
    for h in history_raw:
        sid = int(h["store_id"]) if h.get("store_id") is not None else 0
        st = store_by_id.get(sid)
        history.append(
            {
                "id": int(h["id"]),
                "date": str(h["date"] or "")[:10],
                "store_id": sid,
                "store_code": str(h.get("store_code") or (st.code if st else "unknown")),
                "store_name": str(h.get("store_name") or (st.name if st else "Магазин")),
                "product_id": int(h["product_id"]) if h.get("product_id") is not None else None,
                "raw_name": str(h["raw_name"] or ""),
                "qty": float(h["qty"] or 1),
                "unit_price": float(h["unit_price"] or 0),
                "total": float(h["total"] or 0),
                "receipt_key": str(h.get("receipt_key") or f"row-{h['id']}"),
            }
        )

    receipts_raw = repo.imported_receipts()
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
        pid = int(existing.id)
    else:
        pid = repo.upsert_product(
            Product(
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
                        in_stock=bool(r.get("in_stock", True)),
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
                valid_pids = {
                    int(r["id"]) for r in conn.execute("SELECT id FROM products").fetchall()
                }
                for key, item_list in raw_items.items():
                    try:
                        orig_bid = int(key)
                    except (TypeError, ValueError):
                        continue
                    real_bid = basket_id_map.get(orig_bid)
                    if not real_bid or real_bid not in kept_ids:
                        continue
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
                        conn.execute(
                            "INSERT OR REPLACE INTO basket_items (basket_id, product_id, qty, fallback_product_id)"
                            " VALUES (?,?,?,?)",
                            (real_bid, real_pid, qty, fb_id),
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
            elif not want and cur.connected:
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

def _search_products(query: str, limit: int = 30) -> list[dict]:
    q = " ".join((query or "").split())
    if len(q) < 2:
        return []
    low = q.lower()
    stores = repo.list_stores()
    prices_map = _bulk_latest_prices()
    all_prods = repo.list_products(active_only=True)
    own_hits = [
        p for p in all_prods
        if low in (p.name or "").lower()
        or (p.brand and low in p.brand.lower())
        or (p.category and low in p.category.lower())
    ]
    own_hits.sort(
        key=lambda p: (
            0 if (p.name or "").lower().startswith(low) else 1,
            -len(prices_map.get(str(int(p.id)), {})) if p.id is not None else 0,
            len(p.name or ""),
        )
    )
    photos = _product_photos([int(p.id) for p in own_hits[:limit] if p.id is not None])

    results: list[dict] = []
    seen_names: set[str] = set()

    for p in own_hits[: limit // 2 or 15]:
        if p.id is None:
            continue
        p_prices = prices_map.get(str(int(p.id)), {})
        chains_n = len(p_prices)
        small_parts = [x for x in ("кг" if p.unit == "kg" else "шт", p.brand or "") if x]
        if chains_n:
            small_parts.append(f"в {chains_n} сет.")
        results.append(
            {
                "product": {
                    "id": int(p.id),
                    "name": p.name,
                    "brand": p.brand,
                    "weight_g": p.weight_g,
                    "unit": "kg" if p.unit == "kg" else "pcs",
                    "category": p.category,
                    "active": True,
                    "photo": photos.get(int(p.id)),
                },
                "prices": p_prices,
                "chainsCount": chains_n,
                "small": " · ".join(small_parts),
            }
        )
        seen_names.add(p.name.strip().lower())

    # Добираем из единого каталога data/catalog.db одним батч-запросом (без N+1)
    # и поднимаем товары, известные нескольким сетям, выше позиций из «1 сет.»
    remaining = max(0, limit - len(results))
    if remaining > 0:
        try:
            from app.catalog import store as catalog_store

            conn = catalog_store.connect()
            try:
                hits = catalog_store.search_items(q, limit=remaining * 3, conn=conn, by_name=True) or []
                candidate_hits = []
                for h in hits:
                    item_id = int(h.get("id") or 0)
                    name = str(h.get("name") or "").strip()
                    if not item_id or not name or name.lower() in seen_names:
                        continue
                    candidate_hits.append((item_id, name, h))
                rows_by_item = catalog_store.batch_item_rows(
                    [item_id for item_id, _, _ in candidate_hits], conn=conn
                )
                catalog_results: list[dict] = []
                for item_id, name, h in candidate_hits:
                    rows = rows_by_item.get(item_id) or []
                    cat_prices: dict[str, dict] = {}
                    photo_url: str | None = None
                    for r in rows:
                        if not photo_url:
                            img = str(r.get("image") or "")
                            if img.startswith(("https://", "http://")):
                                photo_url = img
                        chain = str(r.get("chain") or "").strip().lower()
                        price_val = r.get("price")
                        if not chain or price_val is None or chain in cat_prices:
                            continue
                        try:
                            pv = float(price_val)
                        except (TypeError, ValueError):
                            continue
                        if pv <= 0:
                            continue
                        cat_prices[chain] = {
                            "store_code": chain,
                            "sku": str(r.get("sku") or ""),
                            "raw_name": str(r.get("name") or name),
                            "price": pv,
                            "unit": "kg" if r.get("unit") == "kg" else "pcs",
                            "weight_g": float(r["weight_g"]) if r.get("weight_g") is not None else None,
                            "price_per_kg": None,
                            "in_stock": bool(r.get("in_stock", True)),
                            "fetched_at": str(r.get("fetched_at") or _now_iso()),
                            "source": "live",
                        }
                    chains_n = max(len(cat_prices), int(h.get("chains_n") or h.get("chains") or 0))
                    unit = "kg" if h.get("unit") == "kg" else "pcs"
                    small_parts = [x for x in ("кг" if unit == "kg" else "шт", h.get("brand") or "") if x]
                    if chains_n:
                        small_parts.append(f"каталог · {chains_n} сет.")
                    else:
                        small_parts.append("каталог сетей")
                    catalog_results.append(
                        {
                            "product": {
                                "id": -item_id,
                                "name": name,
                                "brand": h.get("brand"),
                                "weight_g": float(h["weight_g"]) if h.get("weight_g") is not None else None,
                                "unit": unit,
                                "category": h.get("category"),
                                "active": True,
                                "photo": photo_url,
                            },
                            "prices": cat_prices,
                            "chainsCount": chains_n,
                            "small": " · ".join(small_parts),
                        }
                    )
                    seen_names.add(name.lower())

                catalog_results.sort(
                    key=lambda item: (
                        0 if item["chainsCount"] >= 2 else 1,
                        0 if item["product"]["name"].lower().startswith(low) else 1,
                        -item["chainsCount"],
                        0 if item["prices"] else 1,
                        0 if item["product"].get("photo") else 1,
                        len(item["product"]["name"]),
                    )
                )
                results.extend(catalog_results[:remaining])
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — отсутствие catalog.db не ломает поиск
            pass

    return results[:limit]


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

def _suggest_address_rich(query: str, limit: int = 6) -> list[dict]:
    q = " ".join((query or "").split())
    if len(q) < 2:
        return []
    out: list[dict] = []
    seen: set[str] = set()

    def _add(label: str, full: str, lat: float, lon: float) -> None:
        key = " ".join(label.lower().split())
        if not key or key in seen:
            return
        seen.add(key)
        out.append({"label": label, "full": full or label, "lat": lat, "lon": lon})

    # 1. Photon (быстрый префиксный автокомплит без троттлинга)
    try:
        r = requests.get(
            geo.PHOTON_URL,
            params={"q": q, "limit": limit},
            headers={"User-Agent": PHOTON_UA, "Accept": "application/json"},
            timeout=3.5,
        )
        if r.status_code == 200:
            for feat in (r.json().get("features") or []):
                props = feat.get("properties") or {}
                cc = str(props.get("countrycode") or "").upper()
                if cc and cc != "RU":
                    continue
                coords = (feat.get("geometry") or {}).get("coordinates") or []
                if len(coords) != 2:
                    continue
                city = props.get("city") or props.get("town") or props.get("village") or props.get("state") or ""
                street = props.get("street") or (props.get("name") if props.get("name") != city else "") or ""
                house = props.get("housenumber") or ""
                label = ", ".join(x for x in (city, street, house) if x) or str(props.get("name") or q)
                full = ", ".join(x for x in (props.get("state"), city, props.get("district"), street, house) if x)
                _add(label, full, float(coords[1]), float(coords[0]))
    except Exception:  # noqa: BLE001
        pass

    # 2. Nominatim (точный поиск номеров домов)
    if len(out) < limit:
        try:
            r = requests.get(
                geo.NOMINATIM_URL,
                params={"format": "json", "addressdetails": "1", "countrycodes": "ru", "limit": limit, "q": q},
                headers={"User-Agent": geo.USER_AGENT, "Accept-Language": "ru"},
                timeout=4.0,
            )
            if r.status_code == 200:
                for item in (r.json() or []):
                    addr = item.get("address") or {}
                    city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("state") or ""
                    road = addr.get("road") or addr.get("pedestrian") or addr.get("residential") or ""
                    house = addr.get("house_number") or ""
                    label = ", ".join(x for x in (city, road, house) if x) or str(item.get("display_name") or q)
                    _add(label, str(item.get("display_name") or label), float(item["lat"]), float(item["lon"]))
        except Exception:  # noqa: BLE001
            pass

    return out[:limit]


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


_fns_lock = threading.Lock()
_fns_running: set[str] = set()


def _start_fns_sync(phone: str) -> bool:
    """Запустить загрузку чеков из кабинета ФНС в отдельном потоке (не чаще одного на номер)."""
    with _fns_lock:
        if phone in _fns_running:
            return False
        _fns_running.add(phone)

    def _worker() -> None:
        from app.fns import sync as cabinet

        try:
            users.open_workspace(phone)
            cabinet.sync()
        except Exception as exc:  # noqa: BLE001
            log.warning("фоновая загрузка чеков ФНС для %s: %s", phone, exc)
        finally:
            try:
                users.deactivate()
            finally:
                with _fns_lock:
                    _fns_running.discard(phone)

    threading.Thread(target=_worker, daemon=True, name="fns-sync").start()
    return True


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
        return _json({"ok": True, **_auth_payload(phone)})

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
        return _json({"ok": True, "results": _search_products(q, limit=30)})

    @bp.route("/address/suggest", methods=["GET"])
    def spa_address_suggest():
        q = (request.args.get("q") or "").strip()
        return _json({"ok": True, "items": _suggest_address_rich(q, limit=6)})

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
        store_code = str(body.get("store_code") or "pyaterochka").strip().lower()
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

        store = repo.get_store(store_code) or repo.list_stores()[0]
        today = datetime.now().strftime("%Y-%m-%d")
        digest = hashlib.sha256(f"{store.code}:{today}:{text}".encode("utf-8")).hexdigest()[:16]
        rkey = f"paste-{digest}"
        total = 0.0
        for name, qty, price in lines:
            existing = repo.find_product_by_name(name)
            if existing and existing.id is not None:
                pid = int(existing.id)
            else:
                pid = repo.upsert_product(Product(name=name, unit="pcs", active=True))
            line_total = round(qty * price, 2)
            total = round(total + line_total, 2)
            repo.add_history_row(
                date=today,
                store_id=int(store.id),
                product_id=pid,
                raw_name=name,
                qty=qty,
                unit_price=price,
                total=line_total,
                receipt_key=rkey,
            )
        repo.mark_receipt_imported(rkey, today, store.name, total, len(lines), source="paste")
        return _json(
            {
                "ok": True,
                "imported": len(lines),
                "total": total,
                "snapshot": serialize_workspace(phone),
            }
        )

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
            from app.fns import sync as cabinet
            from app.fns.client import CabinetError

            if keys:
                unpacked = cabinet.unpack_keys(keys) if not keys.startswith("{") else json.loads(keys)
                cabinet.connect_with_keys(unpacked, phone=fns_phone)
            elif not cabinet.is_connected():
                return _json(
                    {
                        "ok": False,
                        "error": "Кабинет ФНС не подключён — вставьте ключ из lkdr.nalog.ru или войдите через приложение.",
                    },
                    400,
                )
            # Запускаем обновление чеков в фоне (один поток на номер)
            _start_fns_sync(phone)
        except CabinetError as exc:
            return _json({"ok": False, "error": str(exc)}, 400)
        except ValueError as exc:
            return _json({"ok": False, "error": f"Некорректный ключ ФНС: {exc}"}, 400)
        except Exception as exc:  # noqa: BLE001
            log.warning("ошибка /api/fns/sync: %s", exc, exc_info=True)
            return _json({"ok": False, "error": str(exc)}, 400)

        return _json({"ok": True, "snapshot": serialize_workspace(phone)})

    @bp.route("/fns/disconnect", methods=["POST"])
    def spa_fns_disconnect():
        phone = current_spa_phone()
        if not phone:
            return _json({"ok": False, "error": "Требуется вход"}, 401)
        if phone == users.DEMO:
            return _json({"ok": False, "error": auth.DEMO_READ_ONLY}, 403)
        users.open_workspace(phone)
        try:
            from app.fns import sync as cabinet

            cabinet.disconnect()
        except Exception:  # noqa: BLE001
            pass
        return _json({"ok": True, "snapshot": serialize_workspace(phone)})
