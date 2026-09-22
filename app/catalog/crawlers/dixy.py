"""Дикси: каталог через поисковый движок Diginetica, которым живёт сам сайт.

Сайт dixy.ru закрыт с обеих сторон (из-за рубежа — капча за Qrator, с российского
датацентра — страница «Не удалось загрузить сайт»), а движок sort.diginetica.net
отвечает свободно: ключ и код сайта лежат в его же публичном client.js, они те же, что
у коннектора цен (app/connectors/dixy.py). Разведка 16.09.2026.

Как перечислить всё, если пустой запрос запрещён. Страница всегда 400 товаров, offset
работает до конца, а листинга категории без слова нет. Зато почти в каждом названии
есть единица измерения — «г», «л», «мл», «кг», «шт»: пять запросов постранично дают
основную массу (≈ 12–13 тыс. позиций), добор — по брендам из фасета brands с фильтром
filter=brands:<бренд>. Итого 40–60 страниц плюс бренды, около минуты-двух при одном
запросе в секунду; индекс один на всю сеть, регион роли не играет.

Чего здесь нет и не будет: цены (всегда 0.0), наличия (всегда true), веса отдельным
полем (только в названии), штрихкода. Цена Дикси по-прежнему берётся из чеков и прайса.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator

import requests

from app import config
from app.catalog.crawlers import USER_AGENT, Pace, sitemap_locs
from app.catalog.model import ChainProduct, CrawlBlocked, Crawler, Progress

log = logging.getLogger(__name__)

SEARCH = "https://sort.diginetica.net/search"
SITE = "https://dixy.ru"
TOKENS = ("г", "л", "мл", "кг", "шт")
PAGE = 400
DEFAULT_API_KEY = "97PGH0KXFA"

# Карта сайта Дикси. Её адрес объявлен в его же robots.txt; товарные части лежат
# отдельно от страниц сайта и в оглавлении больше не перечислены — проверено
# 19.09.2026, поэтому берём часть прямо, а не через индекс.
SITEMAP = f"{SITE}/sitemap-iblock-1.part1.xml"
PRODUCT_MAPS = re.compile(r"sitemap-iblock")

# Артикул — ПОСЛЕДНИЙ кусок адреса, и он не всегда число: рядом с «2000650171»
# живут «10f0049260», «d000003626», «di00078769». Отличаем его от хвоста фасовки
# («45g», «1l», «500g») длиной: артикулы десятизначные, фасовки короче пяти.
ADDRESS = re.compile(r"/product/(.+)-([0-9a-z]{6,})/?$", re.I)


def from_address(url: str) -> ChainProduct | None:
    """Товар из адреса карты сайта. None — адрес не товарный.

    Имя здесь латиницей, как его пишет сама сеть в адресе: обратно в кириллицу не
    переводим, потому что обратная транслитерация неоднозначна. Сопоставитель
    приводит кириллицу к латинице сам (app/matcher/normalize.py::translit).
    """
    if "/product/" not in url:
        return None
    match = ADDRESS.search(url)
    if not match:
        return None
    slug, sku = match.group(1), match.group(2)
    if not any(ch.isdigit() for ch in sku):
        return None
    name = slug.replace("-", " ").strip()
    return ChainProduct(sku=sku, name=name, url=url) if name else None


def to_product(item: dict) -> ChainProduct | None:
    sku, name = item.get("id"), (item.get("name") or "").strip()
    if not sku or not name:
        return None
    categories = [c for c in (item.get("categories") or []) if isinstance(c, dict) and c.get("name")]
    images = item.get("image_urls") or []
    # `available` движок отдаёт по каждому товару, а мы его до 19.09.2026 выбрасывали.
    # Цены у Дикси нет нигде (движок честно возвращает "0.0"), но «есть или нет» —
    # это ровно та половина опоры «цены и наличие», которую сеть всё-таки сообщает.
    available = item.get("available")
    return ChainProduct(
        sku=str(sku), name=name, brand=(item.get("brand") or "").strip() or None,
        category=categories[0]["name"] if categories else None,
        url=f"{SITE}/product/{sku}",
        image=item.get("image_url") or (images[0] if images else None),
        in_stock=bool(available) if isinstance(available, bool) else None,
    )


class DixyCrawler(Crawler):
    code = "dixy"
    name = "Дикси"

    def __init__(self) -> None:
        self.api_key = str(config.get("connectors.dixy_api_key") or DEFAULT_API_KEY)
        self.brand_cap = int(config.get("catalog.dixy.max_brand_queries") or 400)
        self.pace = Pace()

    def _page(self, term: str, offset: int, extra: dict | None = None) -> dict:
        self.pace.wait()
        params = {"apiKey": self.api_key, "st": term, "strategy": "vectors_extended,zero_queries",
                  "fullData": "true", "withFacets": "true", "withSku": "true", "regionId": "global",
                  "showUnavailable": "true", "offset": offset, **(extra or {})}
        response = requests.get(SEARCH, params=params, timeout=60,
                                headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                         "Referer": SITE + "/"})
        if response.status_code in (401, 403, 429):
            raise CrawlBlocked(f"движок ответил {response.status_code} на «{term}»")
        if "json" not in (response.headers.get("Content-Type") or ""):
            raise CrawlBlocked("движок ответил не JSON — похоже на страницу защиты")
        response.raise_for_status()
        return response.json()

    def _walk(self, term: str, seen: set[str], brands: set[str], extra: dict | None = None
              ) -> Iterator[ChainProduct]:
        """Один запрос страницами. Отказ по ОДНОМУ запросу не отменяет весь обход.

        ПОЧЕМУ ЭТО ЗДЕСЬ ПОЯВИЛОСЬ. Живой обход 19.09.2026 умер на бренде
        «Cillit Bang/Brillit»: в его названии косая черта, движок ответил на такой
        запрос пятисотой, `raise_for_status` поднял исключение — и вся сеть
        отвалилась целиком, потеряв уже собранные 13 600 позиций (в базу они легли,
        но обход пометился упавшим и до конца не дошёл).

        Один плохой бренд из четырёхсот не стоит всей сети. Отказ по запросу
        записывается и пропускается; отказ, за которым стоит защита
        (CrawlBlocked), по-прежнему уходит наружу и останавливает сеть — это
        разные вещи и обходятся они по-разному.
        """
        offset = 0
        while True:
            try:
                data = self._page(term, offset, extra)
            except requests.RequestException as exc:
                log.warning("dixy: запрос «%s» не удался (%s) — пропускаю его, обход идёт дальше",
                            term, str(exc)[:120])
                return
            products = data.get("products") or []
            for facet in data.get("facets") or []:
                if isinstance(facet, dict) and facet.get("name") == "brands":
                    for value in facet.get("values") or []:
                        if isinstance(value, dict) and value.get("name"):
                            brands.add(str(value["name"]))
            for raw in products:
                product = to_product(raw) if isinstance(raw, dict) else None
                if product and product.sku not in seen:
                    seen.add(product.sku)
                    yield product
            total = data.get("totalHits")
            offset += len(products)
            if not products or not isinstance(total, int) or offset >= total:
                return

    def _from_sitemap(self, seen: set[str], say: Progress) -> Iterator[ChainProduct]:
        """Второй источник перечня — собственная карта сайта Дикси.

        ПОЧЕМУ ОН ПОЯВИЛСЯ. Первый источник — сторонний поисковый движок, которым
        живёт поиск на сайте сети. Он работает, но это чужой сервис: сменит сеть
        подрядчика, и каталог Дикси исчезнет целиком. Карта сайта принадлежит самой
        сети, и её адрес объявлен в robots.txt.

        ПОЧЕМУ МЫ ДУМАЛИ, ЧТО ОНА ЗАКРЫТА, И ПОЧЕМУ ЭТО БЫЛО НЕВЕРНО. Замер
        19.09.2026: карта отдалась, а через полтора десятка быстрых запросов подряд
        начала отвечать 403 на всё. Записали как «сеть закрылась». Повторный замер с
        паузой в четыре секунды — пять из пяти по 200 и по 1,35 МБ за десятую долю
        секунды. Это был наш темп, а не её запрет; повтор с паузой теперь общий для
        всех сборщиков (http_get в app/catalog/crawlers/__init__.py).

        В карте 9 198 адресов, из них товарных 9 061. Цен там нет — их у Дикси нет
        нигде, и сборщик их не выдумывает.
        """
        found = 0
        for url in sitemap_locs(SITEMAP, self.pace, PRODUCT_MAPS):
            product = from_address(url)
            if not product or product.sku in seen:
                continue
            seen.add(product.sku)
            found += 1
            yield product
        say(f"из карты сайта добрано {found} товаров")

    def crawl(self, progress: Progress | None = None) -> Iterator[ChainProduct]:
        say = progress or (lambda msg: None)
        seen: set[str] = set()
        brands: set[str] = set()
        # Карта сайта идёт первой: она дешевле (один запрос против сотен) и
        # принадлежит самой сети. Движок потом добирает то, чего в карте нет.
        try:
            yield from self._from_sitemap(seen, say)
        except CrawlBlocked as exc:
            say(f"карта сайта не далась ({exc}) — иду только поисковым движком")
        for token in TOKENS:
            yield from self._walk(token, seen, brands)
            say(f"по «{token}» всего {len(seen)} товаров, брендов в фасетах {len(brands)}")
        before = len(seen)
        for n, brand in enumerate(sorted(brands)[:self.brand_cap], start=1):
            yield from self._walk(brand, seen, set(), {"filter": f"brands:{brand}"})
            if n % 50 == 0:
                say(f"брендов пройдено {n}, добрано {len(seen) - before}")
        say(f"итого {len(seen)} товаров, добрано по брендам {len(seen) - before}")
