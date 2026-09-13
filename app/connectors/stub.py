"""Коннектор-заглушка на фиксированных ценах из data/fallback_prices.csv (раздел 5.4).

Используется тремя способами:
  * тесты — предсказуемые цены без сети;
  * магазин «Пятёрочка» (connector_type='stub') — сетевого API у нас нет;
  * резерв Магнита и ВкусВилла, когда их недокументированный API не ответил.
"""
from __future__ import annotations

import csv
import logging
import os

from app import config
from app.connectors.base import Connector, register, similarity
from app.models import Candidate, PriceSnapshot

log = logging.getLogger(__name__)

CSV_PATH = os.path.join(config.ROOT, "data", "fallback_prices.csv")
DEFAULT_STORE = "pyaterochka"          # чем подменяем абстрактный код 'stub'

_rows_cache: dict[str, list[dict]] | None = None


def _to_float(value: str | None) -> float | None:
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


def load_rows() -> dict[str, list[dict]]:
    """Читает CSV один раз за процесс: store_code -> список строк."""
    global _rows_cache
    if _rows_cache is not None:
        return _rows_cache
    data: dict[str, list[dict]] = {}
    if not os.path.exists(CSV_PATH):
        log.warning("нет файла резервных цен %s", CSV_PATH)
        _rows_cache = data
        return data
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as fh:
        for raw in csv.DictReader(fh):
            code = (raw.get("store_code") or "").strip()
            if not code or not (raw.get("sku") or "").strip():
                continue
            data.setdefault(code, []).append({
                "store_code": code,
                "sku": raw["sku"].strip(),
                "name": (raw.get("name") or "").strip(),
                "price": _to_float(raw.get("price")) or 0.0,
                "unit": (raw.get("unit") or "pcs").strip() or "pcs",
                "weight_g": _to_float(raw.get("weight_g")),
                "price_per_kg": _to_float(raw.get("price_per_kg")),
                "in_stock": str(raw.get("in_stock", "1")).strip() not in ("0", "false", "False", ""),
            })
    _rows_cache = data
    return data


def reload_rows() -> None:
    """Сброс кэша файла (нужен тестам и после правки CSV)."""
    global _rows_cache
    _rows_cache = None


def rows_for(store_code: str) -> list[dict]:
    data = load_rows()
    rows = data.get(store_code, [])
    if not rows and store_code == "stub":
        rows = data.get(DEFAULT_STORE, [])
    return rows


def fallback_search(store_code: str, query: str, limit: int = 3) -> list[Candidate]:
    """Топ-N кандидатов из CSV: подстрока в названии, дальше — похожесть."""
    q = (query or "").strip().lower().replace("ё", "е")
    scored: list[Candidate] = []
    for row in rows_for(store_code):
        name_lc = row["name"].lower().replace("ё", "е")
        score = similarity(query, row["name"])
        if q and q in name_lc:
            score = max(score, 0.95)
        elif score < 0.35:
            continue
        scored.append(Candidate(
            store_code=store_code,
            sku=row["sku"],
            name=row["name"],
            price=row["price"],
            weight_g=row["weight_g"],
            unit=row["unit"],
            url=None,
            score=score,
        ))
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:limit]


def fallback_prices(store_code: str, skus: list[str]) -> list[PriceSnapshot]:
    by_sku = {row["sku"]: row for row in rows_for(store_code)}
    snapshots: list[PriceSnapshot] = []
    for sku in skus:
        row = by_sku.get(str(sku))
        if row is None:
            log.warning("%s: нет резервной цены для SKU %s", store_code, sku)
            continue
        snapshots.append(PriceSnapshot(
            store_code=store_code,
            sku=row["sku"],
            price=row["price"],
            price_per_kg=row["price_per_kg"] or (row["price"] if row["unit"] == "kg" else None),
            in_stock=row["in_stock"],
            name=row["name"],
        ))
    return snapshots


@register("stub")
class StubConnector(Connector):
    """Фиксированные цены из CSV. Поиск — подстрока в названии, регистронезависимо."""

    code = "stub"

    def _search(self, query: str, limit: int) -> list[Candidate]:
        return fallback_search(self.code, query, limit)

    def _get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        return fallback_prices(self.code, skus)
