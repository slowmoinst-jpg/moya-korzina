"""Штрихкод коробки и штуки одного товара — не улика «разные товары».

ЗАМЕР 21.10.2026 (боевая база). У айрана «Долголетие Турецкий 1,8%, 500г» Метро отдаёт
код штуки `04680000192903`, а Лента — коробочный код `14680000192900`. Номер товара у них
один (468000019290), различаются индикатор упаковки и контрольная цифра. Правило «коды
разные — товары разные» держало строки врозь, хотя имя, марка и вес совпадали.

Сколько таких: 355 групп товаров из РАЗНЫХ сетей с одинаковыми именем, маркой, весом и
единицей, но разными едиными товарами. Каждая такая группа — потерянное сравнение цен.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import match  # noqa: E402


def _row(chain: str, name: str, barcode: str | None, weight_g: float | None = 500.0,
         unit: str = "pcs", sku: str = "1") -> match.Row:
    return match.prepare({"id": abs(hash((chain, sku))) % 100000, "chain": chain, "sku": sku,
                          "name": name, "brand": "ДОЛГОЛЕТИЕ", "weight_g": weight_g,
                          "unit": unit, "barcode": barcode, "category": None, "in_stock": 1,
                          "price": 100.0})


def test_a_box_code_does_not_split_one_product():
    """Штука и коробка одного товара склеиваются по словам, а не рубятся штрихкодом."""
    unit = _row("metro", "Айран Долголетие Турецкий 1.8%, 500г", "4680000192903", sku="118055")
    box = _row("lenta", "Айран ДОЛГОЛЕТИЕ Турецкий 1,8%, 500г", "14680000192900", sku="608694")

    ok, method, score = match.same_product(unit, box)

    assert (ok, method) == (True, "words"), (ok, method, score)


def test_different_item_numbers_are_still_a_veto():
    """Разные номера товара — по-прежнему улика «разные товары», и она сильнее слов."""
    one = _row("metro", "Айран Долголетие Турецкий 1.8%, 500г", "4680000192903", sku="1")
    # Коробочный код с ДРУГИМ номером товара (9999 вместо 9290), контрольная цифра верная.
    other = _row("lenta", "Айран Долголетие Турецкий 1,8%, 500г", "14680000199992", sku="2")

    ok, method, _ = match.same_product(one, other)

    assert ok is False and method == "barcode", (ok, method)


def test_the_same_code_is_still_a_match():
    one = _row("metro", "Айран Долголетие Турецкий 1.8%, 500г", "4680000192903", sku="1")
    two = _row("lenta", "Айран Долголетие Турецкий 1,8%, 500г", "4680000192903", sku="2")

    ok, method, _ = match.same_product(one, two)

    assert ok is True and method == "barcode"


def test_payload_and_case_helpers():
    assert match.item_payload("04680000192903") == "468000019290"
    assert match.item_payload("14680000192900") == "468000019290"
    assert match.item_payload("46600012345678#7") is None      # внутренний код сети
    assert match.item_payload("123") is None                    # не GTIN-14
    assert match.same_payload("04680000192903", "14680000192900") is True
    assert match.same_payload("04680000192903", "14680000199999") is False
    assert match.same_payload("04680000192903", "04680000192903") is False  # один и тот же — не «коробка»
