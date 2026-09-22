"""Точки загрузки: приложение ходит туда, где живут люди, и никуда больше.

Правило владельца 16.09.2026: обходить не сеть целиком, а магазины, подобранные к
адресам рабочих мест. Здесь защищается ровно это — и то, что список точек не
разрастается сам собой: повтор адреса, второй человек в том же районе и потолок
в конфиге не должны превращаться в лишний десятиминутный обход.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, places, users  # noqa: E402
from app.db import init_db  # noqa: E402
from app.models import Location  # noqa: E402


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    """Свои рабочие места вместо настоящих: настоящие лежат в data/users."""
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    monkeypatch.setattr(config, "get", _config_without_spare(config.get))

    def make(phone: str, address: str | None) -> None:
        users.open_workspace(phone)
        if address is not None:
            from app import location
            location.save_address(address)
        users.deactivate()

    yield make
    users.deactivate()


def _config_without_spare(original):
    """Запасной адрес из config.yaml в тестах только мешает: он подменяет пустоту."""
    def get(key, default=None):
        if key == "connectors.lenta_address":
            return None
        return original(key, default)
    return get


def _magnit(monkeypatch, by_address: dict, calls: list | None = None):
    from app.connectors import magnit

    def nearest(address):
        if calls is not None:
            calls.append(address)
        return by_address.get(address)

    monkeypatch.setattr(magnit, "nearest_store", nearest)


MSK = {"code": "264856", "format": "MM", "address": "Москва, Микояна, 12"}
EKB = {"code": "668596", "format": "MM", "address": "Екатеринбург, Мамина-Сибиряка, 70"}


# ---------- адреса ----------
def test_addresses_are_collected_from_every_workspace(workspaces):
    workspaces("79990000001", "Москва, Ходынский бульвар 4")
    workspaces("79990000002", "Екатеринбург, улица Малышева 51")
    assert sorted(places.addresses()) == ["Екатеринбург, улица Малышева 51",
                                          "Москва, Ходынский бульвар 4"]


def test_workspace_without_an_address_is_not_an_error(workspaces):
    """Человек завёлся, адрес ещё не ввёл — это норма, а не поломка обхода."""
    workspaces("79990000001", "Москва, Ходынский бульвар 4")
    workspaces("79990000002", None)
    assert places.addresses() == ["Москва, Ходынский бульвар 4"]


def test_the_same_address_twice_is_one_address(workspaces):
    workspaces("79990000001", "Москва, Ходынский бульвар 4")
    workspaces("79990000002", "Москва, Ходынский бульвар 4")
    assert places.addresses() == ["Москва, Ходынский бульвар 4"]


def test_demo_goes_last(workspaces):
    """Витрина не должна вытеснять живого человека из-под потолка."""
    workspaces(users.DEMO, "Москва, Ходынский бульвар 4")
    workspaces("79990000002", "Екатеринбург, улица Малышева 51")
    assert places.addresses()[-1] == "Москва, Ходынский бульвар 4"


def test_without_any_address_the_spare_from_config_is_used(workspaces, monkeypatch):
    """Свежий сервер не должен остаться без каталога вовсе."""
    monkeypatch.setattr(config, "get",
                        lambda key, default=None: ("Москва, Ходынский бульвар 4"
                                                   if key == "connectors.lenta_address" else default))
    assert places.addresses() == ["Москва, Ходынский бульвар 4"]


# ---------- точки ----------
def test_point_is_resolved_for_every_address(workspaces, monkeypatch):
    workspaces("79990000001", "Москва, Ходынский бульвар 4")
    workspaces("79990000002", "Екатеринбург, улица Малышева 51")
    _magnit(monkeypatch, {"Москва, Ходынский бульвар 4": MSK,
                          "Екатеринбург, улица Малышева 51": EKB})

    found = places.points("magnit")

    assert sorted(p.code for p in found) == ["264856", "668596"]
    assert all(p.chain == "magnit" for p in found)
    assert {p.address for p in found} == {"Москва, Ходынский бульвар 4",
                                          "Екатеринбург, улица Малышева 51"}


def test_neighbours_share_one_point(workspaces, monkeypatch):
    """Два человека из соседних домов — один магазин и один обход, а не два."""
    workspaces("79990000001", "Москва, Ходынский бульвар 4")
    workspaces("79990000002", "Москва, улица Гризодубовой 4")
    _magnit(monkeypatch, {"Москва, Ходынский бульвар 4": MSK,
                          "Москва, улица Гризодубовой 4": MSK})

    assert [p.code for p in places.points("magnit")] == ["264856"]


def test_cap_keeps_the_crawl_from_growing_with_the_crowd(workspaces, monkeypatch):
    """Потолок есть затем, что каждая точка Магнита — десять минут обхода."""
    cities = {f"Город {n}": {"code": f"10000{n}", "format": "MM", "address": f"Город {n}, 1"}
              for n in range(5)}
    for n, city in enumerate(cities):
        workspaces(f"7999000000{n}", city)
    _magnit(monkeypatch, cities)
    monkeypatch.setattr(places, "max_points", lambda: 2)

    assert len(places.points("magnit")) == 2


def test_chain_missing_near_the_address_gives_no_point(workspaces, monkeypatch):
    """Магнита рядом нет — обходить в этом городе нечего, и это не ошибка."""
    workspaces("79990000001", "Тикси, улица Морская 1")
    _magnit(monkeypatch, {})
    assert places.points("magnit") == []


def test_chains_without_points_say_so(workspaces):
    """У ВкусВилла и Дикси ответ один на страну: «точек нет» здесь значит «делить нечего»."""
    workspaces("79990000001", "Москва, Ходынский бульвар 4")
    assert places.points("vkusvill") == []
    assert places.points("dixy") == []
    assert "vkusvill" not in places.BY_POINT and "dixy" not in places.BY_POINT


def test_a_silent_chain_does_not_stop_the_others(workspaces, monkeypatch):
    """Сеть молчит на одном адресе — остальные адреса обязаны дойти до списка."""
    workspaces("79990000001", "Москва, Ходынский бульвар 4")
    workspaces("79990000002", "Екатеринбург, улица Малышева 51")

    from app.connectors import magnit

    def nearest(address):
        if "Москва" in address:
            raise RuntimeError("шлюз не ответил")
        return EKB

    monkeypatch.setattr(magnit, "nearest_store", nearest)
    assert [p.code for p in places.points("magnit")] == ["668596"]


# ---------- сборщики получают точки ----------
def test_crawler_gets_every_magnit_point(monkeypatch):
    """Магниту точек можно много: ассортимент у него в каждом магазине свой."""
    from app.catalog.crawlers import make

    spots = [places.Point("magnit", "264856", "Москва", "Москва"),
             places.Point("magnit", "668596", "Екатеринбург", "Екатеринбург")]
    assert make("magnit", spots).stores == ["264856", "668596"]


def test_lenta_crawler_takes_one_hub(monkeypatch):
    """Ленте точка нужна одна: перечень товаров у неё общий, от хаба зависят карточки."""
    from app.catalog.crawlers import make

    spots = [places.Point("lenta", "291", "Москва", "Москва"),
             places.Point("lenta", "203", "Екатеринбург", "Екатеринбург")]
    assert make("lenta", spots).store_id == 291


def test_without_points_the_crawler_falls_back_to_config():
    """Ни одного адреса — обход всё равно должен состояться, иначе каталог пуст."""
    from app.catalog.crawlers import make
    from app.catalog.crawlers.magnit import MagnitCrawler

    assert make("magnit", []).stores == [MagnitCrawler._spare()]
