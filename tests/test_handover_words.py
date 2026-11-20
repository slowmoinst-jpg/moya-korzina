"""Слова о передаче корзины: обещаем ровно то, что сеть делает."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import handover as ho  # noqa: E402


def test_every_chain_stands_on_a_rung():
    """Все семь сетей расставлены по ступеням — ни одна не осталась без способа.

    METRO пришла седьмой 19.09.2026. Её ступень здесь — ITEMS, и это ЗАПАСНОЙ путь:
    главный у неё сильнее всех прочих — собственный метод наполнения корзины
    (app/connectors/metro_cart.py). Сторож смотрит именно на запасной, потому что
    экран результата показывает его, когда наряд не собрался.
    """
    assert set(ho.KIND_BY_STORE) == {"vkusvill", "magnit", "lenta",
                                     "pyaterochka", "dixy", "samokat", "metro"}
    for code, kind in ho.KIND_BY_STORE.items():
        assert kind in (ho.LINK, ho.ITEMS, ho.SEARCH, ho.LIST), code


def test_lenta_warns_that_the_store_does_not_travel_in_the_link():
    """Магазин в ссылке не передаётся, и человек должен узнать это ДО нажатия.

    Живое открытие 18.09.2026: ссылка открылась в чужой точке («Гипер, Москва и
    МО, самовывоз −5 %»), а расчёт шёл по адресу человека. Цены зависят от точки,
    значит промолчать здесь — значит показать одну сумму, а дать другую.
    """
    note = ho.NOTE_BY_STORE["lenta"]

    assert "магазин" in note.lower()
    assert "цен" in note.lower(), "не сказано, чем это грозит"


def test_vkusvill_is_not_scared_with_a_trouble_it_does_not_have():
    """ВкусВилл сам спрашивает про адрес — пугать его предупреждением Ленты нельзя."""
    note = ho.NOTE_BY_STORE["vkusvill"]

    assert "адрес" in note.lower()
    assert "Лента" not in note


def test_a_promise_is_made_only_where_it_was_checked():
    """Про открытие в приложении сети обещаем только Магниту.

    У него это проверено по apple-app-site-association (/product/*). У Дикси
    такого подтверждения нет, и своя подпись как раз это и закрывает.
    """
    assert "приложении магазина" in ho.NOTE_BY_KIND[ho.ITEMS]
    assert "приложени" not in ho.NOTE_BY_STORE["dixy"]
