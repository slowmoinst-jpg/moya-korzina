"""Цены Дикси с витрины через домашний выход: разбор ответа и слияние с каталогом.

Образец товара снят с ЖИВОГО ответа dixy.ru/ajax/listing-json.php через туннель
владельца 25.09.2026 (раздел «Молочные продукты, яйцо», страница 3), а не выдуман:
выдуманный проверял бы, что мы согласны сами с собой. Браузера и сети здесь нет.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import homeexit  # noqa: E402
from app.catalog.crawlers import dixy  # noqa: E402
from app.catalog.model import ChainProduct  # noqa: E402

LIVE = {
    "id": "243918", "xml_id": "2000635792",
    "title": "Йогурт безлактозный Viola Греческий Малина-Мята 3% 140г",
    "weight": "153 гр",
    "url": "/product/yogurt-bezlaktoznyy-viola-grecheskiy-malina-myata-3-140g-2000635792/",
    "symbol": "шт", "realSymbol": "шт", "section": "Йогурты", "section_id": "14",
    "variant": "штучный", "store": {"storeAddress": None}, "brand": "Viola", "type": "Йогурт",
    "canBuy": True, "hidePrice": False, "status": "in-time",
    "oldPrice": "87.<span>99</span><span class='rub'>руб.</span>",
    "price": "79.<span>99</span><span class='rub'>руб.</span>",
    "badges": [{"class": "yellow", "num": "-9%", "title": "по акции"},
               {"class": "grey", "title": "до 04.10.2026"}],
    "crossPrice": True, "priceSimple": "79.99", "oldPriceSimple": "87.99",
}
PAGES = {"element_count": "1264", "page": 3, "pages_count": 43, "page_size": 30, "isLastPage": False}


def test_live_card_is_read_whole():
    got = dixy.shelf_product(LIVE)

    assert got.sku == "2000635792", "артикул — xml_id: тот же, что в карте сайта и движке"
    assert got.name == "Йогурт безлактозный Viola Греческий Малина-Мята 3% 140г"
    assert got.price == 79.99, "цена сейчас, по акции, а не зачёркнутая"
    assert got.in_stock is True and got.unit == "pcs"
    assert got.weight_g == 140.0, "фасовка извлечена из названия"
    assert got.brand == "Viola" and got.category == "Йогурты"
    assert got.url == ("https://dixy.ru/product/"
                       "yogurt-bezlaktoznyy-viola-grecheskiy-malina-myata-3-140g-2000635792/")


def test_a_hidden_price_is_silence_not_a_zero():
    """Витрина прячет цену у части товаров. Ноль увёл бы расчёт в «самый дешёвый магазин»."""
    assert dixy.shelf_product(dict(LIVE, hidePrice=True)).price is None
    assert dixy.shelf_product(dict(LIVE, priceSimple="0.00")).price is None


def test_cannot_buy_is_a_no():
    assert dixy.shelf_product(dict(LIVE, canBuy=False)).in_stock is False


def test_weighed_goods_are_kilograms():
    assert dixy.shelf_product(dict(LIVE, symbol="кг", realSymbol="кг")).unit == "kg"


def test_weight_is_extracted_from_weight_field_if_not_in_title():
    card = dict(LIVE, title="Йогурт безлактозный Viola", weight="153 гр")
    got = dixy.shelf_product(card)
    assert got.weight_g == 153.0
    assert got.unit == "pcs"


def test_a_card_without_an_article_is_dropped():
    assert dixy.shelf_product(dict(LIVE, xml_id="")) is None
    assert dixy.shelf_product(dict(LIVE, title="  ")) is None


def test_the_listing_answer_starts_with_a_newline():
    """Живой ответ начинается с перевода строки — json.loads на нём падает."""
    body = "\n" + json.dumps([{"title": None, "pagenData": PAGES, "cards": [LIVE]}], ensure_ascii=False)

    cards, pages = dixy.listing(body)

    assert [c["xml_id"] for c in cards] == ["2000635792"]
    assert pages["pages_count"] == 43 and pages["isLastPage"] is False


def test_next_pages_are_asked_with_the_showcase_own_address():
    first = ("https://dixy.ru/ajax/listing-json.php?block=product-list&sid=10&perPage=30"
             "&page=1&gl_filter=&useTracking=true")

    assert dixy.page_url(first, 7) == first.replace("&page=1&", "&page=7&")
    assert "perPage=30" in dixy.page_url(first, 7), "размер страницы не трогаем"


def test_the_shelf_improves_what_the_map_and_the_engine_said():
    """Карта даёт латиницу, движок — битую ссылку; витрина — название, ссылку и цену."""
    shelf = {"2000635792": dixy.shelf_product(LIVE)}
    from_map = ChainProduct(sku="2000635792", name="yogurt bezlaktoznyy viola", image="img.webp",
                            url="https://dixy.ru/product/2000635792", in_stock=None)

    got = dixy.with_shelf(from_map, shelf)

    assert got.name.startswith("Йогурт") and got.price == 79.99
    assert got.url.endswith("-2000635792/") and got.image == "img.webp"
    assert shelf == {}, "взятое с витрины не повторяется хвостом"


def test_without_the_home_exit_the_crawl_is_what_it_was(monkeypatch):
    """Туннеля нет — сеть обходится как раньше, и браузер не поднимается вовсе."""
    monkeypatch.setattr(homeexit, "for_chain", lambda chain: None)
    crawler = dixy.DixyCrawler()
    listed = [ChainProduct(sku="1", name="Молоко"), ChainProduct(sku="2", name="Хлеб")]
    monkeypatch.setattr(crawler, "_listed", lambda say: iter(listed))

    got = list(crawler.crawl())

    assert [p.sku for p in got] == ["1", "2"] and all(p.price is None for p in got)


def test_goods_only_on_the_shelf_are_catalogue_too(monkeypatch):
    crawler = dixy.DixyCrawler()
    monkeypatch.setattr(crawler, "_shelf", lambda say: {
        "2000635792": dixy.shelf_product(LIVE),
        "2000999999": dixy.shelf_product(dict(LIVE, xml_id="2000999999", title="Кефир 1%")),
    })
    monkeypatch.setattr(crawler, "_listed", lambda say: iter([
        ChainProduct(sku="2000635792", name="yogurt"), ChainProduct(sku="5", name="Хлеб")]))

    got = {p.sku: p for p in crawler.crawl()}

    assert set(got) == {"2000635792", "5", "2000999999"}
    assert got["2000635792"].price == 79.99 and got["5"].price is None
    assert got["2000999999"].name == "Кефир 1%"


def test_a_paused_home_exit_is_not_knocked(monkeypatch):
    """Сеть через дом недавно показала «я не робот» — витрину не листаем, браузер не поднимаем."""
    monkeypatch.setattr(homeexit, "for_chain", lambda chain: "socks5://127.0.0.1:1080")
    monkeypatch.setattr(homeexit, "paused_until", lambda chain: 9e9)
    said: list[str] = []

    assert dixy.DixyCrawler()._shelf(said.append) == {}
    assert "проверку" in said[0]


def test_a_check_instead_of_the_shelf_pauses_the_chain(monkeypatch, tmp_path):
    """Вместо витрины — проверка: пауза для коннектора и дозора, а не листание дальше."""
    monkeypatch.setattr(homeexit, "STATE_DIR", str(tmp_path))

    class Page:
        def goto(self, *args, **kwargs):
            pass

        def wait_for_timeout(self, ms):
            pass

        def inner_text(self, selector):
            return "Поставь галочку в поле «Я не робот» И продолжай пользоваться сайтом."

        def evaluate(self, *args):
            raise AssertionError("разделы со страницы проверки не читаем")

    crawler = dixy.DixyCrawler()
    monkeypatch.setattr(crawler.pace, "wait", lambda: None)
    found: dict = {}

    crawler._shelf_walk(Page(), found, lambda message: None)

    assert found == {} and homeexit.paused_until("dixy")


def test_promo_collections_are_not_walked():
    """Подборки дублируют разделы теми же товарами — через дом их не гоняем."""
    assert "skidki-po-karte" in dixy.DixyCrawler().shelf_skip
