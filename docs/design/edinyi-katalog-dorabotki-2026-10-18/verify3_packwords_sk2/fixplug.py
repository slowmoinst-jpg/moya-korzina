"""Подмена match.pack_count для проверки исправлений (рабочую копию не трогаем).

FIX=none     — как есть;
FIX=reviewer — правила из находки дословно (без границ слова и без отсечки единиц);
FIX=refined  — уточнённые правила скептика.
Подключение: python -m pytest -p fixplug ... (папка этого файла в PYTHONPATH).
"""
from __future__ import annotations

import os
import re
import sys

ROOT = "."
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.catalog import match  # noqa: E402

MODE = os.environ.get("FIX", "none")

# --- из находки дословно ---
REV_WORD_N = re.compile(r"(?:уп|упак|упаковка|кор|коробка)\.?\s*(\d{1,3})")
REV_N_CONT = re.compile(r"(\d{1,3})\s*(?:бут\w*|бан\w*|пач\w*|рулон\w*)\.?(?![а-яё])")

# --- уточнённые ---
# Слово упаковки и за ним число штук: «уп.12», «уп 6», «упак. 6», «упаковка 12 бут»,
# «кор. 27», «коробка 24», «пак 6». Слово — отдельное (не «суп 250»), число — не фасовка
# и не процент: «упаковка 300 г», «кор. 45%», «уп 2,5 кг» штуками не становятся.
_NOT_AMOUNT = r"(?![\d.,])(?!\s*(?:%|г|гр|кг|мг|мл|л|см|мм|м|g|gr|kg|ml|l)(?![а-яёa-z]))"
FIX_WORD_N = re.compile(r"(?<![а-яёa-z])(?:уп(?:ак(?:овк[аи])?)?|кор(?:обк[аи])?|пак)\.?\s*"
                        r"(?:по\s*)?(\d{1,3})" + _NOT_AMOUNT)
# Число и за ним тара штуками: «6 бут», «12 бут.», «4 банки», «2 пачки», «12 уп».
FIX_N_CONT = re.compile(r"(?<![\d.,])(\d{1,3})\s*(?:бут(?:ыл(?:ка|ки|ок))?|бан(?:ка|ки|ок)"
                        r"|пач(?:ка|ки|ек)|уп(?:ак(?:овк[аи]|овок)?)?)\.?(?![а-яё])")

# Необязательное третье правило (того же рода, в находке нет): объём или вес и за ними
# множитель с голым числом — «0,5л*12», «0,5 л х 12», «125г х 4».
FIX_UNIT_X_N = re.compile(r"\d\s*(?:л|мл|г|гр|кг)\s*[xх×*]\s*(\d{1,3})" + _NOT_AMOUNT)

_orig = match.pack_count


def _make(extra):
    def pack_count(name):
        text = (name or "").lower()
        if match._CYRILLIC.search(text):
            rules = (match._MULTI_UNIT, match._MULTI_TAIL, match._PIECES, *extra, match._MULTI)
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
    return pack_count


VARIANTS = {"none": _orig, "reviewer": _make((REV_WORD_N, REV_N_CONT)),
            "refined": _make((FIX_WORD_N, FIX_N_CONT)),
            "refined3": _make((FIX_WORD_N, FIX_N_CONT, FIX_UNIT_X_N))}


def pytest_configure(config):
    match.pack_count = VARIANTS[MODE]
    print(f"\n[fixplug] FIX={MODE}")
