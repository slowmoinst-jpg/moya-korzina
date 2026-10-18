"""Подмена предложенного исправления на время прогона (рабочая копия не меняется).

FIX=1  — только match.glue: «не X» склеивается, как «без X».
FIX=2  — только _abbrev: двухбуквенное слово чека, не покрытое кандидатом, — не выше слабого.
FIX=12 — оба (по умолчанию).
"""
from __future__ import annotations

import os
import re
import sys

ROOT = "."
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.catalog import link, match  # noqa: E402

MODE = os.environ.get("FIX", "12")

if "1" in MODE:
    match._WITHOUT_WORD = re.compile(r"\b(без|bez|не)\s+(?=[^\W\d_])", re.I)

if "3" in MODE:      # уточнённый вариант: «НЕ/ГАЗ», «БЕЗ/ГАЗА», «НЕ\ГАЗ» — тоже одно слово
    match._WITHOUT_WORD = re.compile(r"\b(без|bez|не)(?:\s+|\s*[/\\]\s*)(?=[^\W\d_])", re.I)

if "2" in MODE:
    _orig = link._abbrev
    _UNITS = frozenset(match.fold(u) for u in match.UNITS | match._UNIT_TOKENS)

    def _abbrev(mine, cand):
        level = _orig(mine, cand)
        if level != 2:
            return level
        words = set(cand.fwords)
        for w in mine.forder:
            if len(w) != 2 or w in _UNITS:
                continue
            if not any(c == w or c.startswith(w) for c in words):
                return 1
        return level

    link._abbrev = _abbrev
