"""Коннектор ВкусВилла — внутренний API каталога (раздел 5.4).

Как и у Магнита: API не документирован, ответ может быть любым.
Любая ошибка или непонятная схема — тихий переход на data/fallback_prices.csv.
"""
from __future__ import annotations

from typing import Any

from app.connectors.base import HttpCatalogConnector, register


@register("vkusvill")
class VkusvillConnector(HttpCatalogConnector):
    code = "vkusvill"
    api_url = "https://vkusvill.ru/api/catalog/search/"
    site_url = "https://vkusvill.ru"
    fallback_sku_prefix = "vkusvill-"

    def _search_params(self, query: str, limit: int) -> dict[str, Any]:
        return {"q": query, "search": query, "limit": limit, "PAGEN_1": 1}
