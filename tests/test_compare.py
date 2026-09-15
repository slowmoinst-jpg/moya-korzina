"""Сравнение товара по доставкам.

Главное, что здесь проверяется, — приведённая цена. Без неё сравнение доставок
превращается в сравнение фасовок: 930 мл за 149 ₽ выглядят дешевле литра за 155 ₽,
хотя на литр выходит дороже.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import compare, config, repo  # noqa: E402
from app.db import init_db  # noqa: E402
from app.models import Candidate, PriceSnapshot, Product  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "c.db"))
    init_db()


class FakeConnector:
    """Магазин, который отвечает тем, что ему велели."""

    def __init__(self, code, candidates=None, prices=None, boom=False):
        self.code = code
        self._candidates = candidates or []
        self._prices = prices or {}
        self._boom = boom

    def search(self, query, limit=3):
        if self._boom:
            raise RuntimeError("магазин прилёг")
        return self._candidates[:limit]

    def get_prices(self, skus):
        return [self._prices[sku] for sku in skus if sku in self._prices]


def _use(monkeypatch, by_code: dict, seen: dict | None = None):
    def get_connector(code, location=None):
        if code not in by_code:
            raise ValueError(f"нет коннектора {code}")
        if seen is not None:
            seen[code] = location
        return by_code[code]

    monkeypatch.setattr("app.connectors.get_connector", get_connector)


# ---------- приведённая цена ----------
def test_bigger_pack_can_be_more_expensive_per_litre():
    """Ровно тот случай, ради которого всё затевалось."""
    small, _ = compare.per_unit_price(149.0, "pcs", None, "Молоко 930 мл")
    big, _ = compare.per_unit_price(155.0, "pcs", None, "Молоко 1 л")

    assert small > big, "930 мл за 149 ₽ дороже литра за 155 ₽ в пересчёте"
    assert round(small, 2) == 160.22


def test_weight_goods_are_already_per_kilo():
    price, label = compare.per_unit_price(215.0, "kg", None, "Огурцы весовые")
    assert price == 215.0 and label == compare.PER_KG


def test_without_weight_we_do_not_invent_one():
    """Выдумать граммовку — значит соврать в сравнении. Честнее сказать «за штуку»."""
    price, label = compare.per_unit_price(99.0, "pcs", None, "Батон нарезной")
    assert price == 99.0 and label == compare.PER_PCS


def test_explicit_weight_beats_the_name():
    price, _ = compare.per_unit_price(100.0, "pcs", 500.0, "Что-то 999 г")
    assert price == 200.0


# ---------- опрос магазинов ----------
def test_every_store_is_asked_and_prices_are_normalised(db, monkeypatch):
    _use(monkeypatch, {
        "magnit": FakeConnector("magnit", [Candidate("magnit", "1", "Молоко 930 мл", price=149.0)]),
        "lenta": FakeConnector("lenta", [Candidate("lenta", "2", "Молоко 1 л", price=155.0)]),
    })
    offers = compare.compare_query("молоко", store_codes=["magnit", "lenta"])

    by_store = {o.store_code: o for o in offers}
    assert by_store["magnit"].per_unit > by_store["lenta"].per_unit
    assert compare.cheapest(offers).store_code == "lenta"


def test_price_is_asked_from_the_card_when_search_has_none(db, monkeypatch):
    """Лента и Дикси цен в поиске не отдают — за ними надо идти в карточку."""
    candidate = Candidate("lenta", "80424", "Молоко пастеризованное 930 мл")
    snapshot = PriceSnapshot(store_code="lenta", sku="80424", price=90.99, in_stock=True)
    _use(monkeypatch, {"lenta": FakeConnector("lenta", [candidate], {"80424": snapshot})})

    offer = compare.compare_query("молоко", store_codes=["lenta"])[0]
    assert offer.price == 90.99 and offer.found


def test_out_of_stock_never_wins(db, monkeypatch):
    """Дешёвое, но отсутствующее — не ответ: туда человека посылать незачем."""
    cheap = Candidate("lenta", "1", "Молоко 1 л")
    pricey = Candidate("magnit", "2", "Молоко 1 л", price=200.0)
    _use(monkeypatch, {
        "lenta": FakeConnector("lenta", [cheap],
                               {"1": PriceSnapshot("lenta", "1", 50.0, in_stock=False)}),
        "magnit": FakeConnector("magnit", [pricey]),
    })
    offers = compare.compare_query("молоко", store_codes=["lenta", "magnit"])

    assert compare.cheapest(offers).store_code == "magnit"


def test_irrelevant_candidate_is_not_shown(db, monkeypatch):
    """На «молоко» прилетало детское пюре. Пустая клетка честнее подмены."""
    _use(monkeypatch, {"magnit": FakeConnector(
        "magnit", [Candidate("magnit", "9", "Пюре детское банан-яблоко 210 г", price=44.9)])})
    offer = compare.compare_query("молоко 2,5%", store_codes=["magnit"])[0]

    assert not offer.found and offer.note == "ничего похожего"


def test_a_broken_store_does_not_break_the_comparison(db, monkeypatch):
    _use(monkeypatch, {
        "magnit": FakeConnector("magnit", boom=True),
        "lenta": FakeConnector("lenta", [Candidate("lenta", "2", "Молоко 1 л", price=155.0)]),
    })
    offers = compare.compare_query("молоко", store_codes=["magnit", "lenta"])

    assert {o.store_code for o in offers} == {"magnit", "lenta"}
    assert next(o for o in offers if o.store_code == "magnit").note == "магазин не ответил"
    assert compare.cheapest(offers).store_code == "lenta"


def test_empty_store_says_so(db, monkeypatch):
    _use(monkeypatch, {"samokat": FakeConnector("samokat", [])})
    offer = compare.compare_query("молоко", store_codes=["samokat"])[0]

    assert not offer.found and offer.note == "ничего не нашлось"


# ---------- разброс ----------
def test_spread_tells_whether_it_is_worth_looking_around():
    offers = [
        compare.StoreOffer("a", "А", sku="1", price=100.0, per_unit=100.0),
        compare.StoreOffer("b", "Б", sku="2", price=140.0, per_unit=140.0),
    ]
    gap = compare.spread(offers)

    assert gap["diff"] == 40.0 and gap["pct"] == 40.0


def test_spread_needs_at_least_two_offers():
    one = [compare.StoreOffer("a", "А", sku="1", price=100.0, per_unit=100.0)]
    assert compare.spread(one)["diff"] is None


# ---------- по подтверждённым сопоставлениям ----------
def test_compare_product_uses_saved_mappings(db):
    product_id = repo.upsert_product(Product(None, "Молоко 1 л", unit="pcs", weight_g=1000))
    store = repo.get_store("magnit")
    sp = repo.upsert_store_product(store.id, "1", "Молоко Простоквашино 1 л", weight_g=1000)
    repo.confirm_mapping(product_id, sp)
    repo.save_price(sp, 99.0)

    offers = compare.compare_product(product_id)
    magnit = next(o for o in offers if o.store_code == "magnit")

    assert magnit.price == 99.0 and magnit.per_unit == 99.0
    assert any(o.note == "товар не связан с магазином" for o in offers)


# ---------- место клиента ----------
def test_client_address_reaches_every_store(db, monkeypatch):
    """Адрес клиента обязан доехать до коннектора, иначе он ничего не решает."""
    from app.models import Location

    seen: dict = {}
    milk = Candidate(store_code="lenta", sku="1", name="Молоко 1 л", price=90.0, score=1.0)
    _use(monkeypatch, {"lenta": FakeConnector("lenta", [milk])}, seen)
    here = Location(address="Москва, Ходынский бульвар 4")

    compare.compare_query("молоко", store_codes=["lenta"], location=here)

    assert seen["lenta"] is here, "коннектор получил не тот адрес, что указал клиент"


def test_two_addresses_do_not_share_a_cache_key():
    """Цена одной точки не должна достаться клиенту другой.

    Проверяется на ключе кэша, а не на ценах: именно ключ решает, разойдутся
    два клиента или молча получат один ответ. Чужая цена выглядит как своя,
    поэтому такую ошибку не видно глазами — только ключом.
    """
    from app.models import Location

    moscow = Location(store_id="62", address="Москва, Ходынский бульвар 4")
    ekb = Location(store_id="229", address="Екатеринбург, Щербакова 4")

    assert moscow.key != ekb.key
    assert Location(store_id="1", delivery=True).key != Location(store_id="1", delivery=False).key


def test_location_without_address_is_falsy():
    """Пустое место должно читаться как «спрашивать нечего», а не как адрес."""
    from app.models import Location

    assert not Location()
    assert Location(address="Москва")
    assert Location(store_id="62")
