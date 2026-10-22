"""Потолок времени на сеть: одна застрявшая сеть не съедает ночь остальным.

Обходы идут ПО ОЧЕРЕДИ (app/catalog/worker.run_all), и если сеть встала, следующие за
ней до утра не начинаются вовсе. Ночь целиком — пять часов: METRO 1,7 ч, Лента с добором
подробностей ~2,2 ч (замер 18.10.2026). Потолок 180 мин выше любой здоровой сети, а
упёршись в него, сеть останавливается и записывается НЕПОЛНОЙ — так пропавшими ничего не
объявляется и следующей ночью обход продолжит с того же места.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import refresh, store  # noqa: E402
from app.catalog import worker as worker_mod  # noqa: E402
from app.catalog.model import ChainProduct, Crawler  # noqa: E402


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    store.upsert_products("lenta", [ChainProduct(sku="old", name="Старый товар")])
    return store


class SlowCrawler(Crawler):
    """Сборщик, который «висит»: отдаёт товары, но время вышло ещё до первого батча."""

    code = "lenta"

    def __init__(self, count: int = 500, slow: bool = True):
        self.count = count
        self.slow = slow

    def crawl(self, progress=None):
        for n in range(self.count):
            if self.slow and n:
                import time
                time.sleep(0.002)          # время идёт: потолок срабатывает в батче
            yield ChainProduct(sku=f"new{n}", name=f"Товар {n}")


def test_a_stuck_chain_stops_at_its_budget(base, monkeypatch):
    """Потолок сработал: обход неполный, пропавшими ничего не объявлено."""
    monkeypatch.setattr(refresh.time, "monotonic", iter([0.0] + [10_000.0] * 50).__next__,
                        raising=False)

    result = refresh.run_chain(SlowCrawler(), budget_min=1)

    assert result["status"] == "partial"
    assert "потолок" in result["note"] and "мин" in result["note"]
    assert result["gone"] == 0, "неполный обход не имеет права объявлять пропавших"
    # Старая строка осталась живой: её просто не дошли.
    assert any(str(r["sku"]) == "old" and r["active"] == 1
               for r in store.all_products(active_only=False))


def test_a_healthy_chain_does_not_notice_the_budget(base):
    """Здоровая сеть до потолка не доходит: обход остаётся полным и считается свежим."""
    result = refresh.run_chain(SlowCrawler(count=5, slow=False), budget_min=180)

    assert result["status"] == "ok"
    assert result["note"] is None
    assert result["gone"] == 1, "полный обход по-прежнему считает пропавших"
    assert store.fresh_chains(1).get("lenta")


def test_without_a_budget_nothing_changes(base):
    """Потолка нет (ноль в настройке) — поведение прежнее."""
    result = refresh.run_chain(SlowCrawler(count=5, slow=False), budget_min=None)

    assert result["status"] == "ok"


def test_the_budget_comes_from_the_config(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "get", lambda key, default=None: 180
                        if key == "catalog.chain_budget_min" else default)

    assert worker_mod.chain_budget_min() == 180
    monkeypatch.setattr(config, "get", lambda key, default=None: 0)
    assert worker_mod.chain_budget_min() is None
