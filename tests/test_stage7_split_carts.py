"""Тесты Этапа 7 (Разбивка на два магазина): наряд по доле сплита и пересчёт сплита по итогам сетей."""
from __future__ import annotations

import json
from datetime import datetime

from app import cartrun, netprice, phonecart, repo
from app.carttotal import NETWORK, Total
from app.models import Product, StoreBreakdown, Variant, VariantLine
from app.web.phone import cheaper


NOW = datetime(2026, 10, 24, 12, 0, 0)
FRESH = "2026-10-24T11:45:00"


def test_phonecart_plan_with_custom_lines_builds_split_share_and_distinct_stamp(monkeypatch):
    """plan() с параметром lines собирает наряд только для указанных позиций с отдельным штампом."""
    monkeypatch.setattr(repo, "confirmed_mapping", lambda pid, sid, real_only=False: {
        "id": pid,
        "sku": f"SKU-{pid}",
        "raw_name": f"Товар {pid}",
        "unit": "pcs"
    })

    custom_lines = [
        {"name": "Сыр Российский 200 г", "qty": 2.0, "unit": "pcs", "product_id": 101},
        {"name": "Масло Сливочное 82.5%", "qty": 1.0, "unit": "pcs", "product_id": 102},
    ]
    res = phonecart.plan({"store": "magnit", "lines": custom_lines})

    assert res["ok"] is True
    assert res["store"] == "magnit"
    assert len(res["lines"]) == 2
    assert res["lines"][0]["name"] == "Товар 101"
    assert res["lines"][0]["sku"] == "SKU-101"
    assert res["lines"][0]["clicks"] == 2
    assert res["lines"][1]["name"] == "Товар 102"
    assert res["lines"][1]["sku"] == "SKU-102"
    assert res["lines"][1]["clicks"] == 1
    # Штамп вычислен от состава переданных позиций
    assert res["stamp"] and len(res["stamp"]) == 12


def test_split_variant_not_priced_by_full_basket_runs():
    """Сплит не берёт итог из прогона полной корзины (иначе доле приписалась бы цена всей корзины)."""
    ln1 = [VariantLine(store_code="magnit", store_name="Магнит", card_id=None, card_name=None,
                       product_id=1, product_name="Товар 1", qty=1.0, price=50.0)]
    ln2 = [VariantLine(store_code="pyaterochka", store_name="Пятёрочка", card_id=None, card_name=None,
                       product_id=2, product_name="Товар 2", qty=1.0, price=60.0)]
    st1 = StoreBreakdown(store_code="magnit", store_name="Магнит", subtotal=50.0, delivery=0.0,
                         discount=0.0, total=50.0, lines=ln1)
    st2 = StoreBreakdown(store_code="pyaterochka", store_name="Пятёрочка", subtotal=60.0, delivery=0.0,
                         discount=0.0, total=60.0, lines=ln2)
    split_var = Variant(stores=[st1, st2], total=110.0, baseline=150.0)

    # Прогоны полной корзины (planned=5, landed=5) — для сплита из 1 позиции не подходят
    row_m = cartrun.Row(code="magnit", state=cartrun.DONE, planned=5,
                        landed=["Т1", "Т2", "Т3", "Т4", "Т5"],
                        total=Total(sum=300.0, source=NETWORK, at=FRESH))
    row_p = cartrun.Row(code="pyaterochka", state=cartrun.DONE, planned=5,
                        landed=["Т1", "Т2", "Т3", "Т4", "Т5"],
                        total=Total(sum=320.0, source=NETWORK, at=FRESH))

    [got] = netprice.apply([split_var], {"magnit": row_m, "pyaterochka": row_p}, positions=5,
                           baseline=150.0, now=NOW)

    assert got.net is None
    assert got.variant.total == 110.0


def test_split_variant_priced_when_both_split_shares_have_verified_network_totals():
    """Когда по обеим сетям сплита собраны именно их доли, сплит получает сетевой итог."""
    ln1 = [
        VariantLine(store_code="magnit", store_name="Магнит", card_id=None, card_name=None,
                    product_id=1, product_name="Товар 1", qty=1.0, price=50.0),
        VariantLine(store_code="magnit", store_name="Магнит", card_id=None, card_name=None,
                    product_id=2, product_name="Товар 2", qty=1.0, price=50.0),
    ]
    ln2 = [
        VariantLine(store_code="vkusvill", store_name="ВкусВилл", card_id=None, card_name=None,
                    product_id=3, product_name="Товар 3", qty=1.0, price=80.0),
    ]
    st1 = StoreBreakdown(store_code="magnit", store_name="Магнит", subtotal=100.0, delivery=0.0,
                         discount=5.0, total=95.0, lines=ln1)
    st2 = StoreBreakdown(store_code="vkusvill", store_name="ВкусВилл", subtotal=80.0, delivery=0.0,
                         discount=10.0, total=70.0, lines=ln2)
    split_var = Variant(stores=[st1, st2], total=165.0, baseline=220.0)

    # Строки сборки конкретных долей сплита:
    # Магнит: planned=2, landed=2, сетевой итог 90.0 (кэшбэк 5.0 -> к оплате 85.0)
    row_m = cartrun.Row(code="magnit", state=cartrun.DONE, planned=2,
                        landed=["Товар 1", "Товар 2"],
                        total=Total(sum=90.0, discount=10.0, source=NETWORK, at=FRESH))
    # ВкусВилл: planned=1, landed=1, сетевой итог 75.0 (кэшбэк 10.0 -> к оплате 65.0)
    row_v = cartrun.Row(code="vkusvill", state=cartrun.DONE, planned=1,
                        landed=["Товар 3"],
                        total=Total(sum=75.0, discount=5.0, source=NETWORK, at=FRESH))

    [got] = netprice.apply([split_var], {"magnit": row_m, "vkusvill": row_v}, positions=3,
                           baseline=220.0, now=NOW)

    assert got.net is not None
    assert got.net.source == NETWORK
    # Итог сети до кэшбэка банка: 90 + 75 = 165
    assert got.net.sum == 165.0
    # Кэшбэк банка суммарный: 5 + 10 = 15
    assert got.cashback == 15.0
    # Итоговый total варианта: 85 + 65 = 150.0
    assert got.variant.total == 150.0
    assert got.variant.stores[0].total == 85.0
    assert got.variant.stores[1].total == 65.0


def test_split_variant_not_priced_if_one_share_has_missing_items():
    """Если в одной из долей сплита часть позиций не легла, сплит остаётся с расчётной ценой."""
    ln1 = [
        VariantLine(store_code="magnit", store_name="Магнит", card_id=None, card_name=None,
                    product_id=1, product_name="Товар 1", qty=1.0, price=50.0),
        VariantLine(store_code="magnit", store_name="Магнит", card_id=None, card_name=None,
                    product_id=2, product_name="Товар 2", qty=1.0, price=50.0),
    ]
    st1 = StoreBreakdown(store_code="magnit", store_name="Магнит", subtotal=100.0, delivery=0.0,
                         discount=0.0, total=100.0, lines=ln1)
    split_var = Variant(stores=[st1], total=100.0, baseline=120.0)

    # Недобор: planned=2, но landed=1, missing=1
    row_m = cartrun.Row(code="magnit", state=cartrun.DONE, planned=2,
                        landed=["Товар 1"], missing=[{"name": "Товар 2", "why": "нет в наличии"}],
                        total=Total(sum=50.0, source=NETWORK, at=FRESH))

    [got] = netprice.apply([split_var], {"magnit": row_m}, positions=2, baseline=120.0, now=NOW)
    assert got.net is None
    assert got.variant.total == 100.0


def test_cheaper_part_includes_serialized_lines_json():
    """_part(store) готовит lines_json для передачи в JavaScript-мост при заказе доли сплита."""
    ln = [
        VariantLine(store_code="magnit", store_name="Магнит", card_id=None, card_name=None,
                    product_id=42, product_name="Молоко Домик в деревне 3.2%", qty=2.0, price=89.9),
    ]
    st = StoreBreakdown(store_code="magnit", store_name="Магнит", subtotal=179.8, delivery=0.0,
                        discount=0.0, total=179.8, lines=ln)
    part = cheaper._part(st)

    assert part["code"] == "magnit"
    assert part["count"] == 1
    assert "lines_json" in part
    parsed = json.loads(part["lines_json"])
    assert len(parsed) == 1
    assert parsed[0]["name"] == "Молоко Домик в деревне 3.2%"
    assert parsed[0]["qty"] == 2.0
    assert parsed[0]["product_id"] == 42
