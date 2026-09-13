"""Цены из собственных чеков — для магазинов, куда нельзя постучаться.

У Пятёрочки и Дикси каталог закрыт: 5ka.ru отдаёт заглушку и 403 на всё, dixy.ru
показывает капчу вместо страниц. MCP у них нет. Значит цену надо брать не у магазина,
а у себя — из чеков, которые человек уже загрузил.

Это не суррогат, а в некотором смысле источник получше каталога. В чеке записана
цена, по которой человек РЕАЛЬНО заплатил в РЕАЛЬНОМ магазине: со всеми скидками
по его карте, в его районе, в его формате магазина. Каталог такого не покажет.

Ограничение честное и важное: цена в чеке — вчерашняя. Поэтому каждый снимок несёт
дату, а интерфейс обязан показывать, насколько она свежая. Чем старше чек, тем
осторожнее к нему надо относиться — но «старая настоящая цена» полезнее, чем
отсутствие цены, и уж точно полезнее выдуманной.

Артикул здесь синтетический: «hist-<id эталона>». Он устойчив между запусками и
не пересекается с артикулами магазинов.
"""
from __future__ import annotations

import logging

from app import repo
from app.connectors.base import Connector, register, similarity
from app.models import Candidate, PriceSnapshot

log = logging.getLogger(__name__)

PREFIX = "hist-"


def _product_id(sku: str) -> int | None:
    if not str(sku).startswith(PREFIX):
        return None
    try:
        return int(str(sku)[len(PREFIX):])
    except ValueError:
        return None


def last_prices(store_code: str) -> dict[int, dict]:
    """product_id -> самая свежая строка чека этого магазина.

    list_history отдаёт строки уже отсортированными по убыванию даты, поэтому первая
    встреченная позиция и есть последняя покупка.
    """
    store = repo.get_store(store_code)
    if not store:
        return {}
    out: dict[int, dict] = {}
    for row in repo.list_history(store_id=store.id):
        pid = row.get("product_id")
        price = row.get("unit_price")
        if not pid or pid in out or not price:
            continue
        out[pid] = row
    return out


class HistoryConnector(Connector):
    """Коннектор для магазина без доступного каталога. Источник — purchase_history."""

    fallback_sku_prefix = PREFIX

    def _candidates(self) -> list[Candidate]:
        found = []
        for pid, row in last_prices(self.code).items():
            name = (row.get("product_name") or row.get("raw_name") or "").strip()
            if not name:
                continue
            found.append(Candidate(
                store_code=self.code,
                sku=f"{PREFIX}{pid}",
                name=name,
                price=round(float(row["unit_price"]), 2),
                unit=row.get("unit") or "pcs",
            ))
        return found

    def _search(self, query: str, limit: int) -> list[Candidate]:
        found = self._candidates()
        if not found:
            log.info("%s: чеков по этому магазину пока нет — беру data/fallback_prices.csv", self.code)
            return self._fallback_search(query, limit)
        for candidate in found:
            candidate.score = similarity(query, candidate.name)
        found.sort(key=lambda c: c.score, reverse=True)
        return [c for c in found if c.score > 0.3][:limit] or self._fallback_search(query, limit)

    def _get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        known = last_prices(self.code)
        out: list[PriceSnapshot] = []
        missing: list[str] = []
        for sku in skus:
            pid = _product_id(sku)
            row = known.get(pid) if pid else None
            if not row:
                missing.append(sku)
                continue
            price = round(float(row["unit_price"]), 2)
            out.append(PriceSnapshot(
                store_code=self.code,
                sku=str(sku),
                price=price,
                price_per_kg=price if (row.get("unit") == "kg") else None,
                in_stock=True,          # чек говорит, что товар был. Что он есть сейчас — не говорит
                fetched_at=str(row.get("date") or ""),
                name=(row.get("product_name") or row.get("raw_name") or "").strip() or None,
            ))
        if missing:
            out.extend(self._fallback_prices(missing))
        return out


@register("pyaterochka")
class PyaterochkaConnector(HistoryConnector):
    """Пятёрочка. Каталог 5ka.ru закрыт наглухо, MCP нет — живём на чеках."""
    code = "pyaterochka"


@register("dixy")
class DixyConnector(HistoryConnector):
    """Дикси. dixy.ru отвечает капчей, публичного каталога нет — живём на чеках."""
    code = "dixy"
