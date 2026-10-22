"""«Быстрее»: один заказ в одной сети, где есть вся корзина.

ЗАЧЕМ. «Где дешевле» отвечает на вопрос «где это дешевле всего», и лучший ответ на
него часто «часть тут, часть там». Такой ответ стоит человеку двух заказов, двух
доставок и двух ожиданий курьера. Владелец просил рядом с ценой видеть второй ответ
(docs/plans/mobilnaya-versiya-2026-10-21.md, Z3+4): одним заказом, всё в наличии, и
сколько за это доплачивается против самой большой экономии.

ЧТО ЗДЕСЬ СЧИТАЕТСЯ «БЫСТРЕЕ». Ровно то, что видно в данных, и ничего больше:
вариант ровно из одной сети и без единой недостающей позиции. Из таких — самый
дешёвый. Срок доставки здесь не считается: его нет ни в одном ответе сетей, и
придуманные «20 минут» были бы обещанием, которого мы не выполним. Самовывоз тоже
называется только расстоянием и только когда магазин сети у адреса известен
(location.nearby / nearby_magnit) — без слотов, часов работы и «за N минут».

ЧЕГО ЗДЕСЬ НЕТ. Flask и базы: это правило, а не экран. Экран «Где дешевле»
(app/web/phone/cheaper.py) берёт ответ отсюда и рисует, а тесты проверяют правило
без браузера.
"""
from __future__ import annotations

import logging

from app import location

log = logging.getLogger(__name__)

# Причина отказа: полного односетевого варианта среди посчитанных нет.
NO_WHOLE = "нет сети, где есть всё"

# Почему блок вообще имеет право быть: это единственное, что мы про него знаем.
WHOLE = "всё есть в одной сети"

# Сети, у которых location знает магазины рядом с адресом: nearby — Лента,
# nearby_magnit — Магнит. У остальных такого вопроса нет вовсе (METRO и сети,
# которые видит только телефон, адрес нам для самовывоза не отдают), и выдумывать
# им точку нельзя.
NEARBY = {"lenta": "Лента", "magnit": "Магнит"}


def fastest(variants, complete_positions: int = 0) -> dict:
    """Самый дешёвый вариант, где вся корзина лежит в одной сети.

    `variants` — то, что вернул расчёт (app/service.calculate) или список мест
    экрана «Где дешевле»; `complete_positions` — сколько позиций в корзине
    (app/web/screens/result.py, _bundle: "positions").

    ВОЗВРАТ — ВСЕГДА СЛОВАРЬ, даже когда подходящего варианта нет:

        {"variant": v, "extra": 12.5, "why": "всё есть в одной сети"}
        {"variant": None, "extra": 0.0, "why": "нет сети, где есть всё"}

    Отказ отдаётся причиной, а не None: экрану всё равно нужна одна проверка
    (`variant is None`), а причина остаётся видимой — «нет» без объяснения читается
    как поломка расчёта.

    `extra` — насколько этот вариант дороже САМОГО ДЕШЁВОГО ИЗ ПОЛНЫХ, то есть
    доплата за один заказ против максимальной экономии на той же корзине. Сравнение
    идёт только с полными вариантами: у варианта, где части корзины нет, свой total и
    своя урезанная база (optimizer._covered_baseline), и «экономия» на неполной
    корзине — не та же экономия, а меньшая сумма за меньшее. Поэтому доплата никогда
    не бывает отрицательной и ничего не занижает.

    «Полный» — не только пустой missing_products, но и покрытие: когда число позиций
    корзины известно, у сети должно быть не меньше строк, чем позиций. Так «всё есть»
    не скажется о варианте, из которого позиция выпала молча.
    """
    counted = [v for v in (variants or []) if v is not None]
    if not counted:
        return {"variant": None, "extra": 0.0, "why": NO_WHOLE}

    def total(variant) -> float:
        try:
            return float(getattr(variant, "total", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    complete = [v for v in counted if not getattr(v, "missing_products", None)]
    whole = [v for v in complete if _whole(v, complete_positions)]
    if not whole:
        return {"variant": None, "extra": 0.0, "why": NO_WHOLE}
    pick = min(whole, key=total)
    best = min(complete, key=total) if complete else pick
    return {"variant": pick, "extra": max(0.0, round(total(pick) - total(best), 2)),
            "why": WHOLE}


def _whole(variant, positions: int = 0) -> bool:
    """Одна сеть и в ней есть вся корзина — без недостающих позиций и без пропусков."""
    stores = list(getattr(variant, "stores", None) or [])
    if len(stores) != 1:
        return False
    if getattr(variant, "missing_products", None):
        return False
    lines = list(getattr(stores[0], "lines", None) or [])
    if not lines:
        return False
    try:
        need = int(positions or 0)
    except (TypeError, ValueError):
        need = 0
    return len(lines) >= need


def pickup_line(store_code: str) -> str | None:
    """«самовывоз: Магнит, 394 м» — если сеть знает свой магазин рядом с адресом.

    None — обычный ответ, а не поломка: адрес не задан, сети в этом городе нет,
    справочник не ответил или сеть про самовывоз ничего не говорит. Выдумывать
    точку, часы и «собираем за 15 минут» здесь не из чего: таких данных у нас нет.
    """
    code = (store_code or "").strip()
    if code not in NEARBY:
        return None
    found = _nearby(code)
    if not found:
        return None
    far = _distance_text(min(found, key=_distance))
    if not far:
        return None
    return f"самовывоз: {NEARBY[code]}, {far}"


def _nearby(code: str) -> list:
    """Магазины сети у адреса. Пусто — адреса нет, города нет или сеть не ответила."""
    try:
        if code == "lenta":
            return list(location.nearby() or [])
        return list(location.nearby_magnit() or [])
    except Exception:  # noqa: BLE001 — молчание сети не повод ронять экран
        log.warning("%s: магазины рядом с адресом не нашлись", code, exc_info=True)
        return []


def _distance(hub) -> float:
    """Расстояние до магазина в метрах. Нет числа — «дальше всех», а не первое место."""
    try:
        return float((hub or {}).get("distance"))
    except (AttributeError, TypeError, ValueError):
        return float("inf")


def _distance_text(hub) -> str:
    """«394 м» и «9,1 км» — то же правило, что у главной (app/web/screens/home.py).

    Правило повторено, а не взято оттуда: главная — экран Flask, а этот модуль
    обязан оставаться правилом без экранов (иначе его нельзя проверить без браузера).
    """
    metres = _distance(hub)
    if metres == float("inf"):
        return ""
    if metres < 1000:
        return f"{round(metres)} м"
    return f"{metres / 1000:.1f} км".replace(".", ",")


__all__ = ["fastest", "pickup_line", "NO_WHOLE", "WHOLE", "NEARBY"]
