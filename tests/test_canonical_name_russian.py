"""Имя единого товара — по-русски, если русское имя есть.

ЗАМЕР 21.10.2026 (боевая база). У «Молоко Parmalat 1,8% безлактозное 1 л» каноническим
именем стала транслитерация: `moloko ultrapasterizovannoe parmalat pitevoe proteinovoe
bezlaktoznoe 18 bez zmzh 1l rossiya` — правило «самое подробное имя» выбрало её, потому
что в ней больше слов. Имя единого товара служит ещё и ЗАПРОСОМ в витрины: телефон искал
в Магните, Ленте и Пятёрочке латиницей и получал пустую выдачу на всех позициях
(живая сборка: у Магнита и Ленты 0 из 4).
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import match  # noqa: E402


def _row(name: str, chain: str = "metro", sku: str = "1") -> match.Row:
    return match.prepare({"id": abs(hash((chain, sku))) % 100000, "chain": chain, "sku": sku,
                          "name": name, "brand": None, "weight_g": 1000.0, "unit": "pcs",
                          "barcode": None, "category": None, "in_stock": 1, "price": 100.0})


def test_the_russian_name_wins_over_a_longer_transliteration():
    latin = _row("moloko ultrapasterizovannoe parmalat pitevoe proteinovoe bezlaktoznoe 18 "
                 "bez zmzh 1l rossiya", chain="metro", sku="1")
    russian = _row("Молоко Parmalat безлактозное 1,8%, 1 л", chain="lenta", sku="2")

    canon = match.canonical([(latin, "self", 1.0), (russian, "self", 1.0)])

    assert canon["name"] == "Молоко Parmalat безлактозное 1,8%, 1 л", canon["name"]


def test_without_russian_names_the_most_detailed_one_stays():
    short = _row("Moloko Parmalat 1,8%", chain="metro", sku="1")
    long_one = _row("moloko ultrapasterizovannoe parmalat pitevoe 1l rossiya",
                    chain="lenta", sku="2")

    canon = match.canonical([(short, "self", 1.0), (long_one, "self", 1.0)])

    assert canon["name"] == "moloko ultrapasterizovannoe parmalat pitevoe 1l rossiya"


def test_cyrillic_detector():
    assert match.has_cyrillic("Молоко 1 л") is True
    assert match.has_cyrillic("Ёлка новогодняя") is True
    assert match.has_cyrillic("moloko 1l") is False
    assert match.has_cyrillic("") is False
    assert match.has_cyrillic(None) is False
