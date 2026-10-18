"""Подмена исправления по NEGDOT_FIX (только в памяти процесса, файлы рабочей копии не трогаются).

Годится и как плагин pytest (-p fixplug) для прогона тестов рабочей копии с исправлением.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = "."
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.catalog import link, match  # noqa: E402

MODE = os.environ.get("NEGDOT_FIX", "").strip()
L = match._LETTER

if MODE == "lit":
    # Буквально по находке: ([нНбБ])\s*[/.\\-]\s*(?=[^\W\d_]{2}) — для всех разделителей.
    match._SLASH_NEG = re.compile(rf"(?<![{L}])([нНбБ])\s*[/.\\\-]\s*(?=[^\W\d_]{{2}})")
elif MODE in ("ref", "refonly"):
    # «/» — как сейчас (хватает одной буквы: «Б/А», «Н/Г», «Б/К»); точка, обратная черта,
    # дефис — только перед основой от двух букв («Б.Ю.АЛЕКСАНДРОВ», «Н-Р» не трогаются).
    match._SLASH_NEG = re.compile(
        rf"(?<![{L}])([нНбБ])(?:\s*/\s*(?=[^\W\d_])|\s*[.\\\-]\s*(?=[^\W\d_]{{2}}))")

elif MODE == "ref2":
    # Как ref, плюс одна буква после [.\\-], если за ней не буква и не «.Буква» (инициалы):
    # «Б.А 0,45Л», «Б-А», «Б.А.» — отрицание; «Б.Ю.АЛЕКСАНДРОВ», «Б. Ю. Александров» — нет.
    match._SLASH_NEG = re.compile(
        rf"(?<![{L}])([нНбБ])(?:\s*/\s*(?=[^\W\d_])"
        rf"|\s*[.\\\-]\s*(?=[^\W\d_]{{2}}|[^\W\d_](?![^\W\d_])(?!\.\s*[^\W\d_])))")

elif MODE == "ref3":
    # Как ref2, но одна буква — только после точки и обратной черты: дефис с одной буквой —
    # это «Н-Р» (набор), его не трогаем.
    match._SLASH_NEG = re.compile(
        rf"(?<![{L}])([нНбБ])(?:\s*/\s*(?=[^\W\d_])"
        rf"|\s*[.\\\-]\s*(?=[^\W\d_]{{2}})"
        rf"|\s*[.\\]\s*(?=[^\W\d_](?![^\W\d_])(?!\.\s*[^\W\d_])))")

if MODE in ("lit", "ref", "ref2", "ref3"):
    _orig_prep = link._prep

    def _prep(row: dict, receipt: bool = False):
        name = row.get("name") or ""
        if receipt:
            name = link._ABBR_DOT.sub(" ", match.glue(name))
        return match.prepare({**row, "id": int(row.get("id") or 0), "name": name})

    link._prep = _prep

print(f"[fixplug] NEGDOT_FIX={MODE or 'none'} _SLASH_NEG={match._SLASH_NEG.pattern}", file=sys.stderr)
