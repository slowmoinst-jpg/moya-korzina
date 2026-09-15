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


# ---------- настоящие чеки, а не только письма ----------
PAPER = """ЛЕНТА
КАССОВЫЙ ЧЕК
1. Молоко ПРОСТОКВАШИНО 2,5% 930мл
2 x 149.00 =298.00
2. Хлеб БОРОДИНСКИЙ нарезка 270г
1 x 94.00 =94.00
3. Огурцы короткоплодные вес
0.412 x 215.00 =88.58
ИТОГ 480.58"""

SCREENSHOT = """Ваш заказ
Молоко Простоквашино
2,5%, 930 мл
2 шт
298 ₽
Хлеб Бородинский
нарезка, 270 г
1 шт
94 ₽
Итого 392 ₽"""

NO_CURRENCY_SIGN = """Молоко Простоквашино 930 мл 2 шт 298 Р
Хлеб Бородинский 270 г 1 шт 94 Р
ИТОГО 478 Р"""


def test_paper_receipt_with_name_above_the_numbers():
    """Бумажный чек: название отдельной строкой, «2 x 149.00 =298.00» под ним."""
    receipt, skipped = parse_order(PAPER)

    assert len(receipt.rows) == 3 and not skipped
    assert receipt.rows[0].raw_name == "Молоко ПРОСТОКВАШИНО 2,5% 930мл"
    assert receipt.rows[0].qty == 2 and receipt.rows[0].total == 298.0
    assert receipt.total == 480.58


def test_receipt_headers_are_not_products():
    """«КАССОВЫЙ ЧЕК» и название сети не должны стать позицией корзины."""
    names = [r.raw_name for r in parse_order(PAPER)[0].rows]

    assert not any("ЧЕК" in n.upper() for n in names)
    assert "ЛЕНТА" not in names


def test_item_numbers_are_stripped_from_names():
    assert not parse_order(PAPER)[0].rows[0].raw_name.startswith("1.")


def test_screenshot_with_name_split_over_two_lines():
    """Скриншот приложения: название на двух строках, потом «2 шт», потом «298 ₽»."""
    receipt, _ = parse_order(SCREENSHOT)

    assert len(receipt.rows) == 2
    assert receipt.rows[0].raw_name == "Молоко Простоквашино, 2,5%, 930 мл"
    assert receipt.rows[0].qty == 2 and receipt.rows[0].total == 298.0


def test_packaging_line_is_kept_in_the_name():
    """«2,5%, 930 мл» — слова из трёх букв в ней нет, но терять её нельзя."""
    first = parse_order(SCREENSHOT)[0].rows[0]
    assert "930 мл" in first.raw_name


def test_currency_sign_may_be_a_letter_or_missing():
    """Распознавание с фото даёт «Р» вместо «₽», а часто не даёт ничего."""
    receipt, _ = parse_order(NO_CURRENCY_SIGN)

    assert len(receipt.rows) == 2
    assert receipt.rows[0].total == 298.0 and receipt.total == 478.0


def test_a_letter_p_inside_a_word_is_not_currency():
    """Иначе «Рис» и «Ряженка» превратятся в суммы."""
    receipt, _ = parse_order("Рис круглозёрный 900 г 1 шт 89 Р")

    assert len(receipt.rows) == 1
    assert receipt.rows[0].raw_name.startswith("Рис")


# ---------- кассовый чек из «Мои чеки онлайн» ----------
FNS = """КАССОВЫЙ ЧЕК
ПРИХОД
Общество с ограниченной ответственностью "Интернет Решения"
ПРЕДМЕТ РАСЧЕТА
ЦЕНА, Р
КОЛ-ВО
СУММА, Р
1. Мультивитамины 2 474,11 1 2 474,11
Myprotein Alpha men,
комплекс витаминов и
минералов для
мужчин, 240 таблеток
ИНН Поставщика: 246214864320
НДС не облагается
2. Обработка заказа в 129,89 1 129,89
пункте выдачи
ИНН Поставщика: 783800600274
НДС 5%
ИТОГ: 2 604,00
Наличные 0,00
Безналичные 0,00
Предоплата (аванс) 2 604,00
НДС не облагается 2 474,11
НДС со ставкой 5% 6,19"""


def test_fns_receipt_three_columns_without_multiplication_sign():
    """Чек ФНС печатает цену, количество и сумму подряд, без «×» и без «₽»."""
    receipt, skipped = parse_order(FNS)

    assert len(receipt.rows) == 2 and not skipped
    assert receipt.rows[0].unit_price == 2474.11 and receipt.rows[0].qty == 1
    assert receipt.total == 2604.0


def test_fns_wrapped_name_is_collected():
    """Длинное название переносится ВНИЗ, под строку с числами, и его нельзя терять."""
    name = parse_order(FNS)[0].rows[0].raw_name

    assert name.startswith("Мультивитамины")
    assert "240 таблеток" in name, "хвост названия обязан дойти до конца"


def test_fns_payment_lines_are_not_products():
    """«Наличные 0,00» и «Предоплата (аванс) 2 604,00» — не товары."""
    names = [r.raw_name for r in parse_order(FNS)[0].rows]

    assert not any("аличные" in n for n in names)
    assert not any("редоплата" in n for n in names)
    assert not any("НДС" in n for n in names)
    assert not any("ИНН" in n for n in names)


def test_columns_are_read_in_printed_order():
    """Порядок берём как напечатано: у ФНС это «ЦЕНА · КОЛ-ВО · СУММА».

    Угадывать его умножением нельзя, и это стоит помнить: «3 × 100» и «100 × 3»
    дают одно и то же, так что любая проверка сошлась бы при любой расстановке.
    """
    receipt, _ = parse_order("Товар 100,00 3 300,00")
    row = receipt.rows[0]

    assert row.unit_price == 100.0 and row.qty == 3 and row.total == 300.0


def test_three_numbers_that_do_not_multiply_keep_only_the_sum():
    """Не сошлось — берём сумму и не множим мусор: последнее число надёжнее всего."""
    receipt, _ = parse_order("Товар весовой 215,00 2 500,00")
    row = receipt.rows[0]

    assert row.total == 500.0
