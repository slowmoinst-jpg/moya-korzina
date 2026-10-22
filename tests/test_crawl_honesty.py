"""Честность обхода: страница защиты видна, а неполный обход не гасит живые товары.

ДВА ДЕФЕКТА, НАЙДЕННЫХ ЖИВЫМ ЗАМЕРОМ 18.10.2026.

1. Пятёрочка и Самокат закрыты для сервера даже через домашний выход владельца: 5ka.ru
   отвечает «Проблемы со связью. Проверьте настройки интернета и VPN … IP: 203.0.113.7»,
   samokat.ru — «Forbidden». Обе страницы рисуют свой текст СКРИПТОМ: сразу после
   перехода в body НОЛЬ символов, фраза появляется примерно через три секунды. Прежняя
   проверка читала текст в тот же миг, ничего не находила и молча обходила все разделы
   (12 у Пятёрочки, 12 у Самоката) по заглушке в 1,8 КБ, а плановому обходу доставалось
   «витрина пуста» без причины.

2. Fix Price 16.10.2026: витрина отдалась рывками, увидено 1 092 товара, обход записан
   «ok» — и база пометила пропавшими 5 400 живых строк. Сборщик обязан сказать, что
   обход неполный, а база — не объявлять пропавшим то, чего в обходе не искали.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import refresh, store  # noqa: E402
from app.catalog.crawlers import blocked_page  # noqa: E402
from app.catalog.crawlers.fixprice import FixPriceCrawler  # noqa: E402
from app.catalog.model import ChainProduct, Crawler  # noqa: E402


class FakePage:
    """Страница: текст появляется не сразу, как у настоящей страницы защиты."""

    def __init__(self, texts, html=""):
        self.texts = list(texts)
        self._html = html
        self.waits = 0

    def inner_text(self, selector):
        if len(self.texts) > 1:
            return self.texts.pop(0)
        return self.texts[0] if self.texts else ""

    def wait_for_timeout(self, ms):
        self.waits += 1

    def content(self):
        return self._html


VPN_PAGE = ("Проблемы со связью. Проверьте настройки интернета и VPN  Дата и время: "
            "2026-10-18 18:15:20 +0000  IP: 203.0.113.7  ID: KFjeRIEYbeA1  Origin: "
            "https://5ka.ru  Если вы считаете блокировку ложной, скопируйте отчет")
FORBIDDEN_PAGE = ("Forbidden  Datetime: 2026-10-18 18:15:58 +0000  IP: 203.0.113.7  "
                  "If you are not a bot, please copy the report and send it to our support team.")
CATALOG = "Молоко 3,2% 1 л 89,99 ₽ " + "Сыр Российский 200 г 219,00 ₽ " * 5


def test_blocked_page_sees_the_vpn_page_that_renders_late():
    """Текст приходит не с первого чтения — проверка обязана дождаться его."""
    page = FakePage(["", "", VPN_PAGE])

    assert blocked_page(page) == "проверьте настройки интернета и vpn"
    assert page.waits == 2, "проверка ждала, а не прочитала пустой body один раз"


def test_blocked_page_sees_forbidden_by_text():
    """Страница «Forbidden» Самоката — тоже страница защиты, а не витрина."""
    page = FakePage([FORBIDDEN_PAGE])

    found = blocked_page(page)

    assert found is not None
    assert found in ("forbidden", "if you are not a bot"), found


def test_blocked_page_sees_servicepipe_only_in_markup():
    """Фраза бывает и в разметке, а не в видимом тексте."""
    page = FakePage([CATALOG], html="<html><body><div id='servicepipe-check'></div></body></html>")

    assert blocked_page(page) == "servicepipe"


def test_blocked_page_says_nothing_about_a_real_catalog():
    page = FakePage([CATALOG])

    assert blocked_page(page) is None
    assert page.waits == 0, "на живой витрине ждать нечего — текст уже есть"


def test_both_blocked_chains_say_why_in_the_status():
    """Причина отказа доходит до статуса обхода, а не теряется по дороге.

    Живой прогон 18.10.2026 (боевой сервер, домашний выход владельца): обход Пятёрочки
    кончился записью «витрина закрыта защитой ServicePipe, а в базе нет сохранённых
    товаров» — без единого слова о том, ЧТО именно показала сеть. Так отказ туннеля,
    отказ адреса владельца и отказ сервера выглядят одинаково.
    """
    from app.catalog.crawlers.pyaterochka import PyaterochkaCrawler
    from app.catalog.crawlers.samokat import SamokatCrawler

    for cls in (PyaterochkaCrawler, SamokatCrawler):
        crawler = cls([])
        crawler.route_note = "через дом витрина показала проверку «forbidden»"
        reason = crawler._closed_reason()

        assert "forbidden" in reason, reason
        assert crawler.name.split()[0].lower() in reason.lower(), reason


def test_blocked_chain_raises_with_the_reason(monkeypatch):
    """Пустой обход закрытой сети — это CrawlBlocked с причиной, а не тихая пустота."""
    from app.catalog.crawlers.pyaterochka import PyaterochkaCrawler
    from app.catalog.model import CrawlBlocked

    crawler = PyaterochkaCrawler([])

    def browser_route(say):
        # Так делает _crawl_browser, наткнувшись на страницу защиты.
        crawler.route_note = "через дом витрина показала проверку «forbidden»"
        return iter(())

    monkeypatch.setattr(crawler, "_crawl_browser", browser_route)
    monkeypatch.setattr(crawler, "_from_store_products", lambda: iter([]))

    with pytest.raises(CrawlBlocked) as caught:
        list(crawler.crawl())

    assert "forbidden" in str(caught.value)


class FakeCrawler(Crawler):
    """Сборщик, который собрал часть каталога и честно об этом сказал."""

    code = "fixprice"

    def __init__(self, incomplete: bool):
        self.incomplete = incomplete
        self.incomplete_note = "каталог неполный, оборвались разделы: produkty#3" if incomplete else ""

    def crawl(self, progress=None):
        yield ChainProduct(sku="1", name="Сахар 1 кг", price=79.0)


def _run(monkeypatch, tmp_path, incomplete: bool) -> tuple[dict, dict]:
    """Прогон run_chain на своей базе: смотрим, объявлялись ли пропавшие."""
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    called = {}
    real = store.retire_unseen

    def spy(chain, seen_at):
        called["chain"] = chain
        return real(chain, seen_at)

    monkeypatch.setattr(store, "retire_unseen", spy)
    result = refresh.run_chain(FakeCrawler(incomplete))
    return result, called


def test_incomplete_crawl_does_not_call_the_base_lost(monkeypatch, tmp_path):
    result, called = _run(monkeypatch, tmp_path, incomplete=True)

    assert result["status"] == "partial"
    assert "неполный" in result["note"]
    assert called == {}, "неполный обход не имеет права объявлять пропавших"


def test_complete_crawl_still_marks_the_unseen_gone(monkeypatch, tmp_path):
    result, called = _run(monkeypatch, tmp_path, incomplete=False)

    assert result["status"] == "ok"
    assert called == {"chain": "fixprice"}, "полный обход пропавших по-прежнему считает"


def test_fixprice_says_it_is_incomplete():
    """Признак ставит сам сборщик: без него база считает обход полным."""
    crawler = FixPriceCrawler()
    assert crawler.incomplete is False and crawler.incomplete_note == ""

    class Page:
        def wait_for_timeout(self, ms):
            pass

    page = Page()
    # Раздел, который не открылся, — ровно тот случай, что был 16.10.2026.
    crawler._sections = lambda p: ["produkty-i-napitki"]
    crawler._page = lambda p, section, number: None      # страница не открылась
    list(crawler._walk(page, None))

    assert crawler.incomplete is True
    assert "produkty-i-napitki" in crawler.incomplete_note
