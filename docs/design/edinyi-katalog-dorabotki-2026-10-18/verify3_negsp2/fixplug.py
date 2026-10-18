"""Подмена исправления находки (код рабочей копии не меняется).

FIX=1   — match.glue клеит «не X» так же, как «без X»;
FIX=2   — _abbrev: слово чека из двух букв (не единица, не шум), которое не равно
          слову кандидата и не начинает его, — сокращение не выше слабого;
FIX=12  — оба.
Подключение: python -m pytest -p fixplug ... (папка пробы в PYTHONPATH).
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

if "2" in MODE:
    _orig = link._abbrev
    _SKIP = frozenset(match.fold(w) for w in match._UNIT_TOKENS) | match.NOISE

    def _abbrev_fixed(mine, cand):
        level = _orig(mine, cand)
        if not level:
            return 0
        words = cand.fwords
        for w in mine.forder:
            if len(w) != 2 or w in _SKIP:
                continue
            if w in words or any(c.startswith(w) for c in words):
                continue
            return 1
        return level

    link._abbrev = _abbrev_fixed
