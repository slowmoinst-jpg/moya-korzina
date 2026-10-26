"""Проверка трёх человеческих шагов (app/humansteps.py).

Код со своей стороны сделан; дальше — Лента (открыть ссылку), Дикси (вход заново +
подтверждение связи), Магнит (выбранный магазин). Модуль ничего не меняет, только читает
состояние — и должен честно говорить, какой шаг ещё не сделан.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import humansteps  # noqa: E402


def test_steps_name_the_human_action_and_report_readiness(monkeypatch):
    monkeypatch.setattr(humansteps, "_basket_lines", lambda: [
        {"product_id": 10, "name": "Молоко Parmalat 1,8% 1 л", "qty": 1, "pack_g": 1000}])

    # Лента: точного совпадения нет; Дикси: вход сохранён, связь подтверждена, но флага нет;
    # Магнит: вход сохранён.
    monkeypatch.setattr(humansteps.storefind, "find",
                        lambda code, name, pack_g=None, **kw: {"ok": False, "found": [],
                                                               "why": "другая линейка"})
    monkeypatch.setattr(humansteps.browser_store, "load", lambda chain: {"cookies": [{}, {}]})
    monkeypatch.setattr(humansteps.store_accounts, "can", lambda chain, what: False)

    class _Store:
        id = 5

    monkeypatch.setattr(humansteps.repo, "get_store", lambda code: _Store())
    monkeypatch.setattr(humansteps.repo, "confirmed_mapping",
                        lambda product_id, store_id, real_only=False: {"sku": "2000328870"})

    state = humansteps.check(use_network=False)

    assert state["lenta"]["ready_to_try"] is False
    assert "Лента не продаёт" in state["lenta"]["note"]
    assert state["dixy"]["login_saved"] is True
    assert state["dixy"]["confirmed_articles"] == 1
    assert state["dixy"]["can_place"] is False, "без флага cart кладка обещать не должны"
    assert "флаг cart" in state["dixy"]["note"]
    assert state["magnit"]["login_saved"] is True

    text = humansteps.render(state)
    assert "открыть ссылку корзины" in text
    assert "войти в кабинет заново" in text
    assert "выбрать магазин" in text


def test_ready_when_link_has_exact_matches(monkeypatch):
    monkeypatch.setattr(humansteps, "_basket_lines", lambda: [
        {"product_id": 1, "name": "Сыр Сулугуни 300 г", "qty": 1, "pack_g": 300}])
    monkeypatch.setattr(humansteps.storefind, "find",
                        lambda code, name, pack_g=None, **kw: {"ok": True, "found": [{"sku": "9"}]})
    monkeypatch.setattr(humansteps.browser_store, "load", lambda chain: None)
    monkeypatch.setattr(humansteps.store_accounts, "can", lambda chain, what: False)
    monkeypatch.setattr(humansteps.repo, "get_store", lambda code: None)
    monkeypatch.setattr(humansteps.repo, "confirmed_mapping",
                        lambda *args, **kwargs: None)

    state = humansteps.check(use_network=False)

    assert state["lenta"]["exact_matches"] == 1
    assert state["lenta"]["ready_to_try"] is True
    assert state["lenta"]["note"] == ""


def test_store_choice_is_unknown_without_network(monkeypatch):
    """Без похода в витрину выбор магазина честно «неизвестно», а не «нет»."""
    monkeypatch.setattr(humansteps, "_basket_lines", lambda: [])
    monkeypatch.setattr(humansteps.browser_store, "load", lambda chain: {"cookies": []})
    monkeypatch.setattr(humansteps.store_accounts, "can", lambda chain, what: False)

    state = humansteps.check(use_network=False)

    assert state["magnit"]["store_choice"] == "unknown"
    text = humansteps.render(state)
    assert "магазин выбран: неизвестно" in text, text


class _Conn:
    """Подключение сети, как его отдаёт store_accounts.connections()."""

    def __init__(self, code, connected):
        self.store_code, self.connected = code, connected


def test_board_covers_every_chain_and_names_the_next_step(monkeypatch):
    from app import cartrun, carttotal

    rows = {
        "pyaterochka": cartrun.Row(code="pyaterochka", state=cartrun.DONE, planned=4,
                                   landed=["a", "b"]),
        "dixy": cartrun.Row(code="dixy", state=cartrun.LOGIN),
        "metro": cartrun.Row(code="metro", state=cartrun.DONE, planned=2, landed=["a", "b"],
                             total=carttotal.Total(sum=340.0, source=carttotal.NETWORK)),
    }
    monkeypatch.setattr(cartrun, "get", lambda code: rows.get(code))

    monkeypatch.setattr(humansteps.store_accounts, "connections", lambda: [
        _Conn("pyaterochka", True), _Conn("dixy", True), _Conn("metro", True),
        _Conn("lenta", False)])

    board = {r["code"]: r for r in humansteps.board()}

    assert set(board) == {"pyaterochka", "dixy", "metro", "lenta"}
    assert "легло 2 из 4" in board["pyaterochka"]["next"] and not board["pyaterochka"]["ready"]
    assert "войти заново" in board["dixy"]["next"]
    assert board["metro"]["ready"] and board["metro"]["network_total"]
    assert "подключить" in board["lenta"]["next"]
    assert "готово" in humansteps.render_board(list(board.values()))


def test_board_takes_the_saved_login_over_the_connection_flag(monkeypatch):
    """Экран «Настройки» знает вход точнее флага: сеть с сохранённым входом не просит «подключить».

    Флаг подключения ставится отчётом о сборе и чаще погашен, чем нет (web/phone/common.py,
    _login). Без signed_in сводка звала «Войти» у сети, что в соседнем списке экрана — среди
    подключённых.
    """
    from app import cartrun

    rows = {"dixy": cartrun.Row(code="dixy", state=cartrun.LOGIN, login="again"),
            "magnit": cartrun.Row(code="magnit", state=cartrun.LOGIN, login="store")}
    monkeypatch.setattr(cartrun, "get", lambda code: rows.get(code))
    monkeypatch.setattr(humansteps.store_accounts, "connections", lambda: [
        _Conn("lenta", False), _Conn("dixy", False), _Conn("magnit", False)])

    by_flag = {r["code"]: r for r in humansteps.board()}
    assert not by_flag["lenta"]["connected"] and "подключить" in by_flag["lenta"]["next"]

    saved = {r["code"]: r for r in humansteps.board(signed_in={"lenta", "dixy", "magnit"})}
    assert saved["lenta"]["connected"] and "подключить" not in saved["lenta"]["next"]
    assert [saved[code]["act"] for code in ("lenta", "dixy", "magnit")] == ["", "login", "store"], \
        "шаг в окне сети назван полем, а не угадывается по словам"
