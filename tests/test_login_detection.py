"""Этап 2: распознавание «выберите магазин» и «войдите заново» по причинам позиций.

Магнит — «выберите магазин» при отсутствии выбранной точки -> login="store".
Дикси — «войдите заново» при протухшей сессии -> login="again".
Паттерны: cartrun.STORE_WHY, cartrun.LOGIN_WHY.

Тесты проверяют:
  1. STORE_WHY срабатывает на фразы выбора магазина/адреса.
  2. LOGIN_WHY срабатывает на фразы авторизации/сессии.
  3. Ни STORE_WHY, ни LOGIN_WHY не срабатывают на «нет в наличии».
  4. _to_row() выставляет state=LOGIN, login="store" при паттерне STORE_WHY.
  5. _to_row() выставляет state=LOGIN, login="again" при паттерне LOGIN_WHY.
  6. cartrun.accept() сохраняет login="store" в строке Row.
  7. _to_row() выставляет FAILED (не LOGIN) при «нет в наличии».
  8. _to_row() НЕ меняет DONE, если всё легло успешно.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


def _make_report(store: str, items: list[dict], **extra) -> dict:
    """Минимальный отчёт телефона для _to_row()."""
    return {
        "store": store,
        "at": datetime.now().isoformat(timespec="seconds"),
        "app_version": "test",
        "planned": len(items),
        "landed": sum(1 for i in items if i.get("ok")),
        "items": items,
        "page_totals": [],
        "api_totals": {},
        "background": False,
        **extra,
    }


# ───── 1. STORE_WHY ────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "выберите магазин",
    "Выберите адрес доставки",
    "выбери магазин",
    "выбери адрес",
    "укажите адрес",
    "выбрать магазин",
    "магазин не выбран",
])
def test_store_why_matches_store_selection_phrases(phrase: str):
    from app.cartrun import STORE_WHY
    assert STORE_WHY.search(phrase), f"STORE_WHY не узнал: {phrase!r}"


# ───── 2. LOGIN_WHY ────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "войдите заново",
    "Авторизуйтесь, чтобы продолжить",
    "сессия истекла",
    "войдите снова",
    "вы не авторизованы",
    "Please log in to continue",
    "нет входа",
])
def test_login_why_matches_login_phrases(phrase: str):
    from app.cartrun import LOGIN_WHY
    assert LOGIN_WHY.search(phrase), f"LOGIN_WHY не узнал: {phrase!r}"


# ───── 3. Игнорирование обычных причин ─────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "нет в наличии",
    "товара нет",
    "кнопки не нашлось",
    "не нашлось",
    "",
    "добавлено",
    "положено",
])
def test_patterns_ignore_normal_phrases(phrase: str):
    from app.cartrun import LOGIN_WHY, STORE_WHY
    assert not STORE_WHY.search(phrase), f"STORE_WHY ложное: {phrase!r}"
    assert not LOGIN_WHY.search(phrase), f"LOGIN_WHY ложное: {phrase!r}"


# ───── 4. _to_row() выставляет LOGIN + login="store" для Магнита ───────────

def test_to_row_sets_login_store_when_store_pattern():
    """Когда позиция не легла с причиной «выберите магазин» — state=login, login=store."""
    from app import cartrun

    items = [
        {"name": "Молоко", "sku": "sku1", "ok": False,
         "why": "выберите магазин перед добавлением", "how": "tap",
         "put": 0, "times": 1, "calls": []},
    ]
    report = _make_report("magnit", items)

    saved_rows: list = []

    def fake_accept(reply, *, by="phone"):
        saved_rows.append(reply)
        return cartrun.Row(code=reply["store"], state=reply.get("state", cartrun.FAILED),
                           login=reply.get("login", ""))

    with patch("app.cartrun.accept", side_effect=fake_accept), \
         patch("app.cartrun.save", side_effect=lambda r: r), \
         patch("app.cartrun.networks", return_value=["magnit"]), \
         patch("app.cartrun.get", return_value=None), \
         patch("app.repo.get_store", return_value=MagicMock(name="Магнит")):
        from app.phonecart import _to_row
        _to_row(report)

    assert saved_rows, "_to_row() не вызвал accept()"
    assert saved_rows[0].get("state") == cartrun.LOGIN
    assert saved_rows[0].get("login") == "store", f"Ожидали login=store, получили: {saved_rows[0].get('login')!r}"


# ───── 5. _to_row() выставляет LOGIN + login="again" для Дикси ────────────

def test_to_row_sets_login_again_when_login_pattern():
    """Когда позиция не легла с причиной «войдите заново» — state=login, login=again."""
    from app import cartrun

    items = [
        {"name": "Сыр", "sku": "sku2", "ok": False,
         "why": "сессия истекла, войдите заново", "how": "tap",
         "put": 0, "times": 1, "calls": []},
    ]
    report = _make_report("dixy", items)

    saved_rows: list = []

    def fake_accept(reply, *, by="phone"):
        saved_rows.append(reply)
        return cartrun.Row(code=reply["store"], state=reply.get("state", cartrun.FAILED),
                           login=reply.get("login", ""))

    with patch("app.cartrun.accept", side_effect=fake_accept), \
         patch("app.cartrun.save", side_effect=lambda r: r), \
         patch("app.cartrun.networks", return_value=["dixy"]), \
         patch("app.cartrun.get", return_value=None), \
         patch("app.repo.get_store", return_value=MagicMock(name="Дикси")):
        from app.phonecart import _to_row
        _to_row(report)

    assert saved_rows, "_to_row() не вызвал accept()"
    assert saved_rows[0].get("state") == cartrun.LOGIN
    assert saved_rows[0].get("login") == "again", f"Ожидали login=again, получили: {saved_rows[0].get('login')!r}"


# ───── 6. cartrun.accept() сохраняет login="store" в строке Row ────────────

def test_cartrun_accept_preserves_store_login():
    """accept() с state=login и login=store правильно сохраняет Row.login = 'store'."""
    from app import cartrun

    reply = {
        "store": "magnit",
        "state": cartrun.LOGIN,
        "login": "store",
        "landed": [],
        "missing": [{"name": "Молоко", "why": "выберите магазин", "gone": False}],
    }
    with patch("app.cartrun.save", side_effect=lambda r: r), \
         patch("app.cartrun.networks", return_value=["magnit"]), \
         patch("app.cartrun.get", return_value=None), \
         patch("app.repo.get_store", return_value=MagicMock(name="Магнит")):
        row = cartrun.accept(reply)

    assert row.state == cartrun.LOGIN
    assert row.login == "store"


# ───── 7. «Нет в наличии» — это FAILED, а не LOGIN ─────────────────────────

def test_to_row_does_not_set_login_for_out_of_stock():
    """«Нет в наличии» — это FAILED, а не LOGIN."""
    from app import cartrun

    items = [
        {"name": "Сыр", "sku": "sku2", "ok": False,
         "why": "нет в наличии", "how": "tap",
         "put": 0, "times": 1, "calls": []},
    ]
    report = _make_report("dixy", items)

    saved_rows: list = []

    def fake_accept(reply, *, by="phone"):
        saved_rows.append(reply)
        state = reply.get("state", cartrun.DONE if reply.get("landed") else cartrun.FAILED)
        return cartrun.Row(code=reply["store"], state=state)

    with patch("app.cartrun.accept", side_effect=fake_accept), \
         patch("app.cartrun.save", side_effect=lambda r: r), \
         patch("app.cartrun.networks", return_value=["dixy"]), \
         patch("app.cartrun.get", return_value=None), \
         patch("app.repo.get_store", return_value=MagicMock(name="Дикси")):
        from app.phonecart import _to_row
        _to_row(report)

    assert saved_rows, "_to_row() не вызвал accept()"
    assert saved_rows[0].get("state") != cartrun.LOGIN


# ───── 8. Все позиции легли — DONE ─────────────────────────────────────────

def test_to_row_done_when_all_landed_no_login_pattern():
    """Все позиции легли — reply не содержит state (accept сам решит DONE)."""
    from app import cartrun

    items = [
        {"name": "Молоко", "sku": "sku1", "ok": True,
         "why": "", "how": "tap", "put": 1, "times": 1, "calls": []},
    ]
    report = _make_report("pyaterochka", items)

    saved_rows: list = []

    def fake_accept(reply, *, by="phone"):
        saved_rows.append(reply)
        state = reply.get("state", cartrun.DONE if reply.get("landed") else cartrun.FAILED)
        return cartrun.Row(code=reply["store"], state=state)

    with patch("app.cartrun.accept", side_effect=fake_accept), \
         patch("app.cartrun.save", side_effect=lambda r: r), \
         patch("app.cartrun.networks", return_value=["pyaterochka"]), \
         patch("app.cartrun.get", return_value=None), \
         patch("app.repo.get_store", return_value=MagicMock(name="Пятёрочка")):
        from app.phonecart import _to_row
        _to_row(report)

    assert saved_rows, "_to_row() не вызвал accept()"
    assert saved_rows[0].get("state") != cartrun.LOGIN
