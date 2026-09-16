"""Экраны входа: главная и магазины.

Проверяется не вёрстка, а то, что помощники экранов не падают и говорят правду.
Это важнее обычного: main.py оборачивает и шапку, и отрисовку в общий перехват,
поэтому сломанный экран показывается пустотой, а не ошибкой — молча.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, repo  # noqa: E402
from app.db import init_db  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "s.db"))
    init_db()


def test_home_survives_an_empty_install(db):
    """Первый запуск — пустая база. Именно тогда главная и нужна больше всего."""
    from app.ui.screens import home

    assert home.header_stats() == "Начните с загрузки чеков"


def test_home_counts_what_is_loaded(db):
    from app.ui.screens import home

    store = repo.get_store("magnit")
    repo.add_history_row("2026-09-01", store.id, None, "Молоко", 1, 80.0, 80.0)

    assert "1" in home.header_stats()


def test_every_store_is_described(db):
    """Магазин без описания показался бы пустой карточкой — это заметно только тут."""
    from app.ui.screens.stores import STORES

    missing = [s.code for s in repo.list_stores() if s.code not in STORES]

    assert not missing, f"не описаны источники цен: {missing}"


def test_store_badges_are_known(db):
    """Каждому состоянию нужна подпись и цвет, иначе карточка упадёт на отрисовке."""
    from app.ui.screens.stores import BADGE, STORES

    for code, (state, *_) in STORES.items():
        assert state in BADGE, f"{code}: состояние {state!r} без подписи"


def test_home_and_stores_are_in_the_navigation():
    """Экран, не попавший в меню, недостижим — а ошибки об этом не будет."""
    from app.ui import main

    assert list(main.SCREENS)[0] == "Главная", "приложение должно открываться главной"
    for name in ("Главная", "Магазины"):
        assert name in main.SCREENS
        assert name in main.SCREEN_MODULES
        assert name in main.EYEBROWS, f"«{name}» без надзаголовка"


# ---------- история как чеки ----------
def test_rows_become_receipts_by_day_and_store():
    """Чек — это день плюс магазин: отдельной сущности чека в базе нет."""
    from app.ui.screens.history import _receipts

    rows = [
        {"date": "2026-08-16", "store_code": "pyaterochka", "store_name": "Пятёрочка", "total": 100},
        {"date": "2026-08-16", "store_code": "pyaterochka", "store_name": "Пятёрочка", "total": 50},
        {"date": "2026-08-16", "store_code": "magnit", "store_name": "Магнит", "total": 30},
        {"date": "2026-08-10", "store_code": "pyaterochka", "store_name": "Пятёрочка", "total": 70},
    ]
    receipts = _receipts(rows)

    assert len(receipts) == 3, "один день в двух магазинах — два разных чека"
    assert len(receipts[0]["lines"]) == 2
    assert receipts[0]["total"] == 150
    assert [r["date"] for r in receipts] == ["2026-08-16", "2026-08-16", "2026-08-10"], \
        "порядок строк сохраняется: свежие чеки сверху"


def test_receipt_label_reads_like_a_receipt():
    from app.ui.screens.history import _receipt_label

    label = _receipt_label({
        "date": "2026-08-16", "store_name": "Пятёрочка",
        "lines": [{}, {}], "total": 3690.4,
    })

    assert "16 августа 2026" in label
    assert "Пятёрочка" in label
    assert "2 позиции" in label


def test_position_count_agrees_in_russian():
    """«2 позиций» в заголовке выдаёт машину — числительное должно согласовываться."""
    from app.ui.screens.history import format_plural

    assert format_plural(1) == "1 позиция"
    assert format_plural(2) == "2 позиции"
    assert format_plural(5) == "5 позиций"
    assert format_plural(11) == "11 позиций"
    assert format_plural(21) == "21 позиция"
    assert format_plural(114) == "114 позиций"
