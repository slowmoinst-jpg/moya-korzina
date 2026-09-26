"""Лента: перечень — из sitemap, подробности — карточкой через MCP.

Почему так, а не поиском. Сайт lenta.com закрыт Qrator (401 с вызовом на любую
страницу и любой API-путь), а sitemap отдаётся свободно и содержит ВСЕ товары —
80 743 адреса вида https://lenta.com/product/<slug>-<id>/ (разведка 16.09.2026).
Единственный читаемый источник названий и фасовок — MCP: storefront_product_details по
id и коду хаба отдаёт name, package, price, stock, url. Поиск отдаёт 10 позиций за
вызов, но требует слов и не гарантирует покрытия; карточка детерминирована.

Цена вопроса: 80 тысяч карточек по одной в секунду — 22 часа. Поэтому сборщик работает
ПРИРАЩЕНИЕМ: за один обход спрашивает карточки только у незнакомых id, не больше
catalog.lenta.details_per_run (по умолчанию 2000 ≈ 35 минут), а всем известным
продлевает жизнь отметкой seen_only. Полный каталог набирается за несколько недель,
дальше в сутки уходят только новинки.

Хаб: код точки ДОСТАВКИ из storefront_resolve_store — поле suggested.delivery.aliasId
(291 для Москвы, Ходынский бульвар 4). Не путать с физическими магазинами из hubs:
по их кодам витрина молчит (проверено 15.09.2026). Категории у Ленты в карточке нет —
поле остаётся пустым, sitemap категорий с товарами не связан.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator

from app import config
from app.catalog.crawlers import Pace, sitemap_locs
from app.catalog.model import ChainProduct, Crawler, Progress
from app.connectors import mcp_client
from app.connectors.lenta import MCP_URL, _number, _unit
from app.matcher.normalize import parse_weight

log = logging.getLogger(__name__)

SITEMAP = "https://lenta.com/sitemap/sitemap_index.xml"
ITEM_MAPS = re.compile(r"sitemap_item_\d+\.xml")
PRODUCT_URL = re.compile(r"/product/(?P<slug>[^/]+?)-(?P<id>\d+)/?$")
DEFAULT_STORE_ID = 291


def to_product(item: dict) -> ChainProduct | None:
    sku, name = item.get("id"), (item.get("name") or "").strip()
    if not sku or not name:
        return None
    package = (item.get("package") or "").strip()
    weight, _ = parse_weight(package or name)
    price = _number(item.get("price"))
    stock = _number(item.get("stock"))
    return ChainProduct(
        sku=str(sku), name=name, weight_g=weight or None, unit=_unit(name),
        url=item.get("url") or f"https://lenta.com/product/{item.get('slug', '')}-{sku}/",
        price=price if price and price > 0 else None,
        in_stock=(stock > 0) if stock is not None else None,
    )


class LentaCrawler(Crawler):
    code = "lenta"
    name = "Лента"

    def __init__(self, hub: int | str | None = None) -> None:
        # Хаб приходит от адреса человека (app/places.py). Перечень товаров у Ленты
        # общий на страну — он берётся из sitemap, — и от точки зависят только
        # карточки: цена, остаток, фасовка. Поэтому хаб здесь ОДИН, а не список:
        # второй хаб не добавил бы каталогу ни строки, зато удвоил бы запросы.
        self.store_id = int(hub or config.get("catalog.lenta.store_id") or DEFAULT_STORE_ID)
        self.cap = int(config.get("catalog.lenta.details_per_run") or 2000)
        self.pace = Pace(chain=self.code)

    def universe(self) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for loc in sitemap_locs(SITEMAP, self.pace, pick=ITEM_MAPS):
            m = PRODUCT_URL.search(loc)
            if m and m.group("id") not in seen:
                seen.add(m.group("id"))
                ids.append(m.group("id"))
        return ids

    def details(self, sku: str) -> dict | None:
        self.pace.wait()
        answer = mcp_client.call_tool(MCP_URL, self.code, "storefront_product_details",
                                      {"id": int(sku), "storeId": self.store_id, "channel": "lo"})
        data = mcp_client.ok_payload(answer) or {}
        item = data.get("item") if isinstance(data.get("item"), dict) else data
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
