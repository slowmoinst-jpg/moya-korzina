"""Перекрёсток Впрок: каталог из карты сайта. Без цен, как и у самого Перекрёстка.

Проверено 19.09.2026 с боевого сервера, российский адрес:

    sitemap.xml → sku0.xml, sku1.xml     200, одна часть 4,75 МБ, 20 000 адресов
    карточка товара                      503

То есть перечень достижим, цены нет. Сборщик берёт первое и молчит про второе:
`price` и `in_stock` остаются пустыми.

ЧЕМ ВПРОК ОТЛИЧАЕТСЯ ОТ ПЕРЕКРЁСТКА. Это разные витрины одной группы: Перекрёсток
— супермаркеты с экспресс-доставкой, Впрок — онлайн-гипермаркет со своими
дарксторами и своим ассортиментом. Артикулы у них тоже свои, поэтому и каталог
отдельный: одна и та же марка лежит у них под разными номерами, и склеивать их
должно сопоставление по названию и штрихкоду, а не наша догадка.

АДРЕС ТОВАРА устроен чуть иначе, чем у Перекрёстка: артикул отделён ДВУМЯ дефисами,

    /product/linakva-linakva-bebi-0-9-aerozol-150-ml--1284993
                     название ^^^^^^^^^^^^^^^^^^^^^   артикул ^^^^^^^

и категории в адресе нет вовсе. Названия — та же латиница без диакритики, что и у
Перекрёстка, со всеми оговорками из его модуля.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator

from app import config
from app.catalog.crawlers import Pace, sitemap_locs
from app.catalog.model import ChainProduct, Crawler, Progress

log = logging.getLogger(__name__)

SITEMAP = "https://www.vprok.ru/sitemap.xml"
# В индексе рядом лежат info, recipe, catalog, filter* и sku_revi* — товары только в sku<N>.
PRODUCT_MAPS = re.compile(r"/sku\d+\.xml$")
# /product/<название>--<артикул>; дефисов перед артикулом бывает один или два.
ADDRESS = re.compile(r"/product/(.+?)-+(\d+)/?$")


def parse(url: str) -> ChainProduct | None:
    """Товар из адреса. None — адрес не товарный."""
    match = ADDRESS.search(url)
    if not match:
        return None
    slug, article = match.group(1), match.group(2)
    name = slug.replace("-", " ").strip()
    if not name or not article:
        return None
    return ChainProduct(sku=article, name=name, url=url)


class VprokCrawler(Crawler):
    code = "vprok"
    name = "Перекрёсток Впрок"

    def __init__(self) -> None:
        self.pace = Pace()
        self.cap = int(config.get("catalog.vprok.max_products") or 0)

    def crawl(self, progress: Progress | None = None) -> Iterator[ChainProduct]:
        say = progress or (lambda msg: None)
        say("карта сайта: беру только части с товарами")
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
            if len(seen) % 20000 == 0:
                say(f"разобрано {len(seen)} товаров")
        say(f"всего товаров {len(seen)}, нетоварных адресов пропущено {skipped}")
