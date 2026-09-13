"""Коннектор ВкусВилла поверх его собственного MCP-сервера.

ВкусВилл опубликовал MCP и открыл его без ключей: https://mcp001.vkusvill.ru/mcp.
Это не разобранная вёрстка и не подсмотренный внутренний вызов, а контракт, который
сеть сама предлагает использовать, — поэтому здесь нет ни регулярных выражений по
HTML, ни угадывания схемы.

Что берём:
  vkusvill_products_search  — поиск: id, название, цена, единица измерения;
  vkusvill_product_details  — карточка по id: цена, единица, ссылка;
  vkusvill_cart_link_create — ссылка на готовую корзину (см. cart_link ниже).

Чего у ВкусВилла НЕТ: остатков. Наличие сервер не отдаёт ни в поиске, ни в карточке,
поэтому in_stock мы честно оставляем True и не делаем вид, что знаем больше.

Правила использования, объявленные самим ВкусВиллом: не более 20 позиций в одной
ссылке на корзину и обязательная оговорка, что цены, наличие и состав нужно
уточнять на карточках товаров. Оговорку показывает интерфейс, ограничение в 20
позиций соблюдает cart_link.
"""
from __future__ import annotations

import html
import logging

from app.connectors import mcp_client
from app.connectors.base import Connector, register, similarity
from app.models import Candidate, PriceSnapshot

log = logging.getLogger(__name__)

MCP_URL = "https://mcp001.vkusvill.ru/mcp"
SITE_URL = "https://vkusvill.ru"
CART_LIMIT = 20                      # потолок объявлен самим ВкусВиллом
MIN_QTY, MAX_QTY = 0.01, 40.0        # границы количества в одной позиции


def _clean(text: str | None) -> str:
    """«Огурцы "Корнишоны", 300&nbsp;г» -> «Огурцы "Корнишоны", 300 г»."""
    return html.unescape(text or "").replace("\xa0", " ").strip()


def _unit(raw: str | None) -> str:
    """Единица ВкусВилла («кг», «шт») в нашу («kg», «pcs»)."""
    return "kg" if (raw or "").strip().lower().startswith("кг") else "pcs"


def _price(node: dict | None) -> float | None:
    value = (node or {}).get("current")
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def cart_link(items: list[tuple[int, float]]) -> str | None:
    """Ссылка на корзину ВкусВилла по списку (id товара, количество).

    Человек открывает её у себя, подтверждает позиции и оформляет заказ сам — своим
    аккаунтом, своей картой, на свой адрес. Ни к его учётной записи, ни к его данным
    мы при этом не прикасаемся.

    Кэшировать нельзя: каждый вызов создаёт новую ссылку.
    """
    products = []
    for sku, qty in items[:CART_LIMIT]:
        try:
            number = round(float(qty), 2)
        except (TypeError, ValueError):
            continue
        number = min(max(number, MIN_QTY), MAX_QTY)
        products.append({"xml_id": int(sku), "q": number})
    if not products:
        return None
    if len(items) > CART_LIMIT:
        log.info("vkusvill: в ссылку влезает %d позиций из %d — остальные придётся отдать второй ссылкой",
                 CART_LIMIT, len(items))
    answer = mcp_client.call_tool(MCP_URL, "vkusvill", "vkusvill_cart_link_create",
                                  {"products": products})
    data = mcp_client.ok_payload(answer) or {}
    link = data.get("link")
    return link if isinstance(link, str) and link.startswith("http") else None


@register("vkusvill")
class VkusvillConnector(Connector):
    code = "vkusvill"
    site_url = SITE_URL
    fallback_sku_prefix = "vkusvill-"

    # --- разбор ответов сервера ---
    def _to_candidate(self, item: dict) -> Candidate | None:
        sku = item.get("id") or item.get("xml_id")
        name = _clean(item.get("name"))
        if not sku or not name:
            return None
        unit = _unit(item.get("unit"))
        return Candidate(
            store_code=self.code,
            sku=str(sku),
            name=name,
            price=_price(item.get("price")),
            unit=unit,
            url=item.get("url") or f"{SITE_URL}/goods/{item.get('slug', '')}-{sku}/",
        )

    # --- контракт ---
    def _search(self, query: str, limit: int) -> list[Candidate]:
        answer = mcp_client.call_tool(MCP_URL, self.code, "vkusvill_products_search",
                                      {"q": query, "page": 1, "mode": "short"},
                                      cache_key=f"search:{query}")
        data = mcp_client.ok_payload(answer) or {}
        found = [c for c in (self._to_candidate(i) for i in data.get("items") or []) if c]
        if not found:
            log.warning("%s: по «%s» MCP ничего не дал — беру data/fallback_prices.csv", self.code, query)
            return self._fallback_search(query, limit)
        for candidate in found:
            candidate.score = similarity(query, candidate.name)
        found.sort(key=lambda c: c.score, reverse=True)
        return found[:limit]

    def _search_barcode(self, barcode: str) -> Candidate | None:
        """Единственная сеть из наших, у которой поиск по штрихкоду — штатный инструмент.

        Сервер отвечает «Товар по штрих-коду не найден», когда такого у него нет:
        это не ошибка, а честный ответ, и мы его так и трактуем.
        """
        answer = mcp_client.call_tool(MCP_URL, self.code, "vkusvill_product_barcode",
                                      {"barcode": barcode}, cache_key=f"barcode:{barcode}")
        item = mcp_client.ok_payload(answer)
        if not item:
            return None
        candidate = self._to_candidate(item)
        if candidate:
            candidate.ean = barcode
            candidate.score = 1.0        # штрихкод совпал — гадать больше не о чем
        return candidate

    def _get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        out: list[PriceSnapshot] = []
        missing: list[str] = []
        for sku in skus:
            if sku.startswith(self.fallback_sku_prefix) or not str(sku).isdigit():
                missing.append(sku)          # синтетический артикул из CSV — в MCP его нет
                continue
            answer = mcp_client.call_tool(MCP_URL, self.code, "vkusvill_product_details",
                                          {"id": int(sku)}, cache_key=f"product:{sku}")
            item = mcp_client.ok_payload(answer) or {}
            price = _price(item.get("price"))
            if price is None:
                missing.append(sku)
                continue
            per_kg = price if _unit(item.get("unit")) == "kg" else None
            out.append(PriceSnapshot(
                store_code=self.code,
                sku=str(sku),
                price=price,
                price_per_kg=per_kg,
                in_stock=True,               # остатков ВкусВилл не отдаёт — не выдумываем
                name=_clean(item.get("name")) or None,
            ))
        if missing:
            out.extend(self._fallback_prices(missing))
        return out
