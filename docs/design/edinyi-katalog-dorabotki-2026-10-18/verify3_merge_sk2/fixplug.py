"""Подмена-проба уточнённого исправления (рабочую копию не трогает).

Подключение: PYTHONPATH=<эта папка> python -m pytest -p fixplug ...
Режим берётся из переменной MERGE_FIX:
  words  — (моё) строки чека с одним display_name, у которых склеенные слова (link._prep,
           receipt=True, fwords) расходятся, с «без…»/«не…» как одним отрицанием, — строк нет;
  finding — дословно по находке: в одной строке основа X, в другой «не»/«без»+X, или
           «сзмж» против «беззмж» (по склеенному тексту) — строк нет;
  off    — без подмены.
MERGE_FIX_CODE=1 — вдобавок собственный штрихкод товара не решает при противоречии.
"""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict

ROOT = "."
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

MODE = os.environ.get("MERGE_FIX", "words")
CODE = os.environ.get("MERGE_FIX_CODE", "0") == "1"


def _canon(word: str) -> str:
    from app.catalog import link
    stem = link._negation(word)
    return "~" + stem if stem else word


def contradicts_words(raw_names) -> bool:
    from app.catalog import link
    from app.matcher.normalize import display_name
    groups: dict[str, set] = defaultdict(set)
    for n in raw_names:
        if not n:
            continue
        row = link._prep({"id": 0, "chain": "mine", "name": n}, receipt=True)
        groups[display_name(n).strip().lower()].add(frozenset(_canon(w) for w in row.fwords))
    return any(len(v) > 1 for v in groups.values())


def contradicts_finding(raw_names) -> bool:
    """Как сказано в находке: X против не/без+X, «сзмж» против «беззмж»."""
    from app.catalog import link, match
    sets = []
    zm = set()
    for n in raw_names:
        if not n:
            continue
        glued = match.glue(n).lower()
        if "беззмж" in glued or "bezzmzh" in glued:
            zm.add("bez")
        if "сзмж" in glued or "szmzh" in glued:
            zm.add("s")
        sets.append(set(link._prep({"id": 0, "chain": "mine", "name": n}, receipt=True).fwords))
    if len(zm) > 1:
        return True
    for a in sets:
        for b in sets:
            if a is b:
                continue
            for w in a:
                stem = link._negation(w)
                if stem and any(x == stem or x.startswith(stem) or stem.startswith(x)
                                for x in b if link._negation(x) is None and len(x) >= 3):
                    return True
    return False


def pytest_configure(config):
    if MODE == "off":
        return
    from app.catalog import link
    test = contradicts_words if MODE == "words" else contradicts_finding
    orig = link._rows_of_person

    def patched(product, raw_names):
        if test([n for n in raw_names if n]):
            return []
        return orig(product, raw_names)

    link._rows_of_person = patched
    link._MERGE_TEST = test
    if CODE:
        from app import repo
        orig_own = link._own_items
        orig_codes = link._codes

        def own_items(product, conn):
            if test(repo.raw_names(product.id)):
                return set()
            return orig_own(product, conn)

        def codes(product, mappings, choices):
            out = orig_codes(product, mappings, choices)
            if test(repo.raw_names(product.id)):
                mine = link._trusted(product.barcode)
                others = {c for c in (link._trusted(m.get("ean")) for m in link._human(mappings)
                                      if m["store_product_id"] not in
                                      {x["store_product_id"] for x in choices if not x["trusted"]}) if c}
                if mine and mine not in others:
                    out = out - {mine}
            return out

        link._own_items = own_items
        link._codes = codes
