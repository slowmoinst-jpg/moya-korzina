"""Коннектор Магнита — внутренний API каталога magnit.ru/api/catalog/search (раздел 5.4).

API не документирован, авторизации не требует, но может ответить 403/каптчей/новой схемой.
Это штатная ситуация (раздел 9): при любом сбое коннектор молча берёт цены
из data/fallback_prices.csv, не роняя расчёт по остальным магазинам.
"""
from __future__ import annotations

from typing import Any

from app.connectors.base import HttpCatalogConnector, register


@register("magnit")
class MagnitConnector(HttpCatalogConnector):
    code = "magnit"
    api_url = "https://magnit.ru/api/catalog/search"
    site_url = "https://magnit.ru"
    fallback_sku_prefix = "magnit-"

    def _search_params(self, query: str, limit: int) -> dict[str, Any]:
        return {"term": query, "q": query, "limit": limit, "pageSize": limit, "storeType": "1"}
