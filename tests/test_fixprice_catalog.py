"""Сборщик Fix Price: разбор товара и правила обхода.

Браузер здесь не поднимается ни разу — проверяется наша обвязка вокруг него.
Образец товара взят из ЖИВОГО состояния витрины (замер 20.09.2026, сахар «Русский
сахар» 1 кг, артикул 1660028), а не выдуман: выдуманный образец проверял бы, что
мы согласны сами с собой.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog.crawlers import registry  # noqa: E402
from app.catalog.crawlers.fixprice import FixPriceCrawler, to_product  # noqa: E402

LIVE = {
    "id": 1660028,
    "sku": "1660028",
    "title": "Сахар белый кристаллический, «Русский сахар», 1 кг",
    "price": "79.00",
    "minPrice": 79,
    "maxPrice": 79,
    "inStock": 28,
    "url": "produkty-i-napitki/p-1660028-sahar-belyy-kristallicheskiy-russkiy-sahar-1-kg",
    "specialPrice": {"price": "", "activeTo": ""},
    "brand": {"id": 540, "title": "Русский Сахар"},
    "category": {"id": 2004, "title": "Продукты и напитки"},
    "_image": "https://img.fix-price.com/insecure/rs:fit:800:800/plain/pim/images/b84.jpg",
}


def test_live_product_is_read_whole():
    got = to_product(LIVE)

    assert got.sku == "1660028"
    assert got.name == "Сахар белый кристаллический, «Русский сахар», 1 кг"
    assert got.price == 79.0
    assert got.in_stock is True
    assert got.weight_g == 1000.0
    assert got.unit == "pcs"
    assert got.brand == "Русский Сахар"
    assert got.category == "Продукты и напитки"
    assert got.url == ("https://fix-price.com/catalog/produkty-i-napitki/"
                       "p-1660028-sahar-belyy-kristallicheskiy-russkiy-sahar-1-kg")


def test_promo_price_wins_over_the_crossed_out_one():
    """Приложение обещает считать то, что человек заплатит СЕГОДНЯ."""
    item = dict(LIVE, price="99.00", specialPrice={"price": "79.00", "activeTo": "2026-09-30"})

    assert to_product(item).price == 79.0


def test_empty_promo_does_not_erase_the_price():
    """`specialPrice.price` пустой строкой — обычное состояние, а не «цены нет»."""
    assert to_product(dict(LIVE, specialPrice={"price": "", "activeTo": ""})).price == 79.0


def test_zero_stock_is_a_no_not_an_unknown():
    """inStock у них ЧИСЛО остатка. Ноль — это «нет», и путать его с «не знаю» нельзя:
    расчёт положит в корзину то, чего в магазине не лежит."""
    assert to_product(dict(LIVE, inStock=0)).in_stock is False
    assert to_product(dict(LIVE, inStock=1)).in_stock is True


def test_missing_stock_stays_unknown():
    item = dict(LIVE)
    item.pop("inStock")

    assert to_product(item).in_stock is None


def test_product_without_a_name_is_dropped():
    """Строка без названия каталогу не нужна: сопоставлять её не с чем."""
    assert to_product(dict(LIVE, title="")) is None
    assert to_product(dict(LIVE, sku="", id=None)) is None


def test_broken_price_is_silence_not_a_zero():
    """Ноль в цене увёл бы расчёт в «самый дешёвый магазин» на пустом месте."""
    assert to_product(dict(LIVE, price="", minPrice=None, specialPrice={})).price is None
    assert to_product(dict(LIVE, price="—", minPrice=0, specialPrice={})).price is None


def test_the_chain_is_registered():
    assert registry()["fixprice"] is FixPriceCrawler


def test_promo_sections_are_skipped():
    """Подборки вроде «Спец-цена по карте» дублируют каталог теми же артикулами."""
    from app.catalog.crawlers.fixprice import SKIP_SECTIONS

    assert "spets-tsena-po-karte" in SKIP_SECTIONS


def test_a_guard_page_is_named_not_swallowed(monkeypatch):
    """Витрина встретила проверкой — обход обязан сказать это вслух, а не отдать пусто.

    Тихий пустой ответ здесь страшнее отказа: каталог сети молча опустел бы, и
    выглядело бы это как «у Fix Price кончились товары».
    """
    import pytest

    from app.catalog.model import CrawlBlocked

    crawler = FixPriceCrawler()

    class Page:
        def goto(self, url, **kwargs):
            return None

        def wait_for_timeout(self, ms):
            return None

        def inner_text(self, selector):
            return "Пожалуйста, пройдите проверку, чтобы получить доступ к сайту"

    with pytest.raises(CrawlBlocked) as caught:
        crawler._open(Page(), "https://fix-price.com/catalog")

    assert "проверк" in str(caught.value)


def test_a_timeout_is_retried_not_taken_for_the_end(monkeypatch):
    """Живой обход 20.09.2026: три раздела оборвались на таймауте, а журнал сказал «ok».

    Неудача страницы считалась концом раздела, и каталог молча вышел неполным —
    «aksessuary дал 72 товара» там, где их сотни. Различать «страница пустая» и
    «страница не далась» обязательно, иначе недостача не видна ничем.
    """
    crawler = FixPriceCrawler()
    tries: list[str] = []

    monkeypatch.setattr("app.catalog.crawlers.fixprice.time.sleep", lambda s: None)

    def flaky_open(page, url):
        tries.append(url)
        if len(tries) < 3:
            raise TimeoutError("Page.goto: Timeout 60000ms exceeded")

    monkeypatch.setattr(crawler, "_open", flaky_open)

    class Page:
        def evaluate(self, js):
            return [LIVE]

    got = crawler._page(Page(), "aksessuary", 4)

    assert len(tries) == 3, "после таймаута надо пробовать снова, а не сдаваться"
    assert got and got[0]["sku"] == "1660028"


def test_a_page_that_never_opens_is_not_silently_an_empty_section(monkeypatch):
    """Не далась совсем — это None, а не [], и в журнал уходит слово «НЕПОЛНЫМ»."""
    crawler = FixPriceCrawler()
    monkeypatch.setattr("app.catalog.crawlers.fixprice.time.sleep", lambda s: None)
    monkeypatch.setattr(crawler, "_open", lambda page, url: (_ for _ in ()).throw(
        TimeoutError("Page.goto: Timeout")))

    assert crawler._page(object(), "odezhda", 11) is None


def test_the_first_page_of_the_catalog_is_retried_too(monkeypatch):
    """Ночной обход 22.09.2026: /catalog не отрисовался за 30 с, и сеть выпала целиком.

    Повтор стоял только на страницах разделов, а первая страница — список разделов —
    падала с первого таймаута: «увидено 0» через полминуты после начала.
    """
    crawler = FixPriceCrawler()
    tries: list[str] = []
    monkeypatch.setattr("app.catalog.crawlers.fixprice.time.sleep", lambda s: None)

    def flaky_open(page, url):
        tries.append(url)
        if len(tries) < 2:
            raise TimeoutError("Page.inner_text: Timeout 30000ms exceeded.")

    monkeypatch.setattr(crawler, "_open", flaky_open)

    class Page:
        def evaluate(self, js):
            return ["/catalog/produkty-i-napitki", "/catalog/rasprodazha", "/catalog/igrushki"]

    assert crawler._sections(Page()) == ["produkty-i-napitki", "igrushki"]
    assert tries == ["https://fix-price.com/catalog"] * 2


def test_a_catalog_without_sections_is_asked_again(monkeypatch):
    """25.09.2026: /catalog открылся без ссылок на разделы, и сеть потеряла сутки."""
    crawler = FixPriceCrawler()
    opened: list[str] = []
    answers = [[], ["/catalog/igrushki"]]
    monkeypatch.setattr("app.catalog.crawlers.fixprice.time.sleep", lambda s: None)
    monkeypatch.setattr(crawler, "_open", lambda page, url: opened.append(url))

    class Page:
        def evaluate(self, js):
            return answers.pop(0)

    assert crawler._sections(Page()) == ["igrushki"]
    assert len(opened) == 2


def test_a_catalog_that_stays_empty_is_given_up_after_the_retries(monkeypatch):
    """Пусто на каждой попытке — тогда уже «не отдала ни одного раздела», как раньше."""
    from app.catalog.crawlers import RETRIES

    crawler = FixPriceCrawler()
    opened: list[str] = []
    monkeypatch.setattr("app.catalog.crawlers.fixprice.time.sleep", lambda s: None)
    monkeypatch.setattr(crawler, "_open", lambda page, url: opened.append(url))

    class Page:
        def evaluate(self, js):
            return []

    assert crawler._sections(Page()) == []
    assert len(opened) == RETRIES


def test_a_catalog_that_never_opens_names_the_reason(monkeypatch):
    """Не открылся за все попытки — обход падает со словами, а не с голым таймаутом."""
    import pytest

    crawler = FixPriceCrawler()
    monkeypatch.setattr("app.catalog.crawlers.fixprice.time.sleep", lambda s: None)
    monkeypatch.setattr(crawler, "_open", lambda page, url: (_ for _ in ()).throw(
        TimeoutError("Page.inner_text: Timeout 30000ms exceeded.")))

    with pytest.raises(RuntimeError) as caught:
        crawler._sections(object())

    assert "каталог" in str(caught.value) and "Timeout" in str(caught.value)


def test_a_guard_on_the_first_page_is_not_waited_out(monkeypatch):
    """Проверка «я не робот» — состояние витрины, а не заминка: ждать её нечего."""
    import pytest

    from app.catalog.model import CrawlBlocked

    crawler = FixPriceCrawler()
    tries: list[str] = []
    monkeypatch.setattr("app.catalog.crawlers.fixprice.time.sleep", lambda s: None)

    def guarded(page, url):
        tries.append(url)
        raise CrawlBlocked("витрина встретила проверкой")

    monkeypatch.setattr(crawler, "_open", guarded)

    with pytest.raises(CrawlBlocked):
        crawler._sections(object())
    assert len(tries) == 1


def test_a_short_page_ends_the_section(monkeypatch):
    """Неполная страница — последняя. Иначе обход просил бы у сети пустоту подряд."""
    crawler = FixPriceCrawler()
    asked: list[int] = []

    monkeypatch.setattr(crawler, "_sections", lambda page: ["produkty-i-napitki"])

    def fake_page(page, section, number):
        asked.append(number)
        return [dict(LIVE, sku=f"{number}-{i}") for i in range(24 if number < 3 else 5)]

    monkeypatch.setattr(crawler, "_page", fake_page)
    got = list(crawler._walk(None, None))

    assert asked == [1, 2, 3], "после неполной страницы четвёртую спрашивать незачем"
    assert len(got) == 24 + 24 + 5
