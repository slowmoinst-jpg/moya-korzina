"""Пятёрочка: каталог с ценами и фасовкой.

ПОЧЕМУ ЭТОТ МОДУЛЬ ПОЯВИЛСЯ.
Пятёрочка — крупнейшая продуктовая сеть страны и базовый магазин расчёта (baseline_store).
Обычному HTTP-запросу сайт 5ka.ru отдаёт заглушку антибота ServicePipe.
Загрузка каталога работает по трём маршрутам:
1. Через наш браузер Playwright Chromium с домашним выходом (connectors.home_exit)
   или сохранённым сеансом (app/shopbrowser/store.py).
2. Через сбор витрины категорий: /catalog/... карточки товаров со ссылками
   вида /product/<slug>--<sku>/, ценами и граммовками.
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
from app.matcher.normalize import parse_weight

log = logging.getLogger(__name__)

SITE = "https://5ka.ru"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Основные разделы каталога Пятёрочки для регулярного обхода
DEFAULT_SECTIONS = [
    "molochnye-produkty-yaytsa",
    "khleb-vypechka",
    "ovoshchi-frukty-griby",
    "myaso-ptitsa",
    "kolbasy-sosiski",
    "syr",
    "bakaleya",
    "voda-soki-napitki",
    "chay-kofe-sakhar",
    "sladosti",
    "zamorozhennye-produkty",
    "ryba-moreprodukty",
]

# /product/<название>--<артикул>/
SKU_RE = re.compile(r"--(\d+)/?(?:[?#]|$)")


def sku_from_url(url: str | None) -> str | None:
    """Извлечь числовой артикул Пятёрочки из адреса карточки."""
    if not url:
        return None
    match = SKU_RE.search(url)
    return match.group(1) if match else None


def parse_card_text(text: str | None) -> dict | None:
    """Разбор видимого текста карточки Пятёрочки.

    Формат на витрине: '4,85 Молоко Простоквашино 2.5% 930 мл 89 99 ₽'
    или 'Батон Нарезной 400 г 39 99 ₽'.
    """
    if not text:
        return None
    clean = " ".join(text.split()).strip()
    # Очищаем приписки вида 'Цена за 100 г' в конце
    clean = re.sub(r"\s*Цена за\s*\d+\s*(?:г|мл|кг|л)\s*$", "", clean, flags=re.I).strip()
    match = re.search(r"^(?:[\d.,]+\s+)?(.+?)\s+(\d+)\s+(\d{2})\s*₽$", clean)
    if not match:
        # Простой вариант 'Название 99.99 ₽'
        match_simple = re.search(r"^(?:[\d.,]+\s+)?(.+?)\s+(\d+(?:[.,]\d+)?)\s*₽$", clean)
        if not match_simple:
            return None
        raw_name = match_simple.group(1).strip()
        price = float(match_simple.group(2).replace(",", "."))
    else:
        raw_name = match.group(1).strip()
        price = float(f"{match.group(2)}.{match.group(3)}")

    name = re.sub(r"\s*Цена за\s*\d+\s*(?:г|мл|кг|л)\s*$", "", raw_name, flags=re.I).strip()
    if not name or price <= 0:
        return None

    weight_g, unit = parse_weight(name)
    return {
        "name": name,
        "price": round(price, 2),
        "weight_g": weight_g,
        "unit": unit or "pcs",
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
        weight_g, parsed_unit = parse_weight(name)
        if not unit:
            unit = parsed_unit

    return ChainProduct(
        sku=sku,
        name=name,
        brand=raw.get("brand") or None,
        weight_g=weight_g,
        unit=unit or "pcs",
        category=raw.get("category") or None,
        url=raw.get("url") or None,
        image=raw.get("image") or None,
        barcode=raw.get("barcode") or None,
        price=price_val,
        in_stock=raw.get("in_stock", True),
    )


class PyaterochkaCrawler(Crawler):
    """Сборщик каталога Пятёрочки."""

    code = "pyaterochka"
    name = "Пятёрочка"

    def __init__(self, stores: list[str] | None = None, sections: list[str] | None = None) -> None:
        self.pace = Pace()
        if stores and isinstance(stores, list) and any(s in DEFAULT_SECTIONS for s in stores):
            self.sections = stores
            self.stores = []
        else:
            self.stores = [str(s) for s in (stores or []) if s]
            self.sections = sections or list(DEFAULT_SECTIONS)
        self.max_pages_per_section = int(config.get("catalog.pyaterochka.max_pages") or 20)

    def crawl(self, progress: Progress | None = None) -> Iterator[ChainProduct]:
        say = progress or (lambda msg: None)
        say("пятёрочка: запуск обхода каталога")
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
            log.warning("пятёрочка: браузерная витрина заблокирована: %s", exc)
        except Exception as exc:
            log.warning("пятёрочка: ошибка браузерного обхода: %s", exc, exc_info=True)

        # Маршрут 2: добор известных товаров из базы и чеков
        db_count = 0
        for product in self._from_store_products():
            if product.sku not in seen:
                seen.add(product.sku)
                db_count += 1
                yield product

        say(f"пятёрочка: собрано товаров с витрины {crawled_count}, из базы/чеков {db_count}")
        if not seen:
            raise CrawlBlocked("пятёрочка: витрина закрыта защитой ServicePipe, а в базе нет сохранённых товаров")

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
                for section in self.sections:
                    sec_url = f"{SITE}/catalog/{section}/"
                    say(f"пятёрочка: обход раздела {section}")
                    try:
                        res = page.goto(sec_url, timeout=30000, wait_until="domcontentloaded")
                    except Exception as e:
                        log.info("пятёрочка: раздел %s не открылся: %s", section, e)
                        continue

                    # Проверка блокировки антиботом
                    body_text = page.inner_text("body")[:1000]
                    if "проверьте настройки интернета и vpn" in body_text.lower() or "servicepipe" in body_text.lower():
                        raise CrawlBlocked("пятёрочка: витрина заблокирована ServicePipe (проверьте VPN / домашний выход)")

                    # Извлечение ссылок на товары
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

                    found_in_sec = 0
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
                            "category": section,
                            "in_stock": True,
                        })
                        if p:
                            found_in_sec += 1
                            yield p
                    say(f"пятёрочка: раздел {section} дал {found_in_sec} товаров")
            finally:
                browser.close()

    def _from_store_products(self) -> Iterator[ChainProduct]:
        """Товары Пятёрочки из базы пользователя (чеки, прайс-пакеты)."""
        store = repo.get_store(self.code)
        if not store:
            return
        with repo.get_conn() as conn:
            rows = conn.execute(
                "SELECT sp.sku, sp.raw_name, sp.barcode, sp.url, p.price "
                "FROM store_products sp "
                "LEFT JOIN (SELECT store_product_id, price FROM store_prices WHERE is_current=1) p "
                "ON p.store_product_id = sp.id "
                "WHERE sp.store_id=?", (store.id,)
            ).fetchall()
        for r in rows:
            if not r["sku"] or not r["raw_name"]:
                continue
            weight_g, unit = parse_weight(r["raw_name"])
            yield ChainProduct(
                sku=str(r["sku"]),
                name=r["raw_name"],
                weight_g=weight_g,
                unit=unit or "pcs",
                barcode=str(r["barcode"]).strip() if r["barcode"] else None,
                price=float(r["price"]) if r["price"] else None,
                url=r["url"] or f"{SITE}/product/{r['sku']}/",
                in_stock=True,
            )


__all__ = ["PyaterochkaCrawler", "to_product", "sku_from_url", "parse_card_text"]
