"""ВкусВилл: перечень — из sitemap, подробности — карточкой через MCP.

Разведка 16.09.2026: поиск MCP требует русских слов (category_id без q не работает,
транслит не понимается), а sitemap отдаёт все 13 609 товаров как
https://vkusvill.ru/goods/<slug>-<xml_id>/ и дерево разделов адресами. Карточка
vkusvill_product_details по id даёт название, бренд, точный вес (в поиске вес
ненадёжен: 1 кг вместо 0,9), единицу, раздел с родителем, адрес, картинку и цену.
Штрихкода нет ни в поиске, ни в карточке (есть только обратный поиск по штрихкоду).

Каталог у ВкусВилла один на всю страну — адрес в поиске не участвует, поэтому
хаб не нужен. Сборщик, как и у Ленты, работает приращением: карточки только для
незнакомых id, не больше catalog.vkusvill.details_per_run за обход.
"""
from __future__ import annotations

import html
import logging
import re
from typing import Iterator

from app import config
from app.catalog.crawlers import Pace, sitemap_locs
from app.catalog.model import ChainProduct, Crawler, Progress
from app.connectors import mcp_client
from app.connectors.vkusvill import MCP_URL, _clean, _price, _unit

log = logging.getLogger(__name__)

SITEMAP = "https://vkusvill.ru/sitemap.xml"
GOODS_MAPS = re.compile(r"sitemap_goods(_\d+)?\.xml")
PRODUCT_URL = re.compile(r"/goods/(?P<slug>[^/]+?)-(?P<id>\d+)/?$")


def _grams(weight: dict | None) -> float | None:
    """{value: 0.9, unit: 'кг'} -> 900; {value: 250, unit: 'г'} -> 250; {value: 1, unit: 'л'} -> 1000."""
    if not isinstance(weight, dict):
        return None
    try:
        value = float(weight.get("value") or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    unit = str(weight.get("unit") or "").lower()
    if unit in ("кг", "л", "kg", "l"):
        return round(value * 1000, 1)
    if unit in ("г", "мл", "g", "ml", "гр"):
        return value
    return None


def to_product(item: dict) -> ChainProduct | None:
    sku = item.get("xml_id") or item.get("id")
    name = html.unescape(_clean(item.get("name")) or "").replace("\xa0", " ").strip()
    if not sku or not name:
        return None
    categories = [c for c in (item.get("category") or []) if isinstance(c, dict) and c.get("name")]
    path = " / ".join(c["name"] for c in reversed(categories)) or None
    images = item.get("images") or []
    image = None
    if images and isinstance(images[0], dict):
        image = images[0].get("medium") or images[0].get("large") or images[0].get("small")
    unit = _unit(item.get("unit"))
    return ChainProduct(
        sku=str(sku), name=name, brand=(item.get("brand") or None),
        weight_g=_grams(item.get("weight")), unit=unit, category=path,
        url=item.get("url") or f"https://vkusvill.ru/goods/{item.get('slug', '')}-{sku}/",
        image=image, price=_price(item.get("price")),
    )


class VkusvillCrawler(Crawler):
    code = "vkusvill"
    name = "ВкусВилл"

    def __init__(self) -> None:
        self.cap = int(config.get("catalog.vkusvill.details_per_run") or 2000)
        self.pace = Pace()

    def universe(self) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for loc in sitemap_locs(SITEMAP, self.pace, pick=GOODS_MAPS):
            m = PRODUCT_URL.search(loc)
            if m and m.group("id") not in seen:
                seen.add(m.group("id"))
                ids.append(m.group("id"))
        return ids

    def details(self, sku: str) -> dict | None:
        self.pace.wait()
        answer = mcp_client.call_tool(MCP_URL, self.code, "vkusvill_product_details", {"id": int(sku)})
        item = mcp_client.ok_payload(answer)
        return item if isinstance(item, dict) and item.get("name") else None

    def crawl(self, progress: Progress | None = None) -> Iterator[ChainProduct]:
        from app.catalog import store

        say = progress or (lambda msg: None)
        ids = self.universe()
        known = store.known_skus(self.code)
        say(f"в sitemap {len(ids)} товаров, известно {len(known & set(ids))}")
        fresh: list[str] = []
        for sku in ids:
            if sku in known:
                yield ChainProduct(sku=sku, name="", seen_only=True)
            else:
                fresh.append(sku)
        taken = 0
        for n, sku in enumerate(fresh[:self.cap], start=1):
            item = self.details(sku)
            product = to_product(item) if item else None
            if product:
                taken += 1
                yield product
            if n % 100 == 0:
                say(f"карточек спрошено {n} из {min(len(fresh), self.cap)}, взято {taken}")
        if len(fresh) > self.cap:
            say(f"незнакомых ещё {len(fresh) - self.cap} — доберём в следующие обходы")
