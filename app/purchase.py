"""Сколько чего кладётся в корзину сети — одно правило на расчёт, наряд и ссылки.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Количество считали в четырёх местах: расчёт цены
(app/service.line_price), наряд браузера (app/shopbrowser/cart), корзина METRO и
ссылки Ленты и ВкусВилла (app/service.cart_link). Каждое округляло по-своему, и
человек видел одну сумму на «Результате», а в корзине оказывалось другое
количество — ревизия 26.09.2026 находила это трижды. Правило здесь, и все четыре
места зовут его, а не своё.
"""
from __future__ import annotations

import math

QTY_LIMIT = 30            # больше тридцати штук одного товара — это ошибка, а не корзина

# Сети, где весовой товар продаётся ПОРЦИЯМИ известного веса, и порции кладёт сама
# сеть. Лента: quantity развесного — число фасовок по weightGrams (замер 19.09.2026,
# app/connectors/lenta._weight_rules). Для них расчёт считает целые порции, как их
# и положит ссылка. Сеть сюда вписывается только после замера.
PORTION_CHAINS = ("lenta",)


def qty_text(value: float) -> str:
    """0.7 → «0,7». Точка в количестве читается как чужая машина, а не как вес."""
    return f"{value:g}".replace(".", ",")


def measure(unit) -> str:
    """Подпись единицы для заметки — по-русски.

    Принимает и код базы («kg»), и уже готовое «кг»: заметку собирают и здесь, из
    наряда, и экраны, у которых единица уже переведена. Латинское «kg» посреди
    русской фразы человек читает как сбой, а не как килограмм.
    """
    word = str(unit or "").strip()
    return {"kg": "кг", "pcs": "шт", "l": "л", "g": "г"}.get(word.lower(), word)


def packs_word(times: int) -> str:
    """«взяли 1 упаковку», «3 упаковки», «5 упаковок» — иначе в заметке видно машину."""
    tail = times % 100
    if 11 <= tail <= 14:
        return "упаковок"
    last = times % 10
    if last == 1:
        return "упаковку"
    return "упаковки" if 2 <= last <= 4 else "упаковок"


def pieces(qty, unit: str | None = None, pack_g: float | None = None,
           per: str | None = None, limit: int | None = QTY_LIMIT) -> tuple[int, str]:
    """Сколько раз нажать. Витрина считает штуками, дробное нажать нельзя.

    Округляем ВВЕРХ и ГОВОРИМ об этом: 0,7 кг сыра превратились в одну упаковку,
    и человек должен увидеть это в отчёте, а не потом в чеке. Вверх — потому что
    недоложенное придётся докупать отдельной поездкой, а лишние двести граммов
    нет; прежний round() к тому же округлял 2,5 до 2 (банковское округление).

    КИЛОГРАММЫ КОРЗИНЫ И УПАКОВКИ СЕТИ — РАЗНЫЕ ЧИСЛА. Если корзина считает
    позицию в килограммах (per="kg"), а сеть продаёт её упаковками известного веса
    (pack_g), нажатий столько, сколько упаковок: 0,4 кг по 200 г — две, 1,5 кг
    сеткой по 2,5 кг — одна. Раньше это было round(0,4) = одна упаковка на 200 г
    и round(1,5) = две сетки на 5 кг.

    Заметка возвращается отдельной строкой от причины неудачи нарочно: округление
    случается и с позицией, которая ЛЕГЛА, а склеенное с причиной оно терялось бы
    ровно в этом, самом частом случае.

    limit — потолок нажатий на одну позицию (QTY_LIMIT). Расчёт цены зовёт без
    потолка (limit=None): деньги считаются за просимое количество, иначе сорок
    бутылок стоили бы как тридцать, и разница уходила в «экономию». Наряд кладёт
    не больше потолка и говорит об этом в заметке.
    """
    try:
        wanted = float(qty)
    except (TypeError, ValueError):
        wanted = 1.0
    try:
        pack = float(pack_g) if pack_g else 0.0
    except (TypeError, ValueError):
        pack = 0.0
    by_packs = (str(per or "").lower() in ("kg", "кг") and pack > 0
                and str(unit or "").lower() not in ("kg", "кг"))
    if by_packs:
        exact = wanted * 1000.0 / pack
        need = max(1, math.ceil(exact - 1e-9))
        times, capped = _cap(need, limit)
        if abs(need - exact) < 1e-9:
            return times, f"{qty_text(wanted)} кг — {need} уп. по {pack:g} г" + capped
        got = round(need * pack / 1000.0, 3)
        return times, (f"{qty_text(wanted)} кг упаковками по {pack:g} г не набрать — "
                       f"взяли {need} {packs_word(need)}, это {qty_text(got)} кг" + capped)
    need = max(1, math.ceil(wanted - 1e-9))
    times, capped = _cap(need, limit)
    if need == wanted:
        return times, capped.lstrip("; ")
    word = measure(per or unit)
    return times, (f"{qty_text(wanted)}{' ' + word if word else ''} нажатием не положить — "
                   f"взяли {need} {packs_word(need)}" + capped)


def _cap(need: int, limit: int | None) -> tuple[int, str]:
    """Нажатий не больше потолка — и сказать, если потолок сработал."""
    if limit is None or need <= limit:
        return need, ""
    return limit, (f"; больше {limit} одного товара за раз приложение не кладёт — "
                   f"остальные {need - limit} положите сами")



__all__ = ["pieces", "qty_text", "measure", "packs_word", "QTY_LIMIT", "PORTION_CHAINS"]
