"""Проверочная подмена уточнённого исправления (рабочая копия не меняется).

Подключается как pytest-плагин: python -m pytest -p fixplug_stub ...
Режим — переменная STUBFIX:
  ui   — только экран: подпись заглушки по виду (чек / прайс / справочник, чей чек,
         дата, идёт ли в сравнение) и своё действие drop_stub, которое удаляет
         ровно эту заглушку, без отказа от сети;
  full — ui + память: убранная заглушка помечается (product_mapping.confirmed=0,
         method='stub_dropped'), и «Подобрать всё» / «Узнать цены» её не возвращают;
         автоматика не подставляет строку чека ДРУГОГО товара (hist-<чужой id>).
STUBFIX_D=1 — отдельно: «Узнать цены» пишет справочную цену CSV с source=fallback.
"""
from __future__ import annotations

import os
import sys

WT = os.environ.get("V3_ROOT") or "."
if WT not in sys.path:
    sys.path.insert(0, WT)

HERE = os.path.dirname(os.path.abspath(__file__))
MODE = os.environ.get("STUBFIX", "full")
FIX_D = os.environ.get("STUBFIX_D") == "1"
DROPPED = "stub_dropped"


def _apply() -> None:
    from datetime import date

    from flask import flash, redirect, request
    from jinja2 import ChoiceLoader, FileSystemLoader

    import app.matcher as matcher_pkg
    import app.matcher.matcher as mm
    import app.web as web_pkg
    from app import freshness, pricelist, repo
    from app.catalog.model import placeholder_sku
    from app.connectors import history
    from app.web.screens import basket, links

    # ---------- repo: убрать одну заглушку пары ----------
    def drop_stub(product_id: int, store_id: int, sku: str) -> bool:
        with repo.get_conn() as c:
            code = (c.execute("SELECT code FROM stores WHERE id=?", (store_id,)).fetchone()
                    or {"code": ""})["code"]
            if not placeholder_sku(sku, code):
                return False                      # настоящий артикул этим путём не снимается
            row = c.execute("SELECT m.store_product_id FROM product_mapping m"
                            " JOIN store_products sp ON sp.id = m.store_product_id"
                            " WHERE m.product_id=? AND sp.store_id=? AND sp.sku=? AND m.confirmed=1",
                            (product_id, store_id, sku)).fetchone()
            if not row:
                return False
            if MODE == "full":
                c.execute("UPDATE product_mapping SET confirmed=0, confirmed_at=NULL, method=?"
                          " WHERE product_id=? AND store_product_id=?",
                          (DROPPED, product_id, row["store_product_id"]))
            else:
                c.execute("DELETE FROM product_mapping WHERE product_id=? AND store_product_id=?",
                          (product_id, row["store_product_id"]))
            c.commit()
            return True

    def dropped_stub(product_id: int, store_id: int, sku: str) -> bool:
        with repo.get_conn() as c:
            return bool(c.execute("SELECT 1 FROM product_mapping m"
                                  " JOIN store_products sp ON sp.id = m.store_product_id"
                                  " WHERE m.product_id=? AND sp.store_id=? AND sp.sku=?"
                                  " AND m.method=?", (product_id, store_id, str(sku), DROPPED)).fetchone())

    def foreign_receipt(product_id: int, sku: str) -> bool:
        pid = history._product_id(str(sku))
        return pid is not None and pid != int(product_id)

    repo.drop_stub = drop_stub
    repo.dropped_stub = dropped_stub

    # ---------- «Связи»: подпись заглушки ----------
    orig_pair = links._pair

    def human(day) -> str | None:
        try:
            return date.fromisoformat(str(day)[:10]).strftime("%d.%m.%Y") if day else None
        except ValueError:
            return str(day)

    def pair_fixed(product, store):
        out = orig_pair(product, store)
        stub = out.get("stub")
        if stub:
            sku = str(stub.get("sku") or "")
            kind = "receipt" if sku.startswith("hist-") else ("list" if sku.startswith("list-") else "reference")
            when = None
            foreign = False
            if kind == "receipt":
                pid = history._product_id(sku)
                foreign = pid is not None and pid != product.id
                row = history.last_prices(store.code).get(pid) if pid else None
                when = row.get("date") if row else None
            elif kind == "list":
                when = pricelist.updated_at(store.code) or None
            out.update(stub_kind=kind, stub_foreign=foreign, stub_date=human(when),
                       stub_counts=freshness.price_for(product.id, store.id) is not None)
        return out

    links._pair = pair_fixed

    orig_page = links.page

    def page_fixed():
        if request.method == "POST" and request.form.get("action") == "drop_stub":
            products = repo.list_products(active_only=False)
            stores = repo.list_stores()
            product = next((p for p in products if str(p.id) == request.form.get("product")), None)
            store = next((s for s in stores if s.code == request.form.get("store")), None)
            sku = request.form.get("sku") or ""
            if product is None or store is None:
                flash("Товар или магазин не найдены — возможно, их успели изменить.", "warn")
            elif drop_stub(product.id, store.id, sku):
                flash(f"Цена по строке {sku} убрана. От сети «{store.name}» вы не отказывались:"
                      " кандидаты и поиск остаются.", "info")
            else:
                flash("Убирать нечего: эту цену уже сменили.", "warn")
            return redirect(links._back(request.form.get("product"), request.form.get("store"),
                                        request.form.get("q") or ""))
        return orig_page()

    page_fixed.methods = ("GET", "POST")
    links.page = page_fixed

    orig_create = web_pkg.create_app

    def create_fixed(*a, **k):
        application = orig_create(*a, **k)
        application.jinja_env.loader = ChoiceLoader([FileSystemLoader(os.path.join(HERE, "fix_templates")),
                                                     application.jinja_env.loader])
        return application

    web_pkg.create_app = create_fixed

    if MODE == "full":
        # ---------- автоматика: не возвращать убранное и не брать чужой чек ----------
        def auto_match(product_ids, store_codes, threshold: float = 0.75) -> dict:
            auto = 0
            need_review = []
            tol = mm._tolerance()
            for product_id in product_ids:
                product = repo.get_product(product_id)
                if product is None:
                    continue
                for store_code in store_codes:
                    store = repo.get_store(store_code)
                    if store is None:
                        need_review.append({"product_id": product_id, "store_code": store_code,
                                            "reason": "магазин не найден", "candidates": []})
                        continue
                    if repo.confirmed_mapping(product_id, store.id, real_only=True):
                        continue
                    if repo.chain_rejected(product_id, store.id):
                        continue
                    held = repo.confirmed_mapping(product_id, store.id)
                    candidates = mm.find_candidates(product_id, store_code, limit=3)
                    if not candidates:
                        need_review.append({"product_id": product_id, "product_name": product.name,
                                            "store_code": store_code, "reason": "нет кандидатов",
                                            "candidates": []})
                        continue
                    best = candidates[0]
                    by_barcode = bool(mm.digits(getattr(product, "barcode", None))
                                      and mm.digits(best.ean) == mm.digits(getattr(product, "barcode", None)))
                    fits = by_barcode or mm.weight_ok(product.weight_g, best.weight_g, tol)
                    if best.score >= threshold and fits:
                        stub = placeholder_sku(best.sku, store_code)
                        if stub and (held or foreign_receipt(product_id, best.sku)
                                     or dropped_stub(product_id, store.id, best.sku)):
                            continue                                    # ← исправление
                        mm.confirm(product_id, store_code, best.sku)
                        auto += int(not stub)
                    else:
                        need_review.append({
                            "product_id": product_id, "product_name": product.name,
                            "store_code": store_code,
                            "reason": "низкий score" if not fits or best.score < threshold else "",
                            "best_score": best.score,
                            "candidates": [{"sku": c.sku, "name": c.name, "score": c.score} for c in candidates],
                        })
            return {"auto": auto, "need_review": need_review}

        mm.auto_match = auto_match
        matcher_pkg.auto_match = auto_match

        orig_agrees = basket._agrees_with_mapping

        def agrees(product_id: int, offer) -> bool:
            store = repo.get_store(offer.store_code)
            sku = str(offer.sku or "")
            if store is not None and placeholder_sku(sku, offer.store_code):
                mapping = repo.confirmed_mapping(product_id, store.id)
                if not (mapping and str(mapping.get("sku")) == sku):      # выбор человека — его
                    if foreign_receipt(product_id, sku) or dropped_stub(product_id, store.id, sku):
                        return False
            return orig_agrees(product_id, offer)

        basket._agrees_with_mapping = agrees

    if FIX_D:
        def remember(product_id: int, offer) -> None:
            store = repo.get_store(offer.store_code)
            if store is None:
                return
            try:
                sp_id = repo.upsert_store_product(store.id, str(offer.sku), offer.name or "",
                                                  weight_g=offer.weight_g, unit=offer.unit, url=offer.url)
                repo.confirm_mapping(product_id, sp_id, confirmed=True)
                if offer.price is not None:
                    reference = str(offer.sku).startswith(f"{offer.store_code}-")
                    repo.save_price(sp_id, float(offer.price),
                                    price_per_kg=offer.per_unit if offer.unit == "kg" else None,
                                    in_stock=bool(offer.in_stock),
                                    source=freshness.REFERENCE if reference else None)
            except Exception:  # noqa: BLE001
                pass

        basket._remember = remember


def pytest_configure(config):
    _apply()
