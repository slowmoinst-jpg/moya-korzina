# Копия verify3_eggs_sk1/eggfix.py (исправление соседней находки «_counted» в link._judge), режим по умолчанию refined.
"""Подмена link._judge для проверки исправлений находки «яйца 10 шт всегда слабые».

EGGFIX=none     — код как есть;
EGGFIX=finder   — дословно предложение проверяющего: в ветке равных слов
                  close or _by_weight or (packs равны > 1 и обе массы неизвестны);
EGGFIX=refined  — то же условие «посчитано штуками», но ещё: ни одна сторона не «kg»
                  и прочие числа названий (кроме числа штук) совпадают — «Pampers 4»
                  и «Pampers 5», «60W» и «75W» сильными не становятся. Применяется в
                  ветке равных слов и в полном сокращении (level 2).
"""
from __future__ import annotations

import os
import re
from dataclasses import replace

from app.catalog import link
from app.catalog import match as matching

MODE = os.environ.get("EGGFIX", "refined")
ORIGINAL = link._judge
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def numbers(row: matching.Row) -> list[str]:
    nums = [n.replace(",", ".") for n in _NUM.findall(matching.glue(row.name).lower())]
    if row.packs > 1 and str(row.packs) in nums:
        nums.remove(str(row.packs))
    return sorted(nums)


def counted_finder(mine: matching.Row, cand: matching.Row) -> bool:
    return mine.packs == cand.packs > 1 and not mine.weight_g and not cand.weight_g


def counted_refined(mine: matching.Row, cand: matching.Row) -> bool:
    if not counted_finder(mine, cand):
        return False
    if "kg" in (mine.unit, cand.unit):
        return False
    return numbers(mine) == numbers(cand)


def _make(counted, in_abbrev: bool):
    def judge(mine, cand, vocab):
        mine, cand = replace(mine, bkey=None), replace(cand, bkey=None)
        ok, method, score = matching.same_product(mine, cand, vocab)
        if ok:
            close = link._close(mine.weight_g, cand.weight_g)
            if mine.words == cand.words:
                return close or link._by_weight(mine, cand) or counted(mine, cand), score, "words"
            small, big = sorted((mine.fwords, cand.fwords), key=len)
            if close and small < big and (big - small) <= link.DESCRIPTORS:
                return True, 0.9, "words"
            return False, score, "translit" if mine.fwords == cand.fwords else "inclusion"
        if matching.compatible(mine, cand):
            level = link._abbrev(mine, cand)
            if level == 2:
                same = link._close(mine.weight_g, cand.weight_g) or (in_abbrev and counted(mine, cand))
                return same, 0.8, "abbrev"
            if level == 1:
                return False, 0.75, "abbrev"
        return None
    return judge


if MODE == "finder":
    link._judge = _make(counted_finder, in_abbrev=False)
elif MODE == "refined":
    link._judge = _make(counted_refined, in_abbrev=True)
