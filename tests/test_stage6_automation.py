"""Тесты Этапа 6 (Автоматичность): фоновая сборка, устаревание итога 6 часов и темп запросов."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

from app import cartrun, repo
from app.carttotal import NETWORK, Total
from app.models import Product
from app.shopbrowser import cart
from app import phonecart


def test_fresh_function_checks_six_hours_by_default():
    now = datetime.now()
    five_hours_ago = (now - timedelta(hours=5)).isoformat()
    seven_hours_ago = (now - timedelta(hours=7)).isoformat()

    assert cartrun._fresh(five_hours_ago) is True
    assert cartrun._fresh(seven_hours_ago) is False
    assert cartrun._fresh(five_hours_ago, hours=4.0) is False
    assert cartrun._fresh(seven_hours_ago, hours=8.0) is True


def test_cart_resend_hours_is_six():
    assert cart.RESEND_HOURS == 6


def test_already_sent_respects_six_hour_window(monkeypatch):
    chain = "pyaterochka"
    now = datetime.now()

    # Сборка 5 часов назад — корзина считается уже переданной (свежая)
    monkeypatch.setattr(cart, "progress", lambda ch: {
        "finished_at": (now - timedelta(hours=5)).isoformat(),
        "items": [{"ok": True, "name": "Молоко"}]
    })
    res_fresh = cart.already_sent(chain)
    assert res_fresh is not None
    assert res_fresh["landed"] == 1

    # Сборка 7 часов назад — устарела (> 6 часов), можно собирать снова
    monkeypatch.setattr(cart, "progress", lambda ch: {
        "finished_at": (now - timedelta(hours=7)).isoformat(),
        "items": [{"ok": True, "name": "Молоко"}]
    })
    res_stale = cart.already_sent(chain)
    assert res_stale is None


def test_phonecart_rules_provide_safe_request_pauses():
    """Правила наряда содержат безопасные паузы между позициями для соблюдения rate limits."""
    for chain in phonecart.CHAINS:
        rules = phonecart.rules(chain)
        assert "pause_ms" in rules
        assert rules["pause_ms"] >= 1000
        assert "request_pause_ms" in rules
        assert rules["request_pause_ms"] >= 300
