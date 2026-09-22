"""Магнит: JSON-шлюз витрины magnit.ru/webgate, найден разведкой 16.09.2026.

Что оттуда берётся и почему не из HTML: один ответ вместо 800-килобайтной страницы,
в нём есть остаток, акция со сроком и признак весового товара, а заголовки и cookies не
нужны — магазин задаётся в теле запроса. Пределы записаны ошибками самого сервера:
limit ≤ 50, offset до ~10 000. Поэтому обход всегда по ЛИСТЬЯМ дерева категорий, а не
плоский: у плоского каталога в 22 тысячи позиций потолок offset наступает раньше конца.

Каталог зависит от магазина сильно (Краснодар 22,5 тыс. позиций, Новосибирск 10,8 тыс.),
поэтому обход идёт ПО ТОЧКАМ ЛЮДЕЙ: магазины подбираются к адресам рабочих мест
(app/places.py), а не задаются настройкой. Так убираются сразу две беды — лишняя
работа («обойдём всю сеть») и враньё каталога, в котором половина товаров человеку
недоступна, потому что лежит в другом городе. Точек нет (никто не указал адрес,
свежий сервер) — берётся запасной магазин из config.yaml или краснодарский.

Несколько точек обходятся ОДНИМ обходом, а не несколькими: пропавшими товары
помечаются только после полного обхода сети, и два отдельных обхода объявили бы
пропавшим всё, чего нет в другом городе. Повторы по артикулу отсекаются — товар,
который есть в обеих точках, это одна строка каталога.

Цены в копейках — делим на сто. Штрихкода нет нигде. Бренд и вес лежат только в
карточке (отдельный запрос на товар), сюда не берём: для сопоставления фасовка
читается из названия, а карточки — 22 тысячи запросов в сутки — не окупаются.
"""
from __future__ import annotations

import logging
from typing import Iterator

import requests

from app import config
from app.catalog.crawlers import USER_AGENT, Pace, http_get
from app.catalog.model import ChainProduct, CrawlBlocked, Crawler, Progress

log = logging.getLogger(__name__)

BASE = "https://magnit.ru"
DEFAULT_STORE = "992301"
PAGE = 50
OFFSET_CAP = 10000


def leaves(tree: list[dict], path: tuple[str, ...] = ()) -> list[tuple[int, str]]:
    """(id листа, «Родитель / Лист») по дереву категорий шлюза."""
    out: list[tuple[int, str]] = []
    for node in tree or []:
        if not isinstance(node, dict) or not node.get("id"):
            continue
        here = path + ((node.get("name") or "").strip(),)
        children = node.get("children") or []
        if children:
            out.extend(leaves(children, here))
        else:
            out.append((int(node["id"]), " / ".join(p for p in here if p)))
    return out


def to_product(item: dict, category: str | None) -> ChainProduct | None:
    sku, name = item.get("id") or item.get("productId"), (item.get("name") or "").strip()
    if not sku or not name:
        return None
    weighted = item.get("weighted") or {}
    is_weighted = bool(weighted.get("isWeighted"))
    price = item.get("price")
    gallery = item.get("gallery") or []
    image = next((g.get("url") for g in gallery if isinstance(g, dict) and g.get("url")), None)
    quantity = item.get("quantity")
    return ChainProduct(
        sku=str(sku), name=name, category=category,
        unit="kg" if is_weighted else "pcs",
        weight_g=None,                      # фасовку разберёт сопоставление из названия
        url=f"{BASE}/product/{sku}-{item.get('seoCode')}" if item.get("seoCode") else f"{BASE}/product/{sku}",
        image=image,
        price=round(price / 100.0, 2) if isinstance(price, (int, float)) else None,
        in_stock=(quantity > 0) if isinstance(quantity, (int, float)) else None,
    )


class MagnitCrawler(Crawler):
    code = "magnit"
    name = "Магнит"

    def __init__(self, stores: list[str] | None = None) -> None:
        self.stores = [str(s) for s in (stores or []) if s] or [self._spare()]
        self.store_type = str(config.get("catalog.magnit.store_type") or "dostavka")
        self.catalog_type = str(config.get("catalog.magnit.catalog_type") or "3")
        self.pace = Pace()

    @staticmethod
    def _spare() -> str:
        """Магазин, когда точек нет вовсе: свежий сервер не должен остаться без каталога."""
        return str(config.get("catalog.magnit.store_code")
                   or config.get("connectors.magnit_shop_code") or DEFAULT_STORE)

    def _tree(self, store: str) -> list[dict]:
        url = (f"{BASE}/webgate/v3/categories/store/{store}"
               f"?storetype={self.store_type}&catalogtype={self.catalog_type}")
        response = http_get(url, self.pace, accept="application/json")
        if "json" not in (response.headers.get("Content-Type") or ""):
            raise CrawlBlocked("дерево категорий пришло не JSON — похоже на страницу защиты")
        return response.json().get("items") or []

    def _page(self, store: str, leaf_id: int, offset: int) -> dict:
        self.pace.wait()
        body = {"categories": [leaf_id], "includeAdultGoods": True,
                "pagination": {"offset": offset, "limit": PAGE},
                "sort": {"order": "desc", "type": "popularity"}, "term": "",
                "storeCode": store, "storeType": self.store_type,
                "catalogType": self.catalog_type}
        response = requests.post(f"{BASE}/webgate/v2/goods/search", json=body, timeout=60,
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                          "Content-Type": "application/json"})
        if response.status_code in (401, 403, 429):
            raise CrawlBlocked(f"поиск по категории {leaf_id}: ответ {response.status_code}")
        if "json" not in (response.headers.get("Content-Type") or ""):
            raise CrawlBlocked("список товаров пришёл не JSON — похоже на страницу защиты")
        response.raise_for_status()
        return response.json()

    def crawl(self, progress: Progress | None = None) -> Iterator[ChainProduct]:
        say = progress or (lambda msg: None)
        seen: set[str] = set()
        say(f"точек к обходу: {len(self.stores)} ({', '.join(self.stores)})")
        for store in self.stores:
            yield from self._crawl_store(store, seen, say)

    def _crawl_store(self, store: str, seen: set[str],
                     say: Progress) -> Iterator[ChainProduct]:
        """Одна точка. Повторы по артикулу общие на весь обход — товар один, точек много."""
        tree = self._tree(store)
        plan = leaves(tree)
        say(f"магазин {store}: листьев категорий {len(plan)}")
        before = len(seen)
        for n, (leaf_id, path) in enumerate(plan, start=1):
            offset = 0
            while True:
                data = self._page(store, leaf_id, offset)
                items = data.get("items") or []
                for item in items:
                    product = to_product(item, path)
                    if product and product.sku not in seen:
                        seen.add(product.sku)
                        yield product
                pagination = data.get("pagination") or {}
                if not items or not pagination.get("hasMore"):
                    break
                offset += len(items)
                if offset >= OFFSET_CAP:
                    log.warning("magnit: категория %s длиннее потолка offset, остаток не взят", path)
                    break
            if n % 25 == 0:
                say(f"магазин {store}: категорий {n} из {len(plan)}, товаров {len(seen)}")
        say(f"магазин {store}: добавил {len(seen) - before} товаров, всего {len(seen)}")
