"""Коннектор Дикси. Каталог — через их поисковый движок, цена — со страницы товара.

Сам dixy.ru встречает незнакомый адрес страницей «Поставь галочку в поле „Я не
робот"». Обходить её мы не будем. Но каталог Дикси живёт не только там: поиск на
их сайте работает на стороннем движке Diginetica, и он отвечает с собственных
хостов, куда защита Дикси не распространяется.

Отсюда разделение труда:

  ПОИСК — autocomplete.diginetica.net. Отдаёт название, бренд, категорию,
  признак available и идентификатор товара — тот самый, что стоит в адресе
  карточки на dixy.ru. Цену Дикси в этот движок не отдаёт вовсе: там всегда
  «0.0», потому что цена у них своя в каждом магазине («Цена в конкретном
  магазине» написано прямо на карточке).

  ЦЕНА — страница товара dixy.ru/catalog/<категория>/<id>/. Разметка разобрана по
  настоящему слепку страницы, он лежит в tests/fixtures/dixy_product.html и на нём
  же проверяется разбор:

      <div class="card-line detail" data-bsk_data="242556 1 1 шт 0" ...>
        <div class="card__price-num">420.<span>90</span><span class='rub'>руб.</span></div>

  Рубли и копейки разнесены по узлам, единица измерения спрятана в data-bsk_data.

ПРО ДОСТУП К ЦЕНАМ. Проверка 13.09.2026 показала, что защита Дикси смотрит на
адрес: с российского подключения страница открывается обычным порядком, а из-под
зарубежного VPN приходит капча. То же самое у Пятёрочки — она прямо пишет
«Проверьте настройки интернета и VPN». Поэтому шаг с ценой надо выполнять оттуда,
где стоит приложение. Если страница не открылась, коннектор не падает: названия и
идентификаторы уже собраны поиском, а цена берётся из прайса и чеков (см.
app/connectors/history.py).
"""
from __future__ import annotations

import html
import logging
import re
from typing import Any

from app import config, pricelist
from app.connectors.base import (
    USER_AGENT,
    api_disabled,
    note_failure,
    note_success,
    register,
    similarity,
)
from app.connectors import smart_extract
from app.connectors.cache import cached_call
from app.connectors.history import HistoryConnector
from app.models import Candidate, PriceSnapshot

log = logging.getLogger(__name__)

SITE_URL = "https://dixy.ru"
SEARCH_URL = "https://autocomplete.diginetica.net/autocomplete"
# Ключ и номер площадки открыто лежат в cdn.diginetica.net/2164/client.js,
# который сайт подключает каждому посетителю. Секретом они не являются.
DEFAULT_API_KEY = "97PGH0KXFA"
DEFAULT_SITE_ID = "2164"

# рубли и копейки разнесены: «420.<span>90</span>»
_PRICE = re.compile(r'card__price-num"[^>]*>\s*([\d\s]+)[.,]?\s*(?:<span[^>]*>(\d{1,2})</span>)?', re.S)
# «242556 1 1 шт 0» — четвёртым идёт единица измерения
_BSK = re.compile(r'data-bsk_data="[^"]*?\s(шт|кг|л|г)\s', re.I)
_NO_PRICE = re.compile(r"(нет в наличии|товар закончил|не продаётся)", re.I)


def _settings() -> dict[str, Any]:
    return {
        "apiKey": str(config.get("connectors.dixy_api_key", DEFAULT_API_KEY) or DEFAULT_API_KEY),
        "sid": str(config.get("connectors.dixy_site_id", DEFAULT_SITE_ID) or DEFAULT_SITE_ID),
    }


def _unit(name: str, page: str = "") -> str:
    match = _BSK.search(page or "")
    if match:
        return "kg" if match.group(1).lower() == "кг" else "pcs"
    return "kg" if re.search(r"\bвесов\w*|/\s*кг\b", name or "", re.I) else "pcs"


def product_url(item: dict) -> str | None:
    """Адрес карточки: категория из поискового движка плюс идентификатор товара.

    Именно так устроены адреса каталога Дикси — проверено по слепкам:
    dixy.ru/catalog/alkogolnye-napitki/belye-vina/2000175281/
    """
    sku = str(item.get("id") or "").strip()
    if not sku:
        return None
    cats = [c for c in (item.get("categories") or []) if c.get("link_url")]
    if not cats:
        return None
    link = str(cats[0]["link_url"]).strip("/")
    return f"{SITE_URL}/{link}/{sku}/"


def parse_price(page: str) -> float | None:
    """Цена со страницы товара. None — если цены на странице нет."""
    if not page or _NO_PRICE.search(page):
        return None
    match = _PRICE.search(page)
    if not match:
        return None
    rubles = re.sub(r"\s", "", match.group(1) or "")
    kopecks = match.group(2) or "0"
    try:
        return round(float(f"{rubles}.{kopecks:0<2.2s}"), 2)
    except ValueError:
        return None


@register("dixy")
class DixyConnector(HistoryConnector):
    """Дикси: каталог из Diginetica, цена со страницы товара, запас — прайс и чеки."""

    code = "dixy"
    site_url = SITE_URL

    # --- сеть ---
    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": f"{SITE_URL}/",
        }

    def _get(self, url: str, params: dict | None = None) -> tuple[Any | None, int]:
        """Ответ и код. Наружу не бросает — это штатный сценарий раздела 9."""
        if api_disabled(self.code):
            return None, 0
        try:
            import requests
        except ImportError:  # pragma: no cover
            return None, 0
        timeout = float(config.get("connectors.timeout_sec", 10) or 10)
        try:
            resp = requests.get(url, params=params, headers=self._headers(), timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: запрос к %s не удался (%s)", self.code, url, exc)
            note_failure(self.code)
            return None, 0
        if resp.status_code != 200:
            log.warning("%s: %s ответил %s", self.code, url, resp.status_code)
            note_failure(self.code)
            return None, resp.status_code
        note_success(self.code)
        if "json" in (resp.headers.get("content-type") or ""):
            try:
                return resp.json(), 200
            except ValueError:
                return None, 200
        return resp.text, 200

    # --- поиск ---
    def _to_candidate(self, item: dict) -> Candidate | None:
        sku = str(item.get("id") or "").strip()
        name = html.unescape(str(item.get("name") or "")).strip()
        if not sku or not name:
            return None
        return Candidate(
            store_code=self.code,
            sku=sku,
            name=name,
            price=None,                     # цену поисковый движок не знает, она в карточке
            unit=_unit(name),
            url=product_url(item),
        )

    def _search(self, query: str, limit: int) -> list[Candidate]:
        answer, _ = cached_call(self.code, f"search:{query}:{limit}", lambda: self._get(
            SEARCH_URL, {"st": query, "num_products": max(limit, 5), "num_suggestions": 0, **_settings()}))
        items = (answer or {}).get("products") if isinstance(answer, dict) else None
        found = [c for c in (self._to_candidate(i) for i in items or []) if c]
        if not found:
            log.info("%s: поиск ничего не дал по «%s» — беру прайс и чеки", self.code, query)
            return super()._search(query, limit)
        for candidate in found:
            candidate.score = similarity(query, candidate.name)
        found.sort(key=lambda c: c.score, reverse=True)
        return found[:limit]

    # --- цены ---
    def _page_price(self, sku: str) -> tuple[float | None, str]:
        """Цена с карточки товара. Пустая страница — не беда, есть чем заменить."""
        url = self._known_url(sku)
        if not url:
            return None, ""
        page, _ = cached_call(self.code, f"product:{sku}", lambda: self._get(url))
        if not isinstance(page, str):
            return None, ""
        price = parse_price(page)
        if price is None:
            # страница есть, а цены в ней не видно — вёрстка могла смениться
            guess = smart_extract.price_from_html(self.code, page, f"артикул {sku}")
            price = guess["price"] if guess else None
        return price, page

    def _known_url(self, sku: str) -> str | None:
        """Адрес карточки, сохранённый матчером при сопоставлении."""
        from app import repo
        from app.db import get_conn

        store = repo.get_store(self.code)
        if not store:
            return None
        with get_conn() as conn:
            row = conn.execute("SELECT url FROM store_products WHERE store_id=? AND sku=?",
                               (store.id, str(sku))).fetchone()
        return (row["url"] if row else None) or None

    def _get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        out: list[PriceSnapshot] = []
        rest: list[str] = []
        listed = {row["sku"] for row in pricelist.load(self.code)}
        for sku in skus:
            if sku in listed or str(sku).startswith(("hist-", self.fallback_sku_prefix)):
                rest.append(sku)             # прайс и чеки разберёт родитель
                continue
            price, page = self._page_price(sku)
            if price is None:
                rest.append(sku)
                continue
            out.append(PriceSnapshot(
                store_code=self.code,
                sku=str(sku),
                price=price,
                price_per_kg=price if _unit("", page) == "kg" else None,
                in_stock=True,
                name=None,
            ))
        if rest:
            out.extend(super()._get_prices(rest))
        return out
