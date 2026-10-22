"""Связи товаров людей ставятся в ФОН после ночного обновления базы.

ЗАМЕР 21.10.2026: на рабочем месте владельца 1 159 товаров, проход связывания — 537 с
(9 минут), привязано 397, не найдено 735. Держать это в конце обхода нельзя: ночь
удлинилась бы на каждого человека. Поэтому зовём link.link_soon (свой поток на рабочее
место, повторный вызов не плодит второй).

Зачем вообще: экран «Где дешевле» по товару без связей пишет «товар не связан с
магазином». Связи создавала только кнопка в кабинете — их наличие зависело от человека.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import users  # noqa: E402
from app.catalog import link, worker  # noqa: E402


@pytest.fixture
def places(monkeypatch):
    calls: dict[str, list] = {"soon": []}
    monkeypatch.setattr(users, "DEMO", "demo", raising=False)
    monkeypatch.setattr(users, "list_workspaces", lambda: ["79000000001", "demo", "79000000002"])
    monkeypatch.setattr(link, "link_soon", lambda phone: (calls["soon"].append(phone) or True))
    return calls


def test_linking_is_put_in_background_for_every_real_place(places):
    worker._link_places()

    assert places["soon"] == ["79000000001", "79000000002"], places["soon"]


def test_one_broken_place_does_not_stop_the_others(places, monkeypatch):
    def soon(phone):
        if phone == "79000000001":
            raise RuntimeError("поток не поднялся")
        places["soon"].append(phone)

    monkeypatch.setattr(link, "link_soon", soon)

    worker._link_places()

    assert places["soon"] == ["79000000002"], places["soon"]


def test_the_sweep_does_not_wait_for_linking(monkeypatch):
    """Обход зовёт постановку в фон, а не сам проход: 9 минут в конце ночи — это дорого."""
    called: list[str] = []
    monkeypatch.setattr(worker, "_link_places", lambda: called.append("link"))
    monkeypatch.setattr(worker, "chains", lambda: [])   # сетей нет: проверяем шаг связывания

    worker.run_all()

    assert called == ["link"], "после обхода связи должны ставиться в фон"
