"""top_up=true в наряде телефона: добор пропущенных позиций без нажатия кнопки.

ЗАЧЕМ (Этап 1 плана «корзина во всех сетях», 25.10.2026). Пятёрочка и Самокат пропускают
2–3 позиции за прогон из-за бот-проверки и таймаутов. С top_up=true следующий наряд
содержит только то, что ещё не легло: SKU из landed последнего отчёта телефона убраны.
Это позволяет добрать корзину следующим прогоном без нажатия кнопки и не удвоить
то, что уже лежит.

Также проверяется: check_signal и check_pause_ms в rules() — паттерн и пауза для
распознавания бот-проверки приложением.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import phonecart, repo, users  # noqa: E402
from app.models import Product  # noqa: E402
from app.shopbrowser import signals  # noqa: E402

PHONE = "79990000099"


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Рабочее место: корзина из 3 товаров, Магниту знакомы все три."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.open_workspace(PHONE)

    milk = repo.upsert_product(Product(id=None, name="Молоко 3,2% 900 мл", unit="pcs"))
    cheese = repo.upsert_product(Product(id=None, name="Сыр Российский 200 г", unit="pcs"))
    bread = repo.upsert_product(Product(id=None, name="Хлеб Дарницкий 400 г", unit="pcs"))

    shop = repo.get_store("magnit")
    milk_sp = repo.upsert_store_product(shop.id, "mag-milk", "Молоко 3,2% 900 мл",
                                        url="https://magnit.ru/product/100")
    cheese_sp = repo.upsert_store_product(shop.id, "mag-cheese", "Сыр Российский 200 г",
                                           url="https://magnit.ru/product/200")
    bread_sp = repo.upsert_store_product(shop.id, "mag-bread", "Хлеб Дарницкий 400 г",
                                          url="https://magnit.ru/product/300")
    for prod, sp in [(milk, milk_sp), (cheese, cheese_sp), (bread, bread_sp)]:
        repo.confirm_mapping(prod, sp, confirmed=True)
        repo.save_price(sp, 99.0)

    basket_id = repo.create_basket("Тест top_up")
    repo.set_basket_item(basket_id, milk, 1.0)
    repo.set_basket_item(basket_id, cheese, 1.0)
    repo.set_basket_item(basket_id, bread, 1.0)
    users.deactivate()

    from app import matcher
    monkeypatch.setattr(matcher, "auto_match",
                        lambda ids, codes, **kw: {"auto": 0, "need_review": []})

    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    yield application
    users.deactivate()


def _client(web):
    c = web.test_client()
    from conftest import sign_in

    sign_in(c, PHONE)
    return c


# ---------- top_up=true убирает из наряда уже лежащие SKU ----------

def test_top_up_excludes_already_landed_skus(web):
    """При top_up=true наряд содержит только позиции, чьего SKU нет в последнем отчёте."""
    client = _client(web)

    # Сначала отчёт: молоко легло (ok=true, sku=mag-milk), остальные нет.
    client.post("/api/device/cartreport", json={
        "store": "magnit",
        "items": [
            {"name": "Молоко 3,2% 900 мл", "sku": "mag-milk",
             "url": "https://magnit.ru/product/100", "ok": True, "put": 1, "times": 1},
            {"name": "Сыр Российский 200 г", "sku": "mag-cheese",
             "url": "https://magnit.ru/product/200", "ok": False, "why": "бот-проверка"},
            {"name": "Хлеб Дарницкий 400 г", "sku": "mag-bread",
             "url": "https://magnit.ru/product/300", "ok": False, "why": "таймаут"},
        ],
    })

    # Теперь запрашиваем наряд с top_up=true.
    data = client.post("/api/device/cartplan", json={"store": "magnit", "top_up": True}).get_json()

    assert data["ok"] is True
    skus = {line["sku"] for line in data["lines"]}
    # Молоко уже лежит — его нет в наряде добора.
    assert "mag-milk" not in skus, "уже лежащая позиция попала в наряд добора"
    # Сыр и хлеб не легли — они должны быть.
    assert "mag-cheese" in skus
    assert "mag-bread" in skus
    # Флаги в ответе.
    assert data["top_up"] is True
    assert data["already_landed"] == 1


def test_top_up_without_report_returns_full_plan(web):
    """Без предыдущего отчёта top_up=true возвращает полный наряд, как обычно."""
    data = _client(web).post("/api/device/cartplan",
                              json={"store": "magnit", "top_up": True}).get_json()

    assert data["ok"] is True
    # Все три позиции — отчёта нет, landed пуст.
    assert len(data["lines"]) == 3
    assert data["already_landed"] == 0


def test_top_up_false_returns_full_plan_even_after_partial_report(web):
    """Без top_up=true полный наряд возвращается даже после частичного отчёта."""
    client = _client(web)
    client.post("/api/device/cartreport", json={
        "store": "magnit",
        "items": [
            {"name": "Молоко 3,2% 900 мл", "sku": "mag-milk",
             "url": "https://magnit.ru/product/100", "ok": True, "put": 1, "times": 1},
        ],
    })

    data = client.post("/api/device/cartplan", json={"store": "magnit"}).get_json()

    assert data["ok"] is True
    # Полный наряд — top_up не задан, фильтрации нет.
    skus = {line["sku"] for line in data["lines"]}
    assert "mag-milk" in skus, "без top_up полный наряд должен включать уже лежащие"
    assert len(data["lines"]) == 3


def test_top_up_kept_positions_are_not_excluded(web):
    """how=kept — позиция уже лежала с ПРОШЛОГО прогона, текущий её не клал.
    При добоере такие позиции НЕ исключаются: телефон должен их снова проверить
    или пропустить по собственной логике (kept-check).
    """
    client = _client(web)
    client.post("/api/device/cartreport", json={
        "store": "magnit",
        "items": [
            # how=kept: молоко лежало с прошлой сборки, этот прогон его не клал.
            {"name": "Молоко 3,2% 900 мл", "sku": "mag-milk",
             "url": "https://magnit.ru/product/100", "ok": True, "how": "kept"},
            {"name": "Сыр Российский 200 г", "sku": "mag-cheese",
             "url": "https://magnit.ru/product/200", "ok": False, "why": "таймаут"},
        ],
    })

    data = client.post("/api/device/cartplan", json={"store": "magnit", "top_up": True}).get_json()

    skus = {line["sku"] for line in data["lines"]}
    # Молоко (kept) НЕ исключается: текущий прогон его не положил, значит добор должен.
    assert "mag-milk" in skus, "kept-позиция ошибочно исключена из наряда добора"
    assert "mag-cheese" in skus
    assert data["already_landed"] == 0


# ---------- check_signal и check_pause_ms в rules() ----------

def test_rules_contain_check_signal(web):
    """rules() содержит check_signal — паттерн бот-проверки — и check_pause_ms."""
    data = _client(web).post("/api/device/cartplan", json={"store": "magnit"}).get_json()
    rules = data["rules"]

    assert "check_signal" in rules, "нет check_signal в rules"
    assert isinstance(rules["check_signal"], str), "check_signal должен быть строкой (regex)"
    assert "я не робот" in rules["check_signal"], "check_signal не содержит ключевую фразу"
    assert rules["check_signal"] == signals.CHECK, "check_signal не совпадает с signals.CHECK"

    assert "check_pause_ms" in rules, "нет check_pause_ms в rules"
    assert isinstance(rules["check_pause_ms"], int)
    assert rules["check_pause_ms"] >= 1000, "пауза бот-проверки слишком мала"
