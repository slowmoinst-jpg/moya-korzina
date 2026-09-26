"""Личные акции в цене: купон из аккаунта человека меняет цену товара в расчёте.

ЗАЧЕМ. Персональные предложения (скидка 30% на молоко, кефир за 69,90 по карте)
видит только вошедший в свой аккаунт человек. Сборщик — расширение или приложение
на телефоне — приносит их отчётом (app/collector.py), но до сих пор они только
показывались списком. Экономия при этом считалась без них, то есть меньше, чем она
есть на самом деле, и «Где дешевле» мог отправить не в тот магазин.

КАК ПРИМЕНЯЕТСЯ. Купон относится к товару, если:
    — в нём назван артикул сети (skus) и он совпал с артикулом сопоставления; или
    — в нём назван товар (product, а без него — название купона), и оно очень
      похоже на название товара в этой сети (similarity ≥ MATCH).
Из подходящих купонов берётся самый выгодный, и цена никогда не растёт: личная цена
выше витринной — значит, витрина уже дешевле, и в ход идёт она.

ЧЕГО НЕ ДЕЛАЕМ. Не угадываем. Купон «-20% на молочную продукцию» ни к какому
конкретному товару не привязан, и растягивать его на все йогурты значило бы
рисовать экономию, которой на кассе может не случиться. Такой купон остаётся на
экране, но в цену не входит. Не применяем неактивированный купон (activated: false)
и истёкший. Порог похожести высокий по той же причине: ошибка здесь — обещанная
скидка, которой нет.

ОДНО МЕСТО ДЛЯ ВСЕХ ЭКРАНОВ. Вызывается из freshness.price_for, поэтому личная
цена одинаково попадает в расчёт, каталог, корзину и проверку перед оформлением.
"""
from __future__ import annotations

import logging

from app import repo

log = logging.getLogger(__name__)

MATCH = 0.8


def _store_code(store_id: int) -> str | None:
    for store in repo.list_stores():
        if store.id == store_id:
            return store.code
    return None


def _applies(coupon: dict, sku: str | None, name: str) -> bool:
    if coupon.get("activated") is False:
        return False
    skus = coupon.get("skus") or []
    if skus:
        return bool(sku) and str(sku) in {str(s) for s in skus}
    target = coupon.get("product") or coupon.get("title") or ""
    if not target or not name:
        return False
    from app.matcher.normalize import similarity
    return similarity(target, name) >= MATCH


def _after(coupon: dict, base: float) -> float | None:
    """Цена за единицу после купона; None — купон ничего не говорит о цене."""
    options = []
    if coupon.get("price"):
        options.append(float(coupon["price"]))
    if coupon.get("percent"):
        options.append(base * (1 - float(coupon["percent"]) / 100))
    if coupon.get("off_rub"):
        options.append(max(0.0, base - float(coupon["off_rub"])))
    return round(min(options), 2) if options else None


def best(store_code: str, sku: str | None, name: str, base: float) -> tuple[float, dict | None]:
    """(цена за единицу с лучшим подходящим купоном, сам купон или None)."""
    from app import collector

    price, chosen = base, None
    for coupon in collector.coupons(store_code):
        if not _applies(coupon, sku, name):
            continue
        after = _after(coupon, base)
        if after is not None and after < price:
            price, chosen = after, coupon
    return price, chosen


def apply(snap: dict, product_id: int, store_id: int) -> dict:
    """Снимок цены с учётом личных акций. Исходный снимок не меняется.

    В ответе появляются `coupon` (название применённого купона) и `shelf_price`
    (цена до купона) — чтобы экран мог показать «по вашей акции».
    """
    code = _store_code(store_id)
    if not code:
        return snap
    try:
        from app import collector
        if not collector.coupons(code):
            return snap
    except Exception:  # noqa: BLE001 — хранилище купонов не прочиталось; цена без них
        log.warning("купоны %s не прочитались — цена без личных акций", code, exc_info=True)
        return snap
    mapping = repo.confirmed_mapping(product_id, store_id) or {}
    sku, name = mapping.get("sku"), mapping.get("raw_name") or ""
    base = snap.get("price")
    if base is None or float(base) <= 0:
        return snap
    price, coupon = best(code, sku, name, float(base))
    if coupon is None:
        return snap
    out = dict(snap, price=price, shelf_price=base, coupon=coupon.get("title"))
    # Купон пишет цену упаковки; цена за килограмм меняется в той же доле, иначе
    # личная цена пачки встала бы ценой килограмма.
    if snap.get("price_per_kg"):
        out["price_per_kg"] = round(float(snap["price_per_kg"]) * price / float(base), 2)
    return out


__all__ = ["apply", "best", "MATCH"]
