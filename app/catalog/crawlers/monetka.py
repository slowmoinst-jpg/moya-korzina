"""Монетка: каталог из карты сайта. Без цен — карточка требует входа.

ПОЧЕМУ ЭТА СЕТЬ ВООБЩЕ ПОЯВИЛАСЬ В СПИСКЕ. Приложение «Монетка – доставка
продуктов» выпущено **ООО «Лента»**, и это навело на мысль, что её товары можно
спросить у уже работающего MCP Ленты: у него в каждом вызове есть параметр
`retailBrand`, и среди объявленных значений стоит `mntk`. Проверено 19.09.2026 —
**параметр принимается, но не действует**: поиск «молоко» с `retailBrand` lo, utk,
mntk, smy и remi отдаёт один и тот же ответ, те же десять позиций и те же цены.
Дорога закрыта, и эта запись нужна, чтобы её не открывали заново.

ЧТО РАБОТАЕТ. Собственная карта сайта сети, объявленная в её же `robots.txt`:

    monetka.ru/robots.txt → Sitemap: monetka.ru/sitemap/sitemap_index.xml
    → sitemap_item_1.xml   200, 1,5 МБ, 7 576 адресов товаров

ЧЕГО НЕТ. Карточка товара отвечает **401**: без входа цену сеть не показывает.
Поэтому `price` и `in_stock` остаются пустыми, и расчёт честно видит, что цены у
этой сети нет.

АДРЕС ТОВАРА несёт имя и артикул, категории в нём нет:

    /product/sok-dobryjj-yabloko-1l-rossiya-810000639/
              название ^^^^^^^^^^^^^^^^^^^^^  артикул ^^^^^^^^^

Имя — латиницей и со своей транслитерацией («dobryjj» — это «Добрый»), обратно в
кириллицу не переводим по той же причине, что у Перекрёстка: обратный перевод
неоднозначен, а сопоставитель приводит кириллицу к латинице сам.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator

from app import config
from app.catalog.crawlers import Pace, sitemap_locs
from app.catalog.model import ChainProduct, Crawler, Progress

log = logging.getLogger(__name__)

SITEMAP = "https://monetka.ru/sitemap/sitemap_index.xml"
# В оглавлении рядом лежат разделы, фильтры и бренды — товары только в item.
PRODUCT_MAPS = re.compile(r"sitemap_item")
# /product/<название>-<артикул>; артикул девятизначный.
ADDRESS = re.compile(r"/product/(.+)-(\d{6,})/?$")


def parse(url: str) -> ChainProduct | None:
    """Товар из адреса. None — адрес не товарный."""
    if "/product/" not in url:
        return None
    match = ADDRESS.search(url)
    if not match:
        return None
    name = match.group(1).replace("-", " ").strip()
    return ChainProduct(sku=match.group(2), name=name, url=url) if name else None


class MonetkaCrawler(Crawler):
    code = "monetka"
    name = "Монетка"

    def __init__(self) -> None:
        self.pace = Pace()
        self.cap = int(config.get("catalog.monetka.max_products") or 0)

    def crawl(self, progress: Progress | None = None) -> Iterator[ChainProduct]:
        say = progress or (lambda msg: None)
        say("карта сайта: беру только часть с товарами")
        seen: set[str] = set()
        skipped = 0
        for url in sitemap_locs(SITEMAP, self.pace, PRODUCT_MAPS):
            product = parse(url)
            if not product:
                skipped += 1
                continue
            if product.sku in seen:
                continue
            seen.add(product.sku)
            yield product
            if self.cap and len(seen) >= self.cap:
                say(f"потолок в {self.cap} товаров — дальше не иду")
                return
        say(f"всего товаров {len(seen)}, нетоварных адресов пропущено {skipped}")
