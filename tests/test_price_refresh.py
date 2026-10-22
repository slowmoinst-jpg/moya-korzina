"""Свежесть цен: знакомые строки перечитываются, самая старая цена — первой.

ЗАМЕР 20.10.2026 (боевая база). Сборщики читают карточку только при первом знакомстве
с артикулом, а знакомым лишь продлевают «видели»: цену никто не перечитывает. У
ВкусВилла 13 379 строк, и 10 074 из них несли цену, записанную до того, как у базы
появилась её дата, ещё 3 132 — старше недели. Проверка восьми случайных карточек
вживую: 3 из 8 разошлись с витриной больше чем на процент, худшая — 129 ₽ в базе
против 167 ₽ на витрине (+29,5 %). У Ленты так же: 9 627 цен старше недели, а карточка
отдаёт и цену, и остаток.

Сравнение сетей по такой цене показывает экономию, которой нет, — а ради него единая
база и строится. Поэтому у каждой сети появился виток: за обход перечитывается не
больше своего потолка карточек, очередь — от самой старой цены к свежей.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import refresh, store  # noqa: E402
from app.catalog.crawlers.lenta import LentaCrawler, OK  # noqa: E402
from app.catalog.crawlers.vkusvill import VkusvillCrawler  # noqa: E402
from app.catalog.model import ChainProduct  # noqa: E402


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


def _seen(chain: str, sku: str, stamp: str | None) -> None:
    """Поставить строке дату её цены (или убрать её — так лежали 10 074 строки ВкусВилла)."""
    with store.connect() as conn:
        conn.execute("UPDATE chain_products SET price_seen=? WHERE chain=? AND sku=?",
                     (stamp, chain, str(sku)))
        conn.commit()


def _price(sku: str, price: float, name: str = "Товар", **fields) -> ChainProduct:
    return ChainProduct(sku=sku, name=name, price=price, **fields)


# ---------- очередь ----------
def test_the_queue_starts_with_the_oldest_price(base):
    """Очередь — от самой старой цены к свежей: виток обязан замыкаться."""
    base.upsert_products("vkusvill", [_price("old", 100.0)], seen_at="2026-09-16T10:00:00")
    base.upsert_products("vkusvill", [_price("mid", 200.0)], seen_at="2026-10-01T10:00:00")
    base.upsert_products("vkusvill", [_price("new", 300.0)], seen_at="2026-10-18T10:00:00")

    assert store.stale_price_skus("vkusvill", 10) == ["old", "mid", "new"]
    assert store.stale_price_skus("vkusvill", 2) == ["old", "mid"]
    assert store.stale_price_skus("vkusvill", 0) == []


def test_rows_without_a_price_are_not_in_the_queue(base):
    """Цены нет — это работа добора подробностей, а не свежести: перечитывать нечего."""
    base.upsert_products("vkusvill", [_price("priced", 100.0)], seen_at="2026-09-16T10:00:00")
    base.upsert_products("vkusvill", [ChainProduct(sku="bare", name="Без цены")],
                         seen_at="2026-09-16T10:00:00")

    assert store.stale_price_skus("vkusvill", 10) == ["priced"]


def test_a_price_without_its_date_is_the_oldest(base):
    """У строк, записанных до появления даты цены, другой даты нет — они первые.

    Ровно так лежали 10 074 строки ВкусВилла: цена из сентября без price_seen. Считаем
    их по дате первого знакомства, иначе они не попадали бы в очередь никогда.
    """
    base.upsert_products("vkusvill", [_price("dateless", 100.0)], seen_at="2026-09-16T10:00:00")
    base.upsert_products("vkusvill", [_price("dated", 200.0)], seen_at="2026-10-15T10:00:00")
    _seen("vkusvill", "dateless", None)

    assert store.stale_price_skus("vkusvill", 5) == ["dateless", "dated"]


def test_a_dead_row_is_not_in_the_queue(base):
    """Пропавший товар не перечитываем: очередь — только по живым строкам."""
    base.upsert_products("vkusvill", [_price("gone", 100.0)], seen_at="2026-09-16T10:00:00")
    with store.connect() as conn:
        conn.execute("UPDATE chain_products SET active=0 WHERE sku='gone'")
        conn.commit()

    assert store.stale_price_skus("vkusvill", 10) == []


def test_a_just_read_price_is_not_asked_again(base):
    """Порог возраста: цену, прочитанную в этом обходе, следующий не переспрашивает.

    Без порога очередь всегда непуста — только что прочитанная цена остаётся «самой
    старой» среди прочих, и сборщик ходил бы по одним и тем же карточкам каждый обход,
    тратя потолок впустую. Поймал старый тест про «не найденные» артикулы Ленты.
    """
    base.upsert_products("vkusvill", [_price("fresh", 100.0)])          # сейчас
    base.upsert_products("vkusvill", [_price("old", 200.0)], seen_at="2026-09-16T10:00:00")

    assert store.stale_price_skus("vkusvill", 10) == ["old"]
    assert store.stale_price_skus("vkusvill", 10, older_than_hours=0) == ["old", "fresh"]


# ---------- ВкусВилл ----------
def test_vkusvill_rereads_the_cards_of_the_stalest_prices(monkeypatch, base):
    """Сборщик перечитывает карточки знакомых строк и обновляет цену в базе."""
    base.upsert_products("vkusvill", [_price("52", 129.0, "Сметана 25%, 350 г")],
                         seen_at="2026-09-16T10:00:00")
    crawler = VkusvillCrawler()
    crawler.refresh = 5
    monkeypatch.setattr(crawler, "universe", lambda: ["52"])       # новинок нет
    monkeypatch.setattr(crawler, "details", lambda sku: {
        "xml_id": int(sku), "name": "Сметана 25%, 350 г",
        "price": {"current": 167, "currency": "RUB"}, "unit": "шт",
    })

    result = refresh.run_chain(crawler)

    assert result["status"] == "ok"
    stored = next(r for r in base.all_products(active_only=True) if str(r["sku"]) == "52")
    assert stored["price"] == 167.0, "цена на витрине выросла — база должна это увидеть"
    history = base.price_history("vkusvill", "52")
    assert history and history[0]["price"] == 167.0, "смена цены ложится в историю (свежие первыми)"


def test_vkusvill_refresh_stops_at_its_own_cap(monkeypatch, base):
    """За обход — не больше refresh_per_run карточек: 13 379 зараз не спрашивают."""
    for n in range(1, 8):
        base.upsert_products("vkusvill", [_price(str(n), 100.0 + n)],
                             seen_at="2026-09-16T10:00:00")
    crawler = VkusvillCrawler()
    crawler.refresh = 3
    asked: list[str] = []

    def details(sku):
        asked.append(sku)
        return {"xml_id": int(sku), "name": f"Товар {sku}", "price": {"current": 999}}

    monkeypatch.setattr(crawler, "universe", lambda: [])
    monkeypatch.setattr(crawler, "details", details)

    refresh.run_chain(crawler)

    assert len(asked) == 3, asked


def test_vkusvill_refresh_can_be_switched_off(monkeypatch, base):
    """Ноль — не перечитывать вовсе (так видно, сколько цен успело состариться)."""
    base.upsert_products("vkusvill", [_price("52", 129.0, "Сметана")],
                         seen_at="2026-09-16T10:00:00")
    crawler = VkusvillCrawler()
    crawler.refresh = 0
    monkeypatch.setattr(crawler, "universe", lambda: [])
    called: list[str] = []

    def details(sku):
        called.append(sku)
        return None

    monkeypatch.setattr(crawler, "details", details)

    assert list(crawler.crawl()) == []
    assert called == []


# ---------- Лента ----------
LIVE_CARD = {
    "id": 21, "name": "Горбуша натуральная, 240г", "price": 219.99, "stock": 7,
    "package": "240г", "slug": "rk-gorbusha-naturalnaya-rossiya-240g",
    "url": "https://lenta.com/product/rk-gorbusha-naturalnaya-rossiya-240g-21/",
    "brand": "PL Lenta TempSubst", "ean": "14607019900708",
}


def test_lenta_rereads_a_stale_price_and_its_stock(monkeypatch, base):
    """У Ленты карточка отдаёт и цену, и остаток — оба старели вместе."""
    base.upsert_products("lenta", [_price("21", 196.99, "Горбуша натуральная, 240г",
                                          brand="PL Lenta TempSubst",
                                          barcode="14607019900708")],
                         seen_at="2026-09-16T10:00:00")
    crawler = LentaCrawler(hub=359)
    crawler.backfill = 0
    crawler.price_refresh = 5
    monkeypatch.setattr(crawler, "universe", lambda: ["21"])
    monkeypatch.setattr(crawler, "details", lambda sku: (dict(LIVE_CARD), OK))

    result = refresh.run_chain(crawler)

    assert result["status"] == "ok"
    stored = next(r for r in base.all_products(active_only=True) if str(r["sku"]) == "21")
    assert stored["price"] == 219.99
    assert stored["in_stock"] == 1


def test_the_refresh_takes_only_what_fits_under_the_sweep_ceiling(monkeypatch, base):
    """Добор не съедает остаток потолка: лучше меньше карточек, чем неполная ночь.

    Замер 20.10.2026: у Ленты ночь стала 6 000 новых + 2 000 марки + 2 000 свежести =
    167 мин при потолке 180. Если новый товар шёл медленнее обычного, последний проход
    упирался в потолок, и обход записывался НЕПОЛНЫМ (partial) — хотя мог спокойно
    закончиться, просто с меньшим добором.
    """
    import time

    for n in range(1, 9):
        base.upsert_products("lenta", [_price(str(n), 100.0 + n, f"Товар {n}",
                                              brand="Бренд", barcode=f"46000000000{n:02d}")],
                             seen_at="2026-09-16T10:00:00")
    crawler = LentaCrawler(hub=359)
    crawler.backfill = 0
    crawler.price_refresh = 100          # хочу много...
    crawler.pace.gap = 1.0               # ...по секунде на карточку
    crawler.deadline = time.monotonic() + 3.0   # ...но до потолка 3 секунды
    asked: list[str] = []

    def details(sku):
        asked.append(sku)
        return {"id": int(sku), "name": f"Товар {sku}", "price": 10.0, "stock": 1}, OK

    monkeypatch.setattr(crawler, "universe", lambda: [])
    monkeypatch.setattr(crawler, "details", details)

    list(crawler.crawl())

    assert 0 < len(asked) <= 4, f"взято {len(asked)} карточек на 3 секунды — не по времени"


def test_without_a_ceiling_the_whole_cap_is_taken(monkeypatch, base):
    """Потолка нет (ручной прогон — `--once` без бюджета) — берём весь свой потолок."""
    for n in range(1, 6):
        base.upsert_products("lenta", [_price(str(n), 100.0 + n, f"Товар {n}",
                                              brand="Бренд", barcode=f"46000000000{n:02d}")],
                             seen_at="2026-09-16T10:00:00")
    crawler = LentaCrawler(hub=359)
    crawler.backfill = 0
    crawler.price_refresh = 3
    crawler.deadline = None
    asked: list[str] = []

    def details(sku):
        asked.append(sku)
        return {"id": int(sku), "name": f"Товар {sku}", "price": 10.0, "stock": 1}, OK

    monkeypatch.setattr(crawler, "universe", lambda: [])
    monkeypatch.setattr(crawler, "details", details)

    list(crawler.crawl())

    assert len(asked) == 3, asked


def test_lenta_price_refresh_goes_last(monkeypatch, base):
    """Порядок важен: новые артикулы и добор марки впереди, свежесть цен — последней.

    Иначе ночь, упёршаяся в потолок на сети, тратила бы время на цены и не успевала
    забрать новые товары — а без них каталог не растёт.
    """
    base.upsert_products("lenta", [_price("21", 196.99, "Горбуша, 240г",
                                          brand="PL Lenta TempSubst",
                                          barcode="14607019900708")],
                         seen_at="2026-09-16T10:00:00")
    crawler = LentaCrawler(hub=359)
    crawler.backfill = 0
    crawler.price_refresh = 5
    order: list[str] = []

    def details(sku):
        order.append(sku)
        card = dict(LIVE_CARD, id=int(sku) if sku.isdigit() else 99, price=10.0,
                    ean=None, stock=1)
        return card, OK

    monkeypatch.setattr(crawler, "universe", lambda: ["21", "new"])
    monkeypatch.setattr(crawler, "details", details)

    list(crawler.crawl())

    assert order[0] == "new", f"новые артикулы должны идти первыми: {order}"
    assert order[-1] == "21", f"свежесть цен должна идти последней: {order}"
