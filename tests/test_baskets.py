"""Сборка корзины из истории и справочник условий банков.

Все тесты работают на временной БД: app.config.db_path переопределяется через
monkeypatch, рабочая data/basket.db не затрагивается.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import bank_reference, baskets, config, repo  # noqa: E402
from app.db import init_db  # noqa: E402
from app.models import Product  # noqa: E402


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    path = str(tmp_path / "baskets.db")
    monkeypatch.setattr(config, "db_path", lambda: path)
    assert config.db_path() != os.path.join(ROOT, "data", "basket.db")
    init_db()


@pytest.fixture
def history():
    """Две покупки в разных месяцах: одна позиция берётся в обе, одна только раз."""
    store = repo.get_store("pyaterochka")
    milk = repo.upsert_product(Product(None, "Молоко 1 л", unit="pcs"))
    apples = repo.upsert_product(Product(None, "Яблоки", unit="kg"))
    once = repo.upsert_product(Product(None, "Торт", unit="pcs"))

    # июль: молоко 2 шт, яблоки 1,2 кг
    repo.add_history_row("2026-07-10", store.id, milk, "Молоко", 2, 80.0, 160.0)
    repo.add_history_row("2026-07-10", store.id, apples, "Яблоки", 1.2, 150.0, 180.0)
    # август: молоко двумя строками (1+3), яблоки 0,8 кг, торт 1 шт
    repo.add_history_row("2026-08-14", store.id, milk, "Молоко", 1, 80.0, 80.0)
    repo.add_history_row("2026-08-14", store.id, milk, "Молоко", 3, 80.0, 240.0)
    repo.add_history_row("2026-08-14", store.id, apples, "Яблоки", 0.8, 150.0, 120.0)
    repo.add_history_row("2026-08-14", store.id, once, "Торт", 1, 700.0, 700.0)
    return {"milk": milk, "apples": apples, "once": once}


# ---------- как в прошлый раз ----------
def test_last_purchase_takes_only_latest_date(history):
    date, items = baskets.last_purchase_items()
    assert date == "2026-08-14"
    assert {i["product_id"] for i in items} == {history["milk"], history["apples"], history["once"]}


def test_last_purchase_merges_duplicate_lines(history):
    """Молоко в чеке двумя строками — в корзине это одна позиция на 4 штуки."""
    _, items = baskets.last_purchase_items()
    milk = next(i for i in items if i["product_id"] == history["milk"])
    assert milk["qty"] == 4


# ---------- по среднему ----------
def test_average_divides_by_months_with_purchases(history):
    """Месяцев два, молока всего 6 штук — в среднем 3 за месяц."""
    months, items = baskets.average_month_items()
    assert months == 2
    milk = next(i for i in items if i["product_id"] == history["milk"])
    assert milk["qty"] == 3


def test_average_rounds_pieces_and_keeps_weight(history):
    """Штучные — целыми, весовые — до грамма: (1,2 + 0,8) / 2 = 1,0 кг."""
    _, items = baskets.average_month_items()
    apples = next(i for i in items if i["product_id"] == history["apples"])
    assert apples["qty"] == pytest.approx(1.0)
    assert all(float(i["qty"]).is_integer() for i in items if i["unit"] == "pcs")


def test_average_drops_items_that_round_to_zero(history):
    """Торт куплен один раз за два месяца — 0,5 штуки в корзину не кладём."""
    _, items = baskets.average_month_items()
    assert history["once"] not in {i["product_id"] for i in items}


# ---------- создание корзины ----------
def test_build_from_history_creates_basket_with_source(history):
    result = baskets.build_from_history(baskets.LAST)
    assert result["basket_id"]
    saved = next(b for b in repo.list_baskets() if b["id"] == result["basket_id"])
    assert saved["source"] == "history"
    assert len(repo.basket_items(result["basket_id"])) == len(result["items"])


def test_build_average_marks_source_average(history):
    result = baskets.build_from_history(baskets.AVERAGE)
    saved = next(b for b in repo.list_baskets() if b["id"] == result["basket_id"])
    assert saved["source"] == "average"
    assert result["months"] == 2


def test_build_from_empty_history_makes_nothing():
    result = baskets.build_from_history(baskets.LAST)
    assert result["basket_id"] is None
    assert result["items"] == []
    assert repo.list_baskets() == []


# ---------- услуги из чека — не товар ----------


# ---------- регулярные покупки ----------
def test_regular_purchases_need_two_different_dates(history):
    regular = {r["product_id"] for r in baskets.regular_purchases(min_times=2)}
    assert history["milk"] in regular and history["apples"] in regular
    assert history["once"] not in regular


def test_missing_regulars_reports_what_is_not_in_basket(history):
    basket_id = repo.create_basket("Только молоко")
    repo.set_basket_item(basket_id, history["milk"], 1)
    missing = {r["product_id"] for r in baskets.missing_regulars(basket_id)}
    assert missing == {history["apples"]}


# ---------- справочник банков ----------
def test_reference_loads_all_banks():
    banks = bank_reference.all_banks()
    assert len(banks) == 8
    assert "Сбер" in bank_reference.bank_names()
    assert bank_reference.find("т-банк")["card"] == "Black"


def test_reference_separates_money_from_points():
    """Рубли складываются с ценой, баллы и ягодки — нет."""
    assert bank_reference.is_money(bank_reference.find("Т-Банк"))
    assert not bank_reference.is_money(bank_reference.find("ВБ Банк"))
    assert not bank_reference.is_money(bank_reference.find("Яндекс Банк"))


def test_reference_warns_about_points_and_subscription():
    wb = bank_reference.warnings(bank_reference.find("ВБ Банк"))
    assert any("не рублями" in w for w in wb)
    alfa = bank_reference.warnings(bank_reference.find("Альфа-Банк"))
    assert any("подписки" in w for w in alfa)


def test_reference_card_title_falls_back_to_program():
    assert bank_reference.card_title(bank_reference.find("ВТБ")) == "Мультибонус"
    assert bank_reference.card_title(bank_reference.find("Сбер")) == "СберСпасибо"


# ---------- живые цены в корзине ----------
class _Offer:
    """Достаточная замена StoreOffer для проверки отрисовки и сумм."""

    def __init__(self, price, in_stock=True, per_unit=None):
        self.price = price
        self.in_stock = in_stock
        self.per_unit = per_unit


# ---------- синхронизированный каталог ----------


def test_regulars_are_what_repeats_not_what_happened_once(tmp_path, monkeypatch):
    """«Обычно берёте» — про повторяющееся, а не про единственную покупку."""
    from app import baskets, config, repo
    from app.db import init_db

    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "reg.db"))
    init_db()
    store = repo.get_store("pyaterochka")
    часто = repo.upsert_product(Product(id=None, name="Молоко", unit="pcs"))
    разово = repo.upsert_product(Product(id=None, name="Торт", unit="pcs"))

    for день in ("2026-09-01", "2026-09-08", "2026-09-15"):
        repo.add_history_row(день, store.id, часто, "Молоко", 1, 80.0, 80.0)
    repo.add_history_row("2026-09-01", store.id, разово, "Торт", 1, 900.0, 900.0)

    обычные = {r["product_id"] for r in baskets.regular_purchases()}

    assert часто in обычные
    assert разово not in обычные, "разовая покупка не делает товар обычным"


def test_autofill_touches_only_an_empty_basket(tmp_path, monkeypatch):
    """Набранное руками автонабор не трогает никогда.

    Ограничитель важнее самой функции: корзина, которую человек правил, — его
    работа, и досыпать в неё «обычное» значило бы отменять его решения.
    """
    from app import config, repo
    from app.db import init_db

    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "af.db"))
    init_db()
    store = repo.get_store("pyaterochka")
    молоко = repo.upsert_product(Product(id=None, name="Молоко", unit="pcs"))
    хлеб = repo.upsert_product(Product(id=None, name="Хлеб", unit="pcs"))
    for день in ("2026-09-01", "2026-09-08"):
        repo.add_history_row(день, store.id, молоко, "Молоко", 1, 80.0, 80.0)

    bid = repo.create_basket("Своя", source="manual")
    repo.set_basket_item(bid, хлеб, 3.0)

    # в непустой корзине автонабор не должен сработать: проверяем через условие,
    # на котором он стоит, — наполняем только пустую
    assert repo.basket_items(bid), "корзина не пуста"
    позиции = {int(i["product_id"]): i["qty"] for i in repo.basket_items(bid)}
    assert позиции == {хлеб: 3.0}, "в корзине должно остаться ровно то, что положил человек"


def test_cleared_basket_is_not_the_same_as_no_history(tmp_path, monkeypatch):
    """Пустая корзина при живой истории — не «нечем наполнить», а «вы её очистили».

    Первая редакция писала «Истории покупок пока нет» человеку, у которого
    шестнадцать покупок загружено, — то есть врала.
    """
    from app import baskets, config, repo
    from app.db import init_db

    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "cl.db"))
    init_db()
    store = repo.get_store("pyaterochka")
    молоко = repo.upsert_product(Product(id=None, name="Молоко", unit="pcs"))
    repo.add_history_row("2026-09-01", store.id, молоко, "Молоко", 1, 80.0, 80.0)

    дата, позиции = baskets.last_purchase_items()

    assert позиции, "история есть, значит наполнять есть чем"
    assert дата == "2026-09-01"


def test_autofill_leaves_no_junk_baskets(tmp_path, monkeypatch):
    """Сборка заводит свою корзину — после наполнения её надо убрать.

    Без уборки список корзин копил по мусорной записи на КАЖДОЕ открытие экрана:
    в проверке их набралось четыре одноимённых за десять минут.
    """
    from app import baskets, config, repo
    from app.db import init_db

    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "junk.db"))
    init_db()
    store = repo.get_store("pyaterochka")
    молоко = repo.upsert_product(Product(id=None, name="Молоко", unit="pcs"))
    repo.add_history_row("2026-09-01", store.id, молоко, "Молоко", 1, 80.0, 80.0)

    цель = repo.create_basket("Моя", source="manual")
    было = len(repo.list_baskets())

    результат = baskets.build_from_history("history")
    источник = int(результат["basket_id"])
    for строка in repo.basket_items(источник):
        repo.set_basket_item(цель, int(строка["product_id"]), float(строка["qty"]))
    repo.delete_basket(источник)

    assert len(repo.list_baskets()) == было, "мусорная корзина осталась в списке"
    assert repo.basket_items(цель), "целевая корзина должна была наполниться"


# ---------- привычки: «Пора докупить» и «Моя обычная корзина» (концепция 2.0, экран 2.1) ----------
@pytest.fixture
def cycles():
    """Молоко раз в 5 дней, масло раз в 30, хлеб — два чека в один день, сыр однажды."""
    from datetime import date
    store = repo.get_store("pyaterochka")
    milk = repo.upsert_product(Product(None, "Молоко 1 л", unit="pcs"))
    oil = repo.upsert_product(Product(None, "Масло оливковое", unit="pcs"))
    bread = repo.upsert_product(Product(None, "Хлеб", unit="pcs"))
    cheese = repo.upsert_product(Product(None, "Сыр", unit="kg"))
    for day in ("2026-09-01", "2026-09-06", "2026-09-11", "2026-09-16"):
        repo.add_history_row(day, store.id, milk, "Молоко", 2, 80.0, 160.0)
    repo.add_history_row("2026-08-17", store.id, oil, "Масло", 1, 900.0, 900.0)
    repo.add_history_row("2026-09-16", store.id, oil, "Масло", 1, 900.0, 900.0)
    repo.add_history_row("2026-09-10", store.id, bread, "Хлеб", 1, 50.0, 50.0)
    repo.add_history_row("2026-09-10", store.id, bread, "Хлеб", 1, 50.0, 50.0)
    repo.add_history_row("2026-09-16", store.id, cheese, "Сыр", 0.35, 900.0, 315.0)
    return {"milk": milk, "oil": oil, "bread": bread, "cheese": cheese,
            "today": date(2026, 9, 22)}


def _by(found, pid):
    return next((h for h in found if h["product_id"] == pid), None)


def test_habits_cycle_is_mean_gap_between_days(cycles):
    found = baskets.habits(cycles["today"])
    milk = _by(found, cycles["milk"])
    assert milk["times"] == 4 and milk["cycle"] == 5 and milk["since"] == 6
    assert milk["qty"] == 2 and milk["due"] is True


def test_two_receipts_same_day_are_one_purchase(cycles):
    """Вернулся за хлебом в тот же день — это один заход, и цикла у хлеба ещё нет."""
    bread = _by(baskets.habits(cycles["today"]), cycles["bread"])
    assert bread["times"] == 1 and bread["cycle"] is None and bread["due"] is False
    assert bread["qty"] == 2


def test_due_items_only_overdue(cycles):
    due = baskets.due_items(cycles["today"])
    assert [h["product_id"] for h in due] == [cycles["milk"]]


def test_due_items_drop_dead_habits_and_put_the_orderable_first(cycles):
    """Купленное дважды подряд три года назад — не «пора докупить»; то, чего не знает ни одна сеть, — в конце."""
    store = repo.get_store("pyaterochka")
    old = repo.upsert_product(Product(None, "Баранка", unit="pcs"))
    for day in ("2023-03-01", "2023-03-02"):
        repo.add_history_row(day, store.id, old, "Баранка", 1, 50.0, 50.0)
    kefir = repo.upsert_product(Product(None, "Кефир", unit="pcs"))
    for day in ("2026-09-08", "2026-09-15"):
        repo.add_history_row(day, store.id, kefir, "Кефир", 1, 90.0, 90.0)
    repo.confirm_mapping(kefir, repo.upsert_store_product(store.id, "3345678", "Кефир 1%"))

    assert [h["product_id"] for h in baskets.due_items(cycles["today"])] == [kefir, cycles["milk"]]


def test_usual_takes_what_runs_out_this_week(cycles):
    """Молоко кончилось, масло куплено шесть дней назад на месяц — его не берём."""
    usual = {h["product_id"] for h in baskets.usual_items(cycles["today"])}
    assert usual == {cycles["milk"]}
    later = {h["product_id"] for h in baskets.usual_items(cycles["today"], horizon_days=30)}
    assert cycles["oil"] in later


def test_build_usual_basket(cycles, monkeypatch):
    from datetime import date as real_date

    class Today(real_date):
        @classmethod
        def today(cls):
            return cycles["today"]

    monkeypatch.setattr(baskets, "date", Today)
    made = baskets.build_from_history(baskets.USUAL)
    assert made["basket_id"]
    got = {int(i["product_id"]): float(i["qty"]) for i in repo.basket_items(made["basket_id"])}
    assert got == {cycles["milk"]: 2.0}
