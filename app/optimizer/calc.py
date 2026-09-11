"""Расчёт стоимости корзины в одном магазине — формула раздела 6 спецификации.

    subtotal_s = сумма(price * qty) по позициям, отнесённым в s
    delivery_s = 0, если subtotal_s >= free_delivery_from, иначе delivery_fee
    discount_s = 0, если subtotal_s < min_check_rub
               = min(subtotal_s * percent / 100, cap_rub - cap_used) иначе
    total_s    = subtotal_s + delivery_s - discount_s

Модуль намеренно не знает ничего о разбиении корзины: это чистые функции,
по которым вручную проверяется критерий приёмки №5.
"""
from __future__ import annotations

from app.models import Offer, Store


def store_total(subtotal: float, store: Store, offer: Offer | None) -> tuple[float, float, float]:
    """Возвращает (delivery, discount, total) для одного магазина по формуле раздела 6.

    subtotal — уже посчитанная сумма позиций (цена * qty), отнесённых в этот магазин.
    offer — выбранная акция карты или None, если карты нет.
    Все три числа округляются до 2 знаков; total согласован с округлёнными
    delivery и discount (total == subtotal + delivery - discount).
    """
    subtotal = float(subtotal)

    # доставка
    delivery = 0.0 if subtotal >= store.free_delivery_from else float(store.delivery_fee)

    # скидка
    discount = offer_discount(subtotal, offer)

    delivery = round(delivery, 2)
    discount = round(discount, 2)
    total = round(subtotal + delivery - discount, 2)
    return delivery, discount, total


def offer_discount(subtotal: float, offer: Offer | None) -> float:
    """Скидка одной акции при данном чеке. Ниже мин. чека — ноль, сверху ограничена остатком лимита."""
    if offer is None:
        return 0.0
    if subtotal < offer.min_check_rub:
        return 0.0
    # cap_left = cap_rub - cap_used (свойство Offer, не уходит ниже нуля)
    return min(subtotal * offer.percent / 100.0, offer.cap_left)


def best_offer(subtotal: float, store: Store, offers: list[Offer]) -> tuple[Offer | None, float]:
    """Выбирает карту, дающую максимальную скидку при данном subtotal. -> (offer, discount)

    Учитываются только действующие акции (срок, активация). Если ни одна акция
    скидки не даёт, возвращается (None, 0.0) — карта в варианте не показывается.
    """
    best: Offer | None = None
    best_discount = 0.0
    for offer in offers or ():
        if not offer.is_valid_on():
            continue
        discount = offer_discount(subtotal, offer)
        if discount <= 0.0:
            continue
        if discount > best_discount + 1e-9:
            best, best_discount = offer, discount
        elif best is not None and abs(discount - best_discount) <= 1e-9:
            # детерминированный разбор ничьей: больший процент, затем меньший id
            if (offer.percent, -(offer.id or 0)) > (best.percent, -(best.id or 0)):
                best, best_discount = offer, discount
    return best, round(best_discount, 2)
