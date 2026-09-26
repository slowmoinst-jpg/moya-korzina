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

Чего в движке и карте нет: цены (всегда 0.0), веса отдельным полем (только в
названии), штрихкода.

ЦЕНЫ — С ВИТРИНЫ, ЧЕРЕЗ ДОМАШНИЙ ВЫХОД (25.09.2026). Сам dixy.ru серверу отказывает по
адресу, а через интернет владельца (app/homeexit.py) отдаёт витрину целиком. Товары
раздела витрина берёт своим запросом ajax/listing-json.php?block=product-list, и
ответ — не вёрстка, а JSON: у товара артикул (xml_id — тот же, что в движке и в
карте), название по-русски, правильная ссылка /product/<название>-<артикул>/,
priceSimple (цена сейчас), oldPriceSimple (зачёркнутая), canBuy, единица.
Страниц по 30 товаров, pagenData говорит, сколько их. Проход идёт тем же запросом
изнутри открытой витрины, раз в секунду и без картинок: через этот выход идёт адрес
владельца. Выхода нет — сеть обходится как раньше, без цен.

Цена — та, что витрина показывает гостю, без выбранного адреса («Укажи адрес, чтобы
посмотреть актуальный каталог»). Привязка к магазину владельца — следующий шаг.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
from typing import Iterator

import requests

from app import config, homeexit
from app.catalog.crawlers import BACKOFF_SEC, RETRIES, USER_AGENT, Pace, sitemap_locs
from app.catalog.model import ChainProduct, CrawlBlocked, Crawler, Progress
from app.matcher.normalize import parse_weight, sold_by_weight

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


# Разделы витрины верхнего уровня: /catalog/<раздел>/.
SECTIONS_JS = """() => [...new Set([...document.querySelectorAll('a[href^="/catalog/"]')]
    .map(a => a.getAttribute('href')))].filter(h => h.split('/').filter(Boolean).length === 2)"""

# Страница раздела — тем же запросом, каким её берёт сама витрина, из неё же: с её
# куками и её адресом. Ответ — [статус, текст].
FETCH_JS = """async (u) => { const r = await fetch(u, {credentials: 'include'});
    return [r.status, await r.text()]; }"""

SETTLE_MS = 4000               # столько витрина дорисовывает разделы после разметки
LISTING = "block=product-list"


def _price(raw) -> float | None:
    try:
        value = round(float(str(raw).replace(",", ".")), 2)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def shelf_product(card: dict) -> ChainProduct | None:
    """Товар из ответа витрины (listing-json, поле cards). Образец — в tests/test_dixy_shelf.py.

    Цена — priceSimple, то есть та, что человек заплатит сейчас: у акционного товара
    это цена по акции, а зачёркнутая лежит в oldPriceSimple. hidePrice означает, что
    витрина цену не показывает (так бывает у алкоголя), — тогда цены нет, а не ноль.
    """
    sku = str(card.get("xml_id") or "").strip()
    name = " ".join(str(card.get("title") or "").split())
    if not sku or not name:
        return None
    path = str(card.get("url") or "").strip()
    symbol = str(card.get("symbol") or card.get("realSymbol") or "").strip().lower()
    can_buy = card.get("canBuy")
    weight_g, _ = parse_weight(name)
    if weight_g is None and card.get("weight"):
        weight_g, _ = parse_weight(str(card.get("weight")))
    # Единица — из символа сети («кг»/«шт») или слова «весовой» в названии. Не из
    # отсутствия граммовки: parse_weight называет «kg» любое название без неё.
    if symbol in ("кг", "kg") or sold_by_weight(name):
        unit = "kg"
    elif symbol or weight_g:
        unit = "pcs"
    else:
        unit = None
    return ChainProduct(
        sku=sku, name=name,
        brand=(str(card.get("brand") or "").strip() or None),
        category=(str(card.get("section") or "").strip() or None),
        weight_g=weight_g,
        unit=unit,
        url=(SITE + path) if path.startswith("/") else (path or None),
        price=None if card.get("hidePrice") else _price(card.get("priceSimple")),
        in_stock=bool(can_buy) if isinstance(can_buy, bool) else None,
    )


def listing(text: str) -> tuple[list[dict], dict]:
    """Ответ listing-json: (товары, сведения о страницах). Витрина ставит перевод строки перед JSON."""
    data = json.loads((text or "").lstrip("﻿ \r\n\t"))
    top = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    cards = [c for c in (top.get("cards") or []) if isinstance(c, dict)]
    return cards, (top.get("pagenData") or {})


def page_url(first: str, number: int) -> str:
    """Адрес n-й страницы из адреса первой, которым её спросила сама витрина."""
    return re.sub(r"([?&])page=1(?=&|$)", rf"\g<1>page={number}", first)


def with_shelf(product: ChainProduct, shelf: dict[str, ChainProduct]) -> ChainProduct:
    """Товар из карты или движка, дополненный тем, что про него сказала витрина.

    Витрина главнее: у неё название по-русски (в карте сайта оно латиницей, из адреса),
    ссылка, которая открывается (движок собирает /product/<артикул> — это 404), цена и
    «можно купить». Картинку и бренд берём оттуда, где они есть.
    """
    hit = shelf.pop(product.sku, None)
    if hit is None:
        return product
    return dataclasses.replace(
        hit, brand=hit.brand or product.brand, category=hit.category or product.category,
        image=product.image, weight_g=hit.weight_g or product.weight_g,
        in_stock=hit.in_stock if hit.in_stock is not None else product.in_stock)


def _no_media(route) -> None:
    """Картинки, видео и шрифты через домашний интернет владельца не качаем."""
    if route.request.resource_type in ("image", "media", "font"):
        route.abort()
    else:
        route.continue_()


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
        self.max_shelf_pages = int(config.get("catalog.dixy.max_shelf_pages") or 80)
        self.shelf_skip = set(config.get("catalog.dixy.shelf_skip") or [])
        self.pace = Pace(chain=self.code)

    # ---------- витрина через домашний выход ----------
    def _shelf(self, say: Progress) -> dict[str, ChainProduct]:
        """Цены с витрины dixy.ru: артикул -> товар. Нет выхода или витрина не далась — пусто.

        Витрина не должна ронять каталог: что бы с ней ни случилось, карта сайта и
        движок обходятся своим чередом, а собранное до обрыва остаётся.
        """
        proxy = homeexit.for_chain(self.code)
        if not proxy:
            return {}
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning("dixy: playwright не установлен — цены с витрины не взять")
            return {}
        found: dict[str, ChainProduct] = {}
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=["--no-sandbox"],
                                             proxy={"server": proxy})
                try:
                    context = browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow",
                                                  viewport={"width": 1366, "height": 900})
                    context.route("**/*", _no_media)
                    self._shelf_walk(context.new_page(), found, say)
                finally:
                    browser.close()
        except Exception:  # noqa: BLE001 — витрина не повод терять каталог
            log.warning("dixy: проход по витрине оборвался, беру собранное (%d)", len(found),
                        exc_info=True)
        priced = sum(1 for product in found.values() if product.price is not None)
        say(f"витрина через домашний выход: товаров {len(found)}, с ценой {priced}")
        return found

    def _shelf_walk(self, page, found: dict[str, ChainProduct], say: Progress) -> None:
        self.pace.wait()
        page.goto(f"{SITE}/catalog/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(SETTLE_MS)
        sections = [href for href in page.evaluate(SECTIONS_JS)
                    if href.strip("/").split("/")[-1] not in self.shelf_skip]
        if not sections:
            log.warning("dixy: витрина не отдала ни одного раздела — цен с неё не будет")
            return
        say(f"витрина: разделов {len(sections)}")
        for href in sections:
            try:
                self._shelf_section(page, href, found)
            except Exception:  # noqa: BLE001 — один раздел не стоит остальных
                log.warning("dixy: раздел %s витрины не дался", href, exc_info=True)
            say(f"витрина: {href} — всего {len(found)}")

    def _shelf_section(self, page, href: str, found: dict[str, ChainProduct]) -> None:
        """Раздел: первую страницу спрашивает сама витрина, остальные — мы, её же запросом."""
        self.pace.wait()
        with page.expect_response(lambda r: LISTING in r.url and re.search(r"[?&]page=1(&|$)", r.url),
                                  timeout=45000) as caught:
            page.goto(SITE + href, wait_until="domcontentloaded", timeout=60000)
        first = caught.value
        cards, pages = listing(first.text())
        self._keep(cards, found)
        total = min(int(pages.get("pages_count") or 1), self.max_shelf_pages)
        for number in range(2, total + 1):
            body = self._fetch(page, page_url(first.url, number))
            if body is None:
                log.warning("dixy: %s страница %d не далась — РАЗДЕЛ ВИТРИНЫ НЕПОЛНЫЙ", href, number)
                return
            cards, pages = listing(body)
            self._keep(cards, found)
            if pages.get("isLastPage") or not cards:
                return

    def _fetch(self, page, url: str) -> str | None:
        """Страница раздела с повтором: отказ бывает просьбой сбавить темп, а не запретом."""
        last = ""
        for attempt in range(RETRIES):
            self.pace.wait()
            try:
                status, body = page.evaluate(FETCH_JS, url)
            except Exception as exc:  # noqa: BLE001 — сеть молчит: подождём и повторим
                status, body, last = 0, "", f"{type(exc).__name__}: {str(exc)[:100]}"
            if status == 200:
                return body
            last = last or f"ответ {status}"
            if attempt < RETRIES - 1:
                pause = BACKOFF_SEC[min(attempt, len(BACKOFF_SEC) - 1)]
                log.info("dixy: витрина — %s, жду %.0f с", last, pause)
                page.wait_for_timeout(int(pause * 1000))
        log.warning("dixy: %s за %d попыток: %s", url[:120], RETRIES, last)
        return None

    @staticmethod
    def _keep(cards: list[dict], found: dict[str, ChainProduct]) -> None:
        for card in cards:
            product = shelf_product(card)
            if product and product.sku not in found:
                found[product.sku] = product

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
        """Витрина (если есть домашний выход), потом карта сайта и движок, дополненные ею.

        Витрину спрашиваем ПЕРВОЙ: её ответ — справочник «артикул → цена», которым
        дополняется каждый товар карты и движка по мере обхода. Товары, которые есть
        только на витрине, идут последними — это тоже каталог сети.
        """
        say = progress or (lambda msg: None)
        shelf = self._shelf(say)
        for product in self._listed(say):
            yield with_shelf(product, shelf)
        if shelf:
            say(f"только на витрине: {len(shelf)} товаров")
        yield from shelf.values()

    def _listed(self, say: Progress) -> Iterator[ChainProduct]:
        """Перечень сети: карта сайта и поисковый движок, как было до витрины."""
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
