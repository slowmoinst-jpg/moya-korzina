"""Подмена исправления в памяти процесса (рабочая копия не трогается).

NEGSP_FIX=1 — только «не X» склеивается (match._WITHOUT_WORD);
NEGSP_FIX=2 — только двухбуквенные слова чека в _abbrev;
NEGSP_FIX=12 (по умолчанию) — оба.
"""
from __future__ import annotations

import os
import re


def _apply() -> None:
    from app.catalog import link, match

    mode = os.environ.get("NEGSP_FIX", "12")
    if "1" in mode:
        match._WITHOUT_WORD = re.compile(r"\b(без|bez|не)\s+(?=[^\W\d_])", re.I)
    if "2" in mode:
        orig = link._abbrev

        def _abbrev(mine, cand):
            level = orig(mine, cand)
            if not level:
                return 0
            words = set(cand.fwords)
            short = [w for w in mine.forder if len(w) == 2]
            if any(not any(c.startswith(s) for c in words) for s in short):
                return min(level, 1)
            return level

        link._abbrev = _abbrev
    if "3" in mode:
        # уточнение: двухбуквенное слово покрывается только словом кандидата, которое
        # не покрыто другим словом чека (иначе «СЛ» покрывает «славяновская»)
        orig3 = link._abbrev

        def _abbrev3(mine, cand):
            level = orig3(mine, cand)
            if not level:
                return 0
            words = set(cand.fwords)
            long_ = [w for w in mine.forder if len(w) >= 3]
            free = {c for c in words if not any(c == r or link._starts(c, r) for r in long_)}
            short = [w for w in mine.forder if len(w) == 2]
            if any(not any(c.startswith(s) for c in free) for s in short):
                return min(level, 1)
            return level

        link._abbrev = _abbrev3

    if "4" in mode:
        # тара через точку или пробел («СТ.Б», «ПЛ Б») — тоже шум, чтобы п.2 не стоил сильных связей
        match._TARE_SLASH = re.compile(
            rf"(?<![{match._LETTER}])(?:ст|пл|пэт|ж|с)\s*[/.\s]\s*б(?:ут)?\.?(?![{match._LETTER}])", re.I)


def pytest_configure(config):
    _apply()
