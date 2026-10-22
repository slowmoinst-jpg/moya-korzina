"""Лента: сеть сказала «товара нет» — больше не спрашиваем, а потолок работает.

ЖИВОЙ ЗАМЕР 18.10.2026, из-за которого это появилось. Sitemap Ленты — 82 908 адресов,
в базе 28 523 известных, незнакомых 54 385. Выборка из 40 незнакомых артикулов по всей
карте: 31 отдался карточкой, 9 ответили «товар N не найден» (77,5 % есть). Но «мёртвые»
артикулы стоят в НАЧАЛЕ карты: у первых 25 незнакомых ответ «не найден» был у всех 25.
Прежний сборщик этот ответ никуда не записывал, поэтому они вечно занимали потолок
обхода (2000 карточек в сутки), и каталог прирастал на ~500 за ночь вместо ~1 500.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import store  # noqa: E402
from app.catalog.crawlers import lenta as lenta_mod  # noqa: E402
from app.catalog.crawlers.lenta import ERROR, MISSING, OK, LentaCrawler  # noqa: E402


@pytest.fixture()
def base(tmp_path, monkeypatch):
    """Своя база каталога: в боевую тесты не пишут."""
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


def crawler_with(monkeypatch, answers: dict) -> tuple[LentaCrawler, list]:
    """Сборщик с подменённым MCP: answers — {артикул: ответ сервера}, вызовы считаем."""
    crawler = LentaCrawler(hub=359)
    crawler.backfill = 0        # добор подробностей здесь не проверяем: он в своём стороже
    calls: list[str] = []

    def fake_call(url, store_code, tool, arguments, cache_key=None):
        sku = str(arguments["id"])
        calls.append(sku)
        return answers.get(sku)

    monkeypatch.setattr(lenta_mod.mcp_client, "call_tool", fake_call)
    return crawler, calls


def found(sku: str, name: str = "Молоко Лента 1 л") -> dict:
    return {"ok": True, "data": {"item": {"id": int(sku), "name": name, "price": 99.9,
                                          "stock": 3, "package": "1 л"}}}


def test_details_tells_missing_from_unreachable(monkeypatch):
    """«Не найден» и «не ответили» — разные вещи, и путать их нельзя."""
    crawler, _ = crawler_with(monkeypatch, {
        "1": found("1"),
        "2": {"ok": False, "error": "товар 2 не найден"},
        "3": None,
        "4": {"ok": False, "error": "магазин занят"},
    })

    assert crawler.details("1")[1] == OK
    assert crawler.details("2")[1] == MISSING
    assert crawler.details("3")[1] == ERROR
    assert crawler.details("4")[1] == ERROR


def write(base, products) -> None:
    """Сложить товары в базу, как это делает refresh.run_chain после обхода."""
    rows = [p for p in products if not p.seen_only]
    if rows:
        base.upsert_products("lenta", rows)


def test_missing_is_remembered_and_not_asked_again(monkeypatch, base):
    """Главное: «не найденный» артикул не занимает потолок следующего обхода."""
    answers = {"1": found("1", "Сыр Лента 200 г"), "2": {"ok": False, "error": "товар 2 не найден"}}
    crawler, calls = crawler_with(monkeypatch, answers)
    crawler.cap = 10
    monkeypatch.setattr(crawler, "universe", lambda: ["1", "2", "3"])

    first = list(crawler.crawl())
    write(base, first)

    assert [p.sku for p in first if not p.seen_only] == ["1"]
    assert calls == ["1", "2", "3"], "первый обход спрашивает всех незнакомых"
    assert base.missed_skus("lenta") == {"2"}, "«не найден» записан"
    assert "3" not in base.missed_skus("lenta"), "без ответа в память не идёт"

    answers["3"] = found("3", "Хлеб Лента 300 г")
    crawler2, calls2 = crawler_with(monkeypatch, answers)
    crawler2.cap = 10
    monkeypatch.setattr(crawler2, "universe", lambda: ["1", "2", "3"])
    second = list(crawler2.crawl())

    assert calls2 == ["3"], "известный и «не найденный» больше не спрашиваются"
    assert [p.sku for p in second if not p.seen_only] == ["3"]
    assert base.missed_skus("lenta") == {"2"}


def test_a_product_that_came_back_clears_the_memory(monkeypatch, base):
    """Товар вернулся в витрину — память о нём не нужна."""
    base.note_miss("lenta", "7", "товар 7 не найден")
    assert base.missed_skus("lenta") == {"7"}

    crawler, _ = crawler_with(monkeypatch, {"7": found("7", "Кефир Лента 500 мл")})
    crawler.cap = 5
    crawler.retry_days = 0                   # память истекла — артикул спрашивают снова
    monkeypatch.setattr(crawler, "universe", lambda: ["7"])
    list(crawler.crawl())

    assert base.missed_skus("lenta") == set()


def test_memory_expires_so_the_product_can_come_back(monkeypatch, base):
    """Старше retry_days артикул спрашивают снова: товар мог вернуться."""
    base.note_miss("lenta", "9", "товар 9 не найден")

    assert base.missed_skus("lenta", retry_days=30) == {"9"}
    assert base.missed_skus("lenta", retry_days=0) == set()


def test_network_trouble_does_not_burn_the_cap(monkeypatch, base):
    """Не ответили — не запоминаем: иначе одна заминка вычеркнет живые товары."""
    answers = {"5": None, "6": {"ok": False, "error": "внутренняя ошибка"}}
    crawler, _ = crawler_with(monkeypatch, answers)
    crawler.cap = 10
    monkeypatch.setattr(crawler, "universe", lambda: ["5", "6"])

    list(crawler.crawl())

    assert base.missed_skus("lenta") == set()
    assert base.miss_counts() == {}


def test_schema_v6_creates_the_miss_memory(base):
    """Пустая база: память «спросили — товара нет» заводится миграцией схемы."""
    assert store.SCHEMA_VERSION >= 6
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 6
        have = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}
    assert "chain_misses" in have


def test_the_cap_counts_only_new_products(monkeypatch, base):
    """Потолок тратится на новые артикулы, а память о «нет» его не съедает."""
    for n in range(50):
        base.note_miss("lenta", str(1000 + n), "товар не найден")
    answers = {str(n): found(str(n), f"Товар {n}") for n in (200, 201, 202)}
    crawler, calls = crawler_with(monkeypatch, answers)
    crawler.cap = 3
    ids = [str(1000 + n) for n in range(50)] + ["200", "201", "202"]
    monkeypatch.setattr(crawler, "universe", lambda: ids)

    products = [p for p in crawler.crawl() if not p.seen_only]

    assert [p.sku for p in products] == ["200", "201", "202"]
    assert calls == ["200", "201", "202"], "память о «нет» потолок не занимает"
