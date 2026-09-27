"""Заказы: покупки человека, из которых приложение узнаёт, что он берёт обычно.

ЧТО ЗДЕСЬ. Заказ — это один чек сети, где мы собираем заказы (app/chains.py). Чеки
приходят двумя путями: из ФНС («Загрузить чеки из ФНС») и добавленные вручную
(«Добавить чек вручную» — файл, QR-код, вставленный текст). По заказам собирается
корзина «Как обычно» и считается экономия «по сравнению с тем, сколько вы платили
раньше». Названия — по словарю docs/design/nazvaniya-2026-09-26.md.

ЧЕКИ ДРУГИХ МАГАЗИНОВ НЕ ПРЯЧУТСЯ. Аптеки, кафе, сети, где мы не собираем заказ,
в заказы не попадают, но и не пропадают молча: внизу одна строка «N чеков из
других магазинов» со списком магазинов. Человек видит, что чек не потерян, а отложен.

ЗАКАЗ — ЭТО ЧЕК, А НЕ ДЕНЬ. В истории у строки есть ключ чека (receipt_key), и
заказ собирается по нему: два похода в одну сеть за день — два заказа. Строки без
ключа (старые загрузки) по-прежнему склеиваются по дню и магазину.

ЧЕГО ЗДЕСЬ НЕТ. Подключение кабинета ФНС и разбор файлов живут на «Моих чеках»
(app/web/screens/receipts.py) — отсюда туда ведут кнопки, а сама загрузка из уже
подключённого кабинета запускается прямо здесь тем же фоновым заданием.
"""
from __future__ import annotations

import logging

from flask import flash, g, redirect, render_template, request

from app import baskets, repo
from app.web.screens.basket import rub
from app.web.screens.history import MONTHS, human_date
from app.web.views import SCREEN_BY_KEY

log = logging.getLogger(__name__)

PATH = SCREEN_BY_KEY["orders"].path

MONTH_NAMES = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
               "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")

SOURCE_FNS = "Чек из ФНС"
SOURCE_HAND = "Чек добавлен вручную"


def plural(n: int, one: str, few: str, many: str) -> str:
    tail = n % 100
    last = n % 10
    word = many if 11 <= tail <= 14 else one if last == 1 else few if 2 <= last <= 4 else many
    return f"{n} {word}"


def goods(n: int) -> str:
    return plural(n, "товар", "товара", "товаров")


def cheques(n: int) -> str:
    return plural(n, "чек", "чека", "чеков")


def _source_label(source: str | None) -> str:
    """Откуда заказ — словами человека. Кабинет ФНС (lkdr) — «из ФНС», остальное — вручную."""
    return SOURCE_FNS if (source or "").lower() in ("lkdr", "fns", "cabinet") else SOURCE_HAND


def _day(iso: str | None) -> str:
    """«2026-09-24» -> «24 сентября»; год — только если не текущий."""
    try:
        year, month, day = (int(x) for x in str(iso).split("-")[:3])
    except (ValueError, TypeError):
        return str(iso or "—")
    from datetime import date
    text = f"{day} {MONTHS[month - 1]}"
    return text if year == date.today().year else f"{text} {year}"


def orders() -> list[dict]:
    """Заказы от свежих к старым: по чеку, с составом."""
    sources = {r["key"]: r.get("source") for r in repo.imported_receipts()}
    out: list[dict] = []
    index: dict[tuple, int] = {}
    for row in baskets.orderable_history():
        key = row.get("receipt_key") or f"{row.get('date')}|{row.get('store_code')}"
        if key not in index:
            index[key] = len(out)
            out.append({"key": key, "date": row.get("date") or "", "day": _day(row.get("date")),
                        "store_code": row.get("store_code") or "",
                        "store_name": row.get("store_name") or "Магазин",
                        "source": _source_label(sources.get(row.get("receipt_key"))),
                        "lines": [], "total": 0.0})
        order = out[index[key]]
        order["lines"].append({"product_id": row.get("product_id"),
                               "name": row.get("product_name") or row.get("raw_name") or "—",
                               "qty": row.get("qty"), "total": row.get("total")})
        order["total"] += float(row.get("total") or 0)
    out.sort(key=lambda o: o["date"], reverse=True)
    return out


def by_month(items: list[dict]) -> list[tuple[str, list[dict]]]:
    from datetime import date

    groups: list[tuple[str, list[dict]]] = []
    for order in items:
        try:
            year, month = int(order["date"][:4]), int(order["date"][5:7])
            title = MONTH_NAMES[month - 1] + ("" if year == date.today().year else f" {year}")
        except (ValueError, IndexError):
            title = "Без даты"
        if not groups or groups[-1][0] != title:
            groups.append((title, []))
        groups[-1][1].append(order)
    return groups


def other_stores() -> dict:
    """Отложенные чеки других магазинов: всего и по магазину."""
    rows = repo.other_store_receipts()
    counts: dict[str, int] = {}
    for row in rows:
        name = (row.get("store") or "Другой магазин").strip()
        counts[name] = counts.get(name, 0) + 1
    shops = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"total": len(rows), "shops": shops}


def page():
    if request.method == "POST":
        return _act()
    items = orders()
    key = request.args.get("key")
    chosen = next((o for o in items if o["key"] == key), None) if key else None
    return render_template(
        "orders.html", screen=SCREEN_BY_KEY["orders"], months=by_month(items),
        count=len(items), chosen=chosen, other=other_stores(), fns=_fns_state(),
        receipts_path=SCREEN_BY_KEY["receipts"].path, show_other=request.args.get("other") == "1",
        rub=rub, goods=goods, cheques=cheques, human_date=human_date)


def _fns_state() -> dict:
    """Подключена ли ФНС и идёт ли сейчас загрузка — для кнопки «Загрузить чеки из ФНС»."""
    from app.fns import sync as cabinet
    from app.web.screens import receipts

    try:
        connected = cabinet.is_connected()
    except Exception:  # noqa: BLE001 — кабинет недоступен; кнопка поведёт подключать
        connected = False
    job = receipts._job_of(getattr(g, "phone", "") or "")
    running = bool(job and job.running)
    return {"connected": connected, "running": running, "saying": job.saying if running else ""}


def _act():
    what = (request.form.get("do") or "").strip()
    if what == "sync":
        from app.web.screens import receipts

        if not _fns_state()["connected"]:
            return redirect(SCREEN_BY_KEY["receipts"].path, 303)
        receipts._do_sync(g.phone)
        return redirect(PATH, 303)
    if what == "repeat":
        return _repeat(request.form.get("key") or "")
    log.warning("на экране заказов нажато неизвестное действие: %r", what[:40])
    return redirect(PATH, 303)


def _repeat(key: str):
    """«Добавить все товары в корзину»: заказ становится корзиной «Вручную»."""
    order = next((o for o in orders() if o["key"] == key), None)
    if order is None:
        flash("Такого заказа больше нет.", "warn")
        return redirect(PATH, 303)
    qty: dict[int, float] = {}
    for line in order["lines"]:
        if line["product_id"]:
            qty[int(line["product_id"])] = qty.get(int(line["product_id"]), 0.0) + float(line["qty"] or 1)
    if not qty:
        flash("В этом заказе нет товаров, которые можно положить в корзину.", "warn")
        return redirect(f"{PATH}?key={key}", 303)
    basket_id = repo.create_basket(f"Вручную: {order['store_name']}, {order['day']}", source="manual")
    for pid, amount in qty.items():
        repo.set_basket_item(basket_id, pid, amount)
    return redirect(f"{SCREEN_BY_KEY['basket'].path}?id={basket_id}", 303)


page.methods = ["GET", "POST"]
