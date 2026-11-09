"""Тесты фонового прогрева «Где дешевле» и отображения сборки корзин (этап 1).

Проверяется:
- warm склеивает вызовы (два вызова, пока идёт, → расчёт не больше двух раз);
- accept_report с basket зовёт warm;
- экран «Где дешевле» со строкой сети в GOING показывает строку «Собираем корзины...» и meta refresh.
"""
from __future__ import annotations

import os
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import cartrun, location, phonecart, repo, users
from app.models import Product
from app.web.phone import cheaper

PHONE = "79997776655"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.open_workspace(PHONE)
    location.save_address("г Москва, ул Арбат, 1")
    yield
    users.deactivate()


def test_warm_coalesces_multiple_calls_to_at_most_two(workspace, monkeypatch):
    """warm склеивает вызовы: два вызова, пока идёт, → расчёт не больше двух раз."""
    calc_count = 0
    first_entered = threading.Event()
    release_first = threading.Event()

    def mock_bundle(basket_id, again=False):
        nonlocal calc_count
        calc_count += 1
        if calc_count == 1:
            first_entered.set()
            release_first.wait(timeout=5)
        return {"variants": [], "positions": 0}

    from app.web.screens import result
    monkeypatch.setattr(result, "_bundle", mock_bundle)
    monkeypatch.setattr(cheaper, "_places", lambda basket_id, bundle: [])

    # Очищаем состояние
    cheaper._WARM_JOBS.clear()

    # Запускаем первый warm
    cheaper.warm(1, PHONE)

    # Ждем пока поток зайдет в первый расчёт
    assert first_entered.wait(timeout=5)

    # Пока он идет, зовем warm еще дважды
    cheaper.warm(1, PHONE)
    cheaper.warm(1, PHONE)

    # Отпускаем первый расчёт
    release_first.set()

    # Ждем завершения работы потока
    for _ in range(50):
        with cheaper._WARM_LOCK:
            if not cheaper._WARM_JOBS:
                break
        time.sleep(0.05)

    # Должно быть ровно 2 вызова (первый + один повтор)
    assert calc_count == 2, f"Ожидалось 2 расчёта, получено {calc_count}"


def test_accept_report_with_basket_calls_warm(workspace, monkeypatch):
    """accept_report с basket зовёт cheaper.warm."""
    calls = []
    monkeypatch.setattr(cheaper, "warm", lambda basket_id, phone: calls.append((basket_id, phone)))

    # Убеждаемся что current() возвращает PHONE
    assert users.current() == PHONE

    report = {
        "store": "magnit",
        "basket": 42,
        "items": [],
    }
    phonecart.accept_report(report)

    assert len(calls) == 1
    assert calls[0] == (42, PHONE)


def test_cheaper_screen_with_going_row_shows_building_and_meta_refresh(tmp_path, monkeypatch):
    """Экран «Где дешевле» со строкой сети в GOING показывает строку и meta refresh."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    from app.web import create_app
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    from conftest import sign_in
    sign_in(client, PHONE)

    users.open_workspace(PHONE)
    location.save_address("г Москва, ул Тверская, 1")
    milk = repo.upsert_product(Product(id=None, name="Молоко 1 л"))
    basket_id = repo.create_basket("Недельная")
    repo.set_basket_item(basket_id, milk, 1.0)
    stamp = cartrun.stamp_of(basket_id)

    # Создаем строку сборки в состоянии GOING для Магнита
    row = cartrun.Row(
        code="magnit",
        name="Магнит",
        road=cartrun.PHONE,
        state=cartrun.GOING,
        by=cartrun.DEVICE,
        basket_id=basket_id,
        stamp=stamp,
    )
    cartrun.save(row)
    users.deactivate()

    # Без настоящей работы по ценам: её поток переживал тест и звал warm(1, PHONE) в чужом.
    monkeypatch.setattr(cheaper, "start_prices", lambda basket_id, items: None)
    resp = client.get("/phone/cheaper")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")

    # Проверяем meta refresh 5
    assert '<meta http-equiv="refresh" content="5">' in html
    # Проверяем строку статуса
    assert "Собираем корзины в сетях: Магнит." in html
    assert "Готово 0 из 1. Экран обновится сам." in html
