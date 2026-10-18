"""Плагин pytest: исправление из находки, подменой в процессе тестов (рабочую копию не трогаем).

SAMEITEM_MODE=fix  — словарь марок пересборки + описания обработки + отрицания;
SAMEITEM_MODE=vocab — только словарь марок пересборки;
SAMEITEM_MODE=asis — без подмены.
"""
import os

from app.catalog import link, refresh
from app.catalog import match as matching

MODE = os.environ.get("SAMEITEM_MODE", "asis")
_ORIG_CLUSTER = matching.cluster
_VOCAB = {"v": frozenset()}


def _cluster_with_vocab(products):
    _VOCAB["v"] = matching.brand_vocabulary([matching.prepare(p) for p in products])
    return _ORIG_CLUSTER(products)


def _same_item(before, canon):
    if not before:
        return False
    fields = ("name", "brand", "weight_g", "unit")
    old = matching.prepare({"id": 1, "chain": "before", **{k: before.get(k) for k in fields}})
    new = matching.prepare({"id": 2, "chain": "after", **{k: canon.get(k) for k in fields}})
    if matching.same_product(old, new, _VOCAB["v"])[0]:
        return True
    if MODE != "fix" or not matching.compatible(old, new):
        return False
    small, big = sorted((old.fwords, new.fwords), key=len)
    if small < big and link._close(old.weight_g, new.weight_g) and (big - small) <= link.DESCRIPTORS:
        return True
    a, b = set(old.fwords), set(new.fwords)
    only_a, only_b = a - b, b - a
    return bool(only_a) and len(only_a) == len(only_b) and all(
        any(link._starts(x, y) or link._starts(y, x) for y in only_b) for x in only_a)


if MODE in ("fix", "vocab"):
    matching.cluster = _cluster_with_vocab
    refresh._same_item = _same_item
