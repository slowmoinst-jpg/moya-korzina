"""Подмена исправлений на время пробы (monkeypatch), рабочая копия не меняется.

Режимы:
  ves    — (1) prepare: сеть единицу не назвала, а в названии «весов…» → unit='kg';
           (2) compatible: весовое без фасовки (kg, weight None) против НЕвесового с известной
               фасовкой — спор.
  ves2   — prepare: сеть единицу не назвала → unit_from_name(name) (kg по слову, pcs по
           граммовке), как уже делают сборщики Пятёрочки, Самоката и Fix Price; compatible не трогаем.
  vesr   — только правило (2) в compatible.
  zmzh   — признак ЗМЖ трёхзначный: «сзмж» True, «беззмж»/«БЗМЖ» False, молчание None;
           compatible разводит только True против False; _split_chained копит его, как процент;
           link._conflict знает его.
  both   — ves + zmzh;  both2 — ves2 + zmzh.
"""
from __future__ import annotations

from functools import lru_cache

from app.catalog import link, match
from app.matcher.normalize import sold_by_weight, unit_from_name

_ORIG_PREPARE = match.prepare
_ORIG_COMPATIBLE = match.compatible
_ORIG_SPLIT = match._split_chained
_ORIG_CONFLICT = link._conflict


@lru_cache(maxsize=65536)
def zmzh_of(name: str | None):
    words = {match.fold(w) for w in match._TOKEN.findall(match.glue(name or ""))}
    if "szmz" in words:
        return True
    if words & {"bezzmz", "bzmz"}:
        return False
    return None


def _prepare_ves(product: dict):
    row = _ORIG_PREPARE(product)
    if row.unit is None and sold_by_weight(row.name):
        row.unit = "kg"
    return row


def _prepare_ves2(product: dict):
    row = _ORIG_PREPARE(product)
    if row.unit is None:
        row.unit = unit_from_name(row.name)
    return row


def _weighed_vs_packed(a, b) -> bool:
    return a.unit == "kg" and not a.weight_g and b.unit != "kg" and bool(b.weight_g)


def _weighed_vs_any_weight(a, b) -> bool:
    """Неуточнённое правило находки: kg без фасовки против ЛЮБОЙ строки с фасовкой."""
    return a.unit == "kg" and not a.weight_g and bool(b.weight_g)


def make_compatible(rule: bool, zmzh: bool, loose: bool = False):
    check = _weighed_vs_any_weight if loose else _weighed_vs_packed

    def compatible(a, b):
        if rule and (check(a, b) or check(b, a)):
            return False
        if zmzh:
            za, zb = zmzh_of(a.name), zmzh_of(b.name)
            if za is not None and zb is not None and za != zb:
                return False
        return _ORIG_COMPATIBLE(a, b)
    return compatible


def _split_zmzh(group, vocab):
    """match._split_chained + признак ЗМЖ копится, как процент."""
    rest = list(group)
    out = []
    while rest:
        seed = max(rest, key=lambda item: (len(item[0].words), len(item[0].name)))
        kept, dropped = [seed], []
        pct = seed[0].pct
        marks = seed[0].marks
        packs = seed[0].packs if seed[0].packs > 1 else None
        z = zmzh_of(seed[0].name)
        for item in rest:
            row = item[0]
            if item is seed:
                continue
            if pct is not None and row.pct is not None and not match.percents_agree(pct, row.pct):
                dropped.append(item)
                continue
            if marks and row.marks and marks != row.marks:
                dropped.append(item)
                continue
            if packs is not None and row.packs > 1 and row.packs != packs:
                dropped.append(item)
                continue
            rz = zmzh_of(row.name)
            if z is not None and rz is not None and z != rz:
                dropped.append(item)
                continue
            if not match.same_product(seed[0], row, vocab)[0]:
                dropped.append(item)
                continue
            kept.append(item)
            if pct is None:
                pct = row.pct
            if not marks:
                marks = row.marks
            if packs is None and row.packs > 1:
                packs = row.packs
            if z is None:
                z = rz
        out.append(kept)
        rest = dropped
    return out


def _conflict_zmzh(mine, rows):
    if _ORIG_CONFLICT(mine, rows):
        return True
    zm = zmzh_of(mine.name)
    if zm is None:
        return False
    return any(zmzh_of(r.name) is not None and zmzh_of(r.name) != zm for r in rows)


_CTX: dict = {"z": None}


def _person_zmzh(product):
    from app import repo
    rows = link._rows_of_person(product, repo.raw_names(product.id))
    said = {zmzh_of(r.name) for r in rows} - {None}
    return next(iter(said)) if len(said) == 1 else None


def install_zlink(monkeypatch):
    """База не трогается: в materialize строки, НАЗВАВШИЕ противоположный признак ЗМЖ,
    не подтверждаются и не ложатся кандидатами (сеть без другой строки — пусто)."""
    from app.catalog import store
    orig_mat, orig_rows = link.materialize, store.item_rows

    def materialize(product, item_id, strong, conn=None):
        _CTX["z"] = _person_zmzh(product)
        try:
            return orig_mat(product, item_id, strong, conn=conn)
        finally:
            _CTX["z"] = None

    def item_rows(item_id, conn=None):
        rows = orig_rows(item_id, conn)
        z = _CTX["z"]
        if z is None:
            return rows
        return [r for r in rows if zmzh_of(r.get("name")) in (None, z)]

    monkeypatch.setattr(link, "materialize", materialize)
    monkeypatch.setattr(store, "item_rows", item_rows)


def install(monkeypatch, mode: str) -> None:
    if mode in ("zlink", "veszl"):
        install_zlink(monkeypatch)
        if mode == "veszl":
            monkeypatch.setattr(match, "prepare", _prepare_ves)
            monkeypatch.setattr(match, "compatible", make_compatible(True, False))
        return
    if mode == "vesu":            # правило без оговорки «другая строка не весовая»
        monkeypatch.setattr(match, "prepare", _prepare_ves)
        monkeypatch.setattr(match, "compatible", make_compatible(True, False, loose=True))
        return
    if mode == "nonoise":         # «весов…» убрать из NOISE (как до второго круга)
        keep = frozenset(w for w in match.NOISE if not w.startswith("vesov"))
        monkeypatch.setattr(match, "NOISE", keep)
        return
    ves = mode in ("ves", "both")
    ves2 = mode in ("ves2", "both2")
    rule = mode in ("ves", "both", "vesr")
    zm = mode in ("zmzh", "both", "both2")
    if ves:
        monkeypatch.setattr(match, "prepare", _prepare_ves)
    if ves2:
        monkeypatch.setattr(match, "prepare", _prepare_ves2)
    if rule or zm:
        monkeypatch.setattr(match, "compatible", make_compatible(rule, zm))
    if zm:
        monkeypatch.setattr(match, "_split_chained", _split_zmzh)
        monkeypatch.setattr(link, "_conflict", _conflict_zmzh)
