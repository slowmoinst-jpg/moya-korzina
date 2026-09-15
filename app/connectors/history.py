"""Цены для магазинов, куда нельзя постучаться: свои чеки плюс прайс руками.

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

Второй источник — прайс-лист, который человек приносит сам: data/prices_<код>.csv,
см. app/pricelist.py. Он шире чеков, потому что покрывает и то, чего человек ещё
не покупал, и обычно свежее. Поэтому при совпадении названий прайс главнее чека.

Артикулы разведены по пространствам: «hist-<id эталона>» — из чеков, всё
остальное — из прайса. Перепутать нельзя.
"""
from __future__ import annotations

import logging

from app import pricelist, repo
from app.connectors.base import Connector, register, similarity
from app.models import Candidate, PriceSnapshot

log = logging.getLogger(__name__)

PREFIX = "hist-"

# магазины, которые живут на прайсе и чеках, — интерфейс предлагает им загрузку прайса
MANUAL_STORES = ("pyaterochka", "samokat", "dixy")


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
    """Магазин без доступного каталога: цены из прайса человека и из его чеков."""

    fallback_sku_prefix = PREFIX

    def _price_rows(self) -> dict[str, dict]:
        """Прайс магазина, разложенный по артикулам."""
        return {row["sku"]: row for row in pricelist.load(self.code)}

    def _candidates(self) -> list[Candidate]:
        found = []
        for row in self._price_rows().values():
            found.append(Candidate(
                store_code=self.code,
                sku=row["sku"],
                name=row["name"],
                price=row["price"],
                unit=row["unit"],
            ))
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
            log.info("%s: ни прайса, ни чеков — беру data/fallback_prices.csv", self.code)
            return self._fallback_search(query, limit)
        for candidate in found:
            candidate.score = similarity(query, candidate.name)
        found.sort(key=lambda c: c.score, reverse=True)
        return [c for c in found if c.score > 0.3][:limit] or self._fallback_search(query, limit)

    def _get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        known = last_prices(self.code)
        prices = self._price_rows()
        listed_at = pricelist.updated_at(self.code)
        out: list[PriceSnapshot] = []
        missing: list[str] = []
        for sku in skus:
            listed = prices.get(str(sku))
            if listed:
                out.append(PriceSnapshot(
                    store_code=self.code,
                    sku=str(sku),
                    price=listed["price"],
                    price_per_kg=listed["price"] if listed["unit"] == "kg" else None,
                    in_stock=True,       # прайс говорит про цену, про наличие он молчит
                    fetched_at=listed_at,
                    name=listed["name"],
                ))
                continue
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
    """Пятёрочка. 5ka.ru закрыт наглухо, MCP нет — живём на прайсе и чеках."""
    code = "pyaterochka"


@register("samokat")
class SamokatConnector(HistoryConnector):
    """Самокат. Каталога у нас нет, зато письмо «Чек на ваш заказ» есть у каждого.

    В apple-app-site-association у них объявлен путь /cart/sharing/* — то есть
    механизм «поделиться корзиной» существует. Ссылку выдаёт их собственное
    приложение, снаружи такую не собрать, так что пока это задел на будущее, а
    не рабочий путь.
    """
    code = "samokat"

