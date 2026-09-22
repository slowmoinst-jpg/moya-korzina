"""Повтор при отказе и карта сайта Дикси. Сеть не трогаем.

Оба сторожа выросли из одной живой ошибки 19.09.2026: карта товаров Дикси
отдалась, потом от нашей же частоты пошли 403, и мы записали сеть в закрытые.
Повторный замер с паузой в четыре секунды дал пять успехов из пяти. Значит один
отказ — не приговор, и код обязан это знать.
"""
from __future__ import annotations

import pytest
import requests

from app.catalog import crawlers
from app.catalog.crawlers.dixy import ADDRESS, from_address
from app.catalog.model import CrawlBlocked


class Reply:
    def __init__(self, code, body=b"<ok/>", headers=None):
        self.status_code = code
        self.content = body
        self.headers = headers or {"Content-Type": "application/xml"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_a_single_refusal_is_not_a_verdict(monkeypatch):
    """403 с первого раза — повод подождать, а не объявить сеть закрытой."""
    seen = []
    replies = [Reply(403), Reply(403), Reply(200, b"<good/>")]

    monkeypatch.setattr(crawlers.time, "sleep", lambda s: seen.append(s))
    monkeypatch.setattr(crawlers.requests, "get", lambda *a, **k: replies.pop(0))

    got = crawlers.http_get("https://dixy.ru/sitemap-iblock-1.part1.xml")
    assert got.content == b"<good/>"
    assert seen == [2.0, 5.0], "паузы между попытками должны расти"


def test_after_all_tries_it_is_a_verdict(monkeypatch):
    monkeypatch.setattr(crawlers.time, "sleep", lambda s: None)
    monkeypatch.setattr(crawlers.requests, "get", lambda *a, **k: Reply(429))
    with pytest.raises(CrawlBlocked) as exc:
        crawlers.http_get("https://dixy.ru/x", retries=3)
    assert "429" in str(exc.value) and "попыток" in str(exc.value)


def test_a_key_is_not_a_matter_of_waiting(monkeypatch):
    """401 значит «нужен ключ». Ждать тут нечего, и повторять — только мешать сети."""
    tries = []
    monkeypatch.setattr(crawlers.time, "sleep", lambda s: tries.append(s))
    monkeypatch.setattr(crawlers.requests, "get", lambda *a, **k: Reply(401))
    with pytest.raises(CrawlBlocked):
        crawlers.http_get("https://example.org/x")
    assert not tries, "на 401 ждали, хотя ждать бессмысленно"


def test_the_network_itself_may_name_the_pause(monkeypatch):
    """Сказала Retry-After — слушаемся её, а не своих таблиц."""
    seen = []
    replies = [Reply(429, headers={"Retry-After": "7"}), Reply(200)]
    monkeypatch.setattr(crawlers.time, "sleep", lambda s: seen.append(s))
    monkeypatch.setattr(crawlers.requests, "get", lambda *a, **k: replies.pop(0))
    crawlers.http_get("https://example.org/x")
    assert seen == [7.0]


def test_an_hour_long_retry_after_is_capped(monkeypatch):
    """Заголовок бывает и на час — обход столько не ждёт."""
    seen = []
    replies = [Reply(503, headers={"Retry-After": "3600"}), Reply(200)]
    monkeypatch.setattr(crawlers.time, "sleep", lambda s: seen.append(s))
    monkeypatch.setattr(crawlers.requests, "get", lambda *a, **k: replies.pop(0))
    crawlers.http_get("https://example.org/x")
    assert seen == [60.0]


# ---------- карта сайта Дикси ----------
REAL = [
    "https://dixy.ru/product/draje-mms-s-molochnym-shokoladom-chernaya-smorodina-45g-2000650171/",
    "https://dixy.ru/product/konfety-krasnyy-oktyabr-krasnaya-shapochka-10f0049260/",
    "https://dixy.ru/product/chesnok-di00078769/",
    "https://dixy.ru/catalog/keshbek-30-monetami/",
]


def test_article_is_not_always_a_number():
    """У Дикси рядом с «2000650171» живут «10f0049260» и «di00078769».

    Первая версия разбора брала только цифры и теряла 387 товаров из 9 061 —
    это было видно лишь по числу строк, то есть никак.
    """
    assert from_address(REAL[0]).sku == "2000650171"
    assert from_address(REAL[1]).sku == "10f0049260"
    assert from_address(REAL[2]).sku == "di00078769"


def test_the_packaging_tail_is_not_taken_for_an_article():
    """«45g» в конце названия — фасовка, а не артикул: длина их и различает."""
    product = from_address(REAL[0])
    assert product.name.endswith("45g"), "фасовку отрезало вместе с артикулом"
    assert from_address("https://dixy.ru/product/moloko-1l/") is None


def test_non_product_addresses_are_skipped():
    assert from_address(REAL[3]) is None
    assert from_address("https://dixy.ru/") is None


def test_dixy_prices_are_still_not_invented():
    """Цен у Дикси нет нигде, и карта сайта их не приносит."""
    product = from_address(REAL[0])
    assert product.price is None and product.in_stock is None


# ---------- один плохой запрос не отменяет всю сеть ----------
def test_one_bad_brand_does_not_kill_the_whole_chain(monkeypatch):
    """Живой обход 19.09.2026 умер на бренде «Cillit Bang/Brillit».

    В его названии косая черта, движок ответил пятисотой, raise_for_status поднял
    исключение — и вся сеть отвалилась, хотя 13 600 позиций уже были собраны.
    Один плохой бренд из четырёхсот не стоит всей сети.
    """
    from app.catalog.crawlers.dixy import DixyCrawler

    crawler = DixyCrawler()
    good = {"products": [{"id": "1", "name": "Молоко", "price": "0.0", "available": True}],
            "totalHits": 1, "facets": []}

    def fake_page(term, offset, extra=None):
        if "Cillit" in term or (extra or {}).get("filter", "").find("Cillit") >= 0:
            raise requests.HTTPError("500 Server Error")
        return good

    monkeypatch.setattr(crawler, "_page", fake_page)

    seen, brands = set(), set()
    got = list(crawler._walk("Cillit Bang/Brillit", seen, brands))
    assert got == [], "плохой запрос всё-таки что-то отдал"

    # А следующий запрос идёт как ни в чём не бывало.
    got = list(crawler._walk("молоко", seen, brands))
    assert [p.sku for p in got] == ["1"], "после плохого запроса обход не продолжился"


def test_a_guard_page_still_stops_the_chain(monkeypatch):
    """Защита — другое дело: её отказ по-прежнему останавливает сеть.

    Смешать её с обычной пятисоткой значило бы молча обходить защиту пачкой
    повторов, а это уже не «один запрос не удался».
    """
    from app.catalog.crawlers.dixy import DixyCrawler

    crawler = DixyCrawler()

    def guarded(term, offset, extra=None):
        raise CrawlBlocked("движок ответил не JSON — похоже на страницу защиты")

    monkeypatch.setattr(crawler, "_page", guarded)
    with pytest.raises(CrawlBlocked):
        list(crawler._walk("молоко", set(), set()))


def test_the_bookmarklet_reads_russian_units():
    """Фасовка в названии: `\b` после русской «г» в JavaScript не срабатывает.

    Живая проверка 19.09.2026: из двенадцати позиций фасовка не разобралась ни у
    одной, хотя «200г» стояло в названиях прямо. Порядок единиц тоже важен —
    «кг» и «мл» обязаны идти перед «г» и «л», иначе «200мл» прочтётся как литры.
    """
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "docs", "grab-prices.src.js"), encoding="utf-8") as fh:
        src = fh.read()
    assert "(кг|мл|г|л)(?![а-яёa-z])" in src, "вернулась ловушка с \b или порядком единиц"
    assert r"\s*(кг|г|л|мл)\b" not in src
