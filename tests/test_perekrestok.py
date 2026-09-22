"""Перекрёсток: каталог из карты сайта. Сеть не трогаем — подменяем sitemap_locs.

Сторож здесь один и важный: сборщик обязан отдавать товары БЕЗ ЦЕН. У сети цены
закрыты, и если однажды в `price` появится число, расчёт молча начнёт считать по
выдуманной цене — а выглядеть это будет как обычная работа.
"""
from __future__ import annotations

import pytest

from app.catalog.crawlers.perekrestok import PerekrestokCrawler, parse

REAL = [
    "https://www.perekrestok.ru/cat/121/p/maslo-slivocnoe-icalki-krestanskoe-72-5-500g-3636734",
    "https://www.perekrestok.ru/cat/114/p/moloko-novaa-derevna-pasterizovannoe-2-5-1l-3376019",
    "https://www.perekrestok.ru/cat/9/p/pivo-okskoe-bockovoe-svetloe-4-7-1-3l-4055641",
    "https://www.perekrestok.ru/shops/adler",          # не товар
    "https://www.perekrestok.ru/cat/278",              # раздел, не товар
]


def test_address_carries_category_name_and_article():
    """Имя, артикул и категория лежат в самом адресе — ради этого всё и затевалось."""
    product = parse(REAL[0])
    assert product.sku == "3636734"
    assert product.category == "121"
    assert product.name == "maslo slivocnoe icalki krestanskoe 72 5 500g"
    assert product.url == REAL[0]


def test_non_product_addresses_are_skipped():
    assert parse(REAL[3]) is None
    assert parse(REAL[4]) is None
    assert parse("https://www.perekrestok.ru/") is None


def test_the_crawler_never_invents_a_price(monkeypatch):
    """Цены у этой сети нет, и сборщик обязан молчать о ней, а не ставить ноль.

    Ноль в цене читался бы расчётом как «бесплатно» и увёл бы всю корзину в
    Перекрёсток. Пустое поле читается как «не знаю» — и это правда.
    """
    import app.catalog.crawlers.perekrestok as mod
    monkeypatch.setattr(mod, "sitemap_locs", lambda *a, **k: iter(REAL))

    got = list(PerekrestokCrawler().crawl())

    assert [p.sku for p in got] == ["3636734", "3376019", "4055641"]
    assert all(p.price is None for p in got), "появилась выдуманная цена"
    assert all(p.in_stock is None for p in got), "появилось выдуманное наличие"
    assert all(p.barcode is None for p in got), "штрихкода у этой сети нет"


def test_duplicate_articles_are_collapsed(monkeypatch):
    import app.catalog.crawlers.perekrestok as mod
    monkeypatch.setattr(mod, "sitemap_locs", lambda *a, **k: iter([REAL[0], REAL[0], REAL[1]]))
    assert len(list(PerekrestokCrawler().crawl())) == 2


def test_the_cap_stops_the_walk(monkeypatch):
    """Потолок нужен на случай, если карта однажды вырастет до миллиона строк."""
    import app.catalog.crawlers.perekrestok as mod
    from app import config
    monkeypatch.setattr(mod, "sitemap_locs", lambda *a, **k: iter(REAL))
    monkeypatch.setattr(config, "get", lambda key, default=None:
                        2 if key == "catalog.perekrestok.max_products" else default)
    assert len(list(PerekrestokCrawler().crawl())) == 2


def test_the_crawler_is_registered_and_the_worker_knows_it():
    from app.catalog import crawlers, worker
    assert "perekrestok" in crawlers.registry()
    assert "perekrestok" in worker.chains()
    assert "metro" in worker.chains()


def test_only_product_maps_are_downloaded():
    """В индексе лежат ещё отзывы, бренды и магазины — качать их незачем."""
    from app.catalog.crawlers.perekrestok import PRODUCT_MAPS
    assert PRODUCT_MAPS.search("https://www.perekrestok.ru/sitemap-products-1.xml")
    assert not PRODUCT_MAPS.search("https://www.perekrestok.ru/sitemap-reviews-1.xml")
    assert not PRODUCT_MAPS.search("https://www.perekrestok.ru/sitemap-shops.xml")


# ---------- Впрок: та же дорога, другой вид адреса ----------
VPROK = [
    "https://www.vprok.ru/product/linakva-linakva-bebi-0-9-aerozol-150-ml--1284993",
    "https://www.vprok.ru/product/durex-dur-gel-smazka-natur-int-100ml--548359",
    "https://www.vprok.ru/recipe/salat",          # не товар
]


def test_vprok_address_has_two_dashes_before_the_article():
    """У Впрока артикул отделён ДВУМЯ дефисами, и лишний не должен попасть в имя."""
    from app.catalog.crawlers.vprok import parse as vparse

    product = vparse(VPROK[0])
    assert product.sku == "1284993"
    assert product.name == "linakva linakva bebi 0 9 aerozol 150 ml"
    assert not product.name.endswith("-"), "хвостовой дефис остался в названии"
    assert vparse(VPROK[2]) is None


def test_vprok_never_invents_a_price(monkeypatch):
    import app.catalog.crawlers.vprok as vmod
    from app.catalog.crawlers.vprok import VprokCrawler

    monkeypatch.setattr(vmod, "sitemap_locs", lambda *a, **k: iter(VPROK))
    got = list(VprokCrawler().crawl())
    assert [p.sku for p in got] == ["1284993", "548359"]
    assert all(p.price is None and p.in_stock is None for p in got)


def test_vprok_takes_only_sku_maps():
    """В индексе Впрока рядом лежат recipe, catalog, filter* и sku_revi* — мимо них."""
    from app.catalog.crawlers.vprok import PRODUCT_MAPS as VM

    assert VM.search("https://www.vprok.ru/sku0.xml")
    assert VM.search("https://www.vprok.ru/sku1.xml")
    assert not VM.search("https://www.vprok.ru/sku_revi0.xml")
    assert not VM.search("https://www.vprok.ru/filter0.xml")
    assert not VM.search("https://www.vprok.ru/catalog.xml")


def test_vprok_is_registered():
    from app.catalog import crawlers, worker
    assert "vprok" in crawlers.registry()
    assert "vprok" in worker.chains()


# ---------- Монетка: каталог из карты, цен нет ----------
MONETKA = [
    "https://monetka.ru/product/sok-dobryjj-yabloko-1l-rossiya-810000639/",
    "https://monetka.ru/product/rascheska-massazhnaya-art-wzh-6122-kitajj-810011056/",
    "https://monetka.ru/catalog/napitki/",
]


def test_monetka_address_gives_name_and_article():
    from app.catalog.crawlers.monetka import parse as mparse

    product = mparse(MONETKA[0])
    assert product.sku == "810000639"
    assert product.name == "sok dobryjj yabloko 1l rossiya"
    assert mparse(MONETKA[2]) is None


def test_monetka_never_invents_a_price(monkeypatch):
    """Карточка Монетки отвечает 401 — цены у нас нет, и выдумывать её нельзя."""
    import app.catalog.crawlers.monetka as mmod
    from app.catalog.crawlers.monetka import MonetkaCrawler

    monkeypatch.setattr(mmod, "sitemap_locs", lambda *a, **k: iter(MONETKA))
    got = list(MonetkaCrawler().crawl())
    assert [p.sku for p in got] == ["810000639", "810011056"]
    assert all(p.price is None and p.in_stock is None for p in got)


def test_monetka_takes_only_the_item_map():
    from app.catalog.crawlers.monetka import PRODUCT_MAPS as MM
    assert MM.search("https://monetka.ru/sitemap/sitemap_item_1.xml")
    assert not MM.search("https://monetka.ru/sitemap/sitemap_cat_1.xml")
    assert not MM.search("https://monetka.ru/sitemap/sitemap_brand_1.xml")


def test_monetka_is_registered():
    from app.catalog import crawlers, worker
    assert "monetka" in crawlers.registry()
    assert "monetka" in worker.chains()


def test_dixy_keeps_the_availability_it_is_told():
    """Движок Дикси цен не даёт («0.0»), но про наличие говорит — и это надо брать."""
    from app.catalog.crawlers.dixy import to_product

    base = {"id": "2000600212", "name": "Молоко Хуторок 2,5% 970мл", "price": "0.0"}
    assert to_product({**base, "available": True}).in_stock is True
    assert to_product({**base, "available": False}).in_stock is False
    # Не сказали — это «не знаю», а не «нет в наличии».
    assert to_product(base).in_stock is None
    assert to_product({**base, "available": True}).price is None
