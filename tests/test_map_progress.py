"""Разбор перечня сети: сколько артикулов в перечне и сколько ещё ждёт разбора.

ЗАМЕР 20.10.2026 (боевая база, перечень спрашивали кодом самого обхода). В перечне Ленты
82 953 артикула, в базе 37 336 — осталось 45 617, при 6 000 карточек за обход и ~5 000
новых за ночь это девять ночей. У ВкусВилла осталось 2 артикула: перечень разобран.

Без этого числа «все товары разобраны» не проверить: 37 тыс. строк Ленты одинаково
выглядят и когда это половина каталога, и когда это всё. Сборщик получает перечень сам,
поэтому числа бесплатны — их надо только записать (store.mark_map) и показать проверкой.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import checkup, refresh, store  # noqa: E402
from app.catalog.crawlers.lenta import LentaCrawler, OK  # noqa: E402


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


def test_the_mark_is_remembered_and_read_back(base):
    store.mark_map("lenta", total=82953, known=37334, left=45617, added=5048)

    marks = store.map_marks()

    assert marks["lenta"]["total"] == 82953
    assert marks["lenta"]["known"] == 37334
    assert marks["lenta"]["left"] == 45617
    assert marks["lenta"]["added"] == 5048


def test_the_crawler_says_how_much_of_its_list_is_left(monkeypatch, base):
    """Сборщик считает остаток по своему перечню: «мёртвые» артикулы в него не входят."""
    from app.catalog.model import ChainProduct

    base.upsert_products("lenta", [ChainProduct(sku="1", name="Уже в базе")])
    crawler = LentaCrawler(hub=359)
    crawler.cap = 0
    crawler.backfill = 0
    crawler.price_refresh = 0
    monkeypatch.setattr(crawler, "universe", lambda: ["1", "2", "3"])

    list(crawler.crawl())

    assert crawler.map_total == 3
    assert crawler.map_known == 1
    assert crawler.map_left == 2


def test_the_sweep_writes_the_mark(monkeypatch, base):
    """Ночной обход записывает остаток сам — отдельной команды человеку не нужно."""
    crawler = LentaCrawler(hub=359)
    crawler.cap = 10
    crawler.backfill = 0
    crawler.price_refresh = 0
    monkeypatch.setattr(crawler, "universe", lambda: ["11", "12", "13"])
    monkeypatch.setattr(crawler, "details",
                        lambda sku: ({"id": int(sku), "name": f"Товар {sku}",
                                      "price": 100.0, "stock": 1}, OK))

    refresh.run_chain(crawler)

    mark = store.map_marks()["lenta"]
    assert mark["total"] == 3
    assert mark["left"] == 3
    assert mark["added"] == 3


def test_a_blocked_chain_does_not_overwrite_the_mark(monkeypatch, base):
    """Сеть не открылась — числа перечня не записываем: они были бы нулями и врали."""
    crawler = LentaCrawler(hub=359)

    def universe():
        raise RuntimeError("сеть закрылась")

    monkeypatch.setattr(crawler, "universe", universe)

    refresh.run_chain(crawler)

    assert store.map_marks() == {}


# ---------- что видит проверка базы ----------
def test_the_snapshot_shows_the_parsing_progress(base):
    store.mark_map("lenta", total=82953, known=37334, left=45617, added=5048)

    snap = checkup.snapshot()

    assert snap["maps"]["lenta"]["left"] == 45617


def test_a_stalled_parsing_is_a_complaint(base):
    """Остаток есть, а новых строк за обход ноль — разбор встал."""
    store.mark_map("lenta", total=82953, known=37334, left=45617, added=0)

    notes = checkup.complaints(checkup.snapshot(), None)

    assert any("разбор стоит" in note and "lenta" in note for note in notes), notes


def test_a_working_parsing_is_not_a_complaint(base):
    store.mark_map("lenta", total=82953, known=37334, left=45617, added=5048)

    notes = checkup.complaints(checkup.snapshot(), None)

    assert not any("разбор стоит" in note for note in notes), notes


def test_a_finished_list_is_not_a_complaint(base):
    """Перечень разобран — замечания нет, даже если новых строк не было."""
    store.mark_map("vkusvill", total=13378, known=13376, left=0, added=0)

    notes = checkup.complaints(checkup.snapshot(), None)

    assert not any("vkusvill" in note and "разбор" in note for note in notes), notes


def test_the_report_prints_how_much_is_left(base):
    store.mark_map("lenta", total=82953, known=37334, left=45617, added=5048)

    text = checkup.render(checkup.snapshot(), [])

    assert "разбор перечня сетей" in text
    assert "разобрано 37336 из 82953" in text
    assert "осталось 45617" in text
