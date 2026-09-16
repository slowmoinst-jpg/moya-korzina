"""Передача корзины в магазин и прайс-лист руками — способы для сетей без MCP.

Магнит, Пятёрочка и Дикси не принимают готовую корзину: у первого в диплинках
объявлены только карточки товаров, вторые два закрыты наглухо. Здесь проверяется,
что мы это честно отражаем и не обещаем больше, чем можем.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, handover, pricelist, repo  # noqa: E402
from app.db import init_db  # noqa: E402
from app.models import Product  # noqa: E402


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
