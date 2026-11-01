"""Тесты запасного товара в корзине: БД, отпечатки, расчёт и наряды."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import cartrun, cartstatus, config, db, phonecart, repo, service
from app.db import init_db
from app.models import Product


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Изолированная база для каждого теста."""
    path = str(tmp_path / "test_fallback.db")
    monkeypatch.setattr(config, "db_path", lambda: path)
    init_db()


def test_late_column_added_to_old_db(tmp_path):
    """Старая база без колонки fallback_product_id досыпает её через _add_late_columns."""
    db_file = tmp_path / "old_schema.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    # Создаём схему прежней версии: вся SCHEMA_SQL без колонки fallback_product_id
    old_schema = db.SCHEMA_SQL.replace("    fallback_product_id INTEGER,\n", "").replace("    fallback_product_id INTEGER,\r\n", "")
    conn.executescript(old_schema)
    conn.commit()

    cols_before = {r["name"] for r in conn.execute("PRAGMA table_info(basket_items)")}
    assert "fallback_product_id" not in cols_before

    db._add_late_columns(conn)

    cols_after = {r["name"] for r in conn.execute("PRAGMA table_info(basket_items)")}
    assert "fallback_product_id" in cols_after
    conn.close()


def test_set_basket_fallback_and_basket_items():
    """set_basket_fallback обновляет строку, None снимает, сам на себя не пишет."""
    b_id = repo.create_basket("Корзина")
    p1 = repo.upsert_product(Product(None, "Товар 1", unit="pcs"))
    p2 = repo.upsert_product(Product(None, "Товар 2", unit="pcs"))
    repo.set_basket_item(b_id, p1, 1.0)

    # Изначально fallback_product_id is None
    items = repo.basket_items(b_id)
    assert len(items) == 1
    assert items[0]["fallback_product_id"] is None

    # Устанавливаем p2 как запасной к p1
    repo.set_basket_fallback(b_id, p1, p2)
    items = repo.basket_items(b_id)
    assert items[0]["fallback_product_id"] == p2

    # Запасной сам на себя — вызов ничего не пишет
    repo.set_basket_fallback(b_id, p1, p1)
    items = repo.basket_items(b_id)
    assert items[0]["fallback_product_id"] == p2

    # None — снять запасной
    repo.set_basket_fallback(b_id, p1, None)
    items = repo.basket_items(b_id)
    assert items[0]["fallback_product_id"] is None


def test_stamp_of_changes_with_fallback_and_matches_legacy_without():
    """Отпечаток корзины без запасного совпадает со старой формулой, а с запасным меняется."""
    b_id = repo.create_basket("Корзина")
    p1 = repo.upsert_product(Product(None, "Товар 1", unit="pcs"))
    p2 = repo.upsert_product(Product(None, "Товар 2", unit="pcs"))
    repo.set_basket_item(b_id, p1, 2.0)

    # Прежняя формула отпечатка: только (product_id, qty)
    items_raw = repo.basket_items(b_id)
    legacy_pairs = sorted((int(i["product_id"]), round(float(i.get("qty") or 0), 3)) for i in items_raw)
    legacy_stamp = hashlib.sha1(repr(legacy_pairs).encode("utf-8")).hexdigest()[:12]

    # stamp_of без запасного в точности равен прежней формуле
    stamp_without_fb = cartrun.stamp_of(b_id)
    assert stamp_without_fb == legacy_stamp

    # Добавляем запасной товар — отпечаток ОБЯЗАН измениться
    repo.set_basket_fallback(b_id, p1, p2)
    stamp_with_fb = cartrun.stamp_of(b_id)
    assert stamp_with_fb != legacy_stamp
    assert stamp_with_fb != stamp_without_fb


def test_calculation_takes_fallback_only_where_original_missing():
    """Расчёт build_basket_lines берёт цену запасного только в сети, где прежнего нет."""
    store_mg = repo.get_store("magnit")
    store_vv = repo.get_store("vkusvill")
    store_py = repo.get_store("pyaterochka")

    p1 = repo.upsert_product(Product(None, "Чай Чёрный", unit="pcs"))
    p2 = repo.upsert_product(Product(None, "Чай Зелёный", unit="pcs"))

    # 1. Магнит: p1 есть в наличии (цена 100), p2 тоже есть (цена 80)
    sp1_mg = repo.upsert_store_product(store_mg.id, "sku-tea1-mg", "Чай Чёрный Магнит")
    repo.confirm_mapping(p1, sp1_mg, confirmed=True)
    repo.save_price(sp1_mg, 100.0, in_stock=1)

    sp2_mg = repo.upsert_store_product(store_mg.id, "sku-tea2-mg", "Чай Зелёный Магнит")
    repo.confirm_mapping(p2, sp2_mg, confirmed=True)
    repo.save_price(sp2_mg, 80.0, in_stock=1)

    # 2. ВкусВилл: p1 GONE (in_stock=0), но запасной p2 есть в наличии (цена 90)
    sp1_vv = repo.upsert_store_product(store_vv.id, "sku-tea1-vv", "Чай Чёрный ВВ")
    repo.confirm_mapping(p1, sp1_vv, confirmed=True)
    repo.save_price(sp1_vv, 110.0, in_stock=0)

    sp2_vv = repo.upsert_store_product(store_vv.id, "sku-tea2-vv", "Чай Зелёный ВВ")
    repo.confirm_mapping(p2, sp2_vv, confirmed=True)
    repo.save_price(sp2_vv, 90.0, in_stock=1)

    # 3. Пятёрочка: p1 NOT_KNOWN (нет сопоставления), а запасной p2 есть (цена 85)
    sp2_py = repo.upsert_store_product(store_py.id, "sku-tea2-py", "Чай Зелёный Пятёрочка")
    repo.confirm_mapping(p2, sp2_py, confirmed=True)
    repo.save_price(sp2_py, 85.0, in_stock=1)

    b_id = repo.create_basket("Чайная")
    repo.set_basket_item(b_id, p1, 1.0)
    repo.set_basket_fallback(b_id, p1, p2)

    lines = service.build_basket_lines(b_id)
    assert len(lines) == 1
    line = lines[0]

    # Магнит: прежний есть в наличии -> остаётся прежний, цена 100, нет fallback
    assert line.prices["magnit"] == 100.0
    assert "magnit" not in line.fallback
    assert line.notes.get("magnit") != "замена: Чай Зелёный"

    # ВкусВилл: прежнего нет в наличии (GONE) -> берётся запасной p2, цена 90
    assert line.prices["vkusvill"] == 90.0
    assert line.fallback["vkusvill"] == p2
    assert line.notes["vkusvill"] == "замена: Чай Зелёный"

    # Пятёрочка: сеть не знает прежний (NOT_KNOWN) -> берётся запасной p2, цена 85
    assert line.prices["pyaterochka"] == 85.0
    assert line.fallback["pyaterochka"] == p2
    assert line.notes["pyaterochka"] == "замена: Чай Зелёный"


def test_phonecart_plan_uses_fallback_sku_only_where_applied():
    """phonecart.plan отдаёт артикул запасного в сети, где он встал, и прежний в сети, где прежний есть."""
    store_mg = repo.get_store("magnit")
    store_vv = repo.get_store("vkusvill")

    p1 = repo.upsert_product(Product(None, "Кофе Арабика", unit="pcs"))
    p2 = repo.upsert_product(Product(None, "Кофе Робуста", unit="pcs"))

    # Магнит: p1 есть (настоящий артикул sku-mg-p1)
    sp1_mg = repo.upsert_store_product(store_mg.id, "sku-mg-p1", "Кофе Арабика")
    repo.confirm_mapping(p1, sp1_mg, confirmed=True)
    repo.save_price(sp1_mg, 300.0, in_stock=1)

    # ВкусВилл: p1 GONE, p2 есть (настоящий артикул sku-vv-p2)
    sp1_vv = repo.upsert_store_product(store_vv.id, "sku-vv-p1", "Кофе Арабика")
    repo.confirm_mapping(p1, sp1_vv, confirmed=True)
    repo.save_price(sp1_vv, 320.0, in_stock=0)

    sp2_vv = repo.upsert_store_product(store_vv.id, "sku-vv-p2", "Кофе Робуста")
    repo.confirm_mapping(p2, sp2_vv, confirmed=True)
    repo.save_price(sp2_vv, 280.0, in_stock=1)

    b_id = repo.create_basket("Кофейная")
    repo.set_basket_item(b_id, p1, 1.0)
    repo.set_basket_fallback(b_id, p1, p2)

    # Наряд для Магнита: должен содержать артикул p1 (sku-mg-p1)
    plan_mg = phonecart.plan({"store": "magnit", "basket": b_id})
    assert plan_mg["ok"] is True
    assert len(plan_mg["lines"]) == 1
    assert plan_mg["lines"][0]["sku"] == "sku-mg-p1"
    assert plan_mg["lines"][0]["name"] == "Кофе Арабика"

    # Наряд для ВкусВилла: должен содержать артикул p2 (sku-vv-p2)
    plan_vv = phonecart.plan({"store": "vkusvill", "basket": b_id})
    assert plan_vv["ok"] is True
    assert len(plan_vv["lines"]) == 1
    assert plan_vv["lines"][0]["sku"] == "sku-vv-p2"
    assert plan_vv["lines"][0]["name"] == "Кофе Робуста"

    # Доля после «Заказать» приходит присланным списком: запасной берётся из названной
    # корзины, даже когда последней создана другая, без запасных.
    newer = repo.create_basket("Новее, без запасных")
    with repo.get_conn() as c:
        c.execute("UPDATE baskets SET created_at=datetime(created_at, '+1 day') WHERE id=?", (newer,))
        c.commit()
    assert int(repo.list_baskets()[0]["id"]) == newer
    plan_share = phonecart.plan({"store": "vkusvill", "basket": b_id,
                                 "lines": [{"product_id": p1, "name": "Кофе Арабика", "qty": 1}]})
    assert [ln["sku"] for ln in plan_share["lines"]] == ["sku-vv-p2"]


def test_swap_lines_real_only_requirement():
    """Запасной встаёт в наряд ТОЛЬКО при наличии настоящего артикула (не заглушки)."""
    store = repo.get_store("magnit")
    p1 = repo.upsert_product(Product(None, "Товар 1", unit="pcs"))
    p2 = repo.upsert_product(Product(None, "Товар 2", unit="pcs"))

    # p1 в магазине GONE
    sp1 = repo.upsert_store_product(store.id, "sku-real-1", "Товар 1")
    repo.confirm_mapping(p1, sp1, confirmed=True)
    repo.save_price(sp1, 100.0, in_stock=0)

    # p2 сопоставлен с заглушкой (начинается с hist- или list-)
    sp2_stub = repo.upsert_store_product(store.id, "hist-stub-2", "Товар 2 заглушка")
    repo.confirm_mapping(p2, sp2_stub, confirmed=True)
    repo.save_price(sp2_stub, 90.0, in_stock=1)

    b_id = repo.create_basket("Корзина")
    repo.set_basket_item(b_id, p1, 1.0)
    repo.set_basket_fallback(b_id, p1, p2)

    # fallback_in даёт p2, но confirmed_mapping(..., real_only=True) вернёт None
    assert cartstatus.fallback_in({"product_id": p1, "unit": "pcs", "fallback_product_id": p2}, store) == p2
    assert repo.confirmed_mapping(p2, store.id, real_only=True) is None

    from app.models import BasketLine
    orig_line = BasketLine(product_id=p1, name="Товар 1", unit="pcs", qty=1.0)
    swapped = cartstatus.swap_lines([orig_line], store, basket_id=b_id)
    # Строка должна остаться прежней!
    assert swapped[0].product_id == p1
    assert swapped[0].name == "Товар 1"
