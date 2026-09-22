"""Очередь адресов: указал адрес — цены поехали сразу, а не следующей ночью.

Требование владельца 16.09.2026: «когда мы указываем адрес, то мы должны начать
грузить сразу в фоне все цены». Здесь защищается именно это, плюс три вещи, без
которых оно развалится на второй день: экран не ждёт сети, повтор адреса не
превращается в лишний обход, а перезапуск планировщика не теряет начатое.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, location, places  # noqa: E402
from app.catalog import store as catalog_store  # noqa: E402
from app.catalog import worker  # noqa: E402
from app.db import init_db  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Своя база человека и свой каталог: настоящие трогать нельзя."""
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "basket.db"))
    monkeypatch.setattr(catalog_store, "path", lambda: str(tmp_path / "catalog.db"))
    init_db()
    catalog_store.init()


# ---------- постановка в очередь ----------
def test_saving_an_address_queues_it(db):
    location.save_address("Екатеринбург, улица Малышева 51")
    queue = catalog_store.queue_state()
    assert [q["address"] for q in queue] == ["Екатеринбург, улица Малышева 51"]
    assert queue[0]["status"] == "waiting"


def test_saving_the_same_address_again_does_not_queue_twice(db):
    """Человек открыл панель адреса и закрыл — это не повод на десять минут в сеть."""
    location.save_address("Москва, Ходынский бульвар 4")
    location.save_address("Москва, Ходынский бульвар 4")
    assert len(catalog_store.queue_state()) == 1


def test_changing_the_address_queues_the_new_one(db):
    location.save_address("Москва, Ходынский бульвар 4")
    location.save_address("Екатеринбург, улица Малышева 51")
    assert {q["address"] for q in catalog_store.queue_state()} == {
        "Москва, Ходынский бульвар 4", "Екатеринбург, улица Малышева 51"}


def test_clearing_the_address_queues_nothing(db):
    location.save_address("Москва, Ходынский бульвар 4")
    location.save_address("   ")
    assert len(catalog_store.queue_state()) == 1


def test_saving_does_not_reach_the_network(db, monkeypatch):
    """Экран обязан ответить мгновенно: подбор точек — работа фонового процесса.

    Сторож грубый нарочно: любой поход к сетям при сохранении адреса — ошибка.
    """
    def explode(*a, **k):
        raise AssertionError("сохранение адреса полезло в сеть")

    monkeypatch.setattr(places, "points_for", explode)
    monkeypatch.setattr(places, "points", explode)
    location.save_address("Москва, Ходынский бульвар 4")
    assert catalog_store.queue_state()


def test_a_broken_queue_does_not_lose_the_address(db, monkeypatch):
    """Очередь — удобство, а адрес — ответ человека. Упала очередь, адрес остаётся."""
    monkeypatch.setattr(catalog_store, "enqueue_address",
                        lambda a: (_ for _ in ()).throw(RuntimeError("каталог недоступен")))
    location.save_address("Москва, Ходынский бульвар 4")
    assert location.address() == "Москва, Ходынский бульвар 4"


# ---------- разбор очереди ----------
def test_take_marks_running_and_returns_oldest_first(db):
    catalog_store.enqueue_address("Первый адрес")
    catalog_store.enqueue_address("Второй адрес")

    first = catalog_store.take_address()
    assert first["address"] == "Первый адрес"
    assert catalog_store.address_status("Первый адрес")["status"] == "running"
    assert catalog_store.take_address()["address"] == "Второй адрес"
    assert catalog_store.take_address() is None


def test_restart_returns_unfinished_work_to_the_queue(db):
    """Выкладка перезапускает планировщик. Начатый адрес обязан вернуться в очередь."""
    catalog_store.enqueue_address("Москва, Ходынский бульвар 4")
    catalog_store.take_address()

    assert catalog_store.revive_stuck() == 1
    assert catalog_store.address_status("Москва, Ходынский бульвар 4")["status"] == "waiting"
    assert catalog_store.take_address() is not None


def test_drain_runs_every_address_and_marks_it_done(db, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(worker, "run_address",
                        lambda a: seen.append(a) or [{"chain": "magnit", "status": "ok", "seen": 7}])
    catalog_store.enqueue_address("Первый адрес")
    catalog_store.enqueue_address("Второй адрес")

    assert worker.drain_queue() == 2
    assert seen == ["Первый адрес", "Второй адрес"]
    assert catalog_store.address_status("Второй адрес")["status"] == "done"
    assert "magnit: ok, 7" in catalog_store.address_status("Второй адрес")["note"]


def test_a_failing_address_does_not_block_the_queue(db, monkeypatch):
    """Один город без сетей или упавший шлюз не должны затыкать очередь остальным."""
    def run(address):
        if "Тикси" in address:
            raise RuntimeError("шлюз не ответил")
        return [{"chain": "lenta", "status": "ok", "seen": 3}]

    monkeypatch.setattr(worker, "run_address", run)
    catalog_store.enqueue_address("Тикси, улица Морская 1")
    catalog_store.enqueue_address("Москва, Ходынский бульвар 4")

    assert worker.drain_queue() == 2
    assert catalog_store.address_status("Тикси, улица Морская 1")["status"] == "failed"
    assert catalog_store.address_status("Москва, Ходынский бульвар 4")["status"] == "done"


def test_address_without_points_is_done_not_failed(db, monkeypatch):
    """Сетей рядом нет — это ответ, а не поломка: повторять такой обход незачем."""
    monkeypatch.setattr(worker, "run_address", lambda a: [])
    catalog_store.enqueue_address("Тикси, улица Морская 1")

    worker.drain_queue()
    state = catalog_store.address_status("Тикси, улица Морская 1")
    assert state["status"] == "done"
    assert "не нашлось" in (state["note"] or "")


def test_run_address_asks_only_chains_that_have_points(db, monkeypatch):
    """ВкусВилл и Дикси адресом не управляются — дёргать их по смене адреса незачем."""
    asked: list[str] = []

    def points_for(address, chain):
        asked.append(chain)
        return [places.Point(chain, "264856", "Москва, Микояна 12", address)] \
            if chain == "magnit" else []

    crawled: list = []
    monkeypatch.setattr(places, "points_for", points_for)
    monkeypatch.setattr(worker, "make", lambda code, spots: crawled.append((code, spots)) or object())
    monkeypatch.setattr(worker.refresh, "run_chain",
                        lambda crawler, progress=None: {"chain": "magnit", "status": "ok", "seen": 1})
    monkeypatch.setattr(worker.refresh, "match_all", lambda progress=None: {})

    worker.run_address("Москва, Ходынский бульвар 4")

    assert set(asked) >= {"magnit", "lenta", "vkusvill", "dixy"}, "спросить надо все сети"
    assert [c for c, _ in crawled] == ["magnit"], "обойти — только те, у кого точка есть"
