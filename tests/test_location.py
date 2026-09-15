"""Адрес клиента: хранение, разрешение в точку и то, как он доезжает до коннектора.

Главное, что здесь защищается, — правило «сменил адрес, забудь точку». Точка,
найденная по старому адресу, к новому отношения не имеет, а на вид цены из неё
неотличимы от настоящих: это ровно тот сорт ошибки, который не находится глазами.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, location, repo  # noqa: E402
from app.db import init_db  # noqa: E402
from app.models import Location  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "loc.db"))
    init_db()


# ---------- хранение ----------
def test_address_survives_restart(db):
    """Адрес — ответ пользователя, а не настройка запуска: он обязан лежать в базе."""
    location.save_address("Москва, Ходынский бульвар 4")
    assert location.address() == "Москва, Ходынский бульвар 4"
    assert location.is_set()


def test_empty_address_is_the_same_as_no_address(db):
    location.save_address("Москва")
    location.save_address("   ")
    assert location.address() is None
    assert not location.is_set()


def test_without_address_store_gets_nothing_and_falls_back(db):
    """Нет адреса — None, чтобы коннектор взял запасное значение из config.yaml."""
    assert location.for_store("lenta") is None


# ---------- чем спрашиваем ----------
def test_address_is_what_we_ask_with(db):
    """Спрашиваем адресом — это единственное, что витрина Ленты принимает."""
    from app.connectors.lenta import _where

    location.save_address("Екатеринбург, улица Щербакова 4")
    where = _where(location.for_store("lenta"))

    assert where["address"] == "Екатеринбург, улица Щербакова 4"
    assert "storeId" not in where


def test_address_beats_store_code(db):
    """Проверено 15.09.2026: коды точек витрина не принимает, адрес принимает.

    Ошибка здесь тихая и дорогая — по неверному коду Лента отвечает не отказом,
    а ценой 0 и остатком 0, то есть выглядит пустым магазином. Поэтому если есть
    и адрес, и код, уходить должен адрес.
    """
    from app.connectors.lenta import _where

    where = _where(Location(address="Москва, Ходынский бульвар 4", store_id="1278"))

    assert where["address"] == "Москва, Ходынский бульвар 4"
    assert "storeId" not in where


def test_magnit_keeps_its_configured_shop(db):
    """Адрес клиента не должен отбирать у Магнита настроенный магазин.

    Магнит выбирает точку кодом в куках, а по адресу его не подобрать. Вернуть ему
    место без кода — значит молча уехать на магазин сайта по умолчанию, в чужом
    городе. Поэтому for_store отдаёт None, и коннектор берёт своё из config.yaml.
    """
    location.save_address("Екатеринбург, улица Щербакова 4")
    assert location.for_store("magnit") is None


# ---------- доставка до коннектора ----------
def test_saved_address_reaches_the_connector(db, monkeypatch):
    """Адрес из базы должен доехать до магазина сам, без передачи его руками."""
    from app import compare

    seen: dict = {}

    class Silent:
        code = "lenta"

        def search(self, query, limit=3):
            return []

    def fake_get_connector(code, location=None):
        seen[code] = location
        return Silent()

    monkeypatch.setattr("app.connectors.get_connector", fake_get_connector)
    location.save_address("Екатеринбург, улица Щербакова 4")

    compare.compare_query("молоко", store_codes=["lenta"])

    assert isinstance(seen["lenta"], Location)
    assert seen["lenta"].address == "Екатеринбург, улица Щербакова 4"


# ---------- проверка адреса ----------
def test_check_names_the_nearest_shop():
    """Проверка адреса должна назвать БЛИЖАЙШИЙ магазин, а не первый попавшийся.

    Лента отдаёт магазины не по расстоянию: ответ по Екатеринбургу начинался
    с 9110 м, а 394 м лежали третьими.
    """
    from app.ui.address import _distance

    hubs = [{"id": "1", "distance": 9110}, {"id": "2", "distance": 394}, {"id": "3"}]

    assert min(hubs, key=_distance)["id"] == "2"
    assert [h["id"] for h in sorted(hubs, key=_distance)] == ["2", "1", "3"],         "магазин без расстояния должен уйти в конец, а не в начало"


def test_distance_reads_like_a_person_wrote_it():
    from app.ui.address import _distance_text

    assert _distance_text({"distance": 394}) == " · 394 м"
    assert _distance_text({"distance": 9110}) == " · 9,1 км"
    assert _distance_text({}) == ""


def test_header_summary_does_not_break(db):
    """Сводка в шапке обязана работать, а не молчать.

    main.py ловит любую ошибку из header_stats и показывает пустую строку — то есть
    сломанная сводка выглядит как «адреса нет». Ровно так и случилось, когда из
    location убрали выбор точки, а обращение к нему в сводке осталось.
    """
    from app.ui.address import summary

    assert summary() == "Адрес не указан"
    location.save_address("Екатеринбург, улица Щербакова 4")
    assert summary() == "Екатеринбург, улица Щербакова 4"
