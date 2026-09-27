"""Сборщик каталога METRO: страницами по точке, со штрихкодами и ценами сразу.

ЧЕМ ОН ОТЛИЧАЕТСЯ ОТ ОСТАЛЬНЫХ СБОРЩИКОВ. Ленте и ВкусВиллу перечень берут из
sitemap, а подробности спрашивают по одной карточке — потому что иначе никак. У
METRO перечень и подробности приходят ОДНИМ ответом: страница в пятьдесят товаров
несёт и название, и штрихкоды, и цену по точке, и остаток. Поэтому здесь нет
ступени `seen_only` и нет потолка `details_per_run`: обход либо проходит целиком,
либо не проходит вовсе.

СКОЛЬКО ЭТО СТОИТ. Замер 19.09.2026, петербургская точка 15: 32 552 товара,
652 страницы. При общем для каталога темпе в один запрос в секунду — около
одиннадцати минут на точку, столько же, сколько обход одного магазина Магнита.

ПОЧЕМУ ТОЧЕК МОЖЕТ БЫТЬ НЕСКОЛЬКО. Цена у METRO своя в каждом городе (один и тот
же артикул 117189 стоит 70,91 ₽ в Петербурге и 66,90 ₽ в другом городе), а остаток
свой в каждом центре даже внутри города. Значит обходить надо каждую точку, к
которой подобран чей-то адрес, ровно как у Магнита.

ПРО ТОКЕН — см. шапку `app/connectors/metro.py`. Сборщик собирает адрес тем же
`_url`, поэтому настройка `connectors.metro_api_token` действует и здесь.
"""
from __future__ import annotations

import logging
from typing import Any, Iterator

from app import config
from app.catalog.crawlers import Pace, http_get
from app.catalog.model import ChainProduct, CrawlBlocked, Crawler, Progress
from app.connectors.metro import PAGE, _page_info, _rows, _url

log = logging.getLogger(__name__)


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _weight_g(row: dict) -> float | None:
    """Фасовка в граммах или миллилитрах, если сеть назвала её числом.

    Не назвала — возвращаем None, и сопоставление достанет фасовку из названия само
    (app/catalog/match.py). Выдумывать её здесь нельзя: ошибка в фасовке склеит
    литровое молоко с трёхлитровым.
    """
    packing = row.get("packing")
    if not isinstance(packing, dict):
        return None
    for key in ("weight", "volume", "netto", "value"):
        value = _num(packing.get(key))
        if value:
            unit = str(packing.get("unit") or packing.get("measure") or "").lower()
            if unit in ("kg", "кг", "l", "л"):
                return value * 1000
            return value
    return None


class MetroCrawler(Crawler):
    code = "metro"
    name = "METRO"

    def __init__(self, stores: list[str] | None = None) -> None:
        fallback = config.get("connectors.metro_store_id")
        self.stores = [str(s) for s in (stores or []) if str(s).strip()] or (
            [str(fallback)] if fallback else [])
        self.pace = Pace(chain=self.code)

    # --- разбор одной карточки ---
    def _product(self, row: dict) -> ChainProduct | None:
        article = str(row.get("article") or "").strip()
        name = str(row.get("name") or "").strip()
        if not article or not name:
            return None
        prices = row.get("prices") if isinstance(row.get("prices"), dict) else {}
        stock = row.get("stock") if isinstance(row.get("stock"), dict) else {}
        manufacturer = row.get("manufacturer") if isinstance(row.get("manufacturer"), dict) else {}
        barcodes = [str(b) for b in (row.get("barcodes") or []) if str(b or "").isdigit()]
        left = _num(stock.get("value"))
        slug = str(row.get("slug") or "").strip()
        return ChainProduct(
            sku=article,
            name=name,
            brand=str(manufacturer.get("name") or "") or None,
            weight_g=_weight_g(row),
            category=str(row.get("category_id") or "") or None,
            url=f"https://online.metro-cc.ru/products/{slug}" if slug else None,
            barcode=barcodes[0] if barcodes else None,
            price=_num(prices.get("price")),
            in_stock=None if left is None else left > 0,
        )

    # --- обход ---
    def _crawl_store(self, store: str, seen: set[str], say: Progress) -> Iterator[ChainProduct]:
        page, last = 1, None
        here: set[str] = set()
        while True:
            response = http_get(_url(f"{store}/products"), self.pace,
                                params={"page": page}, accept="application/json")
            if "json" not in (response.headers.get("Content-Type") or ""):
                raise CrawlBlocked("список товаров пришёл не JSON — похоже на страницу защиты")
            payload = response.json()
            rows = _rows(payload)
            if last is None:
                info = _page_info(payload)
                last = int(info.get("last_page") or 0) or None
                total = info.get("total")
                say(f"точка {store}: товаров {total}, страниц {last or '?'} по {PAGE}")
            for row in rows:
                product = self._product(row)
                if not product or product.sku in here:
                    continue
                here.add(product.sku)
                product.point = store
                if product.sku not in seen:
                    seen.add(product.sku)
                    yield product
                else:
                    # Артикул у METRO общий, а остаток и цена — свои в каждом центре.
                    yield ChainProduct(sku=product.sku, name=product.name, price=product.price,
                                       in_stock=product.in_stock, point=store, seen_only=True)
            if not rows or (last and page >= last):
                break
            page += 1
            if page % 50 == 0:
                say(f"точка {store}: страница {page} из {last or '?'}")

    def crawl(self, progress: Progress | None = None) -> Iterator[ChainProduct]:
        say = progress or (lambda msg: None)
        if not self.stores:
            raise CrawlBlocked("ни одной точки METRO: адреса людей не разобрались, "
                               "а connectors.metro_store_id не задан")
        say(f"точек к обходу: {len(self.stores)} ({', '.join(self.stores)})")
        # Одно множество на все точки: артикул у METRO общий, а вот остаток свой в
        # каждой. Каталог хранит товар один раз, а цену и остаток — по точкам
        # (chain_prices), поэтому повтор из второй точки несёт только их.
        seen: set[str] = set()
        for store in self.stores:
            yield from self._crawl_store(store, seen, say)
