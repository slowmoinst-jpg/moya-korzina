"""Подмена link._disputed для проверки исправления (рабочую копию не трогаем).

SK2_MODE:
  asis     — без подмены;
  finding  — исправление из находки буквально: (б) слово строки человека от трёх букв,
             не покрытое ни одним словом выбора (_starts в обе стороны), и (а) слово выбора,
             кроме марки, DESCRIPTORS и SORT_WORDS, не покрытое ни одной строкой человека;
  refined  — то же, но в (б) нейтральны слова человека, равные или начинающие слово из
             DESCRIPTORS, SORT_WORDS, NOISE («паст», «пит», «нат», «вес»), а в (а)
             покрытие считается по словам всех строк человека, включая двухбуквенные;
  gated    — refined, но только если строки человека С ВЫБОРОМ СВЯЗАНЫ (хоть одно значимое
             слово человека покрыто словом выбора, марка тоже считается). Название, ничем не
             похожее на выбор («Что-то совсем другое по названию»), как и сейчас, не спорит:
             это ярлык человека, и его выбор — мост (tests/test_unified_catalog.py:163).
             (б) срабатывает, только если у КАЖДОЙ строки человека есть непокрытое слово:
             ярлык человека рядом с покрытой строкой чека выбору не возражает.
  receipts — refined, но (б) судит по СТРОКАМ ЧЕКА: выбор спорен, если у каждой строки
             чека есть значимое слово, не покрытое выбором («Сыр Ламбер» → «Сыр Тильзитер»:
             «сыр» выбрасывает normalize_name, общих слов нет, но чек назвал «ламбер»).
             Товар без строк чека судится по эталону, и только если эталон с выбором связан:
             ярлык «Что-то совсем другое по названию» не спорит ни (а), ни (б).
Подключение: python -m pytest -p fixplug_sk2 ... (папка проб — в PYTHONPATH).
"""
from __future__ import annotations

import os

MODE = os.environ.get("SK2_MODE", "asis")


def make(mode: str):
    """Нестрогая проверка покрытия в режиме mode; None — без подмены."""
    if mode == "asis":
        return None
    from app.catalog import link
    from app.catalog import match as matching

    neutral = frozenset(link.DESCRIPTORS | link.SORT_WORDS | matching.NOISE)

    def _neutral(word: str) -> bool:
        return mode != "finding" and any(n.startswith(word) for n in neutral)

    def _covers(a: str, b: str) -> bool:
        return a == b or link._starts(a, b) or link._starts(b, a)

    def extra(person, cand) -> bool:
        if not person:
            return False
        words = set(cand.fwords)
        significant = [[r for r in m.fwords if len(r) >= 3 and not _neutral(r)] for m in person]
        open_ = [[r for r in sig if not any(_covers(c, r) for c in words)] for sig in significant]
        if mode == "receipts":
            tagged = [m.chain == "receipt" for m in person]
            if any(tagged):
                rows = [o for o, s, t in zip(open_, significant, tagged) if t and s]
                if rows and all(rows):
                    return True          # (б) каждая строка чека называет то, чего нет у выбора
            else:
                related = any(len(o) < len(s) for o, s in zip(open_, significant))
                if not related:
                    return False         # ярлык человека, ничем не похожий на выбор
                if any(open_):
                    return True          # (б) по эталону
        elif mode == "gated":
            related = any(len(o) < len(s) for o, s in zip(open_, significant))
            if not related:
                return False             # ярлык человека, ничем не похожий на выбор
            rows = [o for o, s in zip(open_, significant) if s]
            if rows and all(rows):
                return True              # (б) каждая строка человека называет то, чего нет
        elif any(open_):
            return True                  # (б)
        said = set().union(*(m.fwords for m in person))
        if mode == "finding":
            said = {r for r in said if len(r) >= 3}
        for c in words - set(cand.fbrand) - link.DESCRIPTORS - link.SORT_WORDS:
            if not any(_covers(c, r) for r in said):
                return True              # (а) лишнее слово выбора
        return False
    return extra


def _install() -> None:
    extra = make(MODE)
    if extra is None:
        return
    from app.catalog import link

    original = link._disputed

    def disputed(person, cand, strict):
        return original(person, cand, strict) or extra(person, cand)

    link._disputed = disputed
    if MODE == "receipts":
        from dataclasses import replace

        rows_of_person = link._rows_of_person

        def tagged(product, raw_names):
            said = {n for n in raw_names if n} | {link._ABBR_DOT.sub(" ", n) for n in raw_names if n}
            return [replace(r, chain="receipt") if r.name in said else r
                    for r in rows_of_person(product, raw_names)]
        link._rows_of_person = tagged


def pytest_configure(config):  # noqa: ARG001 — крючок pytest
    _install()
