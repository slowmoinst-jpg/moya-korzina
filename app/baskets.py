"""Сборка корзины из истории покупок.

Три способа: два из раздела 5.3 спецификации и один из концепции 2.0 (экран 2.1):

- «как в прошлый раз» — копия последней покупки: берём самый свежий чек и переносим
  его позиции с теми же количествами;
- «по среднему за месяц» — сколько товара семья берёт за месяц в среднем. Считаем по числу
  месяцев, в которых вообще были покупки, а не по календарю: два чека в одном месяце дают
  сумму за месяц, а не среднее двух;
- «моя обычная корзина» — то, чему по циклу покупок пора в корзину на этой неделе
  (habits ниже): молоко, которое берут раз в пять дней, попадёт, а оливковое масло,
  купленное позавчера и уходящее за месяц, — нет. Количество — обычное за один раз.

Штучные округляем до целого — «полторы пачки» в корзине бессмысленны. Весовые оставляем
дробными с точностью до грамма.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from statistics import median

from app import repo

LAST = "history"
AVERAGE = "average"
USUAL = "usual"

# «Обычная корзина» собирается на неделю вперёд: товар попадает, если по его циклу
# он кончится в ближайшие USUAL_HORIZON_DAYS дней. Цикл длиннее USUAL_MAX_CYCLE_DAYS —
# это уже не привычная покупка, а редкая (соль, специи), и в недельную корзину её
# тащить не надо: она сама напомнит о себе в «Пора докупить».
USUAL_HORIZON_DAYS = 7
USUAL_MAX_CYCLE_DAYS = 45


def _round_qty(qty: float, unit: str | None) -> float:
    if (unit or "pcs") == "kg":
        return round(qty, 3)
    return float(round(qty))


def orderable_history(store_id: int | None = None) -> list[dict]:
    """История для корзины «Как обычно» — без покупок в сетях, где мы не собираем заказ.

    Новые чеки таких сетей и так не попадают в историю (app/chains.py), а этот
    фильтр убирает то, что успело лечь раньше: иначе «Как обычно» предлагала бы
    товары, которые приложение заказать не может. Строки без магазина остаются —
    их человек добавил сам, и судить их не по чему.

    Услуги из чека — поездка, аренда, штраф, «Сборка» заказа — тоже не товар: их не
    повторить и не сравнить по цене, а в «Истории заказов» они стояли с кнопкой
    «Повторить» (владелец, 21.10.2026). Судит app/catalog/nonfood.py по имени из чека:
    в нём граммовка, а товарный признак сильнее слова услуги.
    """
    from app import chains
    from app.catalog import nonfood

    allowed = chains.orderable()
    blocked = {s.id for s in repo.list_stores() if s.code not in allowed}
    rows = [r for r in repo.list_history(store_id=store_id)
            if r.get("product_id") and r.get("store_id") not in blocked]
    # Вердикт — раз на имя, а не на строку: имён в истории в разы меньше, чем строк.
    names = {r.get("raw_name") or r.get("product_name") for r in rows}
    services = {n for n in names if nonfood.is_service_name(n)}
    return [r for r in rows if (r.get("raw_name") or r.get("product_name")) not in services]


def last_purchase_items(store_id: int | None = None) -> tuple[str | None, list[dict]]:
    """Позиции самой свежей покупки: (дата, строки). Строки — product_id, qty, unit, name."""
    rows = orderable_history(store_id)
    if not rows:
        return None, []
    last_date = max(str(r["date"]) for r in rows)

    merged: dict[int, dict] = {}
    for row in rows:
        if str(row["date"]) != last_date:
            continue
        pid = int(row["product_id"])
        item = merged.setdefault(pid, {"product_id": pid, "qty": 0.0,
                                       "unit": row.get("unit") or "pcs",
                                       "name": row.get("product_name") or row.get("raw_name")})
        item["qty"] += float(row.get("qty") or 0)
    for item in merged.values():
        item["qty"] = _round_qty(item["qty"], item["unit"])
    return last_date, [i for i in merged.values() if i["qty"] > 0]


def average_month_items(store_id: int | None = None) -> tuple[int, list[dict]]:
    """Среднемесячное количество по каждому товару: (число месяцев, строки)."""
    rows = orderable_history(store_id)
    if not rows:
        return 0, []

    months: set[str] = set()
    totals: dict[int, dict] = defaultdict(lambda: {"qty": 0.0, "unit": "pcs", "name": ""})
    for row in rows:
        date = str(row.get("date") or "")
        if len(date) >= 7:
            months.add(date[:7])
        pid = int(row["product_id"])
        entry = totals[pid]
        entry["qty"] += float(row.get("qty") or 0)
        entry["unit"] = row.get("unit") or "pcs"
        entry["name"] = row.get("product_name") or row.get("raw_name")

    count = max(1, len(months))
    items = []
    for pid, entry in totals.items():
        qty = _round_qty(entry["qty"] / count, entry["unit"])
        if qty > 0:
            items.append({"product_id": pid, "qty": qty, "unit": entry["unit"], "name": entry["name"]})
    return len(months), items


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def habits(today: date | None = None) -> list[dict]:
    """Привычки покупок по каждому товару: как часто, сколько за раз и когда снова.

    Покупка — это день, а не чек: два чека за день (забыл хлеб, вернулся) — один
    заход, и считать их двумя значило бы вдвое укоротить цикл. Цикл — средний
    промежуток между заходами; у товара, купленного один раз, цикла нет.

    Строка: product_id, name, unit, times (сколько дней покупали), last (дата
    последней покупки), cycle (дней между покупками или None), since (дней с
    последней), qty (обычное количество за раз — медиана), due (пора докупить:
    с последней покупки прошло не меньше цикла), overdue (since / cycle).
    """
    today = today or date.today()
    per_day: dict[int, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    names: dict[int, str] = {}
    units: dict[int, str] = {}
    for row in orderable_history():
        day = _day(row.get("date"))
        if day is None:
            continue
        pid = int(row["product_id"])
        per_day[pid][day] += float(row.get("qty") or 0)
        names[pid] = row.get("product_name") or row.get("raw_name") or ""
        units[pid] = row.get("unit") or "pcs"

    out = []
    for pid, days in per_day.items():
        when = sorted(days)
        cycle = None
        if len(when) >= 2:
            cycle = max(1.0, (when[-1] - when[0]).days / (len(when) - 1))
        since = max(0, (today - when[-1]).days)
        bought = [q for q in days.values() if q > 0]
        qty = _round_qty(median(bought), units[pid]) if bought else 0.0
        out.append({"product_id": pid, "name": names[pid], "unit": units[pid],
                    "times": len(when), "last": when[-1].isoformat(),
                    "cycle": round(cycle, 1) if cycle else None, "since": since,
                    "qty": qty if qty > 0 else (0.001 if units[pid] == "kg" else 1.0),
                    "due": bool(cycle and since >= cycle),
                    "overdue": round(since / cycle, 2) if cycle else 0.0})
    out.sort(key=lambda h: (-h["times"], h["name"]))
    return out


def due_items(today: date | None = None) -> list[dict]:
    """«Пора докупить»: привычное, что не покупали дольше обычного. Самое просроченное сверху."""
    found = [h for h in habits(today) if h["due"]]
    found.sort(key=lambda h: (-h["overdue"], -h["times"], h["name"]))
    return found


def usual_items(today: date | None = None, horizon_days: int = USUAL_HORIZON_DAYS) -> list[dict]:
    """«Моя обычная корзина»: привычное, что по циклу кончится в ближайшие horizon_days.

    Берётся только то, что покупали хотя бы дважды и не реже раза в
    USUAL_MAX_CYCLE_DAYS: иначе неделя превращается в «всё, что когда-либо брали».
    """
    return [h for h in habits(today)
            if h["cycle"] and h["cycle"] <= USUAL_MAX_CYCLE_DAYS
            and h["since"] + horizon_days >= h["cycle"]]


def build_from_history(kind: str, name: str | None = None, store_id: int | None = None) -> dict:
    """Создаёт корзину из истории. kind: 'history' — как в прошлый раз, 'average' — по среднему,
    'usual' — моя обычная корзина по циклам покупок.

    Возвращает {'basket_id', 'items', 'months', 'date', 'skipped'}.
    """
    if kind == USUAL:
        items = [{"product_id": h["product_id"], "qty": h["qty"], "unit": h["unit"],
                  "name": h["name"]} for h in usual_items()]
        months, date_ = 0, None
        title = name or f"Моя обычная корзина ({datetime.now():%d.%m.%Y})"
        return _create(kind, title, items, months, date_)
    if kind == AVERAGE:
        months, items = average_month_items(store_id)
        date = None
        title = name or f"По среднему за месяц ({datetime.now():%d.%m.%Y})"
    else:
        kind = LAST
        date, items = last_purchase_items(store_id)
        months = 0
        title = name or (f"Как в прошлый раз ({date})" if date else "Как в прошлый раз")

    return _create(kind, title, items, months, date)


def _create(kind: str, title: str, items: list[dict], months: int, date: str | None) -> dict:
    if not items:
        return {"basket_id": None, "items": [], "months": months, "date": date, "skipped": 0}

    known = {p.id for p in repo.list_products(active_only=False)}
    usable = [i for i in items if i["product_id"] in known]

    # Схема базы знает два источника из истории; обычная корзина — тоже из истории,
    # и ради отметки перестраивать таблицу с CHECK не стоит: отметку никто не читает.
    basket_id = repo.create_basket(title, source=AVERAGE if kind == AVERAGE else LAST)
    for item in usable:
        repo.set_basket_item(basket_id, item["product_id"], float(item["qty"]))
    return {
        "basket_id": basket_id,
        "items": usable,
        "months": months,
        "date": date,
        "skipped": len(items) - len(usable),
    }


def regular_purchases(min_times: int = 2) -> list[dict]:
    """Товары, которые покупают регулярно: встречались минимум в min_times разных покупках."""
    rows = orderable_history()
    seen: dict[int, set[str]] = defaultdict(set)
    names: dict[int, str] = {}
    for row in rows:
        pid = int(row["product_id"])
        seen[pid].add(str(row.get("date") or ""))
        names[pid] = row.get("product_name") or row.get("raw_name") or ""
    return [{"product_id": pid, "name": names[pid], "times": len(dates)}
            for pid, dates in seen.items() if len(dates) >= min_times]


def missing_regulars(basket_id: int, min_times: int = 2) -> list[dict]:
    """Регулярные покупки, которых нет в этой корзине (раздел 5.3 спецификации)."""
    in_basket = {int(i["product_id"]) for i in repo.basket_items(basket_id)}
    return [r for r in regular_purchases(min_times) if r["product_id"] not in in_basket]
