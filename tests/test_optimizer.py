"""Тесты ядра расчёта — критерий приёмки №5 (проверка вручную по формуле раздела 6).

Формула раздела 6:
    delivery_s = 0, если subtotal_s >= free_delivery_from, иначе delivery_fee
    discount_s = 0, если subtotal_s < min_check_rub
               = min(subtotal_s * percent / 100, cap_rub - cap_used) иначе
    total_s    = subtotal_s + delivery_s - discount_s
    total_v    = сумма(total_s) + penalty * (число магазинов в варианте - 1)
"""
from __future__ import annotations

import time

import pytest

from app.models import BasketLine, Offer, Store
from app.optimizer import optimize, store_total
from app.optimizer.calc import best_offer


# ---------- фабрики тестовых данных ----------

def mk_store(code: str, sid: int, name: str | None = None, fee: float = 0.0,
             free_from: float = 0.0, min_order: float = 0.0) -> Store:
    return Store(id=sid, code=code, name=name or code.upper(), delivery_fee=fee,
                 free_delivery_from=free_from, min_order=min_order)


def mk_offer(store_id: int, percent: float, cap: float, min_check: float = 0.0,
             used: float = 0.0, oid: int = 1, card_id: int = 1) -> Offer:
    return Offer(id=oid, card_id=card_id, store_id=store_id, percent=percent, cap_rub=cap,
                 min_check_rub=min_check, cap_used=used)


def mk_line(pid: int, name: str, prices: dict[str, float], qty: float = 1.0,
            in_stock: dict[str, bool] | None = None) -> BasketLine:
    return BasketLine(product_id=pid, name=name, unit="pcs", qty=qty, prices=dict(prices),
                      in_stock=in_stock if in_stock is not None else {c: True for c in prices})


# ---------- store_total: формула раздела 6 ----------

def test_store_total_below_min_check_no_discount():
    # чек 900 < min_check 1000 -> скидки нет; 900 < free_delivery_from 2000 -> доставка 199
    # total = 900 + 199 - 0 = 1099
    s = mk_store("m", 1, fee=199.0, free_from=2000.0)
    o = mk_offer(1, percent=10, cap=500, min_check=1000)
    assert store_total(900.0, s, o) == (199.0, 0.0, 1099.0)


def test_store_total_percent_with_paid_delivery():
    # 1500 >= 1000 -> скидка min(1500*10/100, 500) = 150; 1500 < 2000 -> доставка 199
    # total = 1500 + 199 - 150 = 1549
    s = mk_store("m", 1, fee=199.0, free_from=2000.0)
    o = mk_offer(1, percent=10, cap=500, min_check=1000)
    assert store_total(1500.0, s, o) == (199.0, 150.0, 1549.0)


def test_store_total_free_delivery_threshold():
    # 2500 >= 2000 -> доставка 0; скидка min(250, 500) = 250; total = 2500 + 0 - 250 = 2250
    s = mk_store("m", 1, fee=199.0, free_from=2000.0)
    o = mk_offer(1, percent=10, cap=500, min_check=1000)
    assert store_total(2500.0, s, o) == (0.0, 250.0, 2250.0)


def test_store_total_discount_capped():
    # 6000*10/100 = 600, но cap_rub = 500 -> скидка 500; total = 6000 + 0 - 500 = 5500
    s = mk_store("m", 1, fee=199.0, free_from=2000.0)
    o = mk_offer(1, percent=10, cap=500, min_check=1000)
    assert store_total(6000.0, s, o) == (0.0, 500.0, 5500.0)


def test_store_total_cap_already_used():
    # лимит почти исчерпан: cap_rub 500, cap_used 470 -> cap_left = 30
    # скидка min(2500*10/100=250, 30) = 30; total = 2500 + 0 - 30 = 2470
    s = mk_store("m", 1, fee=199.0, free_from=2000.0)
    o = mk_offer(1, percent=10, cap=500, min_check=1000, used=470)
    assert store_total(2500.0, s, o) == (0.0, 30.0, 2470.0)


def test_store_total_cap_fully_used():
    # cap_used = cap_rub -> cap_left = 0, скидки нет вовсе
    s = mk_store("m", 1, fee=199.0, free_from=2000.0)
    o = mk_offer(1, percent=10, cap=500, min_check=0, used=500)
    assert store_total(2500.0, s, o) == (0.0, 0.0, 2500.0)


def test_store_total_without_offer():
    s = mk_store("m", 1, fee=199.0, free_from=2000.0)
    assert store_total(1000.0, s, None) == (199.0, 0.0, 1199.0)


# ---------- best_offer ----------

def test_best_offer_picks_max_discount():
    s = mk_store("m", 1)
    small = mk_offer(1, percent=5, cap=1000, min_check=0, oid=1, card_id=11)
    big = mk_offer(1, percent=10, cap=1000, min_check=2000, oid=2, card_id=22)
    # чек 1500: big не добрал мин. чек -> берём small, 1500*5% = 75
    assert best_offer(1500.0, s, [small, big]) == (small, 75.0)
    # чек 2500: small = 125, big = 250 -> побеждает big
    assert best_offer(2500.0, s, [small, big]) == (big, 250.0)


def test_best_offer_accounts_for_cap():
    s = mk_store("m", 1)
    small = mk_offer(1, percent=5, cap=1000, min_check=0, oid=1, card_id=11)
    capped = mk_offer(1, percent=10, cap=100, min_check=0, oid=2, card_id=22)
    # чек 2500: small = 125, capped = min(250, 100) = 100 -> побеждает small
    assert best_offer(2500.0, s, [small, capped]) == (small, 125.0)


def test_best_offer_none_when_nothing_applies():
    s = mk_store("m", 1)
    o = mk_offer(1, percent=10, cap=500, min_check=5000)
    assert best_offer(1000.0, s, [o]) == (None, 0.0)
    assert best_offer(1000.0, s, []) == (None, 0.0)


def test_best_offer_skips_inactive_offer():
    s = mk_store("m", 1)
    o = mk_offer(1, percent=10, cap=500)
    o.requires_activation, o.activated = True, False
    assert best_offer(1000.0, s, [o]) == (None, 0.0)


# ---------- оптимизатор: разбиение на два магазина ----------

def _two_store_basket():
    """Три одинаковые позиции, в «b» каждая на 50 ₽ дороже. У обоих магазинов 10 %, лимит 100 ₽."""
    a = mk_store("a", 1, "Магнит")
    b = mk_store("b", 2, "ВкусВилл")
    lines = [
        mk_line(1, "Позиция 1", {"a": 1000.0, "b": 1050.0}),
        mk_line(2, "Позиция 2", {"a": 1000.0, "b": 1050.0}),
        mk_line(3, "Позиция 3", {"a": 1000.0, "b": 1050.0}),
    ]
    offers = {1: [mk_offer(1, percent=10, cap=100, oid=1, card_id=11)],
              2: [mk_offer(2, percent=10, cap=100, oid=2, card_id=22)]}
    return lines, [a, b], offers


def test_split_between_two_stores_beats_single_store():
    """РУЧНОЙ РАСЧЁТ (лимит кэшбэка 100 ₽ на магазин, доставки нет, penalty = 0):

    всё в «a»:      subtotal 3000, скидка min(3000*10/100=300, 100) = 100 -> total 2900
    всё в «b»:      subtotal 3150, скидка min(315, 100) = 100            -> total 3050
    2 в «a», 1 в «b»: a: 2000 - min(200,100)=100 -> 1900
                      b: 1050 - min(105,100)=100 ->  950
                      итого 1900 + 950 + 0*(2-1) = 2850   <-- минимум
    1 в «a», 2 в «b»: a: 1000 - 100 = 900; b: 2100 - 100 = 2000 -> 2900
    Лимит в 100 ₽ берётся дважды — вот почему разбиение выгоднее.
    """
    lines, stores, offers = _two_store_basket()
    variants = optimize(lines, stores, offers, baseline=3000.0, penalty=0.0, top_n=3, max_stores=2)

    best = variants[0]
    assert best.total == 2850.0
    assert len(best.stores) == 2
    assert best.penalty == 0.0
    assert best.missing_products == []

    by_code = {b.store_code: b for b in best.stores}
    assert (by_code["a"].subtotal, by_code["a"].discount, by_code["a"].total) == (2000.0, 100.0, 1900.0)
    assert (by_code["b"].subtotal, by_code["b"].discount, by_code["b"].total) == (1050.0, 100.0, 950.0)
    assert by_code["a"].card_id == 11 and by_code["b"].card_id == 22
    # экономия к baseline 3000: 3000 - 2850 = 150 ₽ = 5 %
    assert best.savings_rub == 150.0
    assert best.savings_pct == 5.0


def test_penalty_for_second_order_kills_the_split():
    """Та же корзина, но штраф за второй заказ 100 ₽:
    разбиение 2850 + 100*(2-1) = 2950, один магазин «a» = 2900 -> побеждает один магазин.
    """
    lines, stores, offers = _two_store_basket()
    variants = optimize(lines, stores, offers, baseline=3000.0, penalty=100.0, top_n=3, max_stores=2)

    best = variants[0]
    assert best.total == 2900.0
    assert len(best.stores) == 1
    assert best.stores[0].store_code == "a"
    assert best.penalty == 0.0
    # разбиение осталось в выдаче, но уже со штрафом и хуже по итогу
    split = next(v for v in variants if len(v.stores) == 2)
    assert (split.penalty, split.total) == (100.0, 2950.0)


def test_more_expensive_position_to_reach_min_check():
    """Следствие 1 раздела 6: выгодно взять позицию дороже, лишь бы добить мин. чек.

    РУЧНОЙ РАСЧЁТ (penalty = 0, доставки нет; у «a» карты нет, у «b» 20 %, лимит 1000, мин. чек 2000):
    всё в «a»:        1500 + 400 = 1900, акции нет                       -> 1900
    всё в «b»:        1500 + 600 = 2100 >= 2000, скидка min(420, 1000)=420 -> 1680  <-- минимум
    L1 в «a», L2 в «b»: 1500 + (600, чек ниже 2000, скидки нет)           -> 2100
    L2 в «a», L1 в «b»:  400 + (1500, чек ниже 2000, скидки нет)          -> 1900
    Жадность по цене положила бы L2 в «a» за 400 ₽ и дала 1900 ₽.
    """
    a = mk_store("a", 1, "Магнит")
    b = mk_store("b", 2, "ВкусВилл")
    lines = [
        mk_line(1, "Дорогая позиция", {"a": 1500.0, "b": 1500.0}),
        mk_line(2, "Мелочь", {"a": 400.0, "b": 600.0}),
    ]
    offers = {2: [mk_offer(2, percent=20, cap=1000, min_check=2000, oid=7, card_id=77)]}

    variants = optimize(lines, [a, b], offers, baseline=1900.0, penalty=0.0, top_n=3, max_stores=2)
    best = variants[0]
    assert best.total == 1680.0
    assert [s.store_code for s in best.stores] == ["b"]
    assert best.stores[0].subtotal == 2100.0
    assert best.stores[0].discount == 420.0
    # «Мелочь» куплена за 600 вместо 400 — сознательно, ради мин. чека
    prices = {line.product_name: line.price for line in best.stores[0].lines}
    assert prices["Мелочь"] == 600.0


def test_returns_exactly_top_n_sorted_by_total():
    a = mk_store("a", 1, "Магнит")
    b = mk_store("b", 2, "ВкусВилл")
    c = mk_store("c", 3, "Лента")
    lines = [
        mk_line(1, "Позиция 1", {"a": 300.0, "b": 320.0, "c": 290.0}),
        mk_line(2, "Позиция 2", {"a": 500.0, "b": 460.0, "c": 520.0}),
        mk_line(3, "Позиция 3", {"a": 700.0, "b": 760.0, "c": 690.0}),
        mk_line(4, "Позиция 4", {"a": 250.0, "b": 210.0, "c": 260.0}),
    ]
    offers = {
        1: [mk_offer(1, percent=10, cap=200, min_check=1000, oid=1, card_id=11)],
        2: [mk_offer(2, percent=5, cap=500, oid=2, card_id=22)],
    }
    variants = optimize(lines, [a, b, c], offers, baseline=1750.0, penalty=50.0,
                        top_n=3, max_stores=2)

    assert len(variants) == 3
    totals = [v.total for v in variants]
    assert totals == sorted(totals)
    assert all(v.total == round(v.total, 2) for v in variants)
    # состав магазинов не повторяется
    keys = [tuple(sorted(s.store_code for s in v.stores)) for v in variants]
    assert len(set(keys)) == 3


def test_missing_product_is_reported_and_variant_demoted():
    a = mk_store("a", 1, "Магнит")
    b = mk_store("b", 2, "ВкусВилл")
    lines = [
        mk_line(1, "Есть везде", {"a": 100.0, "b": 90.0}),
        mk_line(2, "Только в Магните", {"a": 200.0}),
    ]
    variants = optimize(lines, [a, b], {}, baseline=300.0, penalty=0.0, top_n=3, max_stores=2)

    # лучший вариант: «Есть везде» в b за 90, «Только в Магните» в a за 200 -> 290
    assert variants[0].total == 290.0
    assert variants[0].missing_products == []
    # вариант «только b» не покрывает вторую позицию: помечен и уехал в конец,
    # хотя его total (90) формально ниже
    only_b = variants[-1]
    assert [s.store_code for s in only_b.stores] == ["b"]
    assert only_b.missing_products == ["Только в Магните"]


def test_out_of_stock_position_is_not_placed_in_that_store():
    a = mk_store("a", 1, "Магнит")
    b = mk_store("b", 2, "ВкусВилл")
    # в «b» дешевле, но нет в наличии -> позиция обязана уйти в «a»
    lines = [mk_line(1, "Форель", {"a": 400.0, "b": 350.0}, in_stock={"a": True, "b": False})]
    variants = optimize(lines, [a, b], {}, baseline=400.0, penalty=0.0, top_n=3, max_stores=2)
    assert variants[0].total == 400.0
    assert [s.store_code for s in variants[0].stores] == ["a"]
    assert variants[0].missing_products == []


def test_below_min_order_is_flagged_and_demoted():
    """min_order магазина (не мин. чек акции): чек ниже — заказ не оформить."""
    a = mk_store("a", 1, "Магнит")
    b = mk_store("b", 2, "ВкусВилл", min_order=500.0)
    lines = [
        mk_line(1, "Крупная позиция", {"a": 1000.0, "b": 1200.0}),
        mk_line(2, "Мелочь", {"a": 120.0, "b": 100.0}),
    ]
    variants = optimize(lines, [a, b], {}, baseline=1120.0, penalty=0.0, top_n=3, max_stores=2)

    # разбиение дало бы 1000 + 100 = 1100, но чек «b» 100 < min_order 500
    split = next(v for v in variants if len(v.stores) == 2)
    assert split.total == 1100.0
    assert next(s for s in split.stores if s.store_code == "b").below_min_order is True
    # и поэтому наверху более дорогой, но исполнимый вариант «всё в a» = 1120
    assert variants[0].total == 1120.0
    assert all(not s.below_min_order for s in variants[0].stores)


def test_discount_is_distributed_over_lines():
    a = mk_store("a", 1, "Магнит")
    lines = [
        mk_line(1, "Позиция 1", {"a": 100.0}),
        mk_line(2, "Позиция 2", {"a": 200.0}),
        mk_line(3, "Позиция 3", {"a": 700.0}),
    ]
    offers = {1: [mk_offer(1, percent=10, cap=1000, oid=1, card_id=11)]}
    variants = optimize(lines, [a], offers, baseline=1000.0, penalty=0.0, top_n=1, max_stores=1)

    store = variants[0].stores[0]
    # subtotal 1000, скидка 100; доли пропорциональны цене: 10 / 20 / 70
    assert (store.subtotal, store.discount, store.total) == (1000.0, 100.0, 900.0)
    assert [line.discount for line in store.lines] == [10.0, 20.0, 70.0]
    assert round(sum(line.discount for line in store.lines), 2) == store.discount
    assert all(line.card_id == 11 for line in store.lines)


def test_distributed_discount_keeps_kopeck_residue():
    a = mk_store("a", 1, "Магнит")
    lines = [mk_line(i + 1, f"Позиция {i + 1}", {"a": p})
             for i, p in enumerate([33.33, 33.33, 33.34])]
    offers = {1: [mk_offer(1, percent=10, cap=1000, oid=1, card_id=11)]}
    variants = optimize(lines, [a], offers, baseline=100.0, penalty=0.0, top_n=1, max_stores=1)

    store = variants[0].stores[0]
    assert store.subtotal == 100.0 and store.discount == 10.0
    assert round(sum(line.discount for line in store.lines), 2) == 10.0


def test_empty_input_returns_no_variants():
    a = mk_store("a", 1)
    assert optimize([], [a], {}, baseline=0.0, penalty=0.0) == []
    assert optimize([mk_line(1, "x", {"a": 10.0})], [], {}, baseline=0.0, penalty=0.0) == []


def test_full_basket_16_lines_is_fast():
    """Корзина из раздела 8 (16 позиций), два магазина — перебор 2**16 разбиений на пару."""
    receipt = [99.90, 239.00, 99.80, 379.00, 289.00, 215.10, 159.00, 74.50, 99.80,
               45.90, 358.00, 537.00, 229.00, 438.00, 381.50, 45.90]
    a = mk_store("a", 1, "Магнит", fee=199.0, free_from=2000.0)
    b = mk_store("b", 2, "ВкусВилл", fee=149.0, free_from=2500.0)
    lines = [mk_line(i + 1, f"Позиция {i + 1}", {"a": round(p, 2), "b": round(p * 1.07, 2)})
             for i, p in enumerate(receipt)]
    offers = {
        1: [mk_offer(1, percent=10, cap=500, min_check=1500, oid=1, card_id=11)],
        2: [mk_offer(2, percent=15, cap=300, min_check=1000, oid=2, card_id=22)],
    }
    started = time.perf_counter()
    variants = optimize(lines, [a, b], offers, baseline=3690.40, penalty=150.0,
                        top_n=3, max_stores=2)
    elapsed = time.perf_counter() - started

    assert elapsed < 3.0, f"перебор занял {elapsed:.2f} с"
    assert len(variants) == 3
    assert [v.total for v in variants] == sorted(v.total for v in variants)
    assert all(v.missing_products == [] for v in variants)
    best = variants[0]
    # каждая позиция корзины распределена ровно один раз
    placed = [line.product_id for s in best.stores for line in s.lines]
    assert sorted(placed) == list(range(1, 17))
    # итог варианта пересобирается из слагаемых формулы раздела 6
    assert best.total == round(sum(s.total for s in best.stores) + best.penalty, 2)
    for s in best.stores:
        assert s.total == round(s.subtotal + s.delivery - s.discount, 2)
    assert best.savings_rub == round(3690.40 - best.total, 2)


@pytest.mark.parametrize("subtotal,expected", [(999.99, 0.0), (1000.0, 100.0), (1000.01, 100.0)])
def test_min_check_boundary_is_inclusive(subtotal, expected):
    """min_check_rub включительно: «>= min_check» по букве формулы."""
    s = mk_store("m", 1)
    o = mk_offer(1, percent=10, cap=100, min_check=1000)
    assert store_total(subtotal, s, o)[1] == expected


@pytest.mark.parametrize("subtotal,expected_delivery", [(1999.99, 199.0), (2000.0, 0.0)])
def test_free_delivery_boundary_is_inclusive(subtotal, expected_delivery):
    s = mk_store("m", 1, fee=199.0, free_from=2000.0)
    assert store_total(subtotal, s, None)[0] == expected_delivery


# ---------- ручная работа влияет на выбор ----------
def test_a_third_store_is_not_worth_retyping_the_basket():
    """Магазин, куда корзину надо перебивать, не берётся ради копеечной выгоды.

    Это и есть ответ на «система посчитает, а перебивать корзину полчаса»: дешевле
    на 30 ₽, но завести туда корзину стоит 220 ₽ человеческой работы — значит не
    дешевле. Без этого расчёт видел только деньги.
    """
    cheap = mk_store("list_store", 1, fee=0, free_from=0)      # дешевле, но только списком
    easy = mk_store("link_store", 2, fee=0, free_from=0)       # дороже, но корзина уезжает ссылкой

    lines = [
        mk_line(1, "Молоко", {"list_store": 100.0, "link_store": 110.0}),
        mk_line(2, "Хлеб", {"list_store": 100.0, "link_store": 110.0}),
    ]

    # без учёта ручной работы побеждает дешёвый
    money_only = optimize(lines=lines, stores=[cheap, easy], offers={}, baseline=300.0,
                          penalty=0.0, top_n=3, max_stores=2)
    assert money_only[0].stores[0].store_code == "list_store"

    # с учётом — выигрывает тот, куда корзина уезжает сама
    with_effort = optimize(lines=lines, stores=[cheap, easy], offers={}, baseline=300.0,
                           penalty=0.0, top_n=3, max_stores=2,
                           handover={"list_store": 220.0, "link_store": 0.0})
    assert with_effort[0].stores[0].store_code == "link_store"


def test_effort_never_enters_the_price_the_person_pays():
    """Рубли за перебивание — не деньги: в итог к оплате они попасть не должны."""
    store = mk_store("list_store", 1, fee=0, free_from=0)
    lines = [mk_line(1, "Молоко", {"list_store": 100.0})]

    variants = optimize(lines=lines, stores=[store], offers={}, baseline=100.0,
                        penalty=0.0, top_n=1, max_stores=1,
                        handover={"list_store": 220.0})

    assert variants[0].total == 100.0, "ручная работа не должна прибавляться к сумме заказа"
    assert variants[0].handover == 220.0
    assert variants[0].effort_total == 320.0


def test_unknown_store_costs_no_invented_effort():
    """Магазин, способ передачи которого не проверен, получает ноль, а не догадку."""
    from app.handover import penalty_by_store

    prices = penalty_by_store()

    assert prices["lenta"] == 0, "корзина уезжает ссылкой — руками делать нечего"
    assert prices["pyaterochka"] > prices["magnit"] > 0, \
        "искать по названию тяжелее, чем нажать по готовым карточкам"
    assert "неизвестный_магазин" not in prices


def test_effortless_split_uses_only_stores_that_take_a_whole_basket(tmp_path, monkeypatch):
    """«Без перебивания» — это раскладка только между сетями, принимающими корзину целиком.

    Смысл отдельного расчёта: самый дешёвый вариант и самый удобный совпадают
    редко, а разница между ними и есть цена перебивания. Показать её человеку —
    значит дать выбрать; решить за него, что дешевле всегда лучше, — значит
    отправить его класть шестнадцать позиций по одной ради трёхсот рублей.
    """
    from app import config, handover, repo, service
    from app.db import init_db
    from app.models import Product

    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "eff.db"))
    init_db()

    принимающие = {code for code, kind in handover.KIND_BY_STORE.items()
                   if kind == handover.LINK}
    assert принимающие, "должна быть хотя бы одна сеть, принимающая корзину целиком"

    pid = repo.upsert_product(Product(id=None, name="Молоко", unit="pcs"))
    bid = repo.create_basket("Проверка", source="manual")
    repo.set_basket_item(bid, pid, 1.0)
    for code in ("lenta", "magnit"):
        store = repo.get_store(code)
        sp = repo.upsert_store_product(store.id, f"{code}-1", "Молоко")
        repo.confirm_mapping(pid, sp, confirmed=True)
        repo.save_price(sp, 50.0 if code == "magnit" else 90.0)

    удобные, _ = service.effortless_variants(bid)

    assert удобные, "принимающая сеть покрывает корзину — вариант должен быть"
    коды = {b.store_code for b in удобные[0].stores}
    assert коды <= принимающие, f"в удобный вариант попал магазин с ручным вводом: {коды}"
    assert "magnit" not in коды, "Магнит дешевле, но корзину целиком не принимает"
