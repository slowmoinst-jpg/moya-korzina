"""Подмена match.pack_count уточнённым правилом (режим FIX=refined) или правилом находки (FIX=finder).

pytest -p fixplug ...  с PYTHONPATH на эту папку. Без FIX — ничего не подменяет.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = "."
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.catalog import match  # noqa: E402

MODE = os.environ.get("FIX", "")

# Число после слова не должно быть фасовкой или размером: «уп. 200 г», «упаковка 0,5 л».
_NO_UNIT = r"(?![\d.,])(?!\s*(?:г|гр|кг|мг|мл|л|шт|см|мм|м|%|g|gr|kg|ml|l)(?![а-яёa-z]))"
# «уп.12», «уп 6», «упак. 6», «упаковка 12», «кор. 27», «пак 6», «блок 10» — число после слова тары.
_WORD_N = re.compile(r"(?<![а-яё])(?:упаковка|упак|уп|коробка|кор|пак|блок)(?![а-яё])\.?\s*(?:по\s+)?(\d{1,2})"
                     + _NO_UNIT)
# «6 бут», «12 бут.», «6 уп», «2 кор», «4 банки», «10 пачек» — число перед словом тары.
_N_WORD = re.compile(r"(?<![\d.,])(\d{1,3})\s*(?:бут(?:ыл(?:ок|ки|ка))?|уп(?:ак(?:овк[аи]|овок)?)?"
                     r"|кор(?:об(?:ок|ки|ка))?|бан(?:ок|ки|ка)|пач(?:ек|ки|ка))\.?(?![а-яё])")
# «0,5л х 12», «0,5л*12», «125г x 4» — множитель хвостом без «шт».
_TAIL_BARE = re.compile(r"(?:г|гр|кг|мл|л)\.?\s*[xх×*]\s*(\d{1,3})" + _NO_UNIT)

# Правило находки буквально.
_F1 = re.compile(r"(?:уп|упак|упаковка|кор|коробка)\.?\s*(\d{1,3})")
_F2 = re.compile(r"(\d{1,3})\s*(?:бут\w*|бан\w*|пач\w*|рулон\w*)\.?(?![а-яё])")


def refined(name):
    text = (name or "").lower()
    if match._CYRILLIC.search(text):
        rules = (match._MULTI_UNIT, match._MULTI_TAIL, match._PIECES, _N_WORD, _WORD_N, _TAIL_BARE, match._MULTI)
    else:
        rules = (match._MULTI_UNIT_LAT, match._MULTI_TAIL_LAT, match._PIECES_LAT, match._MULTI_LAT)
    for rx in rules:
        found = rx.search(text)
        if found:
            try:
                return max(1, int(found.group(1)))
            except ValueError:
                return 1
    return 1


def finder(name):
    text = (name or "").lower()
    if match._CYRILLIC.search(text):
        rules = (match._MULTI_UNIT, match._MULTI_TAIL, match._PIECES, _F1, _F2, match._MULTI)
    else:
        rules = (match._MULTI_UNIT_LAT, match._MULTI_TAIL_LAT, match._PIECES_LAT, match._MULTI_LAT)
    for rx in rules:
        found = rx.search(text)
        if found:
            try:
                return max(1, int(found.group(1)))
            except ValueError:
                return 1
    return 1


ORIGINAL = match.pack_count


def pytest_configure(config):
    if MODE == "refined":
        match.pack_count = refined
    elif MODE == "finder":
        match.pack_count = finder
