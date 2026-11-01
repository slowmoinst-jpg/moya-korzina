"""Статус позиции корзины в сетях и подстановка запасного товара.

Коды статуса позиции в сети:
  HAVE = "have"             — есть в наличии и со свежей ценой
  GONE = "gone"             — нет в наличии
  NOT_KNOWN = "not_known"   — сеть не знает: нет подтверждённого сопоставления
  NO_DATA = "no_data"       — связь есть, свежей цены нет
  SEARCHING = "searching"   — ищем: сеть сейчас собирает эту корзину
"""
from __future__ import annotations

import copy
from typing import Any

from app import freshness, location, repo

HAVE = "have"
GONE = "gone"
NOT_KNOWN = "not_known"
NO_DATA = "no_data"
SEARCHING = "searching"

__all__ = ["HAVE", "GONE", "NOT_KNOWN", "NO_DATA", "SEARCHING", "status", "fallback_in", "of_basket", "swap_lines"]


def _resolve_store(store: Any):
    """Приводит аргумент store к объекту Store."""
    if store is None:
        return None
    if hasattr(store, "id") and hasattr(store, "code"):
        return store
    if isinstance(store, (int, str)):
        return repo.get_store(store)
    return None


def status(product_id: int, store: Any, point_key: str | None = None) -> str:
    """Статус товара в конкретной сети.

    нет repo.confirmed_mapping(product_id, store.id) -> NOT_KNOWN;
    freshness.price_for(product_id, store.id, point_key=point_key) пусто -> NO_DATA;
    снимок in_stock ложен -> GONE;
    иначе HAVE.
    """
    st = _resolve_store(store)
    if st is None:
        return NOT_KNOWN

    pid = int(product_id)
    if not repo.confirmed_mapping(pid, st.id):
        return NOT_KNOWN

    snap = freshness.price_for(pid, st.id, point_key=point_key)
    if not snap:
        return NO_DATA

    if not snap.get("in_stock", True):
        return GONE

    return HAVE


def fallback_in(item: dict, store: Any, point_key: str | None = None) -> int | None:
    """Определяет, встаёт ли запасной товар для позиции в этой сети.

    Возвращает id запасного товара, если:
    1. У позиции он задан (item.get('fallback_product_id'));
    2. Единица запасного (products.unit, шт/кг) совпадает с единицей прежнего;
    3. Статус прежнего в этой сети NOT_KNOWN или GONE;
    4. Статус запасного в этой сети HAVE или NO_DATA (не NOT_KNOWN и не GONE).
    Иначе возвращает None.
    """
    fb_id = item.get("fallback_product_id")
    if not fb_id:
        return None
    try:
        fb_id = int(fb_id)
    except (TypeError, ValueError):
        return None

    pid = item.get("product_id")
    if not pid or int(pid) == fb_id:
        return None
    pid = int(pid)

    st = _resolve_store(store)
    if st is None:
        return None

    # Единица запасного (products.unit, шт/кг) должна совпадать с единицей прежнего
    fb_prod = repo.get_product(fb_id)
    if not fb_prod:
        return None

    orig_unit = item.get("unit")
    if not orig_unit:
        orig_prod = repo.get_product(pid)
        orig_unit = orig_prod.unit if orig_prod else None

    if orig_unit != fb_prod.unit:
        return None

    # Статус прежнего в этой сети NOT_KNOWN или GONE
    st_orig = status(pid, st, point_key=point_key)
    if st_orig not in (NOT_KNOWN, GONE):
        return None

    # Статус запасного там HAVE или NO_DATA (не NOT_KNOWN и не GONE)
    st_fb = status(fb_id, st, point_key=point_key)
    if st_fb not in (HAVE, NO_DATA):
        return None

    return fb_id


def of_basket(basket_id: int) -> dict[int, dict[str, dict]]:
    """Статусы позиций корзины по всем магазинам.

    Для каждой позиции корзины и каждой сети repo.list_stores():
    -> {"status": код, "fallback": id запасного или None}
    """
    from app import cartrun
    from app.web.screens import basket as basket_screen

    b_id = int(basket_id)
    items = repo.basket_items(b_id)
    stores = repo.list_stores()
    pkey = location.point_key()

    # Идёт ли опрос цен корзины
    job = basket_screen.job_of(b_id)
    job_running = bool(job and job.get("status") == "running")

    # Идёт ли сборка этой корзины в конкретной сети
    current_stamp = cartrun.stamp_of(b_id)
    going_stores = set()
    try:
        c_rows = cartrun.rows()
        for r in c_rows:
            if r.state == cartrun.GOING and r.basket_id == b_id and r.stamp == current_stamp:
                going_stores.add(r.code)
    except Exception:
        pass

    res: dict[int, dict[str, dict]] = {}
    for item in items:
        pid = int(item["product_id"])
        store_map: dict[str, dict] = {}
        for st in stores:
            st_code = status(pid, st, point_key=pkey)
            if st_code == NO_DATA and (job_running or st.code in going_stores):
                st_code = SEARCHING
            fb = fallback_in(item, st, point_key=pkey)
            store_map[st.code] = {"status": st_code, "fallback": fb}
        res[pid] = store_map

    return res


def swap_lines(lines: list, store: Any, basket_id: int | None = None) -> list:
    """Подставляет запасной товар в позиции наряда сети, где это применимо.

    Для каждой строки (BasketLine или строка места) находит запасной её product_id
    в корзине (basket_id, а без него — последняя корзина repo.list_baskets()[0]) и,
    где fallback_in дал id, отдаёт копию строки с product_id, name и unit запасного
    и тем же qty.

    Для наряда нужен настоящий артикул сети: нет у запасного
    repo.confirmed_mapping(fb, store.id, real_only=True) — строка остаётся прежней.
    """
    if not lines:
        return []

    st = _resolve_store(store)
    if st is None:
        return list(lines)

    if basket_id is None:
        baskets = repo.list_baskets()
        if not baskets:
            return list(lines)
        b_id = int(baskets[0]["id"])
    else:
        b_id = int(basket_id)

    b_items = repo.basket_items(b_id)
    if not b_items:
        return list(lines)

    items_by_pid = {int(it["product_id"]): it for it in b_items}
    pkey = location.point_key()

    out = []
    for line in lines:
        pid = getattr(line, "product_id", None)
        if pid is None and isinstance(line, dict):
            pid = line.get("product_id")
        if pid is None:
            out.append(line)
            continue
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            out.append(line)
            continue

        item = items_by_pid.get(pid)
        if not item:
            out.append(line)
            continue

        fb_id = fallback_in(item, st, point_key=pkey)
        if not fb_id:
            out.append(line)
            continue

        # Для наряда нужен настоящий артикул сети
        mapping = repo.confirmed_mapping(fb_id, st.id, real_only=True)
        if not mapping:
            out.append(line)
            continue

        fb_prod = repo.get_product(fb_id)
        if not fb_prod:
            out.append(line)
            continue

        if isinstance(line, dict):
            new_line = dict(line)
            new_line["product_id"] = fb_prod.id
            new_line["name"] = fb_prod.name
            if "product_name" in new_line:
                new_line["product_name"] = fb_prod.name
            new_line["unit"] = fb_prod.unit
            if "title" in new_line:
                new_line["title"] = fb_prod.name
            out.append(new_line)
        else:
            new_line = copy.copy(line)
            new_line.product_id = fb_prod.id
            new_line.name = fb_prod.name
            new_line.unit = fb_prod.unit
            if hasattr(new_line, "product_name"):
                new_line.product_name = fb_prod.name
            if hasattr(new_line, "title"):
                new_line.title = fb_prod.name
            out.append(new_line)

    return out
