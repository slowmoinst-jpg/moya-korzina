"""Единый интерфейс коннектора цен + реестр магазинов (разделы 3.1 и 5.4 спецификации).

Правило раздела 9: внутренние API магазинов не документированы и могут отвалиться,
падение одного коннектора не должно блокировать расчёт. Поэтому публичные search()/get_prices()
никогда не выбрасывают наружу: ловят всё, пишут logging.warning и отдают fallback либо [].
"""
from __future__ import annotations

import difflib
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from app import config
from app.connectors.cache import cached_call
from app.models import Candidate, PriceSnapshot

log = logging.getLogger(__name__)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_JUNK = re.compile(r"[^0-9a-zа-я]+")


class ConnectorError(Exception):
    """Ошибка слоя коннекторов (неизвестный магазин и т.п.)."""


# ---------- похожесть названий ----------
def normalize(text: str | None) -> str:
    """«ВК/ПОЛ.Сыр СТРАЧАТЕЛЛА мяг.200г» -> «вк пол сыр страчателла мяг 200г»."""
    return _JUNK.sub(" ", (text or "").lower().replace("ё", "е")).strip()


def similarity(query: str, name: str) -> float:
    """Грубая похожесть 0..1: SequenceMatcher по нормализованным строкам + бонус за вхождение."""
    q, n = normalize(query), normalize(name)
    if not q or not n:
        return 0.0
    ratio = difflib.SequenceMatcher(None, q, n).ratio()
    if q in n or n in q:
        ratio = max(ratio, 0.9)
    return round(min(1.0, ratio), 3)


# ---------- абстракция ----------
class Connector(ABC):
    """Контракт: code, search(query, limit) -> [Candidate], get_prices(skus) -> [PriceSnapshot]."""

    code: str = ""

    def __init__(self, code: str | None = None) -> None:
        if code:
            self.code = code

    # --- публичные методы: наружу исключений не выпускают ---
    def search(self, query: str, limit: int = 3) -> list[Candidate]:
        try:
            return self._search(query, limit)[:limit]
        except Exception as exc:
            log.warning("%s: search(%r) упал (%s) — пробую fallback", self.code, query, exc)
            try:
                return self._fallback_search(query, limit)
            except Exception as exc2:
                log.warning("%s: fallback тоже не сработал (%s)", self.code, exc2)
                return []

    def get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        if not skus:
            return []
        try:
            return self._get_prices(list(skus))
        except Exception as exc:
            log.warning("%s: get_prices упал (%s) — пробую fallback", self.code, exc)
            try:
                return self._fallback_prices(list(skus))
            except Exception as exc2:
                log.warning("%s: fallback тоже не сработал (%s)", self.code, exc2)
                return []

    # --- реализуют наследники ---
    @abstractmethod
    def _search(self, query: str, limit: int) -> list[Candidate]:
        ...

    @abstractmethod
    def _get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        ...

    # --- общий резерв: CSV data/fallback_prices.csv ---
    def _fallback_search(self, query: str, limit: int) -> list[Candidate]:
        from app.connectors.stub import fallback_prices, fallback_search  # noqa: F401  (ленивый импорт)
        return fallback_search(self.code, query, limit)

    def _fallback_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        from app.connectors.stub import fallback_prices
        return fallback_prices(self.code, skus)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} code={self.code!r}>"


# ---------- предохранитель: не ждём таймаут на каждом SKU, если API магазина мёртв ----------
_FAILS: dict[str, int] = {}
FAIL_LIMIT_DEFAULT = 3


def _fail_limit() -> int:
    return int(config.get("connectors.failure_threshold", FAIL_LIMIT_DEFAULT) or FAIL_LIMIT_DEFAULT)


def api_disabled(store_code: str) -> bool:
    """API магазина отключён на сеанс после серии подряд идущих неудач."""
    return _FAILS.get(store_code, 0) >= _fail_limit()


def note_failure(store_code: str) -> None:
    _FAILS[store_code] = _FAILS.get(store_code, 0) + 1
    if _FAILS[store_code] == _fail_limit():
        log.warning("%s: API не ответил %d раза подряд — до конца сеанса беру data/fallback_prices.csv",
                    store_code, _fail_limit())


def note_success(store_code: str) -> None:
    _FAILS.pop(store_code, None)


def reset_failures() -> None:
    _FAILS.clear()


# ---------- коннектор поверх недокументированного HTTP-API магазина ----------
class HttpCatalogConnector(Connector):
    """Общая механика Магнита и ВкусВилла: GET к внутреннему API, кэш, троттлинг, fallback на CSV.

    API недокументированы (раздел 9): любой сбой, 403, каптча или непонятный JSON —
    не ошибка, а штатный повод взять цены из data/fallback_prices.csv.
    """

    api_url: str = ""
    site_url: str = ""
    fallback_sku_prefix: str = ""          # синтетические SKU из CSV, их в API искать бессмысленно

    # --- переопределяют наследники ---
    def _search_params(self, query: str, limit: int) -> dict[str, Any]:
        return {"term": query, "limit": limit}

    def _item_to_candidate(self, item: dict) -> Candidate | None:
        name = _pick(item, ("name", "title", "productName", "goodsName", "product_name"))
        if not name:
            return None
        price = _pick_price(item)
        return Candidate(
            store_code=self.code,
            sku=str(_pick(item, ("id", "sku", "code", "productId", "article", "vendorCode")) or name),
            name=str(name),
            price=price,
            weight_g=_pick_weight(item),
            unit="kg" if _pick(item, ("isWeight", "is_weight")) else "pcs",
            url=_build_url(self.site_url, _pick(item, ("url", "link", "seoUrl"))),
            ean=_str_or_none(_pick(item, ("ean", "barcode", "gtin"))),
        )

    # --- сеть ---
    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": self.site_url or "https://ya.ru/",
        }

    def _api_get(self, params: dict[str, Any]) -> Any | None:
        """Один запрос к API магазина. None при любой проблеме — это ожидаемый сценарий."""
        if api_disabled(self.code):
            return None
        try:
            import requests
        except ImportError:                                    # pragma: no cover
            return None
        timeout = float(config.get("connectors.timeout_sec", 10) or 10)
        try:
            resp = requests.get(self.api_url, params=params, headers=self._headers(), timeout=timeout)
            if resp.status_code != 200:
                log.warning("%s: %s ответил %s — ухожу в fallback", self.code, self.api_url, resp.status_code)
                note_failure(self.code)
                return None
            note_success(self.code)
            return resp.json()
        except Exception as exc:
            log.warning("%s: запрос к %s не удался (%s) — ухожу в fallback", self.code, self.api_url, exc)
            note_failure(self.code)
            return None

    # --- контракт ---
    def _search(self, query: str, limit: int) -> list[Candidate]:
        payload = cached_call(
            self.code,
            f"search:{normalize(query)}:{limit}",
            lambda: self._api_get(self._search_params(query, limit)),
        )
        candidates: list[Candidate] = []
        for item in _extract_items(payload):
            try:
                cand = self._item_to_candidate(item)
            except Exception:                                  # непонятная структура записи — пропускаем
                cand = None
            if cand:
                candidates.append(cand)
        if not candidates:
            log.warning("%s: API не дал кандидатов по «%s» — беру data/fallback_prices.csv", self.code, query)
            return self._fallback_search(query, limit)
        for cand in candidates:
            cand.score = similarity(query, cand.name)
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:limit]

    def _get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        """Синтетические SKU (magnit-…, vkusvill-…) берём из CSV, остальные пробуем в API."""
        live = [s for s in skus if self.fallback_sku_prefix and not str(s).startswith(self.fallback_sku_prefix)]
        snapshots: list[PriceSnapshot] = []
        for sku in live:
            payload = cached_call(
                self.code, f"sku:{sku}", lambda sku=sku: self._api_get(self._search_params(str(sku), 1))
            )
            for item in _extract_items(payload):
                cand = self._item_to_candidate(item)
                if cand and str(cand.sku) == str(sku) and cand.price:
                    snapshots.append(PriceSnapshot(
                        store_code=self.code, sku=str(sku), price=float(cand.price),
                        price_per_kg=float(cand.price) if cand.unit == "kg" else None,
                        in_stock=True, name=cand.name,
                    ))
                    break
        got = {s.sku for s in snapshots}
        missing = [s for s in skus if s not in got]
        if missing:
            snapshots.extend(self._fallback_prices(missing))
        return snapshots


# ---------- разбор непонятного JSON ----------
_NAME_KEYS = ("name", "title", "productName", "goodsName", "product_name")


def _extract_items(payload: Any, depth: int = 0) -> list[dict]:
    """Ищет в произвольном JSON первый список словарей, похожих на товары."""
    if payload is None or depth > 6:
        return []
    if isinstance(payload, list):
        items = [x for x in payload if isinstance(x, dict) and any(k in x for k in _NAME_KEYS)]
        if items:
            return items
        for x in payload:
            found = _extract_items(x, depth + 1)
            if found:
                return found
        return []
    if isinstance(payload, dict):
        for key in ("items", "products", "goods", "results", "data", "payload", "content", "hits"):
            if key in payload:
                found = _extract_items(payload[key], depth + 1)
                if found:
                    return found
        for value in payload.values():
            if isinstance(value, (list, dict)):
                found = _extract_items(value, depth + 1)
                if found:
                    return found
    return []


def _pick(item: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if item.get(key) not in (None, ""):
            return item[key]
    return None


def _pick_price(item: dict) -> float | None:
    raw = _pick(item, ("price", "currentPrice", "priceRegular", "minPrice", "salePrice", "price_value"))
    if isinstance(raw, dict):
        raw = _pick(raw, ("value", "current", "amount", "price"))
    try:
        price = float(str(raw).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return None
    if price > 100000:                       # копейки во внутренних API — обычное дело
        price /= 100.0
    return round(price, 2) if price > 0 else None


def _pick_weight(item: dict) -> float | None:
    raw = _pick(item, ("weight", "weight_g", "netWeight", "grams"))
    try:
        return float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _build_url(site: str, path: Any) -> str | None:
    if not path:
        return None
    path = str(path)
    return path if path.startswith("http") else f"{site.rstrip('/')}/{path.lstrip('/')}"


# ---------- реестр ----------
_REGISTRY: dict[str, type[Connector]] = {}


def register(*codes: str):
    """Декоратор: @register('magnit') над классом коннектора."""
    def wrap(cls: type[Connector]) -> type[Connector]:
        for code in codes or (cls.code,):
            _REGISTRY[code] = cls
        return cls
    return wrap


def _ensure_loaded() -> None:
    if not _REGISTRY:
        from app.connectors import history, lenta, magnit, stub, vkusvill  # noqa: F401  (регистрация при импорте)


def available_codes() -> list[str]:
    _ensure_loaded()
    return sorted(_REGISTRY)


def get_connector(store_code: str) -> Connector:
    """'magnit' | 'vkusvill' | 'lenta' | 'pyaterochka' | 'dixy' | 'stub' -> экземпляр коннектора."""
    _ensure_loaded()
    code = (store_code or "").strip().lower()
    cls = _REGISTRY.get(code)
    if cls is None:
        raise ConnectorError(f"нет коннектора для магазина {store_code!r}; известны: {available_codes()}")
    return cls(code)
