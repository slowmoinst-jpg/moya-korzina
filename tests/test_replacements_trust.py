"""Тест 4-ступенчатой лестницы доверия при подборе замен (app/web/replacements.py)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from app import config, db, location, repo
from app.catalog import refresh, store
from app.catalog.model import ChainProduct
from app.models import Product


def _load_replacements():
    path = Path(__file__).resolve().parent.parent / "app" / "web" / "replacements.py"
    spec = importlib.util.spec_from_file_location("app_web_replacements_unit", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_replacements_rank_by_four_tier_trust_ladder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "user.db"))
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    db.init_db()
    store.init()
    replacements = _load_replacements()

    addr = "Москва, Тверская 1"
    location.save_address(addr)
    store.remember_points([SimpleNamespace(address=addr, chain="lenta", code="p1", label="Лента 1")])

    store.upsert_products("lenta", [
        ChainProduct("l1", "Йогурт греческий Савушкин 400 г", price=115.0, in_stock=True, unit="pcs", brand="Савушкин", weight_g=400, category="Йогурт", point="p1"),
        ChainProduct("l2", "Йогурт греческий Простоквашино 400 г", price=110.0, in_stock=True, unit="pcs", brand="Простоквашино", weight_g=400, category="Йогурт", point="p1"),
        ChainProduct("l3", "Йогурт греческий Белый город 400 г", price=85.0, in_stock=True, unit="pcs", brand="Белый город", weight_g=400, category="Йогурт", point="p1"),
        ChainProduct("l4", "Йогурт греческий Красная цена 400 г", price=45.0, in_stock=True, unit="pcs", brand="Красная цена", weight_g=400, category="Йогурт", point="p1"),
    ])
    refresh.match_all()

    target_pid = repo.upsert_product(Product(id=None, name="Йогурт греческий Teos 400 г", brand="Teos", weight_g=400, unit="pcs", category="Йогурт"))
    p_bought = repo.upsert_product(Product(id=None, name="Йогурт греческий Савушкин 400 г", brand="Савушкин", weight_g=400, unit="pcs", category="Йогурт"))
    p_other_brand = repo.upsert_product(Product(id=None, name="Молоко Простоквашино 1 л", brand="Простоквашино", weight_g=1000, unit="pcs", category="Молоко"))

    mid_bought = next(r["item_id"] for r in store.products_of_chain("lenta") if r["sku"] == "l1")
    repo.set_master(p_bought, mid_bought, "catalog")
    repo.add_history_row("2026-11-01", None, target_pid, "Йогурт греческий Teos 400 г", 1, 80.0, 80.0, "sample")

    rows = replacements.search(
        query="Йогурт греческий",
        codes=["lenta"],
        unit="pcs",
        exclude=target_pid,
        bought_ids={p_bought, p_other_brand},
        bought_masters={mid_bought},
    )

    assert [r["trustTier"] for r in rows] == ["bought", "brand", "price_band", "cheaper"]
    assert [r["trustLabel"] for r in rows] == ["Вы покупали", "Ваш бренд", "В вашей цене", ""]
