"""Сокращение чека без марки: якорь из самой строки чека и двухбуквенные обрывки.

ЗАЧЕМ (замер 19.10.2026, task-19). Товары Пятёрочки и Самоката собирает закладка
«Забрать цены» (app/pricebundle.import_prices): она кладёт только имя, вес, единицу,
ссылку и цену, поэтому у собранной строки НЕТ марки. `_abbrev` требовал марку
безусловно, и правило сокращений — написанное ровно под строки чеков — для таких
товаров не срабатывало вовсе. Здесь сторожа двух правок:

1. Нет марки — якорь берётся из строки чека: самое длинное слово от четырёх букв,
   начинающее слово кандидата («сараф» → «сарафаново», «тема» → «тема»). Без якоря
   связи по-прежнему нет: «Коктейль молочный» не «Молоко». Связь без марки — только
   СЛАБАЯ (решение 19.10.2026): марка и есть доказательство имени, без неё совпадение
   слов — догадка. Такая пара ложится кандидатом, ждёт человека и в наряд не идёт.
2. Слово-обрывок от двух букв («гл» → «глазированный») покрывает слово кандидата так
   же, как слово от трёх букв. Раньше оно выпадало из said, и «глазированный» в
   названии оставалось непокрытым.

ЧЕГО ЭТА ПРАВКА НЕ ДЕЛАЕТ (честно). Живые строки владельца она одна не закрывает:
«…дет 210 г» — в своде письма «детский» даёт «deckii» (диграф ts→c), и «дет» не
является началом «deckii»; в мороженом не покрыт сорт «сливочное»; а «СВИТЛ.Сырок
твор.гл.с ван.45г» — это Свитлогорье с ванилью, а не Вкуснотеево «в шоколадной
глазури 40 г». Эти строки закрывает подтверждение человеком, а не догадка матчера.
"""
from __future__ import annotations

import pytest

from app import config, repo
from app.catalog import link, refresh, store
from app.catalog.model import ChainProduct as P
from app.db import init_db
from app.models import Product


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    link._SEEN.clear()
    yield


@pytest.fixture
def user_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "user.db"))
    init_db()


def stores() -> dict[str, int]:
    return {s.code: s.id for s in repo.list_stores()}


def product(name: str, **kw) -> int:
    return repo.upsert_product(Product(id=None, name=name, unit=kw.pop("unit", "pcs"), **kw))


def prep(name: str, weight_g: float, *, receipt: bool) -> object:
    return link._prep({"id": 0, "chain": "mine" if receipt else "catalog", "name": name,
                       "brand": None, "weight_g": weight_g, "unit": "pcs"}, receipt=receipt)


def test_collected_row_without_brand_becomes_a_candidate(catalog, user_db):
    """Строка закладки без марки: якорь из чека находит товар, но только кандидатом.

    До правки `_abbrev` отдавал 0 уже на «нет марки», хотя все слова чека покрыты:
    «сараф» → «сарафаново», «мор» → «мороженое», «зеф» → «зефир», «эск» → «эскимо»,
    «ван» → «ванильное». Марки у строки закладки нет, поэтому связь слабая: артикул
    висит кандидатом на «Связях», в наряд сам не идёт и ждёт человека — «не тот товар
    хуже, чем никакой».
    """
    store.upsert_products("pyaterochka", [P(
        "4455673", "Мороженое Сарафаново Зефир Эскимо ванильное 75 г", weight_g=75)])
    refresh.match_all()
    pid = product("Сараф мор зеф эск ван 75", weight_g=75)

    link.link_products()

    assert repo.master_of(pid)["method"] == "abbrev?"        # слабая связь, не сильная
    assert repo.confirmed_mapping(pid, stores()["pyaterochka"]) is None
    assert [c["sku"] for c in repo.base_candidates(pid, stores()["pyaterochka"])] == ["4455673"]


def test_two_letter_stub_covers_the_word(catalog, user_db):
    """«гл» из чека — «глазированный»: двухбуквенный обрывок тоже покрывает слово.

    Пара живая: «СВИТЛ.Сырок твор.гл.с ван.45г». Связь слабая — фасовка 45 против 50 г
    расходится больше 3 %, и это правильно: подтверждать такую подмену человеку.
    """
    mine = prep("Свитл сырок твор гл ван 45", 45, receipt=True)
    cand = prep("Сырок творожный глазированный СВИТЛОГОРЬЕ с ванилью 26%, 50г", 50,
                receipt=False)

    assert link._abbrev(mine, cand) == 2
    assert link._judge(mine, cand, frozenset()) is not None
    assert link._judge(mine, cand, frozenset())[0] is False     # фасовка не та — связь слабая


def test_no_anchor_is_still_no_link(catalog, user_db):
    """Без якоря — 0, а не 2: «Коктейль молочный» не «Молоко»."""
    mine = prep("Коктейль молочный", 0, receipt=True)
    cand = prep("Молоко пастеризованное Простоквашино 2,5% 930 мл", 930, receipt=False)

    assert link._abbrev(mine, cand) == 0
    assert link._judge(mine, cand, frozenset()) is None


def test_extra_receipt_word_is_not_covered(catalog, user_db):
    """Слово чека, которого нет у кандидата, — по-прежнему 0 (чужой вкус не пройдёт)."""
    mine = prep("Сараф мор зеф эск ван клуб 75", 75, receipt=True)
    cand = prep("Мороженое Сарафаново Зефир Эскимо ванильное 75 г", 75, receipt=False)

    assert link._abbrev(mine, cand) == 0
    assert link._judge(mine, cand, frozenset()) is None
