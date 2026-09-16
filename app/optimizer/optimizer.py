"""Перебор разбиений корзины между магазинами (разделы 5.6 и 6 спецификации).

Скидка нелинейна (мин. чек + лимит кэшбэка), поэтому жадность по позициям не работает:
может быть выгодно положить позицию в магазин, где она дороже, чтобы добить мин. чек
(следствия 1-3 раздела 6). Отсюда честный перебор всех разбиений.

Отсечения:
  * позиция рассматривается только в тех магазинах варианта, где для неё есть цена и наличие;
  * для пары магазинов перебор идёт кодом Грея — суммы магазинов пересчитываются
    за O(1) на шаг, в целых копейках (без накопления ошибки float);
  * на набор магазинов оставляем только лучшее разбиение, варианты с одинаковым
    составом магазинов схлопываются.
"""
from __future__ import annotations

from itertools import combinations, product

from app.models import BasketLine, Offer, Store, StoreBreakdown, Variant, VariantLine
from app.optimizer.calc import best_offer, store_total

# Предел честного перебора по числу «свободных» позиций. 2**16 = 65536 на пару
# магазинов — доли секунды. Дальше включается локальный поиск.
BRUTE_FORCE_LIMIT = 22
MAX_COMBOS = 4_000_000


def _cents(value: float) -> int:
    return int(round(float(value) * 100))


def _available(line: BasketLine, store: Store) -> bool:
    """Позиция может уйти в магазин, только если там есть цена и товар в наличии."""
    price = line.prices.get(store.code)
    if price is None:
        return False
    return bool(line.in_stock.get(store.code, True))


def _cost_fn(store: Store, offers: list[Offer], handover: float = 0.0):
    """Быстрая функция стоимости магазина в копейках: subtotal -> subtotal + delivery - discount.

    handover — во что обходится ЗАВЕСТИ корзину в этот магазин. Не выдумка ради
    красоты: у одних сетей корзина уезжает одной ссылкой, у других её надо
    перебить руками по позициям, и без этого числа расчёт охотно отправляет
    человека в третий магазин ради сорока рублей, где он потратит четверть часа.
    Прибавляется к стоимости магазина, а не к подытогу, — на порог бесплатной
    доставки и на минимальный чек оно влиять не должно, это не покупка.
    """
    free_from = _cents(store.free_delivery_from)
    fee = _cents(store.delivery_fee)
    handover_cents = _cents(handover)
    active = [(o.percent / 100.0, _cents(o.cap_left), _cents(o.min_check_rub))
              for o in (offers or ()) if o.is_valid_on()]

    def cost(subtotal: int) -> float:
        value = subtotal + (0 if subtotal >= free_from else fee)
        discount = 0.0
        for percent, cap_left, min_check in active:
            if subtotal >= min_check:
                d = subtotal * percent
                if d > cap_left:
                    d = cap_left
                if d > discount:
                    discount = d
        return value - discount + handover_cents

    return cost


def _score(subs: list[int], counts: list[int], costs: list, penalty_cents: float) -> float:
    """Итог набора магазинов в копейках. Пустой магазин делает разбиение недопустимым:
    вариант «всё в один магазин» перебирается отдельно, набором из одного магазина."""
    total = 0.0
    for j, count in enumerate(counts):
        if not count:
            return float("inf")
        total += costs[j](subs[j])
    return total + penalty_cents * (len(counts) - 1)


def _best_assignment(lines: list[BasketLine], combo: tuple[Store, ...], costs: list,
                     penalty_cents: float) -> tuple[list[int | None], list[int]] | None:
    """Ищет лучшее распределение позиций по магазинам набора.

    Возвращает (assign, missing): assign[i] — индекс магазина в combo или None,
    missing — индексы позиций, которых нет ни в одном магазине набора.
    None — если набор нереализуем (какому-то магазину не досталось ни одной позиции).
    """
    n = len(combo)
    assign: list[int | None] = [None] * len(lines)
    missing: list[int] = []
    free: list[tuple[int, list[int]]] = []
    subs = [0] * n
    counts = [0] * n

    for i, line in enumerate(lines):
        options = [j for j, store in enumerate(combo) if _available(line, store)]
        if not options:
            missing.append(i)
        elif len(options) == 1:
            j = options[0]
            assign[i] = j
            subs[j] += _cents(line.prices[combo[j].code])
            counts[j] += 1
        else:
            free.append((i, options))

    if not free:
        return (assign, missing) if all(counts) else None

    # цены свободных позиций по магазинам набора, в копейках
    prices = [[_cents(lines[i].prices[combo[j].code]) if j in options else None
               for j in range(n)] for i, options in free]

    if n == 2 and len(free) <= BRUTE_FORCE_LIMIT:
        chosen = _brute_force_pair(free, prices, subs, counts, costs, penalty_cents)
    elif n ** len(free) <= MAX_COMBOS and len(free) <= BRUTE_FORCE_LIMIT:
        chosen = _brute_force_any(free, prices, subs, counts, costs, penalty_cents)
    else:
        chosen = _local_search(free, prices, subs, counts, costs, penalty_cents)

    if chosen is None:
        return None
    for pos, (i, _options) in enumerate(free):
        assign[i] = chosen[pos]
    return assign, missing


def _brute_force_pair(free, prices, subs, counts, costs, penalty_cents) -> list[int] | None:
    """Перебор 2**k разбиений пары магазинов кодом Грея: шаг меняет ровно одну позицию."""
    k = len(free)
    p0 = [row[0] for row in prices]
    p1 = [row[1] for row in prices]
    sub0, sub1 = subs[0], subs[1]
    cnt0, cnt1 = counts[0], counts[1]
    for value in p0:
        sub0 += value
    cnt0 += k

    cost0, cost1 = costs[0], costs[1]

    def score(s0: int, c0: int, s1: int, c1: int) -> float:
        if not c0 or not c1:          # пустой магазин — это набор из одного магазина
            return float("inf")
        return cost0(s0) + cost1(s1) + penalty_cents

    mask = 0
    best_mask = None
    best_score = score(sub0, cnt0, sub1, cnt1)
    if best_score < float("inf"):
        best_mask = 0

    for step in range(1, 1 << k):
        b = (step & -step).bit_length() - 1
        if mask >> b & 1:                      # возвращаем позицию в магазин 0
            mask &= ~(1 << b)
            sub1 -= p1[b]
            cnt1 -= 1
            sub0 += p0[b]
            cnt0 += 1
        else:                                  # переносим позицию в магазин 1
            mask |= 1 << b
            sub0 -= p0[b]
            cnt0 -= 1
            sub1 += p1[b]
            cnt1 += 1
        value = score(sub0, cnt0, sub1, cnt1)
        if value < best_score - 1e-9 or best_mask is None and value < float("inf"):
            best_score = value
            best_mask = mask

    if best_mask is None:
        return None
    return [(best_mask >> pos) & 1 for pos in range(k)]


def _brute_force_any(free, prices, subs, counts, costs, penalty_cents) -> list[int] | None:
    """Общий перебор для 1 и 3+ магазинов в наборе (MVP использует пары)."""
    best_choice = None
    best_score = float("inf")
    for choice in product(*[options for _i, options in free]):
        cur_subs = list(subs)
        cur_counts = list(counts)
        for pos, j in enumerate(choice):
            cur_subs[j] += prices[pos][j]
            cur_counts[j] += 1
        value = _score(cur_subs, cur_counts, costs, penalty_cents)
        if value < best_score - 1e-9:
            best_score, best_choice = value, list(choice)
    return best_choice


def _local_search(free, prices, subs, counts, costs, penalty_cents) -> list[int] | None:
    """Страховка на случай очень больших корзин: старт с самой дешёвой цены + улучшения."""
    choice = []
    for pos, (_i, options) in enumerate(free):
        choice.append(min(options, key=lambda j: prices[pos][j]))
    cur_subs, cur_counts = list(subs), list(counts)
    for pos, j in enumerate(choice):
        cur_subs[j] += prices[pos][j]
        cur_counts[j] += 1
    best = _score(cur_subs, cur_counts, costs, penalty_cents)

    improved = True
    while improved:
        improved = False
        for pos, (_i, options) in enumerate(free):
            current = choice[pos]
            for j in options:
                if j == current:
                    continue
                cur_subs[current] -= prices[pos][current]
                cur_counts[current] -= 1
                cur_subs[j] += prices[pos][j]
                cur_counts[j] += 1
                value = _score(cur_subs, cur_counts, costs, penalty_cents)
                if value < best - 1e-9:
                    best, choice[pos], current, improved = value, j, j, True
                else:
                    cur_subs[j] -= prices[pos][j]
                    cur_counts[j] -= 1
                    cur_subs[current] += prices[pos][current]
                    cur_counts[current] += 1
    return choice if best < float("inf") else None


def _build_variant(lines: list[BasketLine], combo: tuple[Store, ...], assign: list[int | None],
                   missing: list[int], offers: dict[int, list[Offer]], baseline: float,
                   penalty: float) -> Variant | None:
    groups: dict[int, list[int]] = {}
    for i, j in enumerate(assign):
        if j is not None:
            groups.setdefault(j, []).append(i)
    if not groups:
        return None

    breakdowns: list[StoreBreakdown] = []
    for j in sorted(groups):
        store = combo[j]
        idxs = groups[j]
        subtotal = round(sum(lines[i].prices[store.code] for i in idxs), 2)
        offer, _discount = best_offer(subtotal, store, offers.get(store.id, []))
        delivery, discount, total = store_total(subtotal, store, offer)

        # скидка распределяется на позиции пропорционально их доле в чеке магазина
        vlines: list[VariantLine] = []
        for i in idxs:
            line = lines[i]
            price = round(float(line.prices[store.code]), 2)
            share = round(discount * price / subtotal, 2) if subtotal else 0.0
            vlines.append(VariantLine(
                store_code=store.code, store_name=store.name,
                card_id=offer.card_id if offer else None, card_name=None,
                product_id=line.product_id, product_name=line.name,
                qty=line.qty, price=price, discount=share,
            ))
        residue = round(discount - sum(v.discount for v in vlines), 2)
        if vlines and residue:
            vlines[-1].discount = round(vlines[-1].discount + residue, 2)

        breakdowns.append(StoreBreakdown(
            store_code=store.code, store_name=store.name,
            subtotal=subtotal, delivery=delivery, discount=discount, total=total,
            card_id=offer.card_id if offer else None, card_name=None,
            offer_id=offer.id if offer else None, lines=vlines,
            below_min_order=bool(store.min_order) and subtotal < store.min_order,
        ))

    variant_penalty = round(float(penalty) * (len(breakdowns) - 1), 2)
    total = round(sum(b.total for b in breakdowns) + variant_penalty, 2)
    return Variant(
        stores=breakdowns,
        total=total,
        baseline=round(float(baseline), 2),
        penalty=variant_penalty,
        missing_products=[lines[i].name for i in missing],
    )


def optimize(lines: list[BasketLine], stores: list[Store], offers: dict[int, list[Offer]],
             baseline: float, penalty: float, top_n: int = 3, max_stores: int = 2,
             handover: dict[str, float] | None = None) -> list[Variant]:
    """Перебирает разбиения корзины на 1..max_stores магазинов, для каждого магазина подбирает
    лучшую карту, считает итог, возвращает top_n лучших вариантов, отсортированных по total.

    Варианты с непокрытыми позициями (missing_products) и с чеком ниже store.min_order
    не выбрасываются, а помечаются и уходят в конец списка: они заведомо хуже полноценных,
    но пользователю видно, почему.
    """
    lines = list(lines or [])
    stores = list(stores or [])
    if not lines or not stores:
        return []

    offers = offers or {}
    handover = handover or {}
    costs_by_store = {s.code: _cost_fn(s, offers.get(s.id, []), handover.get(s.code, 0.0))
                      for s in stores}
    penalty_cents = _cents(penalty)
    limit = max(1, min(int(max_stores or 1), len(stores)))

    best_by_key: dict[tuple[str, ...], Variant] = {}
    for size in range(1, limit + 1):
        for combo in combinations(stores, size):
            costs = [costs_by_store[s.code] for s in combo]
            found = _best_assignment(lines, combo, costs, penalty_cents)
            if found is None:                 # набору не хватило позиций на все магазины
                continue
            assign, missing = found
            variant = _build_variant(lines, combo, assign, missing, offers, baseline, penalty)
            if variant is None:
                continue
            # набор магазинов, реально задействованных в варианте (пара могла схлопнуться в один)
            key = tuple(sorted(b.store_code for b in variant.stores))
            variant.handover = round(sum(handover.get(b.store_code, 0.0) for b in variant.stores), 2)
            current = best_by_key.get(key)
            if current is None or variant.effort_total < current.effort_total:
                best_by_key[key] = variant

    variants = sorted(
        best_by_key.values(),
        key=lambda v: (len(v.missing_products),
                       sum(1 for b in v.stores if b.below_min_order),
                       v.effort_total),
    )
    return variants[:max(0, int(top_n))]
