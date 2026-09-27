"""Смена адреса: цены прежней точки выпадают из расчёта, цены корзины обновляются сами.

Пробел, найденный 26.09.2026 при ответе на вопрос владельца «подгрузит ли система
актуальные наличия после указания адреса». Сохранённый адрес ставил в очередь
только обход общего каталога. Снимки цен товаров, уже лежащих в корзине, оставались
снятыми по ПРЕЖНЕЙ точке — а «Результат» в сеть не ходит и показывал их как свежие:
чужой магазин, а то и чужой город, без единого предупреждения.

По требованию «Не уверен — не показывай» (docs/design/nazvaniya-2026-09-26.md) такая
цена не помечается, а не участвует в расчёте (app/freshness.py) — до обновления.
"""
from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import freshness, location, repo, service, users  # noqa: E402
from app.models import Product  # noqa: E402

PHONE = "79990000099"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    # Очередь адресов живёт в общем каталоге — ему своя временная база.
    from app.catalog import store as catalog_store

    monkeypatch.setattr(catalog_store, "path", lambda: str(tmp_path / "catalog.db"))
    users.open_workspace(PHONE)
    yield
    users.deactivate()


def _priced(code: str, name: str, price: float, fetched_at: str) -> tuple[int, object]:
    store = repo.get_store(code)
    pid = repo.upsert_product(Product(id=None, name=name, unit="pcs"))
    sp = repo.upsert_store_product(store.id, f"{code}-{pid}", name)
    repo.confirm_mapping(pid, sp, confirmed=True)
    repo.save_price(sp, price, fetched_at=fetched_at)
    return pid, store


def test_a_new_address_is_remembered_as_a_move(home):
    assert location.save_address("Москва, Ходынский бульвар 4") is True
    first = location.moved_at()
    assert first
    assert location.save_address("Москва, Ходынский бульвар 4") is False, "тот же адрес — не переезд"
    assert location.moved_at() == first


def test_prices_of_the_old_point_drop_out_after_a_move(home):
    """Цена Магнита, снятая до смены адреса, — цена прежнего магазина, как бы свежа ни была.

    ВкусВилл адресом не управляется: его цена одна на страну, и её не трогаем.
    """
    before = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    milk, magnit = _priced("magnit", "Молоко", 89.0, before)
    kefir, vkusvill = _priced("vkusvill", "Кефир", 99.0, before)
    location.save_address("Екатеринбург, улица Малышева 51")

    assert service.line_price(milk, magnit, 1, "pcs") is None
    assert freshness.price_for(milk, magnit.id) is None, "каталог и «Где дешевле» — тоже"
    assert service.line_price(kefir, vkusvill, 1, "pcs")["value"] == 99.0

    # Снятая после смены — снова своя.
    sp = repo.confirmed_mapping(milk, magnit.id)["id"]
    repo.save_price(sp, 91.0)
    assert service.line_price(milk, magnit, 1, "pcs")["value"] == 91.0


def test_the_basket_waits_for_prices_of_the_new_address(home):
    """Корзина не считается по цене прежней точки — ни в расчёте, ни на «Корзине»."""
    before = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    milk, _ = _priced("magnit", "Молоко", 89.0, before)
    basket = repo.create_basket("Неделя")
    repo.set_basket_item(basket, milk, 1)
    assert service.build_basket_lines(basket)[0].prices == {"magnit": 89.0}

    location.save_address("Екатеринбург, улица Малышева 51")
    assert service.build_basket_lines(basket)[0].prices == {}


def test_refresh_reports_its_progress_by_chain(home, monkeypatch):
    """Обновление идёт минутами — и называет сеть, которую спрашивает сейчас."""
    from app.matcher import matcher

    monkeypatch.setattr(matcher, "_get_connector", lambda code, location=None: None)
    seen: list[tuple[int, int, str]] = []
    matcher.refresh_prices([1], ["magnit", "lenta"], progress=lambda d, t, c: seen.append((d, t, c)))
    assert seen == [(0, 2, "magnit"), (1, 2, "lenta")]


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    from app.catalog import store as catalog_store

    monkeypatch.setattr(catalog_store, "path", lambda: str(tmp_path / "catalog.db"))
    from app.web import create_app

    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    users.deactivate()


def test_a_move_starts_refreshing_the_basket_prices(web, monkeypatch):
    """Сменил адрес — цены последней корзины обновляются сами, ход на «Корзине»."""
    from app.web.screens import basket as basket_screen

    web.post("/login", data={"phone": PHONE, "next": "/"})
    users.open_workspace(PHONE)
    milk = repo.upsert_product(Product(id=None, name="Молоко", unit="pcs"))
    basket = repo.create_basket("Неделя 40")
    repo.set_basket_item(basket, milk, 2)

    started = threading.Event()
    calls: list[tuple[int, str]] = []

    def fake_start(basket_id, kind, items):
        calls.append((basket_id, kind))
        started.set()

    monkeypatch.setattr(basket_screen, "_start_job", fake_start)

    answer = web.post("/address", data={"address": "Екатеринбург, улица Малышева 51"})
    assert answer.status_code == 302
    assert f"fresh={basket}" in answer.headers["Location"]
    assert calls == [(basket, "fresh")]

    page = web.get(answer.headers["Location"]).data.decode("utf-8")
    assert "уже обновляются по новому адресу" in page and "Неделя 40" in page

    # Тот же адрес ещё раз — не переезд, и лишнего обновления нет.
    web.post("/address", data={"address": "Екатеринбург, улица Малышева 51"})
    assert calls == [(basket, "fresh")]
