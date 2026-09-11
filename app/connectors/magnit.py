"""Коннектор Магнита.

Задокументированного в спецификации эндпоинта `magnit.ru/api/catalog/search` не
существует — он отвечает 404. Зато сайт отдаёт каталог серверной отрисовкой:
страница поиска `/search/?term=...` содержит карточки товаров с ценами, а карточка
товара `/product/<артикул>` открывается по одному артикулу, без слага.
Отсюда и берём данные.

Разметка магазина может смениться в любой момент — это штатная ситуация
(раздел 9 спецификации): если разбор ничего не нашёл, коннектор молча берёт цены
из data/fallback_prices.csv и не роняет расчёт по остальным магазинам.

ВАЖНО ПРО РЕГИОН: цена зависит от выбранного магазина. Без указания кода сайт
отдаёт свой магазин по умолчанию (сейчас Краснодар). Свой код можно подсмотреть
в адресной строке magnit.ru после выбора магазина — параметр shopCode — и
прописать в config.yaml как connectors.magnit_shop_code.
"""
from __future__ import annotations

import html
import logging
import re
from typing import Any

from app import config
from app.connectors.base import (
    HttpCatalogConnector,
    api_disabled,
    note_failure,
    note_success,
    register,
    similarity,
)
from app.connectors.cache import cached_call
from app.models import Candidate, PriceSnapshot

log = logging.getLogger(__name__)

SEARCH_URL = "https://magnit.ru/search/"
PRODUCT_URL = "https://magnit.ru/product/{sku}"

# карточка товара в выдаче поиска
_ARTICLE = re.compile(
    r'<article class="unit-catalog-product-preview".*?'
    r'(?=<article class="unit-catalog-product-preview"|</main>)',
    re.S,
)
_LINK = re.compile(r'<a title="([^"]*)"[^>]*href="/product/(\d+)-([^"?]*)', re.S)
_CARD_PRICE = re.compile(
    r'prices__(?:regular|sale)"[^>]*>.*?<span[^>]*>([\d\s  ,.]+)&#8202;₽', re.S
)
# цена на странице самого товара: основная разметка и запасной вариант из описания
_PAGE_PRICE = re.compile(
    r'product-details-price(?:-container)?__current"[^>]*>.*?([\d\s  ,.]+)&#8202;₽', re.S
)
_META_PRICE = re.compile(r'<meta name="description" content="[^"]*?за ([\d,.]+)₽')
_WEIGHT = re.compile(r"(\d+[.,]?\d*)\s*(г|гр|мл|кг|л)\b", re.I)


def _to_float(raw: str) -> float | None:
    cleaned = re.sub(r"[^\d,.]", "", raw or "").replace(",", ".")
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def _weight_of(name: str) -> tuple[float | None, str]:
    m = _WEIGHT.search(name or "")
    if not m:
        return None, "pcs"
    value = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    if unit in ("кг", "л"):
        return value * 1000, "pcs"
    return value, "pcs"


@register("magnit")
class MagnitConnector(HttpCatalogConnector):
    code = "magnit"
    api_url = SEARCH_URL
    site_url = "https://magnit.ru"
    fallback_sku_prefix = "magnit-"

    # --- сеть ---
    def _shop_params(self) -> dict[str, Any]:
        shop = config.get("connectors.magnit_shop_code")
        return {"shopCode": shop, "shopType": "dostavka"} if shop else {}

    def _get_html(self, url: str, params: dict[str, Any] | None = None) -> str | None:
        """Страница магазина. None при любой проблеме — это ожидаемый сценарий."""
        if api_disabled(self.code):
            return None
        try:
            import requests
        except ImportError:  # pragma: no cover
            return None
        timeout = float(config.get("connectors.timeout_sec", 10) or 10)
        try:
            resp = requests.get(url, params=params, headers=self._headers(), timeout=timeout)
            if resp.status_code != 200:
                log.warning("%s: %s ответил %s — ухожу в fallback", self.code, url, resp.status_code)
                note_failure(self.code)
                return None
            note_success(self.code)
            return resp.text
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: запрос к %s не удался (%s) — ухожу в fallback", self.code, url, exc)
            note_failure(self.code)
            return None

    # --- разбор ---
    def _cards(self, page: str) -> list[Candidate]:
        out: list[Candidate] = []
        for block in _ARTICLE.finditer(page or ""):
            body = block.group(0)
            link = _LINK.search(body)
            if not link:
                continue
            name = html.unescape(link.group(1)).strip()
            price_match = _CARD_PRICE.search(body)
            weight_g, unit = _weight_of(name)
            out.append(Candidate(
                store_code=self.code,
                sku=link.group(2),
                name=name,
                price=_to_float(price_match.group(1)) if price_match else None,
                weight_g=weight_g,
                unit=unit,
                url=f"{self.site_url}/product/{link.group(2)}-{link.group(3)}",
            ))
        return out

    # --- контракт ---
    def _search(self, query: str, limit: int) -> list[Candidate]:
        params = {"term": query, **self._shop_params()}
        page = cached_call(self.code, f"search:{query}:{limit}",
                           lambda: self._get_html(SEARCH_URL, params))
        found = self._cards(page) if page else []
        if not found:
            log.warning("%s: по «%s» ничего не разобрано — беру data/fallback_prices.csv",
                        self.code, query)
            return self._fallback_search(query, limit)
        for candidate in found:
            candidate.score = similarity(query, candidate.name)
        found.sort(key=lambda c: c.score, reverse=True)
        return found[:limit]

    def _get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        out: list[PriceSnapshot] = []
        missing: list[str] = []
        for sku in skus:
            if sku.startswith(self.fallback_sku_prefix):  # синтетический артикул из CSV
                missing.append(sku)
                continue
            page = cached_call(self.code, f"product:{sku}",
                               lambda sku=sku: self._get_html(PRODUCT_URL.format(sku=sku),
                                                              self._shop_params()))
            if not page:
                missing.append(sku)
                continue
            match = _PAGE_PRICE.search(page) or _META_PRICE.search(page)
            price = _to_float(match.group(1)) if match else None
            if price is None:
                missing.append(sku)
                continue
            title = re.search(r"<title>(.*?)(?:\s*–|</title>)", page, re.S)
            out.append(PriceSnapshot(
                store_code=self.code,
                sku=sku,
                price=price,
                in_stock=True,
                name=html.unescape(title.group(1)).strip() if title else None,
            ))
        if missing:
            out.extend(self._fallback_prices(missing))
        return out
