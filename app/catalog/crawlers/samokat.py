"""Самокат: каталог с ценами, остатками и фасовкой.

ПОЧЕМУ ЭТОТ МОДУЛЬ ПОЯВИЛСЯ.
Самокат — крупнейший сервис экспресс-доставки продуктов.
Обычному HTTP-запросу сайт samokat.ru и api-web.samokat.ru отдают заглушку ServicePipe.
Загрузка каталога работает по трём маршрутам:
1. Через наш браузер Playwright Chromium с домашним выходом (connectors.home_exit)
   или сохранённым сеансом (app/shopbrowser/store.py).
2. Через сбор витрины категорий: /category/... карточки товаров со ссылками
   вида /product/<sku>, ценами и граммовками.
3. Добор из базы опознанных товаров сети и фискальных чеков (store_products),
   чтобы даже при временной блокировке витрины ранее купленные и подтверждённые
   товары участвовали в общем каталоге и сопоставлении.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator

from app import config, homeexit, repo
from app.catalog.crawlers import Pace
from app.catalog.model import ChainProduct, CrawlBlocked, Crawler, Progress
from app.matcher.normalize import parse_weight, unit_from_name

log = logging.getLogger(__name__)

SITE = "https://samokat.ru"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

DEFAULT_CATEGORIES = [
    "molochnye-produkty-i-yayca",
    "hleb-i-vypechka",
    "ovoshchi-i-frukty",
    "myaso-ptica-i-delikatesy",
    "syr",
    "ryba-i-moreprodukty",
    "voda-i-napitki",
    "chay-i-kofe",
    "sladosti-i-sneki",
    "bakaleya",
    "zamorozka",
    "gotovaya-eda",
]

# /product/<артикул> или /product/<slug>-<артикул>
SKU_RE = re.compile(r"/product/(?:.*-)?([a-zA-Z0-9_-]+)/?(?:[?#]|$)")


def sku_from_url(url: str | None) -> str | None:
    """Извлечь артикул Самоката из адреса карточки."""
    if not url:
        return None
    match = SKU_RE.search(url)
    return match.group(1) if match else None


def parse_card_text(text: str | None) -> dict | None:
    """Разбор видимого текста карточки Самоката."""
    if not text:
        return None
    clean = " ".join(text.split()).strip()
    match = re.search(r"^(?:[\d.,]+\s+)?(.+?)\s+(\d+(?:[.,]\d+)?)\s*₽", clean)
    if not match:
        return None
    name = match.group(1).strip()
    price = float(match.group(2).replace(",", "."))
    if not name or price <= 0:
        return None

    weight_g, _ = parse_weight(name)
    return {
        "name": name,
        "price": round(price, 2),
        "weight_g": weight_g,
        # «kg» только по слову «весовой»/«кг», а не по отсутствию граммовки
        "unit": unit_from_name(name),
    }


def to_product(raw: dict) -> ChainProduct | None:
    """Собрать ChainProduct из словаря карточки."""
    sku = str(raw.get("sku") or "").strip()
    if not sku:
        sku = sku_from_url(raw.get("url"))
    if not sku:
        return None

    name = str(raw.get("name") or "").strip()
    if not name:
        return None

    price = raw.get("price")
    try:
        price_val = round(float(price), 2) if price is not None else None
    except (TypeError, ValueError):
        price_val = None

    weight_g = raw.get("weight_g")
    unit = raw.get("unit")
    if not weight_g:
        weight_g, _ = parse_weight(name)
    if not unit:
        unit = unit_from_name(name)

    return ChainProduct(
        sku=sku,
        name=name,
        brand=raw.get("brand") or None,
        weight_g=weight_g,
        unit=unit,
        category=raw.get("category") or None,
        url=raw.get("url") or None,
        image=raw.get("image") or None,
        barcode=raw.get("barcode") or None,
        price=price_val,
        in_stock=raw.get("in_stock", True),
    )


class SamokatCrawler(Crawler):
    """Сборщик каталога Самоката."""

    code = "samokat"
    name = "Самокат"

    def __init__(self, stores: list[str] | None = None, categories: list[str] | None = None) -> None:
        self.pace = Pace(chain=self.code)
        if stores and isinstance(stores, list) and any(s in DEFAULT_CATEGORIES for s in stores):
            self.categories = stores
            self.stores = []
        else:
            self.stores = [str(s) for s in (stores or []) if s]
            self.categories = categories or list(DEFAULT_CATEGORIES)
        self.max_pages = int(config.get("catalog.samokat.max_pages") or 20)

    def crawl(self, progress: Progress | None = None) -> Iterator[ChainProduct]:
        say = progress or (lambda msg: None)
        say("самокат: запуск обхода каталога")
        seen: set[str] = set()
        crawled_count = 0

        # Маршрут 1: браузерный обход витрины
        try:
            for product in self._crawl_browser(say):
                if product.sku not in seen:
                    seen.add(product.sku)
                    crawled_count += 1
                    yield product
        except CrawlBlocked as exc:
            log.warning("самокат: браузерная витрина заблокирована: %s", exc)
        except Exception as exc:
            log.warning("самокат: ошибка браузерного обхода: %s", exc, exc_info=True)

        # Маршрут 2: добор известных товаров из базы и чеков
        db_count = 0
        for product in self._from_store_products():
            if product.sku not in seen:
                seen.add(product.sku)
                db_count += 1
                yield product

        say(f"самокат: собрано товаров с витрины {crawled_count}, из базы/чеков {db_count}")
        if not seen:
            raise CrawlBlocked("самокат: витрина закрыта защитой ServicePipe, а в базе нет сохранённых товаров")

    def _crawl_browser(self, say: Progress) -> Iterator[ChainProduct]:
        """Обход каталога через Playwright Chromium."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CrawlBlocked("playwright не установлен") from exc

        from app.shopbrowser import store as shopstore

        proxy_url = homeexit.for_chain(self.code)
        launch_args = ["--no-sandbox", "--disable-dev-shm-usage"]
        proxy_opts = {"server": proxy_url} if proxy_url else None

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=launch_args, proxy=proxy_opts)
            saved_state = shopstore.load(self.code)
            context = browser.new_context(
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                user_agent=UA,
                viewport={"width": 1366, "height": 900},
                storage_state=saved_state if isinstance(saved_state, dict) else None,
            )
            page = context.new_page()
            try:
                for cat in self.categories:
                    cat_url = f"{SITE}/category/{cat}"
                    say(f"самокат: обход раздела {cat}")
                    try:
                        res = page.goto(cat_url, timeout=30000, wait_until="domcontentloaded")
                    except Exception as e:
                        log.info("самокат: раздел %s не открылся: %s", cat, e)
                        continue

                    # Проверка капчи и блокировки
                    body_text = page.inner_text("body")[:1000]
                    if "пройдите проверку" in body_text.lower() or "servicepipe" in body_text.lower() or "разверните картинку" in body_text.lower():
                        raise CrawlBlocked("самокат: витрина встретила проверкой капчи (пройдите её в кабинете приложения)")

                    # Извлечение карточек
                    cards_data = page.evaluate("""() => {
                        const out = [];
                        const links = document.querySelectorAll('a[href*="/product/"]');
                        for (const a of links) {
                            const href = a.getAttribute('href');
                            const text = a.innerText;
                            const img = a.querySelector('img');
                            out.push({
                                url: href,
                                text: text,
                                image: img ? (img.getAttribute('src') || img.getAttribute('data-src')) : null
                            });
                        }
                        return out;
                    }""")

                    found_in_cat = 0
                    for item in cards_data:
                        sku = sku_from_url(item.get("url"))
                        if not sku:
                            continue
                        parsed = parse_card_text(item.get("text"))
                        if not parsed:
                            continue
                        full_url = f"{SITE}{item['url']}" if item['url'].startswith("/") else item['url']
                        p = to_product({
                            "sku": sku,
                            "name": parsed["name"],
                            "price": parsed["price"],
                            "weight_g": parsed["weight_g"],
                            "unit": parsed["unit"],
                            "url": full_url,
                            "image": item.get("image"),
                            "category": cat,
                            "in_stock": True,
                        })
                        if p:
                            found_in_cat += 1
                            yield p
                    say(f"самокат: раздел {cat} дал {found_in_cat} товаров")
            finally:
                browser.close()

    def _from_store_products(self) -> Iterator[ChainProduct]:
        """Товары Самоката из базы пользователя (чеки, прайс-пакеты)."""
        store = repo.get_store(self.code)
        if not store:
            return
        with repo.get_conn() as conn:
            rows = conn.execute(
                "SELECT sp.sku, sp.raw_name, sp.ean AS barcode, sp.url, p.price "
                "FROM store_products sp "
                # Последний снимок цены каждого товара. Колонки is_current в
                # store_prices нет и не было, а штрихкод у store_products лежит в
                # ean: запрос падал, и добор из чеков не работал вовсе (тест
                # подменял базу целиком и этого не видел).
                "LEFT JOIN (SELECT store_product_id, price, MAX(fetched_at) AS at "
                "FROM store_prices GROUP BY store_product_id) p "
                "ON p.store_product_id = sp.id "
                "WHERE sp.store_id=?", (store.id,)
            ).fetchall()
        for r in rows:
            if not r["sku"] or not r["raw_name"]:
                continue
            weight_g, _ = parse_weight(r["raw_name"])
            yield ChainProduct(
                sku=str(r["sku"]),
                name=r["raw_name"],
                weight_g=weight_g,
                unit=unit_from_name(r["raw_name"]),
                barcode=str(r["barcode"]).strip() if r["barcode"] else None,
                price=float(r["price"]) if r["price"] else None,
                url=r["url"] or f"{SITE}/product/{r['sku']}/",
                in_stock=True,
            )


__all__ = ["SamokatCrawler", "to_product", "sku_from_url", "parse_card_text"]
