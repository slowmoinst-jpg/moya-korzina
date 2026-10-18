"""Варианты правила штук для проверки находки «3в1 стик 20шт» (скептик №1).

none    — код рабочей копии как есть;
finder  — исправление находки дословно: буквы во взгляде назад _PIECES и _PIECES_LAT;
skip1   — «1 <штуки>» не останавливает поиск: берётся первое число штук больше одного;
spaced  — finder плюс «в 1» / «in 1» / «v 1» через пробел («3 в 1 стик», «All in 1 таблетки»);
strip   — перед правилом штук вырезается само «N в 1» / «N in 1» / «all in 1» / «все в 1».
"""
from __future__ import annotations

import re

from app.catalog import match

ORIG = {"_PIECES": match._PIECES, "_PIECES_LAT": match._PIECES_LAT, "pack_count": match.pack_count}
_LAT_WORDS = (r"(?:sht|st(?!\s*b\b)|paketikov|paketika|pak|kapsul|sashe|sase|stikov|piramidok|tabletok)"
              r"(?![a-z])")
# Взгляд назад как в находке: (?<![\d.,а-яёa-z]) и (?<![\d.,a-z]).
FINDER_PIECES = re.compile(r"(?<![\d.,а-яёa-z])(\d{1,3})\s*(?:" + match._PIECE_WORDS + r")\.?(?![а-яё])")
FINDER_PIECES_LAT = re.compile(r"(?<![\d.,a-z])(\d{1,3})\s*" + _LAT_WORDS)
SPACED_PIECES = re.compile(r"(?<![\d.,а-яёa-z])(?<!в\s)(?<!in\s)(?<!v\s)(\d{1,3})\s*(?:"
                           + match._PIECE_WORDS + r")\.?(?![а-яё])")
SPACED_PIECES_LAT = re.compile(r"(?<![\d.,a-z])(?<!v\s)(?<!in\s)(\d{1,3})\s*" + _LAT_WORDS)
IN_ONE = re.compile(r"(?:(?<![\d.,])\d{1,2}|\ball|\bвсе|\bвсё)\s*-?\s*(?:в|in|v)\s*-?\s*1(?![\d.,])")


def _rules(text: str, pieces, pieces_lat):
    if match._CYRILLIC.search(text):
        return (match._MULTI_UNIT, match._MULTI_TAIL, pieces, match._MULTI), pieces
    return (match._MULTI_UNIT_LAT, match._MULTI_TAIL_LAT, pieces_lat, match._MULTI_LAT), pieces_lat


def pack_count_skip1(name: str | None) -> int:
    text = (name or "").lower()
    rules, pieces = _rules(text, ORIG["_PIECES"], ORIG["_PIECES_LAT"])
    for rx in rules:
        if rx is pieces:
            vals = [int(m.group(1)) for m in rx.finditer(text)]
            if vals:
                more = [v for v in vals if v > 1]
                return more[0] if more else 1
            continue
        found = rx.search(text)
        if found:
            return max(1, int(found.group(1)))
    return 1


def pack_count_strip(name: str | None) -> int:
    text = (name or "").lower()
    rules, pieces = _rules(text, ORIG["_PIECES"], ORIG["_PIECES_LAT"])
    for rx in rules:
        found = rx.search(IN_ONE.sub(" ", text) if rx is pieces else text)
        if found:
            return max(1, int(found.group(1)))
    return 1


def use(variant: str, setter=None) -> None:
    """Включить вариант. setter(obj, name, value) — monkeypatch.setattr или setattr."""
    put = setter or setattr
    put(match, "_PIECES", ORIG["_PIECES"])
    put(match, "_PIECES_LAT", ORIG["_PIECES_LAT"])
    put(match, "pack_count", ORIG["pack_count"])
    if variant == "none":
        return
    if variant == "finder":
        put(match, "_PIECES", FINDER_PIECES)
        put(match, "_PIECES_LAT", FINDER_PIECES_LAT)
    elif variant == "spaced":
        put(match, "_PIECES", SPACED_PIECES)
        put(match, "_PIECES_LAT", SPACED_PIECES_LAT)
    elif variant == "skip1":
        put(match, "pack_count", pack_count_skip1)
    elif variant == "strip":
        put(match, "pack_count", pack_count_strip)
    else:
        raise ValueError(variant)


VARIANTS = ("none", "finder", "skip1", "spaced", "strip")
