"""Цена места из итога сети (app/netprice.py): когда итог сети идёт в цену, а когда нет.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ. Правило без экрана: итог берётся только у ОДНОЙ сети, прочитанный
самой сетью, на всю корзину, без лишнего в её корзине и не старый. Всё остальное —
наш расчёт, как был, а причина названа. Экран и «Заказать» — в
tests/test_phone_screens.py.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import cartrun, freshness, netprice  # noqa: E402
from app.carttotal import NETWORK, OURS, Total  # noqa: E402
from app.models import StoreBreakdown, Variant, VariantLine  # noqa: E402

NOW = datetime(2026, 10, 22, 12, 0, 0)
FRESH = "2026-10-22T11:30:00"


def place(code: str = "magnit", amount: float = 100.0, *, lines: int = 2, missing: int = 0,
          cashback: float = 0.0) -> Variant:
    """Место «вся корзина в одной сети»: наш расчёт, собранный руками."""
    rows = [VariantLine(store_code=code, store_name=code.title(), card_id=None, card_name=None,
                        product_id=n, product_name=f"Товар {n}", qty=1.0,
                        price=round(amount / lines, 2)) for n in range(lines)]
    store = StoreBreakdown(store_code=code, store_name=code.title(), subtotal=amount,
                           delivery=0.0, discount=cashback, total=amount - cashback, lines=rows)
    return Variant(stores=[store], total=amount - cashback, baseline=amount + 50.0,
                   missing_products=[f"Нет {n}" for n in range(missing)])


def row(code: str = "magnit", amount: float = 130.0, *, landed: int = 2, at: str = FRESH,
        source: str = NETWORK, **fields) -> cartrun.Row:
    """Строка сборки: что легло и какой итог написала сеть."""
    return cartrun.Row(code=code, state=cartrun.DONE,
                       landed=[f"Товар {n}" for n in range(landed)],
                       total=Total(sum=amount, source=source, at=at), **fields)


def priced(variants, *rows, positions: int = 2, baseline: float = 200.0):
    return netprice.apply(variants, {r.code: r for r in rows}, positions, baseline, NOW)


def test_a_complete_network_cart_sets_the_price_of_the_place():
    original = place("magnit", 100.0)

    [got] = priced([original], row("magnit", 130.0))

    assert got.net is not None and got.why == ""
    assert got.variant.total == 130.0 and got.variant.stores[0].total == 130.0
    assert original.total == 100.0 and original.stores[0].total == 100.0   # кэш расчёта цел


def test_the_bank_cashback_stays_on_top_of_the_network_total():
    """Кэшбэк карты сеть не знает — вычитаем свой прежний, а не теряем его."""
    [got] = priced([place("magnit", 100.0, cashback=10.0)], row("magnit", 130.0))

    assert got.variant.total == 120.0 and got.cashback == 10.0


def test_a_complete_network_cart_closes_the_gaps_of_our_own_calculation():
    """У нас цены части товаров нет, а сеть положила всё и назвала итог — место полное."""
    [got] = priced([place("pyaterochka", 60.0, lines=1, missing=1)], row("pyaterochka", 140.0),
                   baseline=200.0)

    assert got.variant.missing_products == []
    assert got.variant.baseline == 200.0            # база — всей корзины, а не урезанная


def test_a_partly_landed_cart_is_not_the_price():
    original = place()

    [got] = priced([original], row(landed=1))

    assert got.net is None and got.variant is original
    assert got.why == "легло 1 из 2"


def test_a_cart_with_items_that_did_not_land_is_not_the_price():
    gone = [{"name": "Сыр", "why": "нет в наличии", "gone": True}]

    [got] = priced([place()], row(landed=2, missing=gone))

    assert got.net is None and got.why == "часть позиций не легла"


def test_leftovers_in_the_network_cart_make_its_total_foreign():
    """В корзине сети лежит прежнее — итог сети включает и его, это не цена нашей корзины."""
    [got] = priced([place()], row(extra=[{"name": "Кефир 1%", "more": 1}]))

    assert got.net is None and "лишнее" in got.why


def test_a_stale_total_is_not_the_price():
    old = (NOW - timedelta(hours=freshness.max_age_hours() + 1)).isoformat(timespec="seconds")

    [got] = priced([place()], row(at=old))

    assert got.net is None and got.why == "устарел"


def test_a_total_without_its_own_time_takes_the_time_of_the_run():
    [got] = priced([place()], row(at="", finished_at=FRESH))

    assert got.net is not None and got.net.at == FRESH


def test_a_total_we_calculated_ourselves_is_not_a_network_price():
    [got] = priced([place()], row(source=OURS))

    assert got.net is None and got.why == ""         # сказать нечего: сеть итога не писала


def test_a_zero_total_is_not_a_price():
    [got] = priced([place()], row(amount=0.0))

    assert got.net is None and got.why == ""


def test_a_store_without_a_run_keeps_our_price():
    original = place("lenta", 100.0)

    [got] = priced([original], row("magnit", 130.0))

    assert got.net is None and got.variant is original and got.why == ""


def test_a_split_is_never_priced_by_the_network():
    """В корзине каждой сети лежит вся корзина, а доля разбивки — нет: итога доли сеть не писала."""
    one, two = place("magnit", 50.0), place("pyaterochka", 50.0)
    split = Variant(stores=[one.stores[0], two.stores[0]], total=100.0, baseline=150.0)

    [got] = priced([split], row("magnit", 10.0), row("pyaterochka", 10.0))

    assert got.net is None and got.variant is split


def test_places_keep_their_number_and_order():
    """Номер места — индекс в списке расчёта: по нему экран и «Заказать» понимают друг друга."""
    first, second = place("magnit", 100.0), place("pyaterochka", 110.0)

    got = priced([first, second], row("pyaterochka", 150.0))

    assert [p.variant.stores[0].store_code for p in got] == ["magnit", "pyaterochka"]
    assert got[0].variant is first and got[1].variant.total == 150.0
