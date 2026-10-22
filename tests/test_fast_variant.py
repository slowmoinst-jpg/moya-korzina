"""«Быстрее»: один заказ в одной сети, где есть вся корзина (task-8, зона Z4).

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Правило из app/faster.py, без браузера и без баз: «быстрее» —
это вариант ровно из одной сети, где нет ни одной недостающей позиции, и из таких
самый дешёвый. Рядом — доплата против максимальной экономии (самого дешёвого места
на экране, даже если оно из двух сетей). И вторая, честная половина правила:
самовывоз называется только тогда, когда сеть сама знает свой магазин у адреса.

ЧЕГО ЭТИ ТЕСТЫ НЕ ЗНАЮТ. Что человек увидит на телефоне и попадёт ли пальцем:
это смотрят глазами, а что блок вообще нарисовался — проверяет
tests/test_phone_screens.py. Срок доставки здесь не проверяется вовсе: его в
данных нет, и выдумывать его приложение не должно.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import faster, location  # noqa: E402
from app.models import StoreBreakdown, Variant, VariantLine  # noqa: E402

POSITIONS = 2


def one(code: str, name: str, amount: float, *, lines: int = 2,
        missing: int = 0) -> Variant:
    """Вариант «вся корзина в одной сети»: сколько строк, сколько позиций не нашлось."""
    rows = [VariantLine(store_code=code, store_name=name, card_id=None, card_name=None,
                        product_id=n, product_name=f"Товар {n}", qty=1.0,
                        price=round(amount / max(1, lines), 2))
            for n in range(lines)]
    return Variant(
        stores=[StoreBreakdown(store_code=code, store_name=name, subtotal=amount,
                               delivery=0.0, discount=0.0, total=amount, lines=rows)],
        total=amount, baseline=amount + 100.0,
        missing_products=[f"Нет {n}" for n in range(missing)])


def two(first: str, second: str, amount: float) -> Variant:
    """Вариант из двух сетей: по строке в каждой, обе позиции на месте."""
    def part(code: str, name: str, n: int, price: float) -> StoreBreakdown:
        return StoreBreakdown(
            store_code=code, store_name=name, subtotal=price, delivery=0.0,
            discount=0.0, total=price,
            lines=[VariantLine(store_code=code, store_name=name, card_id=None, card_name=None,
                               product_id=n, product_name=f"Товар {n}", qty=1.0, price=price)])

    half = round(amount / 2, 2)
    return Variant(stores=[part(first, first.title(), 0, half),
                           part(second, second.title(), 1, amount - half)],
                   total=amount, baseline=amount + 100.0)


# ---------- кто быстрее ----------
def test_the_cheapest_full_single_store_is_the_fast_one():
    """Полный односетевой выбирается, и это самый дешёвый из полных."""
    full_pricey = one("vkusvill", "ВкусВилл", 130.0)
    full_cheap = one("magnit", "Магнит", 110.0)
    found = faster.fastest([full_pricey, full_cheap, one("pyaterochka", "Пятёрочка", 125.0)],
                           POSITIONS)
    assert found["variant"] is full_cheap
    assert found["why"] == faster.WHOLE
    assert found["extra"] == 0.0             # дешевле всех — доплаты нет


def test_a_two_store_cheaper_variant_costs_extra_for_one_order():
    """Разбивка на две сети дешевле — и это ровно та доплата, которую человек платит
    за один заказ вместо двух."""
    split = two("magnit", "pyaterochka", 100.0)
    full = one("magnit", "Магнит", 110.0)
    found = faster.fastest([split, full], POSITIONS)
    assert found["variant"] is full
    assert found["extra"] == pytest.approx(10.0)
    assert found["extra"] > 0


def test_a_partial_store_is_not_the_measure_of_economy():
    """Неполный вариант дешевле потому, что в нём не вся корзина: максимум экономии
    считается по полным, иначе доплата мерилась бы против меньшей суммы за меньшее."""
    partial = one("magnit", "Магнит", 60.0, lines=1, missing=1)
    split = two("magnit", "pyaterochka", 100.0)
    full = one("vkusvill", "ВкусВилл", 110.0)
    found = faster.fastest([partial, split, full], POSITIONS)
    assert found["variant"] is full
    assert found["extra"] == pytest.approx(10.0)     # против полной разбивки, а не против 60


def test_a_store_with_a_missing_position_is_not_fast():
    """«Всё есть» — только когда в сети правда всё: полного односетевого нет — нет и блока."""
    found = faster.fastest([one("magnit", "Магнит", 90.0, lines=1, missing=1),
                            two("magnit", "pyaterochka", 120.0)], POSITIONS)
    assert found["variant"] is None
    assert found["why"] == faster.NO_WHOLE
    assert found["extra"] == 0.0


def test_two_stores_are_never_fast_even_when_everything_is_there():
    """Полная разбивка — тоже «всё есть», но это два заказа, а не один."""
    found = faster.fastest([two("magnit", "vkusvill", 100.0)], POSITIONS)
    assert found["variant"] is None
    assert found["why"] == faster.NO_WHOLE


def test_a_variant_that_lost_a_position_is_not_fast():
    """Позиция пропала молча (нет ни строки, ни missing) — «всё есть» сказать нельзя."""
    found = faster.fastest([one("magnit", "Магнит", 110.0, lines=1)], POSITIONS)
    assert found["variant"] is None


def test_no_variants_no_fast():
    assert faster.fastest([], POSITIONS)["variant"] is None
    assert faster.fastest(None, POSITIONS)["why"] == faster.NO_WHOLE


# ---------- самовывоз ----------
@pytest.fixture
def no_shops(monkeypatch):
    """По умолчанию ни одна сеть магазина у адреса не знает: адреса в тесте нет."""
    monkeypatch.setattr(location, "nearby", lambda addr=None: [])
    monkeypatch.setattr(location, "nearby_magnit", lambda addr=None: [])
    return monkeypatch


def test_pickup_is_named_only_when_the_shop_is_known(no_shops):
    assert faster.pickup_line("lenta") is None      # магазинов рядом не нашлось

    no_shops.setattr(location, "nearby", lambda addr=None: [{"distance": 394.0}])
    assert faster.pickup_line("lenta") == "самовывоз: Лента, 394 м"


def test_pickup_names_the_nearest_shop_of_that_network(no_shops):
    no_shops.setattr(location, "nearby_magnit",
                     lambda addr=None: [{"distance": 9110.0}, {"distance": 620.0}])
    assert faster.pickup_line("magnit") == "самовывоз: Магнит, 620 м"


def test_pickup_is_quiet_about_networks_location_does_not_know(no_shops):
    """Пятёрочке и METRO самовывоз не выдумываем: магазин у адреса нам никто не называет."""
    no_shops.setattr(location, "nearby", lambda addr=None: [{"distance": 394.0}])
    assert faster.pickup_line("pyaterochka") is None
    assert faster.pickup_line("metro") is None


def test_pickup_without_a_distance_is_not_invented(no_shops):
    """Магазин есть, а расстояния сеть не дала — «самовывоз» без цифры не пишем."""
    no_shops.setattr(location, "nearby", lambda addr=None: [{"name": "Лента"}])
    assert faster.pickup_line("lenta") is None
