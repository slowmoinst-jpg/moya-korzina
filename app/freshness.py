"""Свежесть цены: какой цене можно верить без оговорок.

ЗАЧЕМ ОТДЕЛЬНОЕ ПРАВИЛО. Интерфейс больше не пишет «цены от вчера» и «обновлено
6 минут назад» (docs/design/nazvaniya-2026-09-26.md, «Требования к данным»): человек
видит цену как факт. Значит, фактом она и должна быть, и отвечает за это сервер.
Раньше расчёт брал последний снимок любой давности — неделю назад пойманная акция
выглядела на экране так же уверенно, как цена этой минуты, и «экономия» на ней была
выдумкой.

ПРАВИЛО ОДНО ДЛЯ ВСЕХ ЭКРАНОВ. Каталог, корзина, «Где дешевле» и наряд на корзину
берут цену отсюда, поэтому одна и та же позиция в одном магазине стоит везде одинаково
и одинаково пропадает, когда устарела.

НЕ УВЕРЕН — НЕ ПОКАЗЫВАЙ. Снимок старше `prices.fresh_hours` не отдаётся вовсе.
Расчёт тогда просто не видит этот магазин по этой позиции и считает без него —
это честнее, чем предложить заказ по цене, которой на полке уже нет. Свежую цену
приносит обновление (matcher.refresh_prices) перед расчётом и проверка перед
оформлением (cartplan.verify).

По той же причине не отдаются, как бы недавно ни были сняты:
  * справочная цена из data/fallback_prices.csv (source="fallback") — её подставляет
    коннектор, когда сеть не ответила; это цена из файла, а не с полки;
  * цена ТОЧКИ, снятая до смены адреса (location.moved_at), — у Магнита, Ленты и
    METRO цена своя у каждого магазина, и прежняя точка бывает в другом городе.
    После смены адреса цены корзины обновляются сами (screens/basket.refresh_after_move).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app import config, location, repo

# PriceSnapshot.source справочной цены из CSV (connectors/stub.FALLBACK).
REFERENCE = "fallback"


def max_age_hours() -> float:
    """Сколько часов цена считается действующей. По умолчанию — срок кэша коннекторов."""
    value = config.get("prices.fresh_hours")
    if value is None:
        value = config.get("connectors.cache_ttl_hours", 6)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 6.0


def _parse(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    text = str(stamp).strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        try:
            moment = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if moment.tzinfo is not None:
        # снимки пишутся местным временем (repo.NOW); чужой пояс приводим к нему
        moment = moment.astimezone().replace(tzinfo=None)
    return moment


def is_fresh(fetched_at: str | None, now: datetime | None = None) -> bool:
    """Снимок взят не раньше чем `max_age_hours` назад. Непонятная дата — не свежий."""
    moment = _parse(fetched_at)
    if moment is None:
        return False
    now = now or datetime.now()
    return now - moment <= timedelta(hours=max_age_hours())


def before_move(fetched_at: str | None, store_code: str | None) -> bool:
    """Цена точки снята до смены адреса — это цена прежнего магазина."""
    if store_code not in location.ADDRESS_STORES:
        return False
    moved = _parse(location.moved_at())
    if moved is None:
        return False
    taken = _parse(fetched_at)
    return taken is None or taken < moved


def price_for(product_id: int, store_id: int, now: datetime | None = None) -> dict | None:
    """Действующая цена эталона в магазине или None, если свежей цены нет.

    Действующая — значит свежая, не справочная, снятая по нынешнему адресу и с
    личными акциями человека (app/personal.py): это та цена, которую он заплатит,
    а не та, что висит на витрине для всех.
    """
    snap = repo.latest_price_for(product_id, store_id)
    if not snap or not is_fresh(snap.get("fetched_at"), now):
        return None
    if snap.get("source") == REFERENCE:
        return None
    store = repo.get_store(store_id)
    if before_move(snap.get("fetched_at"), store.code if store else None):
        return None
    from app import personal
    return personal.apply(snap, product_id, store_id)


__all__ = ["max_age_hours", "is_fresh", "before_move", "price_for", "REFERENCE"]
