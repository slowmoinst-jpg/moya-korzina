"""Передача корзины в магазин: способы для сетей без MCP и наполнение корзины.

ДВЕ ПОЛОВИНЫ ОДНОЙ ДОРОГИ, и они разные по смыслу.

ПЕРВАЯ — способы отдать корзину сети, которая её не принимает. Магнит, Пятёрочка
и Дикси не берут готовую корзину: у первого в диплинках объявлены только карточки
товаров, вторые два закрыты наглухо. Здесь проверяется, что мы это честно
отражаем и не обещаем больше, чем можем. Рядом — прайс-лист руками: последний
источник цен для сетей без каталога.

ВТОРАЯ (ниже, со слов «наполнение корзины») — передача посчитанного в кабинет
человека браузером на нашем сервере. Там стережётся всё, что ломается ТИХО и
стоит человеку денег или времени у полки:

    ДВОЙНАЯ КОРЗИНА. Передача идёт минуту и больше. Пока экран стоит неподвижно,
    человек уверен, что нажатие не сработало, и нажимает ещё раз — то есть кладёт
    себе всё дважды. Отсюда два сторожа: ход виден живьём, а вторая передача, пока
    идёт первая, не пускается ни экраном, ни пускателем.

    ПОВТОР, КОТОРЫЙ ПОРТИТ КОРЗИНУ. «Повторить» обязано доложить ТОЛЬКО то, что не
    легло. Прогон всего наряда заново кладёт удавшееся второй раз — это прямая
    порча чужой корзины, и заметит её человек уже на кассе.

    ЗАПЕРТАЯ КНОПКА. Сервер могли перезапустить посреди наряда, и отметка «идёт»
    осталась бы навсегда. Тогда кнопка не нажмётся уже никогда, а исправить это
    человеку нечем.

    ПОТЕРЯННОЕ ОКРУГЛЕНИЕ. 0,7 кг сыра витрина кладёт одной упаковкой. Сказать об
    этом надо и ДО передачи, и в отчёте — из чека человек это уже не поправит.

    ГРАНИЦА. Передача кончается наполненной корзиной. Ни «заказать», ни «оплатить»
    на этих экранах быть не должно: это деньги человека.

ЖИВОГО БРАУЗЕРА ЗДЕСЬ НЕТ. Chromium поднимается на сервере, и поднимать его в
наборе тестов значило бы ходить в сети магазинов на каждой сборке. Вместо него —
поддельная витрина: карточки с текстом и кнопками, по которым видно ровно то, что
видит настоящий обход (tests/conftest.py на всякий случай запрещает и сеть).
"""
from __future__ import annotations

import os
import re
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, handover, pricelist, repo, users  # noqa: E402
from app.cartplan import CartPlan, PlanLine  # noqa: E402
from app.db import init_db  # noqa: E402
from app.models import Product  # noqa: E402
from app.shopbrowser import cart  # noqa: E402


class Line:
    """То немногое, что handover берёт от строки варианта."""

    def __init__(self, product_id, name, qty, price=0.0):
        self.product_id, self.product_name, self.qty, self.price = product_id, name, qty, price


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "ho.db"))
    init_db()
    return tmp_path


@pytest.fixture
def mapped(db):
    """Один товар, связанный с Магнитом и с Пятёрочкой."""
    milk = repo.upsert_product(Product(None, "Молоко 1 л", unit="pcs"))
    magnit = repo.get_store("magnit")
    sp = repo.upsert_store_product(magnit.id, "1000013732", "Молоко Кубанский молочник 1 л",
                                   url="https://magnit.ru/product/1000013732-moloko")
    repo.confirm_mapping(milk, sp)
    five = repo.get_store("pyaterochka")
    sp5 = repo.upsert_store_product(five.id, "list-1", "Молоко 1 л")
    repo.confirm_mapping(milk, sp5)
    return {"milk": milk}


# ---------- сила способа ----------
def test_magnit_hands_over_item_links_not_a_basket(mapped, monkeypatch):
    """У Магнита в диплинках объявлен только /product/* — значит по одной карточке."""
    monkeypatch.setattr(config, "get", lambda key, default=None:
                        "019652" if key == "connectors.magnit_shop_code" else default)
    result = handover.for_store("magnit", [Line(mapped["milk"], "Молоко 1 л", 2)])

    assert result.kind == handover.ITEMS
    assert result.ready
    assert result.items[0].url.startswith("https://magnit.ru/product/1000013732")


def test_magnit_link_carries_the_chosen_shop(mapped, monkeypatch):
    """Без кода магазина ссылка откроет чужую точку с чужими ценами."""
    monkeypatch.setattr(config, "get", lambda key, default=None:
                        "019652" if key == "connectors.magnit_shop_code" else default)
    url = handover.for_store("magnit", [Line(mapped["milk"], "Молоко", 1)]).items[0].url

    assert "shopCode=019652" in url and "shopType=dostavka" in url


def test_magnit_link_is_left_alone_when_shop_is_not_configured(mapped, monkeypatch):
    monkeypatch.setattr(config, "get", lambda key, default=None: None)
    url = handover.for_store("magnit", [Line(mapped["milk"], "Молоко", 1)]).items[0].url

    assert "shopCode" not in url


def test_pyaterochka_gets_search_links_not_a_dead_list(mapped):
    """Пятёрочка не принимает ни корзину, ни карточки — но поиск по названию принимает.

    Так было до 15.09.2026: человеку доставался список текстом, и каждую позицию он
    набирал в магазине руками. Это и было самое слабое место повтора корзины.
    Поиск устойчив тем, что не требует ни артикула, ни разбора вёрстки, ни нашего
    доступа к каталогу, — а именно об закрытый каталог разбивалось всё остальное.
    """
    result = handover.for_store("pyaterochka", [Line(mapped["milk"], "Молоко 1 л", 2)])

    assert result.kind == handover.SEARCH
    assert all(item.url for item in result.items), "строк без кнопки быть не должно"
    assert "5ka.ru" in result.items[0].url
    # список текстом никуда не делся: он страховка на случай пустого поиска
    assert "Молоко 1 л" in handover.as_text(result)


def test_vkusvill_falls_back_to_a_list_when_store_is_silent(db, monkeypatch):
    """Магазин не ответил — человек всё равно уходит со списком, а не с пустым экраном."""
    from app import service

    monkeypatch.setattr(service, "cart_link", lambda code, lines: None)
    result = handover.for_store("vkusvill", [Line(1, "Молоко", 1)])

    assert result.kind == handover.LIST and result.items


def test_handover_text_shows_units(mapped):
    result = handover.for_store("pyaterochka", [Line(mapped["milk"], "Яблоки", 1.5)])
    assert "1.5" in handover.as_text(result) or "1,5" in handover.as_text(result)


# ---------- прайс-лист руками ----------
def test_pricelist_reads_russian_headers_and_comma_decimals():
    rows = pricelist.parse("Название;Цена;Единица\nМолоко 1 л;79,90;шт\nЯблоки;149;кг")

    assert [r["name"] for r in rows] == ["Молоко 1 л", "Яблоки"]
    assert rows[0]["price"] == 79.90 and rows[0]["unit"] == "pcs"
    assert rows[1]["unit"] == "kg"


def test_pricelist_reads_english_headers_and_commas_as_separator():
    rows = pricelist.parse("name,price,unit\nMilk,79.90,pcs")
    assert rows[0]["price"] == 79.90


def test_pricelist_without_header_takes_first_two_columns():
    rows = pricelist.parse("Молоко 1 л;79,90\nЯблоки;149")
    assert len(rows) == 2 and rows[0]["price"] == 79.90


def test_pricelist_skips_junk_rows_instead_of_failing():
    rows = pricelist.parse("Название;Цена\n;100\nМолоко;\nМолоко;0\nМолоко;79,90")
    assert [r["name"] for r in rows] == ["Молоко"]


def test_pricelist_gives_every_row_an_sku():
    rows = pricelist.parse("Название;Цена\nМолоко;79,90\nХлеб;45")
    assert len({r["sku"] for r in rows}) == 2


def test_pricelist_keeps_given_sku():
    rows = pricelist.parse("Название;Цена;Единица;Артикул\nМолоко;79,90;шт;МК-17")
    assert rows[0]["sku"] == "МК-17"


def test_pricelist_roundtrip_through_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pricelist, "DIR", str(tmp_path))
    saved = pricelist.save("pyaterochka", "Название;Цена;Единица\nМолоко 1 л;79,90;шт\nЯблоки;149;кг")

    assert saved == 2
    rows = pricelist.load("pyaterochka")
    assert [r["price"] for r in rows] == [79.90, 149.0]
    assert pricelist.updated_at("pyaterochka")
    assert pricelist.remove("pyaterochka") and pricelist.load("pyaterochka") == []


def test_connector_prefers_pricelist_and_dates_it(db, tmp_path, monkeypatch):
    """Прайс покрывает и то, чего человек не покупал, — и несёт свою дату."""
    from app.connectors import get_connector

    monkeypatch.setattr(pricelist, "DIR", str(tmp_path / "prices"))
    pricelist.save("dixy", "Название;Цена;Единица;Артикул\nГречка 900 г;129,90;шт;greka")
    snap = get_connector("dixy").get_prices(["greka"])[0]

    assert snap.price == 129.90
    assert snap.fetched_at == pricelist.updated_at("dixy"), "дата прайса обязана дойти до расчёта"


def test_connector_search_finds_pricelist_rows(db, tmp_path, monkeypatch):
    """У Пятёрочки живого каталога нет вовсе — её поиск идёт по прайсу."""
    from app.connectors import get_connector

    monkeypatch.setattr(pricelist, "DIR", str(tmp_path / "prices"))
    pricelist.save("pyaterochka", "Название;Цена\nГречка ядрица 900 г;129,90")
    found = get_connector("pyaterochka").search("гречка", limit=3)

    assert found and found[0].price == 129.90


# ---------- устойчивость: у каждой позиции всегда есть куда нажать ----------
def test_every_line_gets_a_button_in_every_store(tmp_path, monkeypatch):
    """Главное свойство передачи: мёртвых строк не бывает.

    Лестница способов обязана всегда чем-то заканчиваться. Карточку товара мы
    знаем не всегда — у Дикси нашлось 14 из 16, у Пятёрочки и Самоката каталог
    закрыт целиком. Поиск по названию закрывает остаток: он не требует ни
    идентификатора, ни разбора вёрстки, ни доступа к каталогу.
    """
    from app import config, handover, repo
    from app.db import init_db
    from app.models import BasketLine

    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "h.db"))
    init_db()

    # Имя НЕ подставляется руками: именно такая подпорка в первой редакции теста
    # скрыла дефект — _items читал несуществующее поле product_name, и у сетей без
    # сопоставлений поиск открывался по тире.
    lines = [BasketLine(product_id=1, name="Молоко 1 л", unit="pcs", qty=1.0),
             BasketLine(product_id=2, name="Хлеб бородинский", unit="pcs", qty=2.0)]

    for code in ("magnit", "dixy", "pyaterochka", "samokat"):
        result = handover.for_store(code, lines)
        мёртвые = [i.name for i in result.items if not i.url]
        assert not мёртвые, f"{code}: строки без единой кнопки — {мёртвые}"
        безымянные = [i for i in result.items if i.name == "—"]
        assert not безымянные, f"{code}: кнопка ведёт в поиск по тире, а не по товару"
        from urllib.parse import unquote
        assert "Молоко" in unquote(result.items[0].url),             f"{code}: в ссылку не попало название товара"


def test_search_link_needs_nothing_but_a_name():
    """Поиск — самая устойчивая ступень именно тем, что ему не нужен артикул."""
    from app import handover

    url = handover.search_url("pyaterochka", "Молоко 2,5%")

    assert url and url.startswith("https://5ka.ru/")
    assert "%D0%9C" in url, "название должно уехать закодированным"
    assert handover.search_url("pyaterochka", "  ") is None
    assert handover.search_url("несуществующий_магазин", "Молоко") is None


def test_known_card_beats_search():
    """Где карточку знаем — ведём в товар, а не в поиск: это короче на один шаг."""
    from app import config, handover, repo
    from app.db import init_db
    from app.models import BasketLine, Product
    import tempfile, os

    tmp = tempfile.mkdtemp()
    old = config.db_path
    config.db_path = lambda: os.path.join(tmp, "c.db")
    try:
        init_db()
        store = repo.get_store("magnit")
        pid = repo.upsert_product(Product(id=None, name="Молоко 1 л", unit="pcs"))
        sp = repo.upsert_store_product(store.id, "12345", "Молоко 1 л",
                                       url="https://magnit.ru/product/12345-moloko")
        repo.confirm_mapping(pid, sp, confirmed=True)

        line = BasketLine(product_id=pid, name="Молоко 1 л", unit="pcs", qty=1.0)
        result = handover.for_store("magnit", [line])

        assert "/product/12345" in result.items[0].url
    finally:
        config.db_path = old


# ======================================================================
# НАПОЛНЕНИЕ КОРЗИНЫ: живой ход, отчёт по позициям и повтор непроложенного.
# Всё, что ниже, — про вторую половину дороги (app/shopbrowser/cart.py и
# экраны «Кабинеты» и «Результат»). Фикстура db выше — общая: она делает
# ровно то, что нужно и здесь, а вторая такая же рядом только путала бы.
# ======================================================================

PHONE = "79990000041"

TVOROG = "https://magnit.ru/product/tvorog-prostokvashino"
MOLOKO = "https://magnit.ru/product/moloko-prostokvashino"
SYR = "https://magnit.ru/product/syr-rossiyskiy"


# ---------- поддельная витрина ----------

class Node:
    """Кнопка на карточке. Ищут её по доступному имени — его и отдаём.

    `top` — где кнопка нарисована по вертикали. Раньше его не было, и поддельная
    витрина не умела воспроизвести настоящую карточку: на ней кнопки лежат в
    разных местах, и это РЕШАЕТ, какую нажмут. Замер 19.09.2026 на magnit.ru: над
    заголовком товара висит «В корзину» плавающей панели (y=3), под заголовком —
    настоящая кнопка товара (y=523), а ниже двадцать кнопок карусели «с этим
    покупают» (y=1159). Пока витрина была плоской, тесты этого не видели.
    """

    def __init__(self, name: str, broken: bool = False, top: int = 500):
        self.name = name
        self.broken = broken
        self.top = top
        self.clicks = 0

    def is_enabled(self):
        return True

    def is_visible(self):
        return True

    def get_attribute(self, what):
        return self.name if what == "aria-label" else None

    def inner_text(self):
        return self.name

    def scroll_into_view_if_needed(self, timeout=None):
        return None

    def click(self, timeout=None):
        if self.broken:
            raise RuntimeError("Element is not attached to the DOM")
        self.clicks += 1


class Flaky(Node):
    """Кнопка, которая отваливается после нескольких нажатий.

    Так ведёт себя настоящая карточка: после первого «в корзину» витрина
    перерисовывает её, старый узел отваливается от страницы, и следующее нажатие
    падает. Это не экзотика — это обычный вид наряда на три штуки.
    """

    def __init__(self, name: str, works: int):
        super().__init__(name)
        self.works = works

    def click(self, timeout=None):
        if self.clicks >= self.works:
            raise RuntimeError("Element is not attached to the DOM")
        self.clicks += 1


class Dismiss(Node):
    """«Не сейчас» в окне выбора магазина: закрывает окно, магазина не меняя.

    Так ведёт себя настоящая витрина (замер 20.09.2026 с боевого сервера): после
    нажатия окно уходит, кука shopCode остаётся прежней, а на карточке снова
    видны кнопки «В корзину». Двойник обязан воспроизводить именно это, иначе
    проверка «наряд прошёл окно сам» подтверждала бы саму себя.
    """

    def __init__(self, page, after: str = "Магнит Доставка • Экспресс Каталог Акции Корзина"):
        super().__init__("Не сейчас", top=100)
        self.page = page
        self.after = after

    def click(self, timeout=None):
        super().click(timeout)
        self.page.home_text = self.after


class Card:
    """Одна карточка товара: её видимый текст, её кнопки и где заголовок товара."""

    def __init__(self, text: str, buttons=None, title_top: int = 300):
        self.text = text
        self.buttons = list(buttons or [])
        self.title_top = title_top


class Shop:
    """Витрина из нескольких карточек. Адрес, которого тут нет, не открывается."""

    def __init__(self, cards: dict):
        self.cards = cards
        self.url = ""
        self.opened: list[str] = []
        self.picked = None
        self.cart_count = None
        # Узлы, среди которых счётчик ищет своё число: (сколько потомков, текст).
        # None — «не заданы», и тогда двойник просто отдаёт cart_count, как
        # отдавал раньше. Заданы — работает настоящее правило отбора.
        self.cart_nodes: list[tuple[int, str]] | None = None
        # Запросы, которые витрина отправила: сырые строки «адрес, код ответа»,
        # ровно как их отдаёт обёртка в странице. Именно сырые, а не отобранные:
        # отбор своих запросов от чужих и есть то правило, на котором проверка
        # уже ломалась (cart.own_cart_calls), и подсовывать ему готовый ответ
        # значило бы проверять заглушку. None — «прочитать не вышло».
        self.cart_seen: list | None = None
        self.watching = False
        self.home_text = "Магнит Каталог Акции Магазины Корзина"
        # Кнопки, которые видны ДО перехода на карточку. Раньше их не было, и
        # окно выбора магазина изобразить было нечем: двойник умел показать его
        # текст, но не «Не сейчас», которым его закрывают.
        self.home_buttons: list = []

    def goto(self, url, **kw):
        if url not in self.cards:
            raise RuntimeError("Timeout 45000ms exceeded")
        self.url = url
        self.opened.append(url)

    def inner_text(self, _selector):
        # Пока карточка не открыта, витрина показывает свою главную. Раньше здесь
        # вылетал KeyError, и проверка «сеть ждёт выбранного магазина» тихо
        # считалась непройденной — то есть не проверялась вовсе.
        return self.cards[self.url].text if self.url in self.cards else self.home_text

    def query_selector(self, selector):
        # Кнопку, выбранную страницей, отдаём по её метке — ровно как настоящий
        # браузер после того, как скрипт пометил найденный узел.
        return self.picked if selector == f"[{cart.PICKED}]" else None

    def query_selector_all(self, _selector):
        if self.url not in self.cards:
            return list(self.home_buttons)
        return list(self.cards[self.url].buttons)

    def wait_for_load_state(self, *a, **kw):
        return None

    def evaluate(self, _script, arg=None):
        """Витрина сама выбирает кнопку — тем же правилом, что и настоящая.

        Разбор идёт СНАЧАЛА ПО ТЕКСТУ СКРИПТА, и это не придирка: у наряда есть
        три разных вопроса к странице, и два из них зовутся без аргумента —
        счётчик корзины и счётчик запросов к ней. Различать их по аргументу
        нельзя, а перепутать значит подсунуть одному ответ другого.

        Повторяем здесь правило, а не заглушку: смысл двойника в том, чтобы
        «первая видимая НИЖЕ заголовка» проверялась тестом, а не только живой
        сетью. Заглушка «вернуть 900» молча уводила выбор в запасной перебор — то
        есть проверяла ровно тот код, который и был неправ.
        """
        if "__kzWatch" in _script:
            self.watching = True
            return "поставлено"
        if "__kzSeen" in _script:
            # Сырые запросы страницы. None — «прочитать не вышло», и это НЕ
            # пустой список: пустой означает «сеть не сделала ничего».
            return self.cart_seen
        # Без аргумента у страницы спрашивают счётчик корзины (cart.in_cart).
        # None здесь — «счётчика не видно», и это НЕ ноль: у Магнита пустая
        # корзина подписана без числа вовсе.
        if arg is None:
            # Заданы узлы-кандидаты — повторяем ПРАВИЛО отбора, а не итог: иначе
            # проверка «счётчик не берёт числа из товарных плиток» подтверждала
            # бы сама себя. Правило то же, что в cart._COUNT_JS: маленький узел,
            # короткая подпись, слово «корзин» — и первое число в подписи.
            if self.cart_nodes is None:
                return self.cart_count
            best = None
            for kids, text in self.cart_nodes:
                if kids > cart._COUNT_MAX_KIDS or len(text) > cart._COUNT_MAX_CHARS:
                    continue
                if "корзин" not in text.lower():
                    continue
                found = re.search(r"(\d+)", text)
                if found:
                    number = int(found.group(1))
                    best = number if best is None or number > best else best
            return best
        if not isinstance(arg, list) or len(arg) != 3:
            return None
        pattern, _mark, below = arg
        want = re.compile(pattern, re.I)
        card = self.cards[self.url]
        best = None
        for node in card.buttons:
            if not (node.is_enabled() and node.is_visible()):
                continue
            if not want.search(" ".join((node.name or "").split())):
                continue
            if below and node.top < card.title_top:
                continue
            if best is None or node.top < best.top:
                best = node
        self.picked = best
        return None if best is None else best.top


@pytest.fixture
def shop(monkeypatch):
    """Витрина без витрины: браузер подменён целиком, в сеть никто не идёт."""
    monkeypatch.setattr(cart, "STEP_PAUSE", 0)
    monkeypatch.setattr(cart, "CLICK_PAUSE", 0)
    monkeypatch.setattr(cart.driver, "_settle", lambda page, wait=0.8: None)
    monkeypatch.setattr(cart.driver, "open_store", lambda *a, **kw: None)
    monkeypatch.setattr(cart.driver, "look",
                        lambda *a, **kw: {"logged_in": True, "guarded": False})
    monkeypatch.setattr(cart.shopstore, "load",
                        lambda chain: {"cookies": [{"name": "mg_at", "value": "ключ"}]})
    # Точки у поддельной витрины нет, и подделывать надо ИМЕННО ЕЁ ОТСУТСТВИЕ, а
    # не ответы app/shopbrowser/point.py: пусть настоящий разбор точки работает и
    # здесь, честно отвечая «точки не задано». Подменённый целиком, он перестал
    # бы проверяться вовсе — а он теперь решает, пойдёт передача или нет.
    import app.location as client_place

    monkeypatch.setattr(client_place, "for_store", lambda code: None)

    def open_shop(cards: dict) -> Shop:
        page = Shop(cards)
        monkeypatch.setattr(cart.driver, "run", lambda chain, phone, job, **kw: job(page))
        return page

    return open_shop


def naryad(rows) -> CartPlan:
    """Наряд из строк «артикул, сколько, название, единица, адрес карточки»."""
    return CartPlan(store_code="magnit",
                    lines=[PlanLine(sku=sku, qty=qty, name=name, unit=unit, price=None, url=url)
                           for sku, qty, name, unit, url in rows])


# ---------- округление ----------

def test_a_fraction_becomes_packs_and_the_note_names_both_numbers():
    """0,7 кг сыра витрина кладёт одной упаковкой — и заметка называет оба числа.

    Одного «округлено» мало: человек должен увидеть, ЧТО стало ЧЕМ, иначе он не
    поймёт, почему в корзине лежит килограмм вместо семисот граммов.
    """
    times, note = cart._pieces(0.7, "kg")
    assert times == 1
    assert "0,7" in note and "кг" in note and "1 упаковку" in note
    assert "kg" not in note, "латинская единица посреди русской фразы читается как сбой"


def test_a_whole_number_says_nothing():
    """Округлять нечего — и заметки нет: лишняя строка у каждой позиции это шум."""
    assert cart._pieces(2, "pcs") == (2, "")
    assert cart.rounding(3.0, "kg") == ""


def test_the_note_counts_packs_in_russian():
    """«1 упаковку», «3 упаковки», «6 упаковок» — иначе в заметке видно машину."""
    assert "1 упаковку" in cart.rounding(0.7, "кг")
    assert "3 упаковки" in cart.rounding(2.6, "кг")
    assert "6 упаковок" in cart.rounding(5.5, "кг")


# ---------- отчёт по позициям ----------

def test_the_report_names_every_position_and_says_why(db, shop):
    """Отчёт — поимённый, с причиной у каждой неудачи.

    «Легло 1 из 3» без слов «творога не было в наличии» заставляет человека
    пересобирать корзину вслепую: он не знает, чего в ней не хватает.
    """
    shop({
        TVOROG: Card("Творог Простоквашино 5% 200 г. Цена 89 ₽",
                     [Node("в корзину")]),
        MOLOKO: Card("Молоко Простоквашино 930 мл. Нет в наличии", []),
    })
    got = cart.deliver("magnit", "79990000041", naryad([
        ("magnit-1", 1, "Творог Простоквашино 5%", "pcs", TVOROG),
        ("magnit-2", 1, "Молоко Простоквашино 930 мл", "pcs", MOLOKO),
        ("magnit-3", 1, "Хлеб Бородинский", "pcs", None),
    ]))

    assert got["ok"] == ["magnit-1"]
    rows = {r["sku"]: r for r in cart.report("magnit")}
    assert len(rows) == 3, "позиция пропала из отчёта — человек о ней не узнает"
    assert rows["magnit-1"]["ok"] is True
    assert rows["magnit-1"]["name"] == "Творог Простоквашино 5%"
    assert rows["magnit-2"]["ok"] is False
    assert "нет в наличии" in rows["magnit-2"]["why"]
    assert "нет адреса карточки" in rows["magnit-3"]["why"]


def test_a_rounded_position_keeps_its_note_even_when_it_lands(db, shop):
    """Заметка об округлении не теряется у УДАВШЕЙСЯ позиции.

    Именно здесь она и терялась: причина возвращалась только у неудачи, а
    округление склеивалось с ней — то есть пропадало в самом частом случае, когда
    позиция легла. Человек узнавал про лишние триста граммов из чека.
    """
    shop({SYR: Card("Сыр Российский 45%. Цена 240 ₽", [Node("в корзину")])})
    cart.deliver("magnit", "79990000041",
                 naryad([("magnit-7", 0.7, "Сыр Российский 45%", "kg", SYR)]))

    row = cart.report("magnit")[0]
    assert row["ok"] is True, "позиция должна была лечь"
    assert "0,7" in row["note"] and "1 упаковку" in row["note"]


def test_a_broken_browser_does_not_swallow_the_untouched_positions(db, shop, monkeypatch):
    """Браузер отвалился на середине — недошедшие позиции не «пропадают».

    Оборванный молча отчёт дал бы повтору одну позицию, а остальные так и остались
    бы вне корзины и вне глаз человека: он смотрит на список из одной строки и
    считает, что всё остальное лежит.
    """
    shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину")])})

    def dead(chain, phone, job, **kw):
        raise cart.driver.BrowserUnavailable("сеанс магазина закрыт — откройте кабинет заново")

    monkeypatch.setattr(cart.driver, "run", dead)
    cart.deliver("magnit", "79990000041", naryad([
        ("magnit-1", 1, "Творог", "pcs", TVOROG),
        ("magnit-2", 1, "Молоко", "pcs", MOLOKO),
        ("magnit-3", 2.4, "Сыр", "kg", SYR),
    ]))

    rows = cart.report("magnit")
    assert [r["sku"] for r in rows] == ["magnit-1", "magnit-2", "magnit-3"]
    assert all(r["ok"] is False for r in rows)
    assert sorted(cart.failed_skus("magnit")) == ["magnit-1", "magnit-2", "magnit-3"]


def test_a_position_that_landed_in_part_is_topped_up_and_not_doubled(db, shop):
    """Легло два из трёх — повтор кладёт ОДИН, а не три.

    Здесь корзина портилась молча и стоила человеку денег. Число положенного жило
    только внутри фразы «положено 2 из 3» и дальше никуда не ехало: в записи
    оставалось «не легло», повтор брал позицию целиком и щёлкал её заново — в
    корзине пять штук вместо трёх, и видно это только на кассе.
    """
    plus = Flaky("+", works=1)
    shop({MOLOKO: Card("Молоко Простоквашино. Цена 95 ₽", [Node("в корзину"), plus])})
    plan = naryad([("magnit-1", 3, "Молоко Простоквашино", "pcs", MOLOKO)])
    cart.deliver("magnit", PHONE, plan)

    row = cart.report("magnit")[0]
    assert row["ok"] is False, "часть позиции не легла — значит она не легла"
    assert (row["put"], row["times"]) == (2, 3), "сколько именно легло, отчёт не запомнил"
    assert cart.failed_skus("magnit") == ["magnit-1"]

    # Наряд на повтор: та же позиция, но недостающим количеством.
    lines, already = cart._shortlist("magnit", plan, ["magnit-1"])
    assert [ln.qty for ln in lines] == [1], \
        "повтор понёс бы все три — в корзине человека стало бы пять"
    assert already == {"magnit-1": 2}

    # И заход по этому наряду нажимает ровно один раз.
    fresh = Node("в корзину")
    shop({MOLOKO: Card("Молоко Простоквашино. Цена 95 ₽", [fresh, Node("+")])})
    cart._progress("magnit", items=cart._carry("magnit", {"magnit-1"}))
    cart.deliver("magnit", PHONE, cart._Naryad(lines, already))

    assert fresh.clicks == 1, "повтор нажал больше, чем не хватало"
    done = cart.report("magnit")[0]
    assert done["ok"] is True
    assert "уже лежали" in done["note"], \
        "отчёт говорит «легло 1» там, где человек просил три и получил три"


def test_a_card_the_browser_did_not_answer_for_is_never_repeated(db, shop, monkeypatch):
    """«Браузер не ответил» — это не «не легло», и повтору такую позицию не дают.

    Поручение по таймауту не отменяется: оно остаётся в очереди потока браузера и
    дощёлкивает товар в корзину, пока передача уже объявила позицию непроложенной.
    Повтор положил бы её второй раз — то есть ровно то, чего вся эта ветка и не
    должна делать. А вот до остальных позиций дело не дошло вовсе, и они честно не
    легли.
    """
    shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину")])})

    def slow(chain, phone, job, **kw):
        raise cart.driver.BrowserTimeout("браузер не ответил за отведённое время")

    monkeypatch.setattr(cart.driver, "run", slow)
    cart.deliver("magnit", PHONE, naryad([
        ("magnit-1", 2, "Творог", "pcs", TVOROG),
        ("magnit-2", 1, "Молоко", "pcs", MOLOKO)]))

    rows = {r["sku"]: r for r in cart.report("magnit")}
    assert rows["magnit-1"]["unknown"] is True, "неизвестное выдано за неудачу"
    assert "не знает" in rows["magnit-1"]["why"], "человеку не сказали, что это неизвестность"
    assert "корзину в магазине" in rows["magnit-1"]["why"], "не назван следующий шаг"
    assert rows["magnit-2"]["unknown"] is False, "до неё передача не дошла — это твёрдое «не легло»"

    assert cart.failed_skus("magnit") == ["magnit-2"], \
        "позиция, про которую браузер молчит, ушла в повтор — она легла бы второй раз"


def test_one_card_gets_its_own_deadline_counted_from_the_quantity():
    """Срок ожидания карточки считается от количества, а не берётся общий.

    Общий срок (driver.CALL_TIMEOUT, 90 с) короче бюджета одной карточки уже при
    двух штуках: открыть до 45 с, дать улечься около 28 с и на каждое нажатие ещё
    до 14 с. Кто не дождался — получает «неизвестно» и лишнюю работу человеку.
    """
    settle = 15 + 12 + 0.6                      # столько ждёт driver._settle
    step = 5 + 8 + cart.CLICK_PAUSE             # прокрутка, нажатие и пауза после него

    assert cart._budget(2, "pcs") > cart.driver.CALL_TIMEOUT, \
        "двух штук хватает, чтобы упереться в общий срок"
    for qty in (1, 2, 3, 10):
        worst = cart.LOAD_TIMEOUT / 1000 + settle + qty * step
        assert cart._budget(qty, "pcs") >= worst, f"{qty} шт: срок короче самой работы"
    # Но не дольше, чем живёт отметка о ходе: молчащая карточка объявила бы всю
    # передачу мёртвой и пустила бы вторую поверх идущей.
    assert cart._budget(cart.QTY_LIMIT, "pcs") == cart.CARD_LIMIT < cart.STALE_AFTER


# ---------- вторая передача ----------

def test_the_launcher_refuses_a_second_handover_while_the_first_runs(db):
    """Вторая передача положила бы человеку всё в корзину ВТОРОЙ раз.

    Проверка стоит в самом пускателе, а не только на экране: экран человек мог
    открыть до нажатия — или открыть его сразу в двух вкладках.
    """
    cart._progress("magnit", started_at="2026-09-17T18:42:00", finished_at=None,
                   total=16, at=7, done=6, now="Творог", items=[])
    assert cart.running("magnit") is True
    assert cart.start("magnit", "79990000041",
                      naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)])) is False


def test_a_handover_that_died_with_the_server_stops_blocking(db, monkeypatch):
    """Незакрытая отметка не запирает кнопку навсегда.

    Сервер могли перезапустить посреди наряда. Если считать такую отметку живой,
    «Передать корзину» не нажмётся уже никогда, а исправить это человеку нечем.
    """
    cart._progress("magnit", started_at="2026-09-17T18:42:00", finished_at=None,
                   total=16, at=7, done=6, now="Творог",
                   items=[{"sku": "magnit-2", "name": "Молоко", "ok": False,
                           "why": "нет в наличии", "note": "", "earlier": False}])
    assert cart.running("magnit") is True
    assert cart.failed_skus("magnit") == [], "пока идёт — повторять нечего"

    monkeypatch.setattr(cart, "STALE_AFTER", 0)
    assert cart.running("magnit") is False
    assert cart.failed_skus("magnit") == ["magnit-2"], "мёртвый заход можно повторить"


def test_an_old_mark_without_a_pulse_is_not_taken_for_a_live_one(db):
    """Отметка, написанная прежним кодом, не запирает кнопку.

    У неё нет пульса, и считать её живой значило бы поверить записи, про которую
    вообще неизвестно, когда её трогали в последний раз.
    """
    import json

    repo.set_setting(cart.PROGRESS_KEY, json.dumps(
        {"magnit": {"started_at": "2026-09-17T18:42:00", "finished_at": None, "done": 3}}))
    assert cart.running("magnit") is False


# ---------- повтор только неудавшихся ----------

def test_a_repeat_touches_only_what_did_not_land(db, shop):
    """Повтор не трогает того, что уже лежит в корзине.

    Прогон всего наряда заново кладёт удавшееся второй раз. Это не «лишний
    запрос», а испорченная корзина человека: он платит за два творога.
    """
    tvorog = Node("в корзину")
    moloko_gone = Card("Молоко Простоквашино 930 мл. Нет в наличии", [])
    shop({TVOROG: Card("Творог Простоквашино. Цена 89 ₽", [tvorog]), MOLOKO: moloko_gone})

    plan = naryad([("magnit-1", 1, "Творог Простоквашино", "pcs", TVOROG),
                   ("magnit-2", 1, "Молоко Простоквашино 930 мл", "pcs", MOLOKO)])
    cart.deliver("magnit", "79990000041", plan)
    assert tvorog.clicks == 1
    assert cart.failed_skus("magnit") == ["magnit-2"]

    # Второй заход: молоко появилось, творог трогать нельзя.
    page = shop({TVOROG: Card("Творог Простоквашино. Цена 89 ₽", [tvorog]),
                 MOLOKO: Card("Молоко Простоквашино 930 мл. Цена 95 ₽", [Node("в корзину")])})
    only = [ln for ln in plan.lines if ln.sku == "magnit-2"]
    cart.deliver("magnit", "79990000041", cart._Naryad(only))

    assert tvorog.clicks == 1, "удавшаяся позиция легла второй раз — корзина испорчена"
    assert page.opened == [MOLOKO], "повтор открыл карточку, которую трогать было нечего"


def test_a_repeat_keeps_in_the_report_what_already_lies_in_the_cart(db, shop):
    """Отчёт повтора показывает и то, что легло в прошлый раз.

    Иначе после повтора двух позиций экран скажет «легло 2» там, где в корзине
    лежит четырнадцать, — и человек пойдёт собирать всё заново руками.
    """
    cart._progress("magnit", started_at="2026-09-17T18:00:00",
                   finished_at="2026-09-17T18:03:00", done=2, total=2, items=[
                       {"sku": "magnit-1", "name": "Творог Простоквашино", "qty": 1,
                        "unit": "pcs", "ok": True, "why": "", "note": "", "earlier": False},
                       {"sku": "magnit-2", "name": "Молоко Простоквашино", "qty": 1,
                        "unit": "pcs", "ok": False, "why": "нет в наличии", "note": "",
                        "earlier": False}])

    carried = cart._carry("magnit", {"magnit-2"})
    assert [r["sku"] for r in carried] == ["magnit-1"]
    assert carried[0]["earlier"] is True, "лежащее с прошлого раза должно быть помечено"

    page = shop({MOLOKO: Card("Молоко Простоквашино. Цена 95 ₽", [Node("в корзину")])})
    cart._progress("magnit", items=carried)
    cart.deliver("magnit", "79990000041",
                 cart._Naryad([PlanLine(sku="magnit-2", qty=1, name="Молоко Простоквашино",
                                        unit="pcs", price=None, url=MOLOKO)]))

    rows = cart.report("magnit")
    assert [r["sku"] for r in rows] == ["magnit-1", "magnit-2"]
    assert rows[0]["earlier"] is True and rows[0]["ok"] is True
    assert rows[1]["earlier"] is False and rows[1]["ok"] is True
    assert page.opened == [MOLOKO]


# ---------- экран «Кабинеты» ----------

@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    from app.web.screens import result as result_screen

    result_screen._CALC.clear()
    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    result_screen._CALC.clear()
    users.deactivate()


def enter(client):
    return client.post("/login", data={"phone": PHONE, "next": "/"})


def text(answer) -> str:
    return answer.data.decode("utf-8")


def seed_basket() -> int:
    """Корзина: молоко штуками и сыр на вес — ради заметки об округлении.

    Магнит и Пятёрочка нарочно: обе принимают корзину нарядом, а не ссылкой, —
    значит, показ страницы никуда не пойдёт по сети (tests/conftest.py это ловит).
    """
    users.open_workspace(PHONE)
    milk = repo.upsert_product(Product(id=None, name="Молоко Простоквашино 930 мл", unit="pcs"))
    cheese = repo.upsert_product(Product(id=None, name="Сыр Российский 45%", unit="kg"))
    for code, price_milk, price_cheese in (("magnit", 89.0, 240.0), ("pyaterochka", 95.0, 260.0)):
        store = repo.get_store(code)
        milk_sp = repo.upsert_store_product(store.id, f"{code}-1", "Молоко Простоквашино 930 мл")
        cheese_sp = repo.upsert_store_product(store.id, f"{code}-2", "Сыр Российский 45%")
        repo.confirm_mapping(milk, milk_sp, confirmed=True)
        repo.confirm_mapping(cheese, cheese_sp, confirmed=True)
        repo.save_price(milk_sp, price_milk)
        repo.save_price(cheese_sp, price_cheese)
    basket_id = repo.create_basket("Неделя 38")
    repo.set_basket_item(basket_id, milk, 2.0)
    repo.set_basket_item(basket_id, cheese, 0.7)
    return basket_id


def save_login(chain: str = "magnit") -> None:
    from app.shopbrowser import store as shopstore

    users.open_workspace(PHONE)
    shopstore.save(chain, {"cookies": [{"name": "mg_at", "value": "ключ"}], "origins": []})


def test_the_live_progress_is_a_small_answer_and_not_the_whole_page(web):
    """Ход спрашивают раз в пару секунд — и отвечать на это целой страницей нельзя.

    Страница «Кабинеты» собирает наряд по всем шести сетям и ходит в справочник
    товаров. На таком опросе сервер ляжет раньше, чем наполнится корзина.
    """
    enter(web)
    users.open_workspace(PHONE)
    cart._progress("magnit", started_at="2026-09-17T18:42:00", finished_at=None,
                   total=16, at=7, done=6, now="Творог Простоквашино",
                   items=[{"sku": "magnit-2", "name": "Сыр Российский", "qty": 0.7,
                           "unit": "kg", "ok": True, "why": "",
                           "note": "0,7 кг нажатием не положить — взяли 1 упаковку",
                           "earlier": False}])

    answer = web.get("/accounts?going=magnit")
    assert answer.status_code == 200
    assert answer.mimetype == "application/json"
    assert b"<html" not in answer.data, "на опрос хода приехала целая страница"

    body = answer.get_json()
    assert body["going"] is True
    assert body["at"] == 7 and body["total"] == 16
    assert body["now"] == "Творог Простоквашино"
    assert body["rows"][0]["name"] == "Сыр Российский"
    assert body["rows"][0]["qty"] == "0,7 кг"
    assert "1 упаковку" in body["rows"][0]["note"]


def test_the_live_progress_does_not_obey_an_unknown_chain(web):
    """Код сети приходит адресом, а адрес приходит откуда угодно."""
    enter(web)
    answer = web.get("/accounts?going=azbuka")
    assert answer.status_code == 400
    assert answer.get_json()["going"] is False


def test_the_screen_watches_the_handover_itself(web):
    """Пока идёт передача, экран обновляет её сам — иначе он выглядит застывшим.

    Застывший экран и есть причина двойной корзины: человек решает, что нажатие не
    сработало, и нажимает ещё раз.
    """
    enter(web)
    seed_basket()
    save_login()
    users.open_workspace(PHONE)
    cart._progress("magnit", started_at="2026-09-17T18:42:00", finished_at=None,
                   total=16, at=7, done=6, now="Творог Простоквашино", items=[])

    page = text(web.get("/accounts"))
    assert 'data-going="1"' in page, "экран не пометил передачу как живую"
    assert "?going=' + encodeURIComponent" in page, "экран не спрашивает ход"
    assert "Кладу 7 из 16" in page
    assert "Творог Простоквашино" in page
    assert re.search(r'value="send:magnit"[^>]*disabled', page), \
        "кнопка «Передать» не погашена, пока идёт передача"


def test_the_screen_shows_the_report_with_names_and_reasons(web):
    """Отчёт — человеческими словами: название, причина и что делать дальше.

    Одних артикулов мало: «magnit-8841» человеку не говорит ничего, а решать по
    этому отчёту ему — идти ли в магазин за тем, что не легло.
    """
    enter(web)
    seed_basket()
    save_login()
    users.open_workspace(PHONE)
    cart._progress("magnit", started_at="2026-09-17T18:42:00",
                   finished_at="2026-09-17T18:45:00", total=2, done=2, at=0, now="",
                   note="1 позиц. не легло — причины ниже, их можно повторить.",
                   items=[{"sku": "magnit-1", "name": "Молоко Простоквашино 930 мл",
                           "qty": 2, "unit": "pcs", "ok": True, "why": "", "note": "",
                           "earlier": False},
                          {"sku": "magnit-2", "name": "Сыр Российский 45%", "qty": 0.7,
                           "unit": "kg", "ok": False,
                           "why": "карточка говорит, что товара нет в наличии",
                           "note": "0,7 кг нажатием не положить — взяли 1 упаковку",
                           "earlier": False}])

    page = text(web.get("/accounts"))
    assert "Молоко Простоквашино 930 мл" in page
    assert "Сыр Российский 45%" in page
    assert "нет в наличии" in page, "причина неудачи до человека не доехала"
    assert "0,7 кг нажатием не положить" in page, "заметка об округлении потерялась"
    assert 'value="retry:magnit"' in page, "непроложенное нечем доложить"
    assert "Повторить только их" in page


def test_a_handover_that_never_started_still_says_why(web):
    """Передача кончилась, не начавшись, — и человек слышит, почему и что делать.

    Так она кончается в самых вероятных случаях: устарел сохранённый вход, сеть
    встретила проверкой, оборвался браузер. Ни одной позиции и одно объяснение —
    а экран отбрасывал отчёт без позиций целиком, вместе с этим объяснением.
    Человек нажимал «Передать корзину», видел зелёное «передача пошла», через пару
    секунд экран сам возвращался — и молчал: кнопка снова нажимается, корзина
    пуста, причины нет.
    """
    enter(web)
    seed_basket()
    save_login()
    users.open_workspace(PHONE)
    cart._progress("magnit", started_at="2026-09-17T18:42:00",
                   finished_at="2026-09-17T18:42:04", total=0, done=0, at=0, now="",
                   note="Сеть больше не считает вас вошедшим — сохранённый вход устарел. "
                        "Откройте кабинет и войдите заново.",
                   items=[])

    page = text(web.get("/accounts"))
    assert "сохранённый вход устарел" in page, "причина до человека не доехала"
    assert "войдите заново" in page, "не сказан следующий шаг"
    assert "легло 0 из 0" not in page, "отписка вместо ответа"


def test_the_repeat_button_counts_only_what_it_will_actually_repeat(web):
    """Число на кнопке «Повторить только их (N)» — то же, что она и повторит.

    Корзину после передачи могли поправить, и непроложенного в ней могло уже не
    быть. Кнопка считала всё непроложенное, а повторяла пересечение с сегодняшней
    корзиной: звала «повторить три» и уводила на «повторять нечего».
    """
    enter(web)
    seed_basket()
    save_login()
    users.open_workspace(PHONE)
    cart._progress("magnit", started_at="2026-09-17T18:42:00",
                   finished_at="2026-09-17T18:45:00", total=2, done=2,
                   items=[{"sku": "magnit-2", "name": "Сыр", "qty": 0.7, "unit": "kg",
                           "ok": False, "why": "нет в наличии", "note": "", "earlier": False},
                          {"sku": "magnit-404", "name": "Гречка, которой в корзине уже нет",
                           "qty": 1, "unit": "pcs", "ok": False, "why": "нет в наличии",
                           "note": "", "earlier": False}])

    page = text(web.get("/accounts"))
    assert "Повторить только их (1)" in page, \
        "кнопка обещает повторить то, чего в сегодняшней корзине уже нет"


def test_the_repeat_button_sends_only_what_did_not_land(web, monkeypatch):
    """Кнопка «повторить» уносит в передачу ровно непроложенное — и ничего больше.

    Сторож смотрит не на надпись, а на наряд, который получила передача: надпись
    нарисовалась бы и при полном прогоне, а корзина человека была бы испорчена.
    """
    enter(web)
    seed_basket()
    save_login()
    users.open_workspace(PHONE)
    cart._progress("magnit", started_at="2026-09-17T18:42:00",
                   finished_at="2026-09-17T18:45:00", total=2, done=2,
                   items=[{"sku": "magnit-1", "name": "Молоко", "qty": 2, "unit": "pcs",
                           "ok": True, "why": "", "note": "", "earlier": False},
                          {"sku": "magnit-2", "name": "Сыр", "qty": 0.7, "unit": "kg",
                           "ok": False, "why": "нет в наличии", "note": "", "earlier": False}])

    done = threading.Event()
    seen: dict = {}

    def fake_deliver(chain, phone, plan):
        seen["skus"] = [line.sku for line in plan.lines]
        seen["carried"] = [(r["sku"], r["earlier"]) for r in cart.report(chain)]
        done.set()
        return {"ok": [], "failed": [], "note": ""}

    monkeypatch.setattr(cart, "deliver", fake_deliver)

    answer = web.post("/accounts", data={"do": "retry:magnit"})
    assert answer.status_code == 302
    assert "sent=magnit" in answer.headers["Location"]
    assert done.wait(timeout=5), "повтор не начался — человек ждал бы напрасно"

    assert seen["skus"] == ["magnit-2"], "повтор потащил в корзину то, что уже там лежит"
    assert seen["carried"] == [("magnit-1", True)], \
        "положенное в прошлый раз выпало из отчёта — человек решит, что корзина пуста"


def test_pressing_send_again_while_it_runs_does_not_fill_the_cart_twice(web):
    """Второе нажатие не пускается: оно положило бы всё в корзину повторно.

    Погашенной кнопки мало — страницу человек мог открыть до нажатия или открыть
    её сразу в двух вкладках.
    """
    enter(web)
    seed_basket()
    save_login()
    users.open_workspace(PHONE)
    cart._progress("magnit", started_at="2026-09-17T18:42:00", finished_at=None,
                   total=16, at=3, done=2, now="Творог", items=[])

    answer = web.post("/accounts", data={"do": "send:magnit"})
    assert answer.status_code == 302
    assert "busy=magnit" in answer.headers["Location"]

    page = text(web.get("/accounts?busy=magnit"))
    assert "второй раз" in page or "вторую приложение" in page


def test_a_repeat_with_nothing_to_repeat_says_so(web):
    """Корзина изменилась после передачи — и повторять стало нечего.

    Молчаливый переход на ту же страницу человек прочёл бы как «кнопка сломана».
    """
    enter(web)
    seed_basket()
    save_login()

    answer = web.post("/accounts", data={"do": "retry:magnit"})
    assert answer.status_code == 302
    assert "gone=magnit" in answer.headers["Location"]
    page = text(web.get("/accounts?gone=magnit"))
    assert "Повторять нечего" in page


def test_rounding_is_visible_before_the_handover(web):
    """0,7 кг сыра станут упаковкой — и человек узнаёт это ДО передачи.

    Увидев заранее, он поправит количество сам; из чека он это уже не поправит.
    """
    enter(web)
    seed_basket()
    save_login()

    page = text(web.get("/accounts"))
    assert "Сыр Российский 45%" in page
    assert "нажатием не положить" in page
    assert "1 упаковку" in page


def test_the_result_screen_shows_the_rounding_next_to_the_position(web):
    """То же округление видно и на «Результате», у самой позиции наряда."""
    enter(web)
    basket_id = seed_basket()

    page = text(web.get(f"/result?basket={basket_id}"))
    assert "нажатием не положить" in page
    assert "1 упаковку" in page


def test_no_screen_offers_to_place_an_order(web):
    """Передача кончается наполненной корзиной. Дальше решает человек.

    Сторож грубый нарочно: кнопка «заказать» на нашей стороне означала бы, что
    ошибка в нашем расчёте становится списанием с его карты, а не видимой ошибкой.
    """
    enter(web)
    basket_id = seed_basket()
    save_login()

    for page in (text(web.get("/accounts")), text(web.get(f"/result?basket={basket_id}"))):
        assert "Оформить заказ" not in page
        assert "Оплатить" not in page
        assert 'value="order:' not in page and 'value="pay:' not in page
    assert "Заказ оформляете вы сами" in text(web.get("/accounts?sent=magnit"))


def test_the_result_screen_does_not_start_a_second_handover(web, monkeypatch):
    """С «Результата» вторая передача тоже не пускается.

    Экранов-пускателей два, и проверка, стоящая только на одном, — это дыра
    ровно того же размера: человек нажимает «Собрать корзину» здесь, потом ещё раз
    там, и товары ложатся дважды.
    """
    enter(web)
    basket_id = seed_basket()
    save_login()
    users.open_workspace(PHONE)
    cart._progress("magnit", started_at="2026-09-17T18:42:00", finished_at=None,
                   total=16, at=3, done=2, now="Творог", items=[])

    def never(*a, **kw):
        raise AssertionError("вторая передача всё-таки пошла — корзина легла бы дважды")

    monkeypatch.setattr(cart, "deliver", never)

    answer = web.post("/result", data={"basket": basket_id, "do": "link:magnit"})
    assert answer.status_code == 302
    assert "busy=magnit" in answer.headers["Location"]


def test_the_result_screen_says_the_handover_is_already_going(web):
    """И говорит об этом словами, а не молча гасит кнопку."""
    enter(web)
    basket_id = seed_basket()
    save_login()
    users.open_workspace(PHONE)
    cart._progress("magnit", started_at="2026-09-17T18:42:00", finished_at=None,
                   total=16, at=3, done=2, now="Творог Простоквашино", items=[])

    page = text(web.get(f"/result?basket={basket_id}"))
    assert "уже уезжает" in page
    assert "кладу 3 из 16" in page.lower()


# ---------- METRO: корзина наполняется одним запросом ----------
#
# ПОЧЕМУ ЭТА СЕТЬ ПРОВЕРЯЕТСЯ ОТДЕЛЬНО ОТ ОСТАЛЬНЫХ. У METRO передача идёт МИМО
# браузера: сеть описала наполнение корзины сама, и шестнадцать позиций уходят
# одним запросом вместо шестнадцати загрузок страниц. Значит и ломаться она будет
# иначе — не на чужой вёрстке, а на том, чью корзину мы наполнили и что в ней
# после этого оказалось.

@pytest.fixture
def metro_shop(db, monkeypatch):
    """METRO без METRO: сеть подменена, наружу не ходит никто."""
    from app import location
    from app.connectors import metro, metro_cart

    HASH = "5502aed9f4501b12da05daf6f5347e7b"
    monkeypatch.setattr(cart.shopstore, "load", lambda chain: {
        "cookies": [{"name": metro_cart.HASH_COOKIE, "value": HASH,
                     "domain": "api.metro-cc.ru"}]})
    monkeypatch.setattr(location, "for_store", lambda code: None)
    monkeypatch.setattr(metro, "_store_id", lambda loc: "16")
    # Токен считаем выданным: без него передача честно отказывается до всякой сети
    # (замер 19.09.2026), и эти сторожа проверяли бы один отказ вместо того, ради
    # чего написаны. Сам отказ сторожится отдельно, ниже.
    monkeypatch.setattr(metro_cart, "writable", lambda: True)

    sent: dict = {"filled": None}

    def open_metro(inside: list[dict], unavailable: list[dict] | None = None):
        def fill(store_id, user_hash, lines):
            sent["filled"] = {"store": store_id, "hash": user_hash,
                              "lines": [(str(l.sku), l.qty) for l in lines]}
            return metro_cart.Basket()

        monkeypatch.setattr(metro_cart, "fill", fill)
        monkeypatch.setattr(metro_cart, "read", lambda store_id, user_hash: metro_cart.Basket(
            user_hash=user_hash, lines=list(inside), unavailable=list(unavailable or [])))
        return sent

    return open_metro


def metro_naryad(rows) -> CartPlan:
    return CartPlan(store_code="metro",
                    lines=[PlanLine(sku=sku, qty=qty, name=name, unit="pcs", price=None, url=None)
                           for sku, qty, name in rows])


def test_metro_cart_leaves_in_one_request_without_a_browser(metro_shop, monkeypatch):
    """Браузер к METRO не поднимается вовсе: за этим весь смысл её канала.

    Сторож тут не про скорость. Пока передача шла бы через окно, она зависела бы
    от того, отдала ли сеть страницу нашему серверу и не переехала ли у неё кнопка,
    — при том что сеть САМА описала способ положить товар в корзину.
    """
    def forbidden(*a, **kw):
        raise AssertionError("к METRO подняли браузер, хотя у неё есть свой способ")

    monkeypatch.setattr(cart.driver, "open_store", forbidden)
    monkeypatch.setattr(cart.driver, "run", forbidden)
    sent = metro_shop([{"article": 117189, "count": 2, "eshop_product_id": 55}])

    got = cart.deliver("metro", "79990000041", metro_naryad([("117189", 2, "Творог")]))

    assert got["ok"] == ["117189"] and not got["failed"]
    assert sent["filled"] == {"store": "16", "hash": "5502aed9f4501b12da05daf6f5347e7b",
                              "lines": [("117189", 2)]}


def test_metro_counts_as_landed_only_what_the_cart_shows(metro_shop):
    """Успех — это позиция, видная в перечитанной корзине, а не код 200.

    Иначе человек прочитал бы «передано 2» и пришёл бы к корзине с одной строкой:
    сеть вправе принять запрос и не положить товар, которого нет в этом центре.
    """
    metro_shop([{"article": 117189, "count": 1, "eshop_product_id": 55}],
               unavailable=[{"article": 900}])

    got = cart.deliver("metro", "79990000041",
                       metro_naryad([("117189", 1, "Творог"), ("900", 1, "Молоко")]))

    assert got["ok"] == ["117189"]
    rows = {r["sku"]: r for r in cart.report("metro")}
    assert rows["900"]["ok"] is False
    assert "нет в выбранном торговом центре" in rows["900"]["why"]


def test_metro_without_a_hash_says_what_to_do(db, monkeypatch):
    """Неизвестно, чья корзина — не наполняем ничью и говорим, как это починить."""
    from app.connectors import metro_cart

    monkeypatch.setattr(cart.shopstore, "load", lambda chain: {"cookies": []})
    monkeypatch.setattr(metro_cart, "writable", lambda: True)

    got = cart.deliver("metro", "79990000041", metro_naryad([("117189", 1, "Творог")]))

    assert not got["ok"]
    assert "окне магазина" in got["note"]


def test_metro_without_a_trade_centre_does_not_guess_one(db, monkeypatch):
    """Цены, наличие и корзина у METRO свои в каждом центре — выдумывать нельзя."""
    from app import location
    from app.connectors import metro, metro_cart

    monkeypatch.setattr(cart.shopstore, "load", lambda chain: {
        "cookies": [{"name": metro_cart.HASH_COOKIE, "domain": "api.metro-cc.ru",
                     "value": "5502aed9f4501b12da05daf6f5347e7b"}]})
    monkeypatch.setattr(location, "for_store", lambda code: None)
    monkeypatch.setattr(metro, "_store_id", lambda loc: None)
    monkeypatch.setattr(metro_cart, "writable", lambda: True)

    got = cart.deliver("metro", "79990000041", metro_naryad([("117189", 1, "Творог")]))

    assert not got["ok"]
    assert "торговый центр" in got["note"].lower()


def test_metro_failing_to_accept_the_cart_is_loud_on_every_line(db, monkeypatch):
    """Сеть отказала — не легла НИ ОДНА позиция, и это сказано про каждую.

    Молчаливый обрыв дал бы пустой отчёт: человек увидел бы «передано» без строк
    и пошёл бы к корзине, в которой ничего нет.
    """
    from app import location
    from app.connectors import metro, metro_cart

    monkeypatch.setattr(cart.shopstore, "load", lambda chain: {
        "cookies": [{"name": metro_cart.HASH_COOKIE, "domain": "api.metro-cc.ru",
                     "value": "5502aed9f4501b12da05daf6f5347e7b"}]})
    monkeypatch.setattr(location, "for_store", lambda code: None)
    monkeypatch.setattr(metro, "_store_id", lambda loc: "16")

    def refuse(*a, **kw):
        raise RuntimeError("METRO отказала на POST корзины")

    monkeypatch.setattr(metro_cart, "fill", refuse)
    monkeypatch.setattr(metro_cart, "writable", lambda: True)

    got = cart.deliver("metro", "79990000041",
                       metro_naryad([("117189", 1, "Творог"), ("117190", 1, "Молоко")]))

    assert not got["ok"] and len(got["failed"]) == 2
    rows = cart.report("metro")
    assert len(rows) == 2 and all(r["ok"] is False for r in rows)
    assert "отказала" in rows[0]["why"]


def test_metro_without_a_named_token_refuses_before_touching_the_network(db, monkeypatch):
    """Без именного токена передача отказывается СРАЗУ и называет, чего не хватает.

    ЗАМЕР 19.09.2026, ради которого этот сторож и стоит. POST наполнения получил
    400 «Basket not found» при настоящем коде точки (store_id=10), настоящем
    артикуле в наличии (117189) и с хешем, который сеть выдала нашему же окну;
    метода «создать корзину» у METRO нет вовсе, а её собственная витрина ходит
    адресом с сегментом токена. То есть бестокенный путь отдаёт чтение и не отдаёт
    запись.

    Отсюда два требования, и оба проверяются здесь. Запрос в сеть не уходит —
    ответ известен заранее, и заставлять человека ждать чужой отказ незачем. А
    сказанное ему называет НАСТРОЙКУ, а не «что-то пошло не так»: это починимо,
    и чинится одной строкой, когда сеть выдаст токен.
    """
    from app import location
    from app.connectors import metro, metro_cart

    monkeypatch.setattr(cart.shopstore, "load", lambda chain: {
        "cookies": [{"name": metro_cart.HASH_COOKIE, "domain": "api.metro-cc.ru",
                     "value": "5502aed9f4501b12da05daf6f5347e7b"}]})
    monkeypatch.setattr(location, "for_store", lambda code: None)
    monkeypatch.setattr(metro, "_store_id", lambda loc: "16")
    monkeypatch.setattr(metro_cart, "writable", lambda: False)

    def forbidden(*a, **kw):
        raise AssertionError("запрос ушёл в сеть, хотя ответ известен заранее")

    monkeypatch.setattr(metro_cart, "fill", forbidden)
    monkeypatch.setattr(metro_cart, "read", forbidden)

    got = cart.deliver("metro", "79990000041", metro_naryad([("117189", 1, "Творог")]))

    assert not got["ok"]
    assert "metro_api_token" in got["note"], "не названа настройка, которой не хватает"


def test_metro_promises_no_cart_while_it_cannot_fill_one():
    """Слова про METRO не обещают корзину, которой сегодня нет.

    Обещание «положим сами», сбывающееся когда-нибудь потом, человеку не помогает,
    а мешает: он ждёт наполненную корзину и не собирает её руками. Пока сеть не
    принимает запись без именного токена, ни умение CART, ни слова о нём.
    """
    from app import store_accounts
    from app.shopbrowser import signals

    assert not store_accounts.can("metro", store_accounts.CART)
    assert store_accounts.CART not in store_accounts.ABILITIES["metro"].gives

    words = (handover.NOTE_BY_STORE["metro"] + " "
             + store_accounts.ABILITIES["metro"].note + " "
             + signals.ENTRANCE["metro"].trouble).lower()
    assert "наполняет сам" not in words and "уезжают в неё одним разом" not in words


# ---------- какую кнопку жмёт наряд ----------
#
# ЭТО САМЫЙ ДОРОГОЙ ИЗ НАЙДЕННЫХ ПРОМАХОВ, И СЛОМАН ОН БЫЛ ТИХО. Наряд брал первую
# подходящую кнопку в порядке разметки — то есть на карточке Магнита жал элемент
# плавающей панели (y=3), а настоящая кнопка товара лежала под заголовком (y=523).
# Панель перерисовывалась, нажатие падало, и человек читал «карточка перерисовалась
# под пальцем» при живой рабочей кнопке в пятистах точках ниже.

def test_the_order_clicks_the_button_of_this_product_not_the_first_one(db, shop):
    """Кнопка товара — под заголовком. Приманки выше и ниже не должны победить.

    Расстановка взята с настоящей карточки Магнита (замер 19.09.2026): панель
    сверху, заголовок, кнопка товара, карусель «с этим покупают» внизу.
    """
    panel = Node("В корзину", top=3)                    # плавающая панель сверху
    real = Node("Добавить в корзину", top=523)          # кнопка ЭТОГО товара
    carousel = [Node("В корзину", top=1159) for _ in range(20)]
    shop({TVOROG: Card("Творог Простоквашино 5%. Цена 89 ₽",
                       [panel, real, *carousel], title_top=356)})

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог Простоквашино 5%", "pcs", TVOROG)]))

    assert got["ok"] == ["magnit-1"]
    assert real.clicks == 1, "нажали не кнопку товара"
    assert panel.clicks == 0, "нажали панель — это она перерисовывалась под пальцем"
    assert sum(c.clicks for c in carousel) == 0, "нажали карусель «с этим покупают»"


def test_a_card_without_a_title_still_gets_its_button(db, shop):
    """Заголовка нет — берём самую верхнюю видимую, а не сдаёмся.

    Так бывает на страницах, собранных иначе, и остаться без кнопки там, где она
    есть, хуже, чем нажать верхнюю: верхняя хотя бы чаще всего и есть товарная.
    """
    top = Node("В корзину", top=120)
    lower = Node("В корзину", top=900)
    # title_top = 0 означает «заголовка нет»: ниже него всё, и правило вырождается
    # в «самая верхняя», как и написано в _PICK_JS.
    shop({TVOROG: Card("Творог. Цена 89 ₽", [lower, top], title_top=0)})

    cart.deliver("magnit", "79990000041",
                 naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert top.clicks == 1 and lower.clicks == 0


def test_when_the_page_refuses_to_choose_the_order_still_tries(db, shop, monkeypatch):
    """Страница не дала выбрать — идём перебором, а не бросаем позицию.

    evaluate запрещён политикой страницы или сломался: это не повод оставить
    человека без товара. Перебор снова может взять чужую кнопку, но «может не ту»
    лучше, чем «точно никакой».
    """
    only = Node("В корзину", top=700)
    shop({TVOROG: Card("Творог. Цена 89 ₽", [only], title_top=300)})

    def broken(*a, **kw):
        raise RuntimeError("Execution context was destroyed")

    monkeypatch.setattr(Shop, "evaluate", broken)

    cart.deliver("magnit", "79990000041",
                 naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert only.clicks == 1, "запасной перебор не сработал"


# ---------- последнее слово за корзиной магазина ----------
#
# Наряд верил нажатию. 19.09.2026 на METRO это чуть не стоило неверного отчёта в
# обе стороны сразу: нажатие прошло, витрина товар приняла (ушёл её собственный
# рекламный пиксель product_add_to_cart), а счётчик корзины остался нулём. Значит
# «легло» без взгляда в корзину — это мнение, а не факт.

def test_the_verdict_names_what_the_shop_cart_shows(db, shop):
    """Счётчик магазина попадает в итог: человек видит не только наше мнение."""
    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину", top=500)])})
    page.cart_count = 3

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert got["ok"] == ["magnit-1"]
    assert "3" in got["note"] and "корзина магазина" in got["note"].lower()


def test_an_empty_shop_cart_after_a_successful_order_is_shouted_about(db, shop):
    """Положили, а корзина пуста — это громко, а не «передано».

    Именно этот случай и произошёл живьём. Промолчать здесь значит отправить
    человека оформлять заказ из пустой корзины.
    """
    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину", top=500)])})
    page.cart_count = 0

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert got["ok"] == ["magnit-1"], "нажатие-то прошло"
    assert "ВНИМАНИЕ" in got["note"]
    assert "ноль" in got["note"], "не сказано, что именно не так"


def test_a_cart_without_a_visible_counter_invents_nothing(db, shop):
    """Счётчика не видно — молчим. «Не видно» и «ноль» — разные ответы.

    У Магнита пустая корзина подписана «Перейти в корзину» без числа вовсе, и
    прочитать отсутствие числа как ноль значило бы объявить провалившейся
    удавшуюся передачу.
    """
    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину", top=500)])})
    page.cart_count = None

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert got["ok"] == ["magnit-1"]
    assert "ВНИМАНИЕ" not in got["note"] and "корзина магазина" not in got["note"].lower()


def test_a_failed_order_is_not_second_guessed_by_the_counter(db, shop):
    """Ничего не легло — счётчик не спрашиваем: сверять нечего.

    Лишний поход к странице после провала стоил бы времени и мог бы дописать к
    честному «ни одна позиция не легла» бодрое «корзина показывает 2» — из
    прошлого захода человека.
    """
    page = shop({TVOROG: Card("Творог. Нет в наличии", [])})
    page.cart_count = 2

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert not got["ok"]
    assert "2" not in got["note"]


# ---------- сеть ждёт выбранного магазина ----------

def test_the_order_stops_before_the_first_card_when_no_shop_is_chosen(db, shop):
    """Магазин не выбран — не открываем ни одной карточки и говорим почему.

    ЗАМЕР 19.09.2026, ИЗ-ЗА КОТОРОГО ЭТОТ СТОРОЖ И ПОЯВИЛСЯ. Пока магазин не
    выбран, Магнит держит поверх страницы окно «Выберите магазин или адрес»,
    нажать «в корзину» ДАЁТ, а в корзину не кладёт ничего; /cart показывает то же
    окно. Наряд прошёл бы шестнадцать карточек, отчитался «легло 16» и оставил
    человека с пустой корзиной.
    """
    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину", top=500)])})
    page.home_text = ("Магнит Каталог Выберите магазин или адрес, чтобы посмотреть "
                      "актуальный каталог Не сейчас Выбрать")

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert not got["ok"] and not got["failed"]
    assert page.opened == [], "открыли карточку, хотя класть всё равно было некуда"
    assert "выберите свою точку" in got["note"].lower()


def test_a_chosen_shop_does_not_stop_the_order(db, shop):
    """Обычная витрина наряду не мешает: сторож не должен ловить всех подряд."""
    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину", top=500)])})
    page.home_text = "Магнит Магазин: Санкт-Петербург, Королёва 20 Каталог Акции Корзина"

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert got["ok"] == ["magnit-1"]


def test_the_shop_choice_is_never_made_for_the_person():
    """Магазин за человека не выбираем — ни одной кнопкой, ни в одном месте.

    Это не про удобство, а про его деньги: цена, наличие и доставка у сетей свои
    в каждой точке. Выбрать за него значит молча решить, где он купит и почём.
    Поэтому в сообщении зовём его выбрать, а кода, который жмёт «Выбрать», нет.
    """
    from app.shopbrowser import signals

    assert "выберите свою точку" in signals.NEEDS_STORE_NOTE.lower()
    source = open(cart.__file__, encoding="utf-8").read().lower()
    for word in ("«выбрать»", "click_choose", "выбрать магазин за"):
        assert word not in source, f"в наряде появилось {word}: выбор точки — дело человека"


def test_a_network_that_bans_our_address_is_not_called_a_captcha(db, shop, monkeypatch):
    """Отказ по адресу и капча — разные беды, и совет у них разный.

    ЗАМЕР 19.09.2026 настоящим браузером с нашего сервера: Лента отвечает
    «403 Forbidden. Доступ к сайту lenta.com запрещен. IP: <адрес сервера>», Дикси —
    «Не удалось загрузить сайт». Ни там, ни там проходить нечего. Прежняя единая
    фраза звала человека «пройти проверку „я не робот“» — он искал бы капчу,
    которой нет, а потом решил бы, что сломались мы.
    """
    shop({TVOROG: Card("Творог", [Node("в корзину", top=500)])})
    monkeypatch.setattr(cart.driver, "look",
                        lambda *a, **kw: {"logged_in": True, "guarded": True,
                                          "guard": "forbidden"})

    got = cart.deliver("lenta", "79990000041",
                       naryad([("lenta-1", 1, "Творог", "pcs", TVOROG)]))

    assert "я не робот" not in got["note"]
    assert "не пускает наш сервер" in got["note"]
    assert "телефон" in got["note"], "не сказано, что делать вместо этого"


def test_a_blocked_page_is_not_called_a_captcha_either(db, shop, monkeypatch):
    """Глухая страница: нажимать нечего, и звать нажимать нельзя."""
    shop({TVOROG: Card("Творог", [Node("в корзину", top=500)])})
    monkeypatch.setattr(cart.driver, "look",
                        lambda *a, **kw: {"logged_in": True, "guarded": True,
                                          "guard": "blocked"})

    got = cart.deliver("dixy", "79990000041",
                       naryad([("dixy-1", 1, "Творог", "pcs", TVOROG)]))

    assert "я не робот" not in got["note"]
    assert "проходить там нечего" in got["note"]


def test_a_real_captcha_still_says_so(db, shop, monkeypatch):
    """А там, где проверка настоящая, человека по-прежнему зовут её пройти."""
    shop({TVOROG: Card("Творог", [Node("в корзину", top=500)])})
    monkeypatch.setattr(cart.driver, "look",
                        lambda *a, **kw: {"logged_in": True, "guarded": True,
                                          "guard": "check"})

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert "я не робот" in got["note"] and "пройдите" in got["note"].lower()


def test_the_person_is_told_which_point_the_calculation_used(db, shop, monkeypatch):
    """Зовём выбрать магазин — и называем тот, по которому считали.

    Отправить человека выбирать вслепую — плохая половина честности: он выберет
    соседнюю точку, корзина соберётся по другим ценам, и расчёт, который он
    видел, окажется не про его покупку. Ровно это уже случилось со ссылкой Ленты:
    она открылась в чужой точке, а цены человек видел по своей.
    """
    from app import places

    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину", top=500)])})
    page.home_text = "Магнит Выберите магазин или адрес, чтобы посмотреть актуальный каталог"
    monkeypatch.setattr(places, "points", lambda chain: [
        places.Point(chain, "798387", "Санкт-Петербург, Королёва 20 корп.1", "адрес места")])

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert "Королёва 20" in got["note"], "не названа точка, по которой считали"


def test_an_unknown_point_is_not_invented(db, shop, monkeypatch):
    """Точки не знаем — молчим. Выдуманный адрес увёл бы человека в чужой магазин."""
    from app import places

    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину", top=500)])})
    page.home_text = "Магнит Выберите магазин или адрес, чтобы посмотреть актуальный каталог"
    monkeypatch.setattr(places, "points", lambda chain: [])

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert "считало по" not in got["note"]
    assert "выберите свою точку" in got["note"].lower(), "просьба выбрать должна остаться"


# ---------- корзина собирается в точке расчёта ----------

def test_the_window_gets_the_point_the_calculation_used(db, shop, monkeypatch):
    """Точка расчёта уезжает в окно магазина ДО первого перехода.

    ЗАМЕР 20.09.2026, ИЗ-ЗА КОТОРОГО ЭТО ПОЯВИЛОСЬ. Чистое окно Магнита приходит
    с уже поставленной кукой shopCode=%22992301%22 — это магазин, который витрина
    выбрала СЕБЕ САМА. То же молоко стоит в нём 84,99 ₽, а расчёт показывал
    человеку 89,99 ₽ по его точке. Корзина собиралась настоящая, сумма в ней — не
    та, и заметить это человек мог только на кассе. Та же беда уже случалась со
    ссылкой Ленты, открывшейся в чужой точке.

    Порядок здесь не косметика: подставленная после первой страницы кука
    опоздала бы ровно так же, как опаздывает подставленный после входа ключ.
    """
    import app.location as client_place
    from app.models import Location

    seen: dict = {}
    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину", top=500)])})
    monkeypatch.setattr(client_place, "for_store",
                        lambda code: Location(store_id="264856", shop_type="MM", delivery=True))
    monkeypatch.setattr(cart.driver, "open_store",
                        lambda chain, phone, **kw: seen.update(kw) or None)

    cart.deliver("magnit", "79990000041", naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    got = {c["name"]: c["value"] for c in (seen.get("state") or {}).get("cookies", [])}
    assert got.get("shopCode") == "%22264856%22", "корзина уехала бы в магазин витрины"
    assert got.get("mg_at") == "ключ", "вход потерялся вместе с точкой"


def test_a_shop_with_no_online_storefront_stops_the_order_instead_of_filling_a_strangers(
        db, shop, monkeypatch):
    """Из точки нельзя заказать — останавливаемся, а не собираем в чужой.

    Замер 20.09.2026: карточка Магнита в магазине формата MM_MINI отвечает «Не
    удалось загрузить» — и так на двух разных магазинах этого формата в двух
    городах. Интернет-витрины у формата нет вовсе.

    Соблазн здесь ровно один и он дорогой: не передавать точку и дать витрине
    взять свой магазин. Корзина тогда наполнится — по ценам, которых человек не
    видел. Это и есть та самая тихая ошибка, ради которой всё писалось.
    """
    import app.location as client_place
    from app.models import Location

    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину", top=500)])})
    monkeypatch.setattr(client_place, "for_store",
                        lambda code: Location(store_id="628425", shop_type="MM_MINI"))

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert not got["ok"] and not got["failed"]
    assert page.opened == [], "открыли карточку в магазине, из которого не заказать"
    assert "у дома мини" in got["note"]
    assert "позже" not in got["note"].lower(), "совет ждать здесь не работает никогда"


def test_the_verdict_names_the_point_the_cart_was_filled_in(db, shop, monkeypatch):
    """Удавшаяся передача называет точку: проверить её человеку больше нечем."""
    import app.location as client_place
    from app import places
    from app.models import Location

    shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину", top=500)])})
    monkeypatch.setattr(client_place, "for_store",
                        lambda code: Location(store_id="264856", shop_type="MM"))
    monkeypatch.setattr(places, "points", lambda chain: [
        places.Point(chain, "264856", "Москва, Авиаконструктора Микояна, 12", "адрес места")])

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert got["ok"] == ["magnit-1"]
    assert "Микояна, 12" in got["note"]


def test_a_card_that_never_opens_does_not_blame_the_markup(db, shop):
    """«Не удалось загрузить» — не поломка вёрстки, и говорить так нельзя.

    Слова «сайт» в этой странице нет, поэтому прежний сторож её не ловил: разбор
    считал её обычной витриной, кнопки на ней не находил и объявлял человеку
    «похоже, вёрстку переделали» — шестнадцать раз подряд. То есть приложение
    винило магазин в том, чего тот не делал, и звало чинить несуществующее.
    """
    shop({TVOROG: Card("Не удалось загрузить\nПопробуйте обновить страницу или зайдите позже")})

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    why = got["failed"][0]["why"]
    assert "вёрстку" not in why, "снова винят магазин в том, чего он не делал"
    assert "выберите другую точку" in why.lower()


def test_the_order_closes_the_shop_window_itself_when_the_point_is_already_ours(
        db, shop, monkeypatch):
    """Окно выбора магазина наряд закрывает сам — потому что магазин уже наш.

    ЗАМЕР 20.09.2026, РАЗОШЕДШИЙСЯ С ДОМАШНИМ. С рабочей машины витрина Магнита
    окна не показывает вовсе, а НАШЕМУ СЕРВЕРУ показывает его даже при верной
    куке точки: дело в адресе, с которого мы стучимся, а не в невыбранном
    магазине. Передача упиралась в вопрос, на который ответ уже был дан.

    После «Не сейчас» окно уходит, кука остаётся той же, кнопки «В корзину» на
    месте, шапка пишет «Доставка • Экспресс». Это отказ выбирать, а не выбор.
    """
    import app.location as client_place
    from app.models import Location

    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину", top=500)])})
    page.home_text = "Магнит Выберите магазин или адрес, чтобы посмотреть актуальный каталог"
    page.home_buttons = [Dismiss(page)]
    monkeypatch.setattr(client_place, "for_store",
                        lambda code: Location(store_id="264856", shop_type="MM"))

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert got["ok"] == ["magnit-1"], "наряд встал там, где вопрос уже был закрыт"
    assert page.opened == [TVOROG]


def test_without_a_point_the_window_is_not_closed_behind_the_persons_back(db, shop, monkeypatch):
    """Точки нет — окно не трогаем: закрыть его значило бы согласиться на чужой магазин.

    Вся разница между честным и нечестным нажатием здесь. Когда точка передана,
    за окном стоит магазин расчёта и «Не сейчас» его оставляет. Когда не
    передана — там магазин витрины, и то же нажатие молча согласилось бы на его
    цены, которых человек не видел.
    """
    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину", top=500)])})
    page.home_text = "Магнит Выберите магазин или адрес, чтобы посмотреть актуальный каталог"
    page.home_buttons = [Dismiss(page)]

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert page.home_buttons[0].clicks == 0, "согласились за человека на чужой магазин"
    assert not got["ok"]
    assert "выберите свою точку" in got["note"].lower()


def test_a_window_that_will_not_close_still_stops_the_order(db, shop, monkeypatch):
    """«Не сейчас» не помогло — прежняя честная остановка остаётся на месте."""
    import app.location as client_place
    from app.models import Location

    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину", top=500)])})
    page.home_text = "Магнит Выберите магазин или адрес, чтобы посмотреть актуальный каталог"
    # Кнопка нажимается, а окно остаётся: витрина могла переделать окно в любой день.
    page.home_buttons = [Dismiss(page, after=page.home_text)]
    monkeypatch.setattr(client_place, "for_store",
                        lambda code: Location(store_id="264856", shop_type="MM"))

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert page.opened == [], "пошли по карточкам сквозь окно, которое не ушло"
    assert "выберите свою точку" in got["note"].lower()


def test_the_counter_does_not_read_prices_off_product_tiles(db, shop):
    """Счётчик корзины не берёт число из товарной плитки.

    ЖИВОЙ СЛУЧАЙ 20.09.2026, БОЕВОЙ СЕРВЕР. Сквозной наряд положил ОДИН товар, а
    отчёт сказал «Корзина магазина показывает 208 позиц.». Под «узел, в тексте
    которого есть „корзин"» попадала каждая плитка товара — внутри неё своя
    кнопка «В корзину», — и первым числом в её тексте оказывалась цена или число
    отзывов: «Финальная цена 249.99 ₽ … 9738 отзывов В корзину».

    Соврать этим числом хуже, чем промолчать: «208 позиц.» выглядит настоящим
    счётчиком и не вызывает подозрений — человек идёт оформлять заказ.

    Отличаем по размеру узла: настоящий счётчик Магнита несёт три потомка и
    подпись «Перейти в корзину», плитка — тридцать пять и больше.
    """
    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину", top=500)])})
    page.cart_nodes = [
        (3, "Перейти в корзину Корзина 1"),
        (35, "Финальная цена 249.99 ₽ Сыр Брест-Литовск 5 · 9738 отзывов В корзину"),
        (45, "Финальная цена 74.99 ₽ Томаты 4.6 · 208 отзывов 500г В корзину"),
    ]

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert "показывает 1 позиц" in got["note"], got["note"]
    assert "208" not in got["note"] and "9738" not in got["note"]


def test_an_empty_header_counter_is_still_not_a_zero(db, shop):
    """Счётчик без числа — «не видно», а не ноль: у Магнита пустая корзина без числа."""
    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("в корзину", top=500)])})
    page.cart_nodes = [(3, "Перейти в корзину Корзина")]

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert got["ok"] == ["magnit-1"]
    assert "ВНИМАНИЕ" not in got["note"], "отсутствие числа принято за ноль"
    assert "показывает" not in got["note"]


def test_a_click_the_network_ignored_is_not_called_a_success(db, shop):
    """Нажали, а сеть не отправила к корзине ничего — значит не легло.

    ЗАМЕР 20.09.2026, БОЕВОЙ СЕРВЕР И РАБОЧАЯ МАШИНА ОДИНАКОВО. Сквозной наряд
    нашёл правильную кнопку («Добавить в корзину», не панель и не карусель),
    нажал её и отчитался «легло» — а карточка после перезагрузки по-прежнему
    предлагала положить товар. Подсмотр изнутри страницы объяснил: после
    нажатия витрина отправила два запроса, и оба к счётчикам посещений
    (mc.yandex.com, sync.bumlam.com). К корзине — ни одного.

    Ни один прежний признак этого не ловил: кнопка нашлась, нажатие не упало,
    счётчика корзины у Магнита нет вовсе. Человек прочитал бы «легло 16».
    """
    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("добавить в корзину", top=500)])})
    page.cart_seen = []

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert not got["ok"], "нажатие в пустоту снова объявлено успехом"
    assert "не обратилась к своей корзине" in got["failed"][0]["why"]


def test_a_network_that_did_send_the_request_is_not_doubted(db, shop):
    """Запрос ушёл — проверка молчит и успеха не отменяет.

    Ответ этой проверки ОДНОСТОРОННИЙ нарочно: «запросов не было» доказывает,
    что не легло, а «запрос ушёл» не доказывает, что легло, — сеть могла
    ответить отказом. Объявлять по нему успех значило бы завести вторую ложь
    вместо первой.
    """
    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("добавить в корзину", top=500)])})
    page.cart_seen = [["https://magnit.ru/webgate/v1/cart/add", 200]]

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert got["ok"] == ["magnit-1"]


def test_a_network_we_never_measured_is_not_accused(db, shop, monkeypatch):
    """Сеть, чьих запросов никто не видел, проверкой не судим.

    Выдуманный список слов дал бы ЛОЖНОЕ «не легло» на работающей передаче — а
    это хуже молчания: человек полез бы чинить целое вместо того, чтобы просто
    оформить собранный заказ.
    """
    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("добавить в корзину", top=500)])})
    page.cart_seen = []
    monkeypatch.setitem(cart.SENDS_CART, "magnit", ())

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert got["ok"] == ["magnit-1"]
    assert page.watching is False, "поставили счётчик там, где считать не умеем"


def test_a_stranger_counter_is_not_mistaken_for_the_shops_own_cart(db, shop):
    """Чужой счётчик посещений не считается обращением сети к своей корзине.

    ЖИВОЙ СЛУЧАЙ 20.09.2026, И ОН СЛОМАЛ САМУ ПРОВЕРКУ. Первая её версия
    отбирала запросы по слову в адресе, и «единственным запросом к корзине»
    оказался пиксель Яндекс.Метрики:

        POST mc.yandex.ru/watch/56708149/1
             ?page-url=goal://magnit.ru/product_productPage_buyBox_toCart_click

    Слово «cart» в нём есть, ответ 200, летит он при КАЖДОМ нажатии — то есть
    ноль не наступал никогда, и сторож, поставленный ловить молчание сети, не
    защищал ни от чего, выглядя при этом рабочим.

    Имя сети в чужих параметрах — тоже не повод: «magnit.ru» написано прямо в
    адресе Метрики, и сравнение по строке снова приняло бы чужое за своё.
    """
    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("добавить в корзину", top=500)])})
    page.cart_seen = [
        ["https://mc.yandex.ru/watch/56708149/1?page-url=goal%3A%2F%2Fmagnit.ru"
         "%2Fproduct_productPage_buyBox_toCart_click", 200],
        ["https://sync.bumlam.com/gp/", 200],
    ]

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert not got["ok"], "чужой счётчик снова сошёл за обращение к корзине"
    assert "не обратилась к своей корзине" in got["failed"][0]["why"]


def test_the_networks_refusal_is_named_by_its_code(db, shop):
    """Сеть отказала — говорим чем именно, а не «что-то пошло не так».

    Код ответа сети — это готовый следующий шаг для человека: 401 значит
    «войдите заново», и сказать это словами дешевле, чем заставить его гадать.
    """
    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("добавить в корзину", top=500)])})
    page.cart_seen = [["https://magnit.ru/webgate/v1/cart/add", 401]]

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert not got["ok"]
    assert "не считает вас вошедшим" in got["failed"][0]["why"]


def test_one_refusal_among_successes_does_not_condemn_the_transfer(db, shop):
    """Витрина дёргает корзину и сама по себе — один отказ среди удачных молчит.

    Объявить передачу провалившейся по единственному чужому отказу значило бы
    завести новую ложь вместо той, которую мы только что убрали.
    """
    page = shop({TVOROG: Card("Творог. Цена 89 ₽", [Node("добавить в корзину", top=500)])})
    page.cart_seen = [["https://magnit.ru/webgate/v1/cart/count", 404],
                      ["https://magnit.ru/webgate/v1/cart/add", 200]]

    got = cart.deliver("magnit", "79990000041",
                       naryad([("magnit-1", 1, "Творог", "pcs", TVOROG)]))

    assert got["ok"] == ["magnit-1"]


def test_the_rule_that_tells_our_requests_from_strangers_is_checked_directly():
    """Правило отбора своих запросов — отдельно и поимённо.

    Оно живёт в питоне, а не в скрипте страницы, именно ради этой проверки:
    в скрипте его не смог бы потрогать ни один тест, и там оно сразу оказалось
    неверным.
    """
    seen = [
        ["https://mc.yandex.ru/watch/1?page-url=goal://magnit.ru/toCart_click", 200],
        ["https://magnit.ru/product/123", 200],            # свой адрес, но не корзина
        ["https://magnit.ru/webgate/v1/cart/add", 201],     # своё и корзина
        ["https://cart.example.com/add", 200],              # «cart» в чужом хозяине
        # Короткие адреса («/webgate/v1/cart») сюда не доходят: обёртка в
        # странице разворачивает их в полные ещё до записи — иначе свой же
        # запрос, написанный коротко, остался бы без хозяина и не засчитался.
        ["/webgate/v1/basket", 200],
    ]

    assert cart.own_cart_calls(seen, "magnit") == [201]
    assert cart.own_cart_calls(seen, "vkusvill") == [], "сеть без замера судить нечем"
