"""Живая цена Дикси: карточка через домашний выход настоящим браузером, без выхода — как раньше.

Сервер в дата-центре dixy.ru не пускает: 403 «Поставь галочку в поле „Я не робот“» или
заглушка. Через домашний интернет владельца карточка приходит целиком (замер
27.09.2026), и в ней тот же JSON товара, что в листинге витрины. Простые запросы через
дом Qrator в тот же день пометил проверкой после двенадцати карточек подряд, поэтому
через дом коннектор ходит браузером, как сборщик каталога. Здесь проверяется, что
браузер получает выход из app/homeexit.py, что без выхода запрос уходит напрямую, как
до выхода, и что проверка через дом ставит паузу, а не прилипает к кэшу.

Карточка — фрагмент живой страницы (tests/fixtures/dixy_card_live.html). В сеть тесты не
ходят (tests/conftest.py): requests.get и Playwright подменены.
"""
from __future__ import annotations

import os
import sys
import time
import types
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import requests  # noqa: E402

from app import config, homeexit, repo  # noqa: E402
from app.catalog import store as catalog  # noqa: E402
from app.connectors import base, cache, dixy, get_connector, stub  # noqa: E402
from app.db import init_db  # noqa: E402
from app.models import Product  # noqa: E402

CARD = os.path.join(ROOT, "tests", "fixtures", "dixy_card_live.html")
OLD_CARD = os.path.join(ROOT, "tests", "fixtures", "dixy_product.html")
SKU = "2000329470"
URL = "https://dixy.ru/product/avokado-khass-v-upakovke-700-g-2000329470/"
HOME = "socks5://172.17.0.1:1080"
QRATOR = ("<html><head><title>403</title></head><body><h1>Поставь галочку в поле «Я не робот»</h1>"
          "<p>И продолжай пользоваться сайтом.</p></body></html>")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class Answer:
    """Ответ сайта, как его отдаёт requests: код, заголовки, текст."""

    def __init__(self, status: int = 200, text: str = "", ctype: str = "text/html; charset=UTF-8"):
        self.status_code = status
        self.text = text
        self.headers = {"content-type": ctype}

    def json(self):
        raise ValueError("не JSON")


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(cache, "PACE_DIR", str(tmp_path / "pace"))
    monkeypatch.setattr(homeexit, "STATE_DIR", str(tmp_path / "pace"))
    cache.reset_throttle()
    base.reset_failures()
    stub.reload_rows()
    # темп здесь не проверяется — ждать по две секунды на карточку незачем
    monkeypatch.setattr(cache, "throttle", lambda store_code: None)
    monkeypatch.setattr(catalog, "product_url", lambda chain, sku: None)
    monkeypatch.setattr(dixy, "SETTLE_MS", 0)
    yield
    base.reset_failures()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "dixy.db"))
    init_db()


@pytest.fixture
def home_exit(monkeypatch):
    """Дикси в списке домашнего выхода, как в config.yaml. up=True — туннель жив."""
    state = {"up": True}
    real = config.get
    monkeypatch.setattr(config, "get", lambda key, default=None: (
        {"proxy": HOME, "chains": ["dixy", "magnit"], "rate_limit_rps": 0.5, "refusal_pause_min": 60}
        if key == "connectors.home_exit" else real(key, default)))
    monkeypatch.setattr(homeexit, "alive", lambda url, timeout=5.0: state["up"])
    return state


@pytest.fixture
def site(monkeypatch):
    """Подменённый requests.get — дорога напрямую: записывает, куда пошёл запрос."""
    calls: list[dict] = []
    answers: list[Answer] = []

    def fake_get(url, params=None, **kwargs):
        calls.append({"url": url, "params": params, **kwargs})
        return answers.pop(0) if answers else Answer(200, _read(CARD))

    monkeypatch.setattr(requests, "get", fake_get)
    return calls, answers


@pytest.fixture
def browser(monkeypatch):
    """Подменённый Playwright — дорога через дом.

    world["launch"] — с чем запущен Chromium, world["fetch"] — какие карточки спрошены
    изнутри страницы, world["answers"] — что на них ответить (по умолчанию живая карточка),
    world["home"] — видимый текст главной.
    """
    world: dict = {"launch": [], "goto": [], "fetch": [], "answers": [], "closed": 0,
                   "home": "Дикси — продукты рядом с домом. Каталог. Акции."}

    class Page:
        def goto(self, url, **kwargs):
            world["goto"].append(url)

        def wait_for_timeout(self, ms):
            pass

        def inner_text(self, selector):
            return world["home"]

        def evaluate(self, script, url):
            world["fetch"].append(url)
            return list(world["answers"].pop(0)) if world["answers"] else [200, _read(CARD)]

    class Context:
        def route(self, pattern, handler):
            world["route"] = handler

        def new_page(self):
            return Page()

    class Browser:
        def new_context(self, **kwargs):
            return Context()

        def close(self):
            world["closed"] += 1

    class Chromium:
        def launch(self, **kwargs):
            world["launch"].append(kwargs)
            return Browser()

    class Playwright:
        chromium = Chromium()

        def stop(self):
            pass

    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: types.SimpleNamespace(start=lambda: Playwright())
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    return world


def _mapped(url: str = URL, sku: str = SKU) -> int:
    """Товар человека, опознанный у Дикси, — как после сопоставления с каталогом."""
    store = repo.get_store("dixy")
    product_id = repo.upsert_product(Product(None, "Авокадо Хасс 700 г", unit="pcs"))
    sp_id = repo.upsert_store_product(store.id, sku, "Авокадо Хасс в упаковке, 700 г", url=url)
    repo.confirm_mapping(product_id, sp_id, confirmed=True)
    return product_id


# ---------- разбор карточки ----------
def test_the_live_card_gives_price_and_availability():
    """Цена сейчас и «раскупили» — из JSON товара, того же, что в листинге витрины."""
    got = dixy.read_card(_read(CARD))

    assert got["price"] == 438.90
    assert got["in_stock"] is False, "canBuy=false, bskState «not» — сейчас не купить"
    assert got["unit"] == "pcs" and got["name"] == "Авокадо Хасс в упаковке, 700 г"


def test_the_old_markup_still_reads_when_there_is_no_json():
    """Страница без JSON (слепок из веб-архива) — прежний разбор вёрстки, наличие неизвестно."""
    got = dixy.read_card(_read(OLD_CARD))

    assert got["price"] == 420.90 and got["in_stock"] is None


def test_a_hidden_price_is_an_answer_not_a_broken_parse():
    page = _read(CARD).replace('"hidePrice": false', '"hidePrice": true')

    assert dixy.read_card(page)["price"] is None


def test_a_refusal_page_is_not_a_card():
    assert dixy.is_card(_read(CARD)) and dixy.is_card(_read(OLD_CARD))
    assert not dixy.is_card(QRATOR)
    assert not dixy.is_card("Не удалось загрузить сайт. Возможно, у вас включён ВПН")
    assert not dixy.is_card("")


def test_only_the_named_address_opens_a_card():
    """Голый /product/<артикул>/ отвечает 404 — его собирает движок каталога."""
    assert dixy.is_product_url(URL, SKU)
    assert not dixy.is_product_url("https://dixy.ru/product/2000329470/", SKU)
    assert dixy.is_bare_url("https://dixy.ru/product/2000329470", SKU)
    assert not dixy.is_bare_url("https://dixy.ru/catalog/ovoshchi-frukty/ekzotika/2000329470/", SKU)


# ---------- дорога через домашний выход ----------
def test_the_card_goes_through_the_home_exit_in_a_real_browser(db, home_exit, site, browser):
    calls, _ = site
    _mapped()

    snaps = get_connector("dixy").get_prices([SKU])

    assert [k["proxy"] for k in browser["launch"]] == [{"server": HOME}], \
        "выход — из homeexit.for_chain, тот же, что у сборщика каталога"
    assert browser["goto"] == ["https://dixy.ru/"], "сначала главная: скрипты Qrator ставят куки"
    assert browser["fetch"] == [URL], "карточка — запросом изнутри страницы"
    assert calls == [], "простым запросом через дом не ходим: Qrator метит им адрес"
    assert browser["closed"] == 1, "браузер живёт один заход"
    snap = snaps[0]
    assert (snap.sku, snap.price, snap.in_stock, snap.source) == (SKU, 438.90, False, None)


def test_pictures_do_not_go_through_home(db, home_exit, site, browser):
    _mapped()
    get_connector("dixy").get_prices([SKU])
    aborted, passed = [], []

    def route(kind):
        request = types.SimpleNamespace(resource_type=kind)
        return types.SimpleNamespace(request=request, abort=lambda: aborted.append(kind),
                                     continue_=lambda: passed.append(kind))

    for kind in ("image", "media", "font", "document", "script", "fetch"):
        browser["route"](route(kind))

    assert aborted == ["image", "media", "font"]
    assert passed == ["document", "script", "fetch"], "скрипты нужны: без них Qrator не пустит"


def test_without_the_exit_dixy_goes_direct_as_before(db, home_exit, site, browser):
    """Туннеля нет — простой запрос напрямую, как до домашнего выхода, и ничего не падает."""
    calls, _ = site
    home_exit["up"] = False
    _mapped()

    snaps = get_connector("dixy").get_prices([SKU])

    assert [c["url"] for c in calls] == [URL] and calls[0].get("proxies") is None
    assert browser["launch"] == [], "без выхода браузер не поднимаем"
    assert [s.price for s in snaps] == [438.90], "откуда ответил сайт — оттуда и цена"


def test_a_check_through_home_pauses_the_chain_for_every_process(db, home_exit, site, browser):
    """Проверка «я не робот» через дом — пауза: повторный стук продлил бы метку на адресе."""
    browser["answers"].append((403, QRATOR))
    second = "2000301583"
    _mapped()
    _mapped(url="https://dixy.ru/product/avokado-khaas-v-upakovke-2sht-2000301583/", sku=second)

    assert get_connector("dixy").get_prices([SKU, second]) == []
    assert browser["fetch"] == [URL], "после проверки вторую карточку не спрашиваем"
    assert homeexit.paused_until("dixy"), "пауза записана для всех процессов"

    assert get_connector("dixy").get_prices([SKU, second]) == []
    assert len(browser["launch"]) == 1, "на паузе браузер через дом не поднимается вовсе"


def test_a_check_on_the_home_page_stops_before_any_card(db, home_exit, site, browser):
    browser["home"] = "Поставь галочку в поле «Я не робот» И продолжай пользоваться сайтом."
    _mapped()

    assert get_connector("dixy").get_prices([SKU]) == []
    assert browser["fetch"] == [] and homeexit.paused_until("dixy")


def _refused_minutes_ago(minutes: float) -> None:
    with open(homeexit._refused_path("dixy"), "w", encoding="utf-8") as fh:
        fh.write(repr(time.time() - minutes * 60))


def test_the_pause_ends_and_the_card_is_asked_again(db, home_exit, site, browser):
    browser["answers"].append((403, QRATOR))
    _mapped()
    get_connector("dixy").get_prices([SKU])
    base.reset_failures()

    _refused_minutes_ago(61)                       # час паузы прошёл
    snaps = get_connector("dixy").get_prices([SKU])

    assert [s.price for s in snaps] == [438.90], "отказ в кэш не лёг — после паузы карточка берётся"


def test_a_lost_connection_is_not_a_check(db, home_exit, site, browser, monkeypatch):
    """Браузер не открылся (туннель мигнул) — не метка на адресе, паузы нет."""
    def broken(self, proxy_url):
        raise TimeoutError("Timeout 60000ms exceeded")

    monkeypatch.setattr(dixy._Browser, "__init__", broken)
    _mapped()

    assert get_connector("dixy").get_prices([SKU]) == []
    assert homeexit.paused_until("dixy") is None


def test_a_refusal_does_not_stick_in_the_cache(db, home_exit, site, browser):
    """Напрямую — отказ; поднялся туннель — следующий заход берёт карточку, а не отказ."""
    calls, answers = site
    _mapped()
    home_exit["up"] = False
    answers.append(Answer(403, QRATOR))
    assert get_connector("dixy").get_prices([SKU]) == []
    assert homeexit.paused_until("dixy") is None, "отказ серверу — не метка на адресе владельца"

    home_exit["up"] = True
    snaps = get_connector("dixy").get_prices([SKU])

    assert len(calls) == 1 and browser["fetch"] == [URL]
    assert [s.price for s in snaps] == [438.90]


def test_a_stub_page_is_a_refusal_too_and_the_model_is_not_asked(db, home_exit, site, monkeypatch):
    """Заглушка с кодом 200 — не карточка: вёрстку «чинить» моделью незачем, её там нет."""
    from app.connectors import smart_extract

    calls, answers = site
    home_exit["up"] = False
    answers.append(Answer(200, "Не удалось загрузить сайт. Возможно, у вас включён ВПН"))
    asked = []
    monkeypatch.setattr(smart_extract, "price_from_html", lambda *a, **k: asked.append(a) or None)
    _mapped()

    assert get_connector("dixy").get_prices([SKU]) == []
    assert asked == [], "модель — для сменившейся вёрстки карточки, а не для страницы отказа"


def test_a_direct_refusal_ends_the_visit(db, home_exit, site):
    calls, answers = site
    home_exit["up"] = False
    answers.append(Answer(403, QRATOR))
    second = "2000301583"
    _mapped()
    _mapped(url="https://dixy.ru/product/avokado-khaas-v-upakovke-2sht-2000301583/", sku=second)

    assert get_connector("dixy").get_prices([SKU, second]) == []
    assert len(calls) == 1, "после отказа сайт в этом заходе больше не спрашиваем"


def test_cached_cards_need_no_browser(db, home_exit, site, browser, monkeypatch):
    """Вторую корзину с тем же товаром отдаёт кэш: браузер через дом не поднимается."""
    _mapped()
    clock = {"now": datetime(2026, 9, 27, 12, 0, 0)}
    monkeypatch.setattr(dixy, "datetime", type("Clock", (), {"now": staticmethod(lambda: clock["now"])}))
    first = get_connector("dixy").get_prices([SKU])[0]
    clock["now"] = datetime(2026, 9, 27, 13, 0, 0)

    again = get_connector("dixy").get_prices([SKU])[0]

    assert len(browser["launch"]) == 1 and browser["fetch"] == [URL]
    assert first.fetched_at == again.fetched_at == "2026-09-27T12:00:00", \
        "снимок из кэша несёт время карточки, а не «сейчас»"


def test_the_search_engine_does_not_go_through_home(db, home_exit, site, browser):
    """Diginetica отвечает серверу сама — через адрес владельца её не гоняем."""
    calls, answers = site
    answers.append(Answer(200, "", "application/json"))
    answers[0].json = lambda: {"products": [{"id": SKU, "name": "Авокадо Хасс 700 г",
                                             "categories": [{"link_url": "/catalog/ovoshchi-frukty/ekzotika/"}]}]}

    found = get_connector("dixy").search("авокадо", limit=1)

    assert found and found[0].sku == SKU
    assert calls[0]["url"] == dixy.SEARCH_URL and calls[0].get("proxies") is None
    assert browser["launch"] == []


def test_a_bare_address_is_not_asked_but_the_catalog_one_is(db, home_exit, site, browser, monkeypatch):
    """Движок дал /product/<артикул>/ (404), а каталог уже знает рабочий адрес с витрины."""
    _mapped(url="https://dixy.ru/product/2000329470")

    assert get_connector("dixy").get_prices([SKU]) == []
    assert browser["launch"] == [], "по голому адресу сайт ответил бы 404 — браузер не нужен"

    monkeypatch.setattr(catalog, "product_url", lambda chain, sku: URL)
    snaps = get_connector("dixy").get_prices([SKU])

    assert browser["fetch"] == [URL] and snaps[0].price == 438.90


# ---------- темп, пауза и предохранитель ----------
def test_requests_through_home_keep_a_slower_pace(home_exit):
    assert homeexit.pace_interval("dixy") == pytest.approx(2.0)
    assert homeexit.pace_interval("vkusvill") == 0.0, "остальные сети идут общим темпом"


def test_a_pause_from_a_skewed_clock_does_not_hold_the_chain(home_exit):
    homeexit.note_refusal("dixy")
    assert homeexit.paused_until("dixy")

    _refused_minutes_ago(-24 * 60)                 # отметка «из завтра» — сбитые часы
    assert homeexit.paused_until("dixy") is None


def test_the_breaker_pauses_a_chain_instead_of_switching_it_off_for_good():
    """Три неудачи — пауза; вышла пауза — одна попытка, и неудача снова выключает сеть."""
    for _ in range(base._fail_limit()):
        base.note_failure("dixy")
    assert base.api_disabled("dixy")

    base._FAILED_AT["dixy"] -= base._cooldown_sec() + 1      # пауза прошла
    assert not base.api_disabled("dixy"), "ноутбук проснулся — сеть снова спрашиваем"

    base.note_failure("dixy")
    assert base.api_disabled("dixy"), "не вышло — новая пауза, без трёх лишних таймаутов"
