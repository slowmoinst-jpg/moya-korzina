"""Разбор письма из доставки и страницы заказа.

Образцы повторяют формы, которые встречаются в письмах Ленты, Самоката и прочих
доставок: таблица, столбик, HTML, простой список. Разбор должен понимать все
четыре — иначе историю покупок человеку неоткуда взять, кроме как вбивать руками,
а этого никто делать не станет.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, repo  # noqa: E402
from app.db import init_db  # noqa: E402
from app.importers.orders import parse_order, strip_html  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "o.db"))
    init_db()


TABLE = """Ваш заказ №12345678 от 15.09.2026
Молоко Простоквашино 2,5% 930 мл      2 шт × 149,00 ₽ = 298,00 ₽
Огурцы короткоплодные                 0,4 кг × 215 ₽    86,00 ₽
Хлеб Бородинский нарезка 270 г        1 шт              94,00 ₽
Доставка                                                199,00 ₽
Итого: 478,00 ₽"""

COLUMN = """Чек на ваш заказ
Молоко Простоквашино 2,5% 930 мл
2 × 149 ₽
298 ₽
Сыр Страчателла 200 г
1 × 219 ₽
219 ₽
Оплачено картой
Итого 517 ₽"""

MAIL = """<table><tr><td>Яблоки Голден</td><td>1,2 кг</td><td>149,90&nbsp;₽</td></tr>
<tr><td>Кефир 1%, 900 мл</td><td>2 шт</td><td>178,00&nbsp;₽</td></tr></table>
<p>Итого: 327,90 ₽</p>"""


def test_table_shape():
    receipt, skipped = parse_order(TABLE)

    assert [r.raw_name for r in receipt.rows][:1] == ["Молоко Простоквашино 2,5% 930 мл"]
    assert len(receipt.rows) == 3 and not skipped
    assert receipt.rows[0].qty == 2 and receipt.rows[0].unit_price == 149.0


def test_weight_goods_keep_their_fraction():
    receipt, _ = parse_order(TABLE)
    cucumbers = next(r for r in receipt.rows if "Огурцы" in r.raw_name)

    assert cucumbers.qty == 0.4 and cucumbers.total == 86.0


def test_delivery_and_total_are_not_products():
    """«Доставка 199 ₽» — не товар, иначе она уедет в корзину как позиция."""
    receipt, _ = parse_order(TABLE)

    assert not any("оставка" in r.raw_name for r in receipt.rows)
    assert receipt.total == 478.0


def test_column_shape_joins_name_with_the_line_below():
    """В письмах название и цифры часто стоят на разных строках."""
    receipt, skipped = parse_order(COLUMN)

    assert len(receipt.rows) == 2 and not skipped
    assert receipt.rows[0].qty == 2 and receipt.rows[0].total == 298.0
    assert receipt.total == 517.0


def test_html_mail_is_understood():
    receipt, _ = parse_order(MAIL)

    assert [r.raw_name for r in receipt.rows] == ["Яблоки Голден", "Кефир 1%, 900 мл"]
    assert receipt.rows[0].qty == 1.2 and receipt.rows[0].total == 149.9


def test_packaging_is_not_quantity():
    """«Сыр 200 г — 219 ₽»: двести грамм это фасовка, а не сколько взяли."""
    receipt, _ = parse_order("Сыр Страчателла 200 г — 219 ₽")
    row = receipt.rows[0]

    assert row.qty == 1.0 and row.unit_price == 219.0
    assert row.raw_name == "Сыр Страчателла 200 г"


def test_date_is_picked_from_the_order_header():
    assert parse_order(TABLE)[0].date == "2026-09-15"


def test_html_tags_do_not_glue_words():
    assert "Яблоки" in strip_html("<td>Яблоки</td><td>Голден</td>")
    assert "<td>" not in strip_html("<td>Яблоки</td>")


def test_import_writes_history_and_creates_products(db):
    from app.importers import import_order_text

    result = import_order_text(TABLE, "lenta")

    assert result["rows"] == 3 and result["products_created"] == 3
    history = repo.list_history()
    assert len(history) == 3
    assert all(row["store_code"] == "lenta" for row in history)


def test_import_of_nothing_changes_nothing(db):
    from app.importers import import_order_text

    result = import_order_text("просто текст без цен", "lenta")

    assert result["rows"] == 0 and repo.list_history() == []


def test_preview_does_not_touch_the_database(db):
    from app.importers.text_import import preview

    seen = preview(TABLE, "lenta")

    assert len(seen["rows"]) == 3 and seen["store"] == "Лента"
    assert repo.list_history() == [], "предпросмотр обязан быть безопасным"


def test_samokat_is_a_store(db):
    from app import handover
    from app.connectors import get_connector

    assert repo.get_store("samokat") is not None
    assert get_connector("samokat").code == "samokat"
    assert handover.KIND_BY_STORE["samokat"] == handover.LIST
