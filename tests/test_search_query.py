"""Запрос для поиска в сети: обрубок чека превращается в то, что сеть поймёт.

Повод — живая сборка 20.10.2026: у 3 сетей из 4 не легло ни одной позиции, причина в
отчётах «поиск сети ничего не нашёл за 12 с», а в поиск уходило
«Молоко neo high protein 0 5 безлактозное ультрапастеризованное 950 г» (это «0,5 %»).
"""
from __future__ import annotations

import pytest

from app import phonecart


@pytest.mark.parametrize("raw, expected", [
    ("Молоко neo high protein 0 5 безлактозное 950 г",
     "Молоко neo high protein 0,5 безлактозное 950 г"),
    ("Молоко parmalat 1 8 ультрапастеризованное 1 кг",
     "Молоко parmalat 1,8 ультрапастеризованное 1 кг"),
])
def test_numbers_from_a_receipt_are_glued_back(raw, expected):
    """«0 5» — это жирность 0,5 %, а не два числа: без запятой сеть не находит ничего."""
    assert phonecart._clean_query(raw) == expected


def test_a_clean_name_is_left_alone():
    name = "Молоко Простоквашино 2,5% 930 мл"
    assert phonecart._clean_query(name) == name


def test_a_name_that_is_already_fine_is_not_rewritten(monkeypatch):
    """Канонического имени нет — возвращаем почищенный обрубок, а не выдумку."""
    monkeypatch.setattr(phonecart, "_catalog_name", lambda text: None)

    answer = phonecart._query_name(0, "Молоко neo high protein 0 5 950 г")

    assert answer == "Молоко neo high protein 0,5 950 г"


def test_a_found_canonical_name_wins(monkeypatch):
    """Нашлось каноническое имя — ищем им: сеть его понимает."""
    monkeypatch.setattr(phonecart, "_catalog_name",
                        lambda text: "Молоко Neo High Protein безлактозное 0,5%, 950 мл")

    answer = phonecart._query_name(0, "Молоко neo high protein 0 5 950 г")

    assert answer == "Молоко Neo High Protein безлактозное 0,5%, 950 мл"


def test_a_link_to_the_unified_base_is_still_first(monkeypatch):
    """Привязка к единому товару — главный довод, поиск по каталогу её не заменяет."""
    monkeypatch.setattr(phonecart.repo, "master_of", lambda pid: {"master_id": 5})
    monkeypatch.setattr("app.catalog.store.item", lambda item_id: {"name": "Молоко Нео 0,5% 950 мл"})
    monkeypatch.setattr(phonecart, "_catalog_name", lambda text: "Другое имя из поиска")

    answer = phonecart._query_name(1, "Молоко neo 0 5")

    assert answer == "Молоко Нео 0,5% 950 мл"
