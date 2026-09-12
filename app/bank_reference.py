"""Справочник условий банков — общий контур данных.

Здесь проверенная структура: какие программы у банков, как устроен кэшбэк, в чём он
начисляется. Здесь НЕТ процентов и лимитов по месяцам: они меняются каждый месяц и у каждого
свои — это личный контур, его заполняет человек на экране «Карты и акции».

Цель справочника — не считать за пользователя, а избавить его от вопроса «а как вообще
устроен кэшбэк в моём банке» и предупредить о ловушках: где начисляют баллы вместо рублей
и где число категорий зависит от подписки.
"""
from __future__ import annotations

import csv
import os
from functools import lru_cache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "bank_reference.csv")

# в чём банк начисляет выгоду: рубли складываются с ценой напрямую, остальное — нет
MONEY = ("рубли",)


@lru_cache(maxsize=1)
def all_banks() -> list[dict]:
    if not os.path.exists(PATH):
        return []
    with open(PATH, encoding="utf-8-sig", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def bank_names() -> list[str]:
    return [row["bank"] for row in all_banks()]


def find(bank: str) -> dict | None:
    for row in all_banks():
        if row["bank"].lower() == (bank or "").lower():
            return row
    return None


def card_title(row: dict) -> str:
    """Как назвать карту при заведении: «Black», «Мультибонус» или имя банка."""
    return (row.get("card") or row.get("program") or row.get("bank") or "").strip()


def is_money(row: dict) -> bool:
    """Начисляется ли выгода рублями. Баллы и бонусы расчёт не может складывать с ценой."""
    return (row.get("currency") or "").strip().lower() in MONEY


def warnings(row: dict) -> list[str]:
    """Что стоит сказать человеку до того, как он заведёт эту карту."""
    out: list[str] = []
    if not is_money(row):
        out.append(f"Начисляется не рублями, а в «{row.get('currency')}» — расчёт считает деньги, "
                   "поэтому такую выгоду нельзя складывать с ценой наравне.")
    if "подписк" in (row.get("categories") or "").lower() or "подписк" in (row.get("note") or "").lower():
        out.append("Число категорий зависит от подписки — проверьте, сколько доступно именно вам.")
    if row.get("limit_hint"):
        out.append(f"Ограничение программы: {row['limit_hint']}.")
    return out
