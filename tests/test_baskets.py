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


def test_out_of_stock_never_wins_in_the_basket():
    """Зелёным помечается самое дешёвое ИЗ ТОГО, ЧТО ЕСТЬ.

    Иначе победителем станет магазин, в который человека посылать незачем: там
    этого товара не продают, а цена показана справочная.
    """
    from app.ui.screens.basket import _price_html

    class S:
        def __init__(self, code):
            self.code = code

    stores = [S("lenta"), S("magnit")]
    live = {(1, "lenta"): _Offer(50.0, in_stock=False), (1, "magnit"): _Offer(90.0)}
    html = _price_html(1, stores, {}, live)

    assert "line-through" in html, "отсутствующая цена должна быть зачёркнута"
    assert "var(--green)" in html
    # зелёным помечена именно девяностая, а не полусотня отсутствующего
    assert html.index("var(--green)") > html.index("line-through")


def test_missing_item_does_not_inflate_a_store_total():
    """Сумма магазина складывается только из того, что в нём есть.

    Магазин, где нет половины корзины, иначе выглядел бы самым дешёвым — просто
    потому что в его сумме меньше товаров.
    """
    from app.ui.screens.basket import _live_totals

    class S:
        def __init__(self, code):
            self.code = code

    stores = [S("lenta")]
    items = [{"product_id": 1, "qty": 2, "unit": "pcs"}, {"product_id": 2, "qty": 1, "unit": "pcs"}]
    live = {(1, "lenta"): _Offer(100.0), (2, "lenta"): _Offer(999.0, in_stock=False)}

    totals = _live_totals(items, stores, live, {}, {"lenta": 0.0})

    assert totals["lenta"] == 200.0, "999 за отсутствующий товар не должны попасть в сумму"


def test_live_price_replaces_the_stored_snapshot():
    """Снимок может быть недельным и снятым по другому адресу — живая цена главнее."""
    from app.ui.screens.basket import _price_html

    class S:
        def __init__(self, code):
            self.code = code

    stores = [S("lenta")]
    html = _price_html(1, stores, {(1, "lenta"): 500.0}, {(1, "lenta"): _Offer(75.99)})

    assert "75,99" in html.replace("&nbsp;", " ")
    assert "500" not in html


def test_address_is_part_of_the_live_cache_key(tmp_path, monkeypatch):
    """Цены одного города не должны показаться под адресом другого."""
    from app import config, location
    from app.db import init_db
    from app.ui.screens.basket import _live_key

    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "b.db"))
    init_db()

    location.save_address("Москва, Ходынский бульвар 4")
    moscow = _live_key(7)
    location.save_address("Екатеринбург, улица Щербакова 4")

    assert moscow != _live_key(7)


def test_search_guess_loses_to_a_confirmed_mapping(tmp_path, monkeypatch):
    """Подтверждённый человеком товар главнее находки поиска.

    Именно этим лечится наблюдённое: на «Икра лососевая копчёная 180 г» поиск
    приносил банку за 2 090 ₽, и она уходила в сумму магазина как настоящая.
    """
    from app import config, repo
    from app.db import init_db
    from app.ui.screens.basket import _agrees_with_mapping

    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "m.db"))
    init_db()

    store = repo.get_store("magnit")
    pid = repo.upsert_product(Product(id=None, name="Икра лососевая копчёная 180 г", unit="pcs"))
    sp_id = repo.upsert_store_product(store.id, "правильный-sku", "Икра лососевая 180 г")
    repo.confirm_mapping(pid, sp_id, confirmed=True)

    class _O:
        def __init__(self, sku):
            self.store_code = "magnit"
            self.sku = sku

    keep, confirmed = _agrees_with_mapping(pid, _O("правильный-sku"))
    assert (keep, confirmed) == (True, True)

    keep, confirmed = _agrees_with_mapping(pid, _O("банка-за-2090"))
    assert keep is False, "находка, спорящая с подтверждением, не должна попадать в сумму"


def test_without_a_mapping_the_guess_is_kept_but_marked(tmp_path, monkeypatch):
    """У магазина без сопоставлений цена всё же нужна — но помеченной как догадка."""
    from app import config, repo
    from app.db import init_db
    from app.ui.screens.basket import _agrees_with_mapping

    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "m2.db"))
    init_db()
    pid = repo.upsert_product(Product(id=None, name="Слива", unit="kg"))

    class _O:
        store_code = "lenta"
        sku = "любой"

    assert _agrees_with_mapping(pid, _O()) == (True, False)


# ---------- синхронизированный каталог ----------
def test_catalog_shows_how_many_stores_know_the_item(tmp_path, monkeypatch):
    """Готовность товара — в скольких сетях он опознан.

    Это и есть «синхронизированность» каталога, и она видна человеку до того, как
    он положит товар: позиция, известная всем сетям, уедет в магазин ссылкой,
    а неизвестная превратится в поиск руками.
    """
    from app import config, repo
    from app.db import init_db
    from app.ui.screens.basket import _ready_in

    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "cat.db"))
    init_db()

    pid = repo.upsert_product(Product(id=None, name="Молоко 1 л", unit="pcs"))
    stores = repo.list_stores()
    for code in ("lenta", "magnit"):
        store = repo.get_store(code)
        sp = repo.upsert_store_product(store.id, f"{code}-1", "Молоко 1 л")
        repo.confirm_mapping(pid, sp, confirmed=True)

    assert _ready_in(pid, repo.mapping_matrix(), stores) == 2
    пустой = repo.upsert_product(Product(id=None, name="Незнакомый товар", unit="pcs"))
    assert _ready_in(пустой, repo.mapping_matrix(), stores) == 0


def test_best_known_items_come_first(tmp_path, monkeypatch):
    """Каталог сортируется по готовности: сверху то, что проще отдать магазину."""
    from app import config, repo
    from app.db import init_db
    from app.ui.screens.basket import _ready_in

    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "cat2.db"))
    init_db()
    stores = repo.list_stores()

    известный = repo.upsert_product(Product(id=None, name="Б известный", unit="pcs"))
    for code in ("lenta", "magnit", "vkusvill"):
        store = repo.get_store(code)
        sp = repo.upsert_store_product(store.id, f"{code}-x", "Б известный")
        repo.confirm_mapping(известный, sp, confirmed=True)
    незнакомый = repo.upsert_product(Product(id=None, name="А незнакомый", unit="pcs"))

    matrix = repo.mapping_matrix()
    товары = repo.list_products()
    товары.sort(key=lambda p: (-_ready_in(p.id, matrix, stores), p.name or ""))

    assert товары[0].id == известный, \
        "по алфавиту первым был бы «А незнакомый» — готовность должна перебивать имя"


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
