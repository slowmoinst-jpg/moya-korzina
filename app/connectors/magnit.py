"""Коннектор Магнита.

Задокументированного в спецификации эндпоинта `magnit.ru/api/catalog/search` не
существует — он отвечает 404. Зато сайт отдаёт каталог серверной отрисовкой:
страница поиска `/search/?term=...` содержит карточки товаров с ценами, а карточка
товара `/product/<артикул>` открывается по одному артикулу, без слага.
Отсюда и берём данные.

Разметка магазина может смениться в любой момент — это штатная ситуация
(раздел 9 спецификации). Тогда порядок такой: сначала пробуем прочесть страницу
разбором, потом, если включён, запасным разбором языковой моделью
(app/connectors/smart_extract.py), и только в последнюю очередь берём цену из
data/fallback_prices.csv. Расчёт по остальным магазинам при этом не страдает.

ВАЖНО ПРО РЕГИОН. Проверено запросами 12.09.2026: магазин выбирается НЕ параметром
в адресе, а КУКАМИ shopCode / x_shop_type, а способ получения — кукой nmg_dt.
Тот же адрес с разными shopCode в query отдаёт одну и ту же цену; с разными куками —
разные. Из одного и того же адреса: коктейль «Чудо» 960 г — 175,99 ₽ в Краснодаре и
169 ₽ в Зеленограде; молоко «Кубанский молочник» — 89 ₽ при доставке и 89,99 ₽ при
самовывозе. А молока «Кубанский молочник» в московском магазине нет вовсе: страница
отвечает 404.

Отсюда два правила ниже. Первое: куки, а не параметры. Второе: 404 — это НЕ поломка
разбора, а ответ «такого товара в этом магазине не продают»; подставлять на его место
цену из data/fallback_prices.csv нельзя — человек соберёт корзину, которую не сможет
заказать. Такой товар возвращается с in_stock=False, и оптимизатор его в этот магазин
не кладёт.

Свой код магазина можно подсмотреть в адресе magnit.ru после выбора магазина
(параметр shopCode) и прописать в config.yaml как connectors.magnit_shop_code.
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
from app.connectors import smart_extract
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
# разметка Schema.org на странице товара: самый устойчивый источник цены, меняется реже вёрстки
_LD_PRICE = re.compile(r'"offers"\s*:\s*\{[^}]*?"price"\s*:\s*([\d.]+)')
_WEIGHT = re.compile(r"(\d+[.,]?\d*)\s*(г|гр|мл|кг|л)\b", re.I)


def _unpack(value: Any) -> tuple[str | None, int]:
    """Приводит значение из кэша к паре (страница, код ответа).

    В кэше могут лежать записи прежней версии коннектора, когда `_get_html` возвращал
    одну строку. Распаковывать такую запись как пару нельзя — расчёт свалится в резервный
    CSV на ровном месте, и понять почему будет непросто.
    """
    if isinstance(value, (list, tuple)) and len(value) == 2:
        page, status = value
        return (page if isinstance(page, str) else None), int(status or 0)
    if isinstance(value, str):
        return value, 200
    return None, 0


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
        """Параметры адреса. Сервер их не слушает, но с ними ссылка открывается как на сайте."""
        shop = config.get("connectors.magnit_shop_code")
        return {"shopCode": shop, "shopType": "dostavka"} if shop else {}

    def _shop_cookies(self) -> dict[str, str]:
        """Куки, которыми сайт на самом деле выбирает магазин и способ получения."""
        shop = config.get("connectors.magnit_shop_code")
        if not shop:
            return {}
        delivery = config.get("connectors.magnit_delivery", True)
        return {
            "shopCode": f'"{shop}"',
            "x_shop_type": str(config.get("connectors.magnit_shop_type", "ME") or "ME"),
            "nmg_dt": "DELIVERY_TYPE_DELIVERY" if delivery else "DELIVERY_TYPE_PICKUP",
        }

    def _get_html(self, url: str, params: dict[str, Any] | None = None) -> tuple[str | None, int]:
        """Страница магазина и код ответа.

        Код нужен вызывающему: 404 — это осмысленный ответ «в этом магазине такого товара
        нет», и путать его с обрывом связи нельзя. Поэтому 404 не считается неудачей
        коннектора и предохранитель на него не реагирует.
        """
        if api_disabled(self.code):
            return None, 0
        try:
            import requests
        except ImportError:  # pragma: no cover
            return None, 0
        timeout = float(config.get("connectors.timeout_sec", 10) or 10)
        try:
            resp = requests.get(url, params=params, headers=self._headers(),
                                cookies=self._shop_cookies(), timeout=timeout)
            if resp.status_code == 404:
                note_success(self.code)          # магазин ответил, просто товара у него нет
                return None, 404
            if resp.status_code != 200:
                log.warning("%s: %s ответил %s — ухожу в fallback", self.code, url, resp.status_code)
                note_failure(self.code)
                return None, resp.status_code
            note_success(self.code)
            return resp.text, 200
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: запрос к %s не удался (%s) — ухожу в fallback", self.code, url, exc)
            note_failure(self.code)
            return None, 0

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
        page, _ = _unpack(cached_call(self.code, f"search:{query}:{limit}",
                                      lambda: self._get_html(SEARCH_URL, params)))
        found = self._cards(page) if page else []
        if not found:
            log.warning("%s: по «%s» ничего не разобрано — беру data/fallback_prices.csv",
                        self.code, query)
            return self._fallback_search(query, limit)
        for candidate in found:
            candidate.score = similarity(query, candidate.name)
        found.sort(key=lambda c: c.score, reverse=True)
        return found[:limit]

    def _out_of_stock(self, skus: list[str]) -> list[PriceSnapshot]:
        """Товары, которых в выбранном магазине нет.

        Цену берём справочную, чтобы экран мог показать порядок величины, но помечаем
        отсутствие: оптимизатор такую позицию в этот магазин не положит.
        """
        known = {snap.sku: snap.price for snap in self._fallback_prices(list(skus))}
        return [PriceSnapshot(store_code=self.code, sku=sku,
                              price=known.get(sku, 0.0), in_stock=False) for sku in skus]

    def _get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        out: list[PriceSnapshot] = []
        missing: list[str] = []        # не дозвонились или не разобрали — берём справочник
        absent: list[str] = []         # магазин ответил «нет такого товара»
        for sku in skus:
            if sku.startswith(self.fallback_sku_prefix):  # синтетический артикул из CSV
                missing.append(sku)
                continue
            page, status = _unpack(cached_call(
                self.code, f"product:{sku}",
                lambda sku=sku: self._get_html(PRODUCT_URL.format(sku=sku), self._shop_params())))
            if status == 404:
                log.info("%s: %s не продаётся в выбранном магазине", self.code, sku)
                absent.append(sku)
                continue
            if not page:
                missing.append(sku)
                continue
            match = _LD_PRICE.search(page) or _PAGE_PRICE.search(page) or _META_PRICE.search(page)
            price = _to_float(match.group(1)) if match else None
            in_stock = True
            if price is None:
                # страницу мы получили, а прочесть не смогли: похоже, сменилась вёрстка.
                # прежде чем подсунуть справочную цену, спросим модель — если она включена
                guess = smart_extract.price_from_html(self.code, page, f"артикул {sku}")
                if not guess:
                    missing.append(sku)
                    continue
                price, in_stock = guess["price"], guess["in_stock"]
            title = re.search(r"<title>(.*?)(?:\s*–|</title>)", page, re.S)
            out.append(PriceSnapshot(
                store_code=self.code,
                sku=sku,
                price=price,
                in_stock=in_stock,
                name=html.unescape(title.group(1)).strip() if title else None,
            ))
        if absent:
            out.extend(self._out_of_stock(absent))
        if missing:
            out.extend(self._fallback_prices(missing))
        return out
