"""Тесты модуля app/cartstatus: пять кодов статуса и правило выбора запасного."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import cartrun, cartstatus, config, repo
from app.db import init_db
from app.models import Product
from app.web.screens import basket as basket_screen


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Изолированная база для каждого теста."""
    path = str(tmp_path / "test_cartstatus.db")
    monkeypatch.setattr(config, "db_path", lambda: path)
    init_db()


def test_status_five_codes(monkeypatch):
    """Каждый из пяти кодов статуса позиции в сети."""
    store = repo.get_store("magnit")
    assert store is not None

    # 1. NOT_KNOWN: у товара нет сопоставления в этой сети
    prod_unknown = repo.upsert_product(Product(None, "Неизвестный товар", unit="pcs"))
    assert cartstatus.status(prod_unknown, store) == cartstatus.NOT_KNOWN

    # 2. NO_DATA: сопоставление подтверждено, но цены нет (или она устарела)
    prod_nodata = repo.upsert_product(Product(None, "Товар без цены", unit="pcs"))
    sp_nodata = repo.upsert_store_product(store.id, "sku-nodata", "Товар без цены")
    repo.confirm_mapping(prod_nodata, sp_nodata, confirmed=True)
    assert cartstatus.status(prod_nodata, store) == cartstatus.NO_DATA

    # 3. GONE: сопоставление подтверждено, цена есть, но in_stock = 0
    prod_gone = repo.upsert_product(Product(None, "Закончившийся товар", unit="pcs"))
    sp_gone = repo.upsert_store_product(store.id, "sku-gone", "Закончившийся товар")
    repo.confirm_mapping(prod_gone, sp_gone, confirmed=True)
    repo.save_price(sp_gone, 100.0, in_stock=0)
    assert cartstatus.status(prod_gone, store) == cartstatus.GONE

    # 4. HAVE: подтверждён, свежая цена, есть в наличии
    prod_have = repo.upsert_product(Product(None, "Товар в наличии", unit="pcs"))
    sp_have = repo.upsert_store_product(store.id, "sku-have", "Товар в наличии")
    repo.confirm_mapping(prod_have, sp_have, confirmed=True)
    repo.save_price(sp_have, 120.0, in_stock=1)
    assert cartstatus.status(prod_have, store) == cartstatus.HAVE

    # 5. SEARCHING: при NO_DATA переходит в SEARCHING при опрессе корзины или активной сборке сети
    b_id = repo.create_basket("Тестовая корзина")
    repo.set_basket_item(b_id, prod_nodata, 1.0)

    # Без активных процессов — NO_DATA
    res = cartstatus.of_basket(b_id)
    assert res[prod_nodata]["magnit"]["status"] == cartstatus.NO_DATA

    # С фоновым опросом цен корзины (job status == running) -> SEARCHING
    monkeypatch.setattr(basket_screen, "job_of", lambda bid: {"status": "running"} if bid == b_id else None)
    res_job = cartstatus.of_basket(b_id)
    assert res_job[prod_nodata]["magnit"]["status"] == cartstatus.SEARCHING

    # С активной сборкой этой сети (cartrun.Row GOING со stamp корзины) -> SEARCHING
    monkeypatch.setattr(basket_screen, "job_of", lambda bid: None)
    curr_stamp = cartrun.stamp_of(b_id)
    running_row = cartrun.Row(code="magnit", state=cartrun.GOING, basket_id=b_id, stamp=curr_stamp)
    monkeypatch.setattr(cartrun, "rows", lambda: [running_row])

    res_cartrun = cartstatus.of_basket(b_id)
    assert res_cartrun[prod_nodata]["magnit"]["status"] == cartstatus.SEARCHING
    # А для другой сети, где сопоставление есть, но сборка не идёт — остаётся NO_DATA
    store_vv = repo.get_store("vkusvill")
    sp_vv = repo.upsert_store_product(store_vv.id, "sku-nodata-vv", "Товар без цены ВВ")
    repo.confirm_mapping(prod_nodata, sp_vv, confirmed=True)
    res_cartrun2 = cartstatus.of_basket(b_id)
    assert res_cartrun2[prod_nodata]["magnit"]["status"] == cartstatus.SEARCHING
    assert res_cartrun2[prod_nodata]["vkusvill"]["status"] == cartstatus.NO_DATA


def test_fallback_in_unit_mismatch():
    """Единица запасного (шт vs кг) должна совпадать с единицей прежнего."""
    store = repo.get_store("magnit")
    orig_pcs = repo.upsert_product(Product(None, "Яблоки фасованные", unit="pcs"))
    fb_kg = repo.upsert_product(Product(None, "Яблоки весовые", unit="kg"))

    # Запасной есть в наличии
    sp_fb = repo.upsert_store_product(store.id, "sku-fb-kg", "Яблоки весовые")
    repo.confirm_mapping(fb_kg, sp_fb, confirmed=True)
    repo.save_price(sp_fb, 150.0, in_stock=1)

    item = {"product_id": orig_pcs, "unit": "pcs", "fallback_product_id": fb_kg}
    assert cartstatus.fallback_in(item, store) is None


def test_fallback_in_rules():
    """Правило подстановки запасного: прежний NOT_KNOWN/GONE, запасной HAVE/NO_DATA."""
    store = repo.get_store("magnit")

    # Прежний товар
    p_orig = repo.upsert_product(Product(None, "Молоко Домик 3.2%", unit="pcs"))
    # Запасной товар
    p_fb = repo.upsert_product(Product(None, "Молоко Простоквашино 3.2%", unit="pcs"))

    item = {"product_id": p_orig, "unit": "pcs", "fallback_product_id": p_fb}

    # 1. Если прежний HAVE — запасной НЕ встаёт
    sp_orig = repo.upsert_store_product(store.id, "sku-orig", "Молоко Домик")
    repo.confirm_mapping(p_orig, sp_orig, confirmed=True)
    repo.save_price(sp_orig, 90.0, in_stock=1)

    sp_fb = repo.upsert_store_product(store.id, "sku-fb", "Молоко Простоквашино")
    repo.confirm_mapping(p_fb, sp_fb, confirmed=True)
    repo.save_price(sp_fb, 85.0, in_stock=1)

    assert cartstatus.fallback_in(item, store) is None

    # 2. Если прежний NO_DATA — запасной НЕ встаёт (прежний должен быть NOT_KNOWN или GONE)
    with repo.get_conn() as c:
        c.execute("DELETE FROM store_prices WHERE store_product_id=?", (sp_orig,))
        c.commit()
    assert cartstatus.status(p_orig, store) == cartstatus.NO_DATA
    assert cartstatus.fallback_in(item, store) is None

    # 3. Прежний GONE (out of stock):
    repo.save_price(sp_orig, 90.0, in_stock=0)
    assert cartstatus.status(p_orig, store) == cartstatus.GONE

    # 3a. Запасной HAVE -> встаёт
    assert cartstatus.fallback_in(item, store) == p_fb

    # 3b. Запасной NO_DATA -> встаёт (не NOT_KNOWN и не GONE)
    with repo.get_conn() as c:
        c.execute("DELETE FROM store_prices WHERE store_product_id=?", (sp_fb,))
        c.commit()
    assert cartstatus.status(p_fb, store) == cartstatus.NO_DATA
    assert cartstatus.fallback_in(item, store) == p_fb

    # 3c. Запасной GONE -> НЕ встаёт
    repo.save_price(sp_fb, 85.0, in_stock=0)
    assert cartstatus.status(p_fb, store) == cartstatus.GONE
    assert cartstatus.fallback_in(item, store) is None

    # 3d. Запасной NOT_KNOWN -> НЕ встаёт
    with repo.get_conn() as c:
        c.execute("DELETE FROM product_mapping WHERE product_id=?", (p_fb,))
        c.commit()
    assert cartstatus.status(p_fb, store) == cartstatus.NOT_KNOWN
    assert cartstatus.fallback_in(item, store) is None

    # 4. Прежний NOT_KNOWN:
    with repo.get_conn() as c:
        c.execute("DELETE FROM product_mapping WHERE product_id=?", (p_orig,))
        c.commit()
    assert cartstatus.status(p_orig, store) == cartstatus.NOT_KNOWN

    # Запасной снова HAVE -> встаёт
    repo.confirm_mapping(p_fb, sp_fb, confirmed=True)
    repo.save_price(sp_fb, 85.0, in_stock=1)
    assert cartstatus.fallback_in(item, store) == p_fb


def test_of_basket_returns_structure():
    """of_basket возвращает полный словарь по позициям корзины и сетям."""
    store_mg = repo.get_store("magnit")
    store_vv = repo.get_store("vkusvill")

    p1 = repo.upsert_product(Product(None, "Товар 1", unit="pcs"))
    p2 = repo.upsert_product(Product(None, "Запасной к товару 1", unit="pcs"))

    # В Магните товар 1 есть
    sp1_mg = repo.upsert_store_product(store_mg.id, "sku-1-mg", "Товар 1")
    repo.confirm_mapping(p1, sp1_mg, confirmed=True)
    repo.save_price(sp1_mg, 50.0, in_stock=1)

    # Во ВкусВилле товара 1 нет (GONE), но запасной p2 есть (HAVE)
    sp1_vv = repo.upsert_store_product(store_vv.id, "sku-1-vv", "Товар 1")
    repo.confirm_mapping(p1, sp1_vv, confirmed=True)
    repo.save_price(sp1_vv, 60.0, in_stock=0)

    sp2_vv = repo.upsert_store_product(store_vv.id, "sku-2-vv", "Запасной к товару 1")
    repo.confirm_mapping(p2, sp2_vv, confirmed=True)
    repo.save_price(sp2_vv, 55.0, in_stock=1)

    b_id = repo.create_basket("Корзина")
    repo.set_basket_item(b_id, p1, 2.0)
    repo.set_basket_fallback(b_id, p1, p2)

    res = cartstatus.of_basket(b_id)
    assert p1 in res
    # Магнит: товар 1 есть, запасной не встаёт
    assert res[p1]["magnit"]["status"] == cartstatus.HAVE
    assert res[p1]["magnit"]["fallback"] is None

    # ВкусВилл: товар 1 GONE, запасной p2 встаёт
    assert res[p1]["vkusvill"]["status"] == cartstatus.GONE
    assert res[p1]["vkusvill"]["fallback"] == p2
