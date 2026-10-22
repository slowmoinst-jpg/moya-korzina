"""Маршруты каталога Пятёрочки и Самоката через домашний выход владельца.

ЗАЧЕМ ЭТИ СТОРОЖА. У обеих сетей каталог берётся только витриной в браузере, а
серверу витрина отдаёт заглушку ServicePipe («Проверьте настройки интернета и VPN»).
Ходить туда можно через домашний выход владельца (app/homeexit.py) — но выход бывает
выключен, а адрес владельца после проверки «я не робот» трогать нельзя вовсе. Здесь
проверяется ровно это:

  · туннель отвечает — браузер идёт через него, и темп у обхода редкий, домашний;
  · туннеля нет — браузер не поднимается, причина сказана словами и уходит в
    CrawlBlocked, а обход не рушится: добор из базы и чеков остаётся;
  · сеть через дом показала проверку — пауза для всех (refusal_pause_min), и в неё
    никто не стучит;
  · сеть не настроена на дом — всё как было: браузер поднимается напрямую.

Живой туннель ноутбука владельца — его шаг, не теста: сеть здесь запрещена
(tests/conftest.py), браузер подменяется, а решение о маршруте берётся у homeexit.
"""
from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, homeexit  # noqa: E402
from app.catalog.crawlers import Pace  # noqa: E402
from app.catalog.crawlers.dixy import DixyCrawler  # noqa: E402
from app.catalog.crawlers.pyaterochka import PyaterochkaCrawler  # noqa: E402
from app.catalog.crawlers.samokat import SamokatCrawler  # noqa: E402
from app.catalog.model import ChainProduct, CrawlBlocked  # noqa: E402

PROXY = "socks5://172.17.0.1:1080"
SECTION = "molochnye-produkty-yaytsa"
CATEGORY = "molochnye-produkty-i-yayca"

PYATEROCHKA_CARD = {"url": "/product/moloko-prostokvashino--78001656/",
                    "text": "Молоко Простоквашино пастеризованное 2.5% 930 мл 89 99 ₽",
                    "image": None}
SAMOKAT_CARD = {"url": "/product/moloko-samokat-3-2-900-ml",
                "text": "Молоко Самокат 3.2% 900 мл 109 ₽", "image": None}


def crawler(code: str):
    return (PyaterochkaCrawler(sections=[SECTION]) if code == "pyaterochka"
            else SamokatCrawler(categories=[CATEGORY]))


def both():
    return [("pyaterochka", PyaterochkaCrawler(sections=[SECTION])),
            ("samokat", SamokatCrawler(categories=[CATEGORY]))]


# ---------- выбор маршрута ----------
def test_both_chains_are_wired_to_the_home_exit():
    """Маршрут уже прописан в config.yaml — сборщик обязан читать его оттуда же."""
    assert homeexit.configured("pyaterochka") == PROXY
    assert homeexit.configured("samokat") == PROXY


def test_a_live_tunnel_is_the_route(monkeypatch):
    """Туннель настроен и отвечает — маршрут через него, причина пуста.

    Пауза подменяется нарочно: без этого тест читал бы живое состояние data/pace и
    упал бы по чужой причине, когда у владельца появится файл паузы (F4-проверка
    18.10.2026).
    """
    monkeypatch.setattr(homeexit, "configured", lambda chain: PROXY)
    monkeypatch.setattr(homeexit, "alive", lambda url, timeout=5.0: True)
    monkeypatch.setattr(homeexit, "paused_until", lambda chain: None)

    assert homeexit.route("pyaterochka") == (PROXY, "")


def test_a_dead_tunnel_is_a_reason_and_not_a_silence(monkeypatch):
    """Выход настроен, но не отвечает: маршрута нет, и сказано, почему именно."""
    monkeypatch.setattr(homeexit, "configured", lambda chain: PROXY)
    monkeypatch.setattr(homeexit, "alive", lambda url, timeout=5.0: False)
    monkeypatch.setattr(homeexit, "paused_until", lambda chain: None)

    url, note = homeexit.route("pyaterochka")

    assert url is None
    assert PROXY in note and "не проводит трафик" in note


def test_a_chain_without_the_home_exit_goes_direct(monkeypatch):
    """Не настроена на дом — причина пуста, и маршрут прежний, прямой."""
    monkeypatch.setattr(homeexit, "configured", lambda chain: None)

    assert homeexit.route("magnit") == (None, "")


def test_a_paused_chain_is_not_even_pinged(monkeypatch):
    """Сеть через дом показала проверку — пауза, и туннель не щупаем вовсе."""
    monkeypatch.setattr(homeexit, "configured", lambda chain: PROXY)
    monkeypatch.setattr(homeexit, "paused_until", lambda chain: time.time() + 600)

    def no_ping(url, timeout=5.0):
        raise AssertionError("в паузу домашний выход не проверяем")

    monkeypatch.setattr(homeexit, "alive", no_ping)
    url, note = homeexit.route("pyaterochka")

    assert url is None and "проверку" in note and "пауза" in note


# ---------- темп через дом ----------
def test_the_crawl_pace_respects_the_home_exit():
    """Общий потолок остаётся, но сеть домашнего выхода идёт реже — как у коннекторов."""
    home = homeexit.pace_interval("pyaterochka")
    assert home > 0, "Пятёрочка не настроена на домашний выход — тест проверяет не то"
    assert homeexit.pace_rps("pyaterochka", general=1.0) == pytest.approx(1.0 / home)
    assert homeexit.pace_rps("lenta", general=1.0) == pytest.approx(1.0)

    assert crawler("pyaterochka").pace.gap == pytest.approx(home)
    assert crawler("samokat").pace.gap == pytest.approx(homeexit.pace_interval("samokat"))


def test_the_home_exit_pace_never_raises_the_general_ceiling(monkeypatch):
    """Домашний выход настроен чаще общего — потолок всё равно общий."""
    monkeypatch.setattr(homeexit, "pace_interval", lambda chain: 0.25)

    assert homeexit.pace_rps("pyaterochka", general=1.0) == pytest.approx(1.0)


# ---------- один потолок темпа на всё приложение (T6) ----------
def general_rate() -> float:
    return float(config.get("catalog.rate_limit_rps") or 1.0)


def test_the_pace_of_a_home_chain_is_the_home_one():
    """Дикси листает витрину через дом — темп у него домашний, а не общий.

    До T6 (18.10.2026) правило знали только коннекторы: Pace брал общий
    catalog.rate_limit_rps, и витрина Дикси шла через адрес владельца вдвое чаще
    разрешённого — ровно то, чего правила нагрузки не допускают.
    """
    home = homeexit.pace_interval("dixy")
    assert home > 0, "Дикси не настроена на домашний выход — тест проверяет не то"
    assert Pace(chain="dixy").gap == pytest.approx(home)
    assert DixyCrawler().pace.gap == pytest.approx(home), \
        "сборщик Дикси разошёлся с общим правилом темпа"


def test_a_chain_without_the_home_exit_keeps_the_general_ceiling(monkeypatch):
    """Сеть без домашнего выхода — прежний общий потолок, ничего не поменялось."""
    assert Pace(chain="lenta").gap == pytest.approx(1.0 / general_rate())

    monkeypatch.setattr(homeexit, "pace_interval", lambda chain: 0.0)
    assert Pace(chain="dixy").gap == pytest.approx(1.0 / general_rate())


def test_the_general_ceiling_holds_even_if_the_home_exit_is_faster(monkeypatch):
    """Домашний выход настроен чаще общего (4 запроса в секунду) — потолок общий."""
    monkeypatch.setattr(homeexit, "pace_interval", lambda chain: 0.25)

    assert Pace(chain="dixy").gap == pytest.approx(1.0 / general_rate())


def test_an_explicit_pace_is_not_slowed_by_the_home_exit():
    """Свой темп, заданный явно, правилом не перебивается."""
    assert Pace(rps=4.0, chain="dixy").gap == pytest.approx(0.25)


def test_a_pace_without_a_chain_is_what_it_was():
    """Поток без сети (пачки sitemap, свои запросы) живёт на общем темпе, как раньше."""
    assert Pace().gap == pytest.approx(1.0 / general_rate())


def test_a_zero_pace_means_not_set_and_keeps_the_home_ceiling():
    """rps=0 — «темп не задан», а не «общий потолок»: домашний потолок не теряется.

    Ноль falsy, и до правки (F4, adversarial-проверка 18.10.2026) `rps=0.0` тихо
    уходил на общий потолок, минуя домашний. Правило теперь однозначно: положительный
    rps — свой темп, ноль и отрицательное — «не задан», то есть общий потолок и, для
    сети домашнего выхода, ещё и домашний.
    """
    home = homeexit.pace_interval("dixy")
    assert home > 0, "Дикси не настроена на домашний выход — тест проверяет не то"
    assert Pace(rps=0.0, chain="dixy").gap == pytest.approx(home)
    assert Pace(rps=-1.0, chain="dixy").gap == pytest.approx(home)
    # Сеть без дома: «не задан» — это прежний общий потолок.
    assert Pace(rps=0.0, chain="lenta").gap == pytest.approx(1.0 / general_rate())
    assert Pace(rps=0.0).gap == pytest.approx(1.0 / general_rate())


# ---------- браузер: маршрут, отказ, падение ----------
def fake_playwright(page, seen: dict) -> SimpleNamespace:
    """Playwright, который ничего не запускает, но помнит, с каким прокси его позвали."""

    class Browser:
        def new_context(self, **kwargs):
            seen["context"] = kwargs
            return SimpleNamespace(new_page=lambda: page)

        def close(self):
            seen["closed"] = True

    class Chromium:
        def launch(self, **kwargs):
            seen["launch"] = kwargs
            return Browser()

    class Playwright:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return SimpleNamespace(sync_playwright=lambda: Playwright())


class FakePage:
    def __init__(self, body: str, cards: list[dict]) -> None:
        self.body = body
        self.cards = cards
        self.gotos: list[str] = []

    def goto(self, url, **kwargs):
        self.gotos.append(url)
        return SimpleNamespace(status=200)

    def inner_text(self, selector):
        return self.body

    def evaluate(self, script):
        return list(self.cards)

    def wait_for_timeout(self, ms):
        pass


def prepare(monkeypatch, code: str, body: str, cards: list[dict]):
    """Сборщик с подменённым браузером: живой Chromium в тестах не поднимаем."""
    from app.shopbrowser import store as shopstore

    page = FakePage(body, cards)
    seen: dict = {}
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_playwright(page, seen))
    monkeypatch.setattr(shopstore, "load", lambda name: None)
    crawler_ = crawler(code)
    monkeypatch.setattr(crawler_.pace, "wait", lambda: None)
    return crawler_, page, seen


@pytest.mark.parametrize("code,body,cards,key", [
    ("pyaterochka", "Сыр Российский 200 г 199 90 ₽", [PYATEROCHKA_CARD], "78001656"),
    # У Самоката артикула в адресе нет — ключом идёт весь слаг (замер 19.10.2026).
    ("samokat", "Круассан 120 г 159.90 ₽", [SAMOKAT_CARD], "moloko-samokat-3-2-900-ml"),
])
def test_a_live_tunnel_is_taken_by_the_browser(monkeypatch, code, body, cards, key):
    """Туннель отвечает — витрина читается через него, а не адресом сервера."""
    crawler_, page, seen = prepare(monkeypatch, code, body, cards)
    monkeypatch.setattr(homeexit, "route", lambda chain: (PROXY, ""))

    got = list(crawler_._crawl_browser(lambda message: None))

    assert [p.sku for p in got] == [key]
    assert got[0].price is not None
    assert seen["launch"]["proxy"] == {"server": PROXY}
    assert page.gotos, "витрину не открывали"


@pytest.mark.parametrize("code,body,cards", [
    ("pyaterochka", "Сыр 199 90 ₽", []),
    ("samokat", "Круассан 159.90 ₽", []),
])
def test_a_dead_tunnel_does_not_lift_the_browser(monkeypatch, code, body, cards):
    """Туннеля нет — браузер не поднимаем: серверу та же заглушка, а стук — лишний."""
    crawler_, page, seen = prepare(monkeypatch, code, body, cards)
    monkeypatch.setattr(homeexit, "route",
                        lambda chain: (None, f"домашний выход {PROXY} не отвечает"))

    got = list(crawler_._crawl_browser(lambda message: None))

    assert got == []
    assert "launch" not in seen, "браузер подняли без домашнего выхода"
    assert crawler_.route_note and "не отвечает" in crawler_.route_note


@pytest.mark.parametrize("code,body,cards", [
    ("pyaterochka", "Сыр 199 90 ₽", []),
    ("samokat", "Круассан 159.90 ₽", []),
])
def test_a_chain_not_on_the_home_exit_is_crawled_as_before(monkeypatch, code, body, cards):
    """Сеть не настроена на дом — прежнее поведение: браузер поднимается напрямую."""
    crawler_, page, seen = prepare(monkeypatch, code, body, cards)
    monkeypatch.setattr(homeexit, "route", lambda chain: (None, ""))

    list(crawler_._crawl_browser(lambda message: None))

    assert seen["launch"]["proxy"] is None


@pytest.mark.parametrize("code,body,cards", [
    ("pyaterochka", "Проверьте настройки интернета и VPN", []),
    ("samokat", "Пройдите проверку, разверните картинку", []),
])
def test_a_servicepipe_page_through_home_pauses_the_chain(monkeypatch, tmp_path, code, body, cards):
    """Проверка вместо витрины — пауза для всех, кто ходит через дом (refusal_pause_min)."""
    monkeypatch.setattr(homeexit, "STATE_DIR", str(tmp_path))
    crawler_, page, seen = prepare(monkeypatch, code, body, cards)
    # Маршрут настоящий: туннель отвечает, паузы пока нет — витрину открываем через дом.
    monkeypatch.setattr(homeexit, "configured", lambda chain: PROXY)
    monkeypatch.setattr(homeexit, "alive", lambda url, timeout=5.0: True)

    with pytest.raises(CrawlBlocked):
        list(crawler_._crawl_browser(lambda message: None))

    assert seen["launch"]["proxy"] == {"server": PROXY}, "через дом не пошли"
    assert homeexit.paused_until(code), "проверка на адресе владельца не записала паузу"
    assert "проверку" in crawler_.route_note
    # Следующий обход в паузу не стучит, хотя сам туннель отвечает.
    url, note = homeexit.route(code)
    assert url is None and "пауза" in note


# ---------- отказ не роняет обход ----------
@pytest.mark.parametrize("code", ["pyaterochka", "samokat"])
def test_a_dead_tunnel_still_gives_the_goods_from_the_base(monkeypatch, code):
    """Выхода нет — сеть обходится как раньше: добор из базы и чеков, без падения.

    Проверяются сами товары, а не только отсутствие исключения: обход обязан отдать
    ровно то, что дала база, с ценой и названием, и сказать причину словами.
    """
    crawler_ = crawler(code)
    note = f"домашний выход {PROXY} не отвечает"
    monkeypatch.setattr(homeexit, "route", lambda chain: (None, note))
    known = ChainProduct(sku="111", name="Молоко 1 л", price=79.9,
                         url="https://5ka.ru/product/moloko--111/")
    monkeypatch.setattr(crawler_, "_from_store_products", lambda: iter([known]))
    said: list[str] = []

    got = list(crawler_.crawl(said.append))

    assert got == [known], "товары из базы не вернулись"
    assert got[0].price == 79.9 and got[0].name == "Молоко 1 л"
    assert any(note in message for message in said), "причина отказа не сказана словами"


@pytest.mark.parametrize("code", ["pyaterochka", "samokat"])
def test_an_empty_crawl_names_the_reason(monkeypatch, code):
    """Пусто и в базе — в отказе сказано, чего не было: туннеля или доступа."""
    crawler_ = crawler(code)
    monkeypatch.setattr(homeexit, "route",
                        lambda chain: (None, f"домашний выход {PROXY} не отвечает"))
    monkeypatch.setattr(crawler_, "_from_store_products", lambda: iter([]))

    with pytest.raises(CrawlBlocked) as caught:
        list(crawler_.crawl())

    message = str(caught.value)
    assert "ServicePipe" in message and "не отвечает" in message


@pytest.mark.parametrize("code", ["pyaterochka", "samokat"])
def test_a_paused_chain_is_not_knocked_by_the_crawler(monkeypatch, code):
    """В паузу обход не поднимает браузер и говорит об этом, а не молчит."""
    crawler_ = crawler(code)
    monkeypatch.setattr(homeexit, "route",
                        lambda chain: (None, "через дом сеть недавно показала проверку"
                                             " «я не робот» — пауза ещё 30 мин"))
    monkeypatch.setattr(crawler_, "_from_store_products", lambda: iter([]))
    seen: dict = {}
    monkeypatch.setitem(sys.modules, "playwright.sync_api",
                        fake_playwright(FakePage("", []), seen))

    with pytest.raises(CrawlBlocked) as caught:
        list(crawler_.crawl())

    assert "пауза" in str(caught.value)
    assert "launch" not in seen, "в паузу браузер всё-таки подняли"
