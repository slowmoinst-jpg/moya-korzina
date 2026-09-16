"""Ответ на два вопроса: сколько покупок уже загружено и что ещё можно загрузить.

Почему это отдельный модуль, а не строчка на экране. «Загрузите чеки» — совет,
который человек не может выполнить, потому что не знает, чего именно не хватает.
Здесь считается конкретика: сколько чеков лежит, за какой срок, и сколько чеков
кабинет показывает, а мы ещё не взяли. Разница между описью кабинета и загруженным
(таблица receipts, см. repo) — это и есть «можно ещё».

Второй вопрос шире: кроме кабинета ФНС есть источники, до которых руки не дошли.
Их перечень со смыслом «что это добавит» лежит в SOURCES — он нужен человеку,
у которого кабинет уже вычерпан, а истории всё равно мало.
"""
from __future__ import annotations

from datetime import date as _date
from datetime import datetime

from app import repo
from app.db import query

# Источники, кроме кабинета ФНС. Порядок — по тому, сколько человек получит за усилие.
SOURCES: list[dict] = [
    {"code": "lkdr", "name": "Кабинет ФНС «Мои чеки онлайн»",
     "gives": "все чеки с позициями, включая офлайн-магазины",
     "how": "кнопка «Забрать все чеки» на экране «Мои чеки»"},
    {"code": "email", "name": "Письма доставок",
     "gives": "заказы Самоката, Ленты, ВкусВилла — там, где чек приходит письмом",
     "how": "открыть письмо, выделить, вставить в поле на экране «Мои чеки»"},
    {"code": "orders", "name": "Страница «Мои заказы» в магазине",
     "gives": "историю доставок за всё время, даже если писем уже нет",
     "how": "открыть список заказов, выделить, вставить"},
    {"code": "ofd", "name": "PDF или JSON от ОФД",
     "gives": "чек целиком со штрихкодами — самое точное сопоставление",
     "how": "загрузить файл на экране «История»"},
    {"code": "table", "name": "Выгрузка из личного кабинета магазина",
     "gives": "заказы таблицей, если магазин отдаёт CSV или XLSX",
     "how": "загрузить файл на экране «История»"},
    {"code": "paper", "name": "Бумажный чек",
     "gives": "покупку, которой нет нигде в цифре",
     "how": "распознать телефоном и вставить текстом"},
]


def _months_between(first: str, last: str) -> int:
    try:
        a = datetime.strptime(first, "%Y-%m-%d")
        b = datetime.strptime(last, "%Y-%m-%d")
    except (ValueError, TypeError):
        return 0
    return max(1, (b.year - a.year) * 12 + b.month - a.month + 1)


def loaded() -> dict:
    """Что уже лежит в истории."""
    row = query("SELECT COUNT(*) AS rows, MIN(date) AS first, MAX(date) AS last,"
                " ROUND(SUM(total), 2) AS total FROM purchase_history")[0]
    receipts = repo.imported_receipts()
    stores = query("SELECT COALESCE(s.name, 'без магазина') AS name, COUNT(*) AS rows"
                   " FROM purchase_history h LEFT JOIN stores s ON s.id = h.store_id"
                   " GROUP BY 1 ORDER BY 2 DESC")
    # Строки, загруженные до появления учёта чеков: они в истории есть, а какому
    # чеку принадлежат — неизвестно. Молчать о них нельзя, иначе счёт чеков
    # выглядит меньше правды.
    loose = query("SELECT COUNT(*) AS rows FROM purchase_history WHERE receipt_key IS NULL")[0]["rows"]
    return {
        "receipts": len(receipts),
        "rows": row["rows"] or 0,
        "rows_without_receipt": loose or 0,
        "total": row["total"] or 0.0,
        "first": row["first"],
        "last": row["last"],
        "months": _months_between(row["first"], row["last"]) if row["first"] else 0,
        "stores": [dict(s) for s in stores],
    }


def pending() -> dict:
    """Чеки, которые кабинет показывает, а мы ещё не взяли."""
    rows = repo.pending_receipts()
    dates = [r["date"] for r in rows if r["date"]]
    return {
        "receipts": len(rows),
        "total": round(sum(r["total"] or 0 for r in rows), 2),
        "first": min(dates) if dates else None,
        "last": max(dates) if dates else None,
        "sample": rows[:5],
    }


def stale_days() -> int | None:
    """Сколько дней прошло с последней покупки в истории."""
    last = loaded()["last"]
    if not last:
        return None
    try:
        return (_date.today() - datetime.strptime(last, "%Y-%m-%d").date()).days
    except ValueError:
        return None


def next_steps() -> list[dict]:
    """Что делать дальше — по состоянию, а не списком вообще всего.

    Первым идёт то, что даёт больше всего за одно действие: невзятые чеки из
    кабинета, если они есть; иначе — источники, которых человек ещё не касался.
    """
    used = {r["source"] for r in repo.imported_receipts() if r["source"]}
    left = pending()
    steps: list[dict] = []
    if left["receipts"]:
        steps.append({
            "code": "lkdr-pending",
            "name": f"Невзятые чеки из кабинета — {left['receipts']}",
            "gives": "позиции этих чеков попадут в историю",
            "how": "нажать «Забрать все чеки» ещё раз: дозагрузит только новое",
        })
    for source in SOURCES:
        if source["code"] == "lkdr" and ("lkdr" in used or left["receipts"]):
            continue
        if source["code"] in used:
            continue
        steps.append(source)
    return steps


def summary() -> dict:
    """Всё сразу — то, что показывает экран."""
    have, left = loaded(), pending()
    return {"loaded": have, "pending": left, "next": next_steps(),
            "stale_days": stale_days(), "headline": headline(have, left)}


def headline(have: dict | None = None, left: dict | None = None) -> str:
    """Одна строка в человеческих словах: сколько загружено и сколько осталось."""
    have = have if have is not None else loaded()
    left = left if left is not None else pending()

    if not have["rows"]:
        return "Покупок пока нет ни одной — загрузите чеки, и появится история."

    if have["receipts"]:
        head = f"Загружено {plural(have['receipts'], 'чек', 'чека', 'чеков')}"
        head += f", {plural(have['rows'], 'позиция', 'позиции', 'позиций')}"
    else:
        head = f"Загружено {plural(have['rows'], 'позиция', 'позиции', 'позиций')}"
    if have["first"] and have["last"]:
        head += (f" за период с {human(have['first'])} по {human(have['last'])}"
                 if have["first"] != have["last"] else f" за {human(have['last'])}")
    head += "."
    if left["receipts"]:
        head += (f" В кабинете видно ещё {plural(left['receipts'], 'чек', 'чека', 'чеков')}"
                 " — они пока не загружены.")
    return head


def short() -> str:
    """Та же мысль в одну строку — для подзаголовка экрана.

    Полную фразу подзаголовок повторять не должен: она стоит карточкой ниже, и
    два одинаковых предложения подряд читаются как сбой вёрстки.
    """
    have, left = loaded(), pending()
    if not have["rows"]:
        return "Загрузка чеков из сервиса ФНС"
    parts = []
    if have["receipts"]:
        parts.append(plural(have["receipts"], "чек", "чека", "чеков"))
    parts.append(plural(have["rows"], "позиция", "позиции", "позиций"))
    if have["last"]:
        parts.append(f"последняя покупка {human(have['last'])}")
    if left["receipts"]:
        parts.append(f"не загружено {left['receipts']}")
    return " · ".join(parts)


def plural(n: int, one: str, few: str, many: str) -> str:
    tail = n % 100
    if 11 <= tail <= 14:
        word = many
    elif n % 10 == 1:
        word = one
    elif 2 <= n % 10 <= 4:
        word = few
    else:
        word = many
    return f"{n} {word}"


def human(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return iso


__all__ = ["loaded", "pending", "next_steps", "summary", "headline", "short",
           "stale_days", "plural", "human", "SOURCES"]
