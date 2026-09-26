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
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app import config, repo


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


def price_for(product_id: int, store_id: int, now: datetime | None = None) -> dict | None:
    """Действующая цена эталона в магазине или None, если свежей цены нет."""
    snap = repo.latest_price_for(product_id, store_id)
    if not snap or not is_fresh(snap.get("fetched_at"), now):
        return None
    return snap


__all__ = ["max_age_hours", "is_fresh", "price_for"]
