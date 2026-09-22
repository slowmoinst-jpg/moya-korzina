"""Fix Price: каталог с ценами и наличием, через наш собственный браузер.

ПОЧЕМУ БРАУЗЕР, А НЕ ЗАПРОС. Замер 20.09.2026 с боевого сервера, один и тот же
адрес двумя дверьми:

    requests     HTTP 200, 1 804 байта — заглушка, ни одного товара
    наш Chromium HTTP 200, 1 190 622 байта, 27 видимых цен

Сеть ждёт исполнения JS и настоящего окна. Ничего не подменяем: Chromium наш
собственный, отпечаток его же, никаких проверок за человека не проходим. Дверь
просто другая — та, которой ходит обычный покупатель.

ГДЕ ЛЕЖАТ ТОВАРЫ. Витрина на Nuxt: сервер отрисовывает страницу и кладёт рядом
её состояние в `window.__NUXT__`. Товары там целиком, разбирать разметку не надо:

    {"id": 1660028, "sku": "1660028", "price": "79.00", "inStock": 28,
     "title": "Сахар белый кристаллический, «Русский сахар», 1 кг",
     "brand": {"title": "Русский Сахар"}, "category": {"title": "Продукты и напитки"},
     "specialPrice": {"price": "", "activeTo": ""},
     "url": "produkty-i-napitki/p-1660028-sahar-belyy-..."}

Путь до списка в состоянии — `fetch.data-v-<хеш>.products`, и хеш меняется от
сборки к сборке витрины. Поэтому список ищется ОБХОДОМ состояния по признаку
«массив словарей, у которых есть title и price», а не по заученному пути: путь
протух бы на следующем их релизе молча, отдав пустой каталог.

ПОЧЕМУ НЕ ЧЕРЕЗ ИХ API. У витрины есть api.fix-price.com/buyer, и она сама им
пользуется. Снаружи он закрыт, а изнутри страницы его режет CORS («Failed to
fetch») — при этом у сети стоит Cloudflare Turnstile. Читать состояние страницы
и дешевле, и честнее: мы берём то, что витрина УЖЕ отдала нашему окну.

ЦЕНА ПО АКЦИИ. `price` — обычная цена, `specialPrice.price` — цена по акции,
когда она есть. Приложение обещает считать то, что человек заплатит сегодня,
поэтому при непустой акционной цене берётся она, а не зачёркнутая.
"""
from __future__ import annotations

import logging
from typing import Iterator

import time

from app import config
from app.catalog.crawlers import BACKOFF_SEC, RETRIES, Pace
from app.catalog.model import ChainProduct, CrawlBlocked, Crawler, Progress

log = logging.getLogger(__name__)

SITE = "https://fix-price.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Разделы, которые не про товары: распродажи и подборки дублируют обычный каталог
# теми же артикулами, и обходить их — удваивать запросы ради тех же строк.
SKIP_SECTIONS = {"spets-tsena-po-karte", "rasprodazha", "novinki", "khit-prodazh"}

# Признаки страницы проверки. Витрина Fix Price нашему серверу их не показывает
# (замер 20.09.2026), но молча считать это вечным нельзя.
GUARD_WORDS = ("не робот", "пройдите проверку", "доступ к сайту", "включён впн",
               "checking your browser")

# Список товаров ищем обходом состояния: путь в Nuxt живёт до их следующего релиза,
# а форма товара — дольше. Возвращаем ПЕРВЫЙ подходящий массив: на странице раздела
# он один, списки рекомендаций рядом с ним не лежат.
PRODUCTS_JS = """
() => {
  const seen = new Set();
  const isProduct = (o) =>
    o && typeof o === 'object' && !Array.isArray(o) &&
    ('price' in o) && ('title' in o) && ('sku' in o || 'id' in o);
  const walk = (node, depth) => {
    if (depth > 8 || node === null || typeof node !== 'object' || seen.has(node)) return null;
    seen.add(node);
    if (Array.isArray(node)) {
      if (node.length && isProduct(node[0])) return node;
      for (const v of node.slice(0, 40)) { const hit = walk(v, depth + 1); if (hit) return hit; }
      return null;
    }
    for (const k of Object.keys(node)) { const hit = walk(node[k], depth + 1); if (hit) return hit; }
    return null;
  };
  return walk(window.__NUXT__, 0) || [];
}
"""

SECTIONS_JS = """
() => [...new Set([...document.querySelectorAll('a[href^="/catalog/"]')]
  .map(a => a.getAttribute('href'))
  .filter(h => h.split('/').length === 3))]
"""


def _price(item: dict) -> float | None:
    """Цена, которую человек заплатит сегодня: по акции, если она идёт."""
    special = (item.get("specialPrice") or {}).get("price")
    for raw in (special, item.get("price"), item.get("minPrice")):
        try:
            value = round(float(str(raw).replace(",", ".")), 2)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _stock(item: dict) -> bool | None:
    """inStock у них — ЧИСЛО остатка, а не признак. Ноль это «нет», а не «не знаю»."""
    raw = item.get("inStock")
    if raw is None:
        return None
    try:
        return int(raw) > 0
    except (TypeError, ValueError):
        return None


def to_product(item: dict) -> ChainProduct | None:
    sku = str(item.get("sku") or item.get("id") or "").strip()
    name = str(item.get("title") or "").strip()
    if not sku or not name:
        return None
    url = str(item.get("url") or "").strip()
    return ChainProduct(
        sku=sku,
        name=name,
        brand=(item.get("brand") or {}).get("title") or None,
        category=(item.get("category") or {}).get("title") or None,
        url=f"{SITE}/catalog/{url}" if url else None,
        image=item.get("_image") or None,
        price=_price(item),
        in_stock=_stock(item),
    )


class FixPriceCrawler(Crawler):
    """Каталог Fix Price: разделы с витрины, товары постранично из состояния Nuxt."""

    code = "fixprice"
    name = "Fix Price"

    def __init__(self) -> None:
        self.pace = Pace()
        self.per_page = 24                     # столько витрина кладёт на страницу сама
        self.max_pages = int(config.get("catalog.fixprice.max_pages") or 60)
        self.max_sections = int(config.get("catalog.fixprice.max_sections") or 40)

    def crawl(self, progress: Progress | None = None) -> Iterator[ChainProduct]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CrawlBlocked("playwright не установлен — окна для Fix Price нет") from exc

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
            # Один контекст на весь обход: витрина видит одного посетителя, который
            # листает каталог, а не сорок разных, пришедших в одну секунду.
            context = browser.new_context(
                locale="ru-RU", timezone_id="Europe/Moscow", user_agent=UA,
                viewport={"width": 1366, "height": 900})
            page = context.new_page()
            try:
                yield from self._walk(page, progress)
            finally:
                browser.close()

    def _walk(self, page, progress: Progress | None) -> Iterator[ChainProduct]:
        sections = self._sections(page)
        if not sections:
            raise CrawlBlocked("витрина не отдала ни одного раздела")
        if progress:
            progress(f"fixprice: разделов {len(sections)}")

        total = 0
        partial: list[str] = []
        for section in sections:
            found = 0
            for number in range(1, self.max_pages + 1):
                items = self._page(page, section, number)
                if items is None:
                    partial.append(f"{section}#{number}")
                    break
                if not items:
                    break
                for item in items:
                    product = to_product(item)
                    if product:
                        found += 1
                        total += 1
                        yield product
                # Неполная страница — последняя: следующей просто нет, и лишний
                # запрос за пустотой сети не нужен.
                if len(items) < self.per_page:
                    break
            if progress:
                progress(f"fixprice: {section} — {found}, всего {total}")
            log.info("fixprice: раздел %s дал %s товаров", section, found)

        # Обход прошёл, но часть разделов оборвалась — сказать об этом обязаны мы, а
        # не человек, который потом не найдёт в каталоге половину товаров.
        if partial:
            log.warning("fixprice: каталог НЕПОЛНЫЙ, оборвались разделы: %s",
                        ", ".join(partial[:10]))

    def _sections(self, page) -> list[str]:
        self._open(page, f"{SITE}/catalog")
        found = [h.strip("/").split("/")[-1] for h in page.evaluate(SECTIONS_JS)]
        clean = [s for s in dict.fromkeys(found) if s and s not in SKIP_SECTIONS]
        return clean[:self.max_sections]

    def _page(self, page, section: str, number: int) -> list[dict] | None:
        """Товары страницы, [] — раздел кончился, None — страница не далась.

        РАЗЛИЧАТЬ ЭТИ ТРИ ИСХОДА ОБЯЗАТЕЛЬНО, и вот чего стоило их спутать. Первый
        живой обход 20.09.2026: страницы `odezhda?page=11`, `aksessuary?page=4` и
        `meditsinskie-tovary?page=3` не открылись за минуту, а код считал неудачу
        концом раздела — и обход шёл дальше, записав «раздел дал 72 товара» там, где
        их сотни. Каталог вышел неполным, и ничто на это не указывало: в журнале
        стояло бодрое «обход ok». Тихая недостача хуже честного отказа.

        Поэтому таймаут теперь не приговор: пробуем RETRIES раз с паузами — тем же
        правилом, которым живёт Дикси, где 403 оказался просьбой сбавить темп.
        """
        url = f"{SITE}/catalog/{section}"
        if number > 1:
            url = f"{url}?page={number}"
        last = ""
        for attempt in range(RETRIES):
            try:
                self._open(page, url)
            except CrawlBlocked:
                raise
            except Exception as exc:  # noqa: BLE001 — сеть молчит: подождём и повторим
                last = f"{type(exc).__name__}: {str(exc).splitlines()[0][:100]}"
                if attempt < RETRIES - 1:
                    pause = BACKOFF_SEC[min(attempt, len(BACKOFF_SEC) - 1)]
                    log.info("fixprice: %s страница %s не далась (%s), жду %.0f с",
                             section, number, last, pause)
                    time.sleep(pause)
                continue
            items = page.evaluate(PRODUCTS_JS)
            return [i for i in items if isinstance(i, dict)]

        log.warning("fixprice: %s страница %s не далась за %s попыток (%s) — "
                    "РАЗДЕЛ ОСТАЛСЯ НЕПОЛНЫМ", section, number, RETRIES, last)
        return None

    def _open(self, page, url: str) -> None:
        """Открыть адрес в своём темпе и убедиться, что пришла витрина, а не проверка."""
        self.pace.wait()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        # Состояние Nuxt появляется не в момент разбора разметки, а чуть позже.
        page.wait_for_timeout(int(float(config.get("catalog.fixprice.settle_ms") or 2500)))
        head = (page.inner_text("body")[:400] or "").lower()
        if any(word in head for word in GUARD_WORDS):
            raise CrawlBlocked(f"витрина встретила проверкой: {head.strip()[:120]}")
