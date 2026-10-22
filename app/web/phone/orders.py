from __future__ import annotations

import json

from flask import redirect, request, url_for

from app import repo
from app.web.auth import needs_phone

from .common import (ORDERS_PAGE, REPEATED, SHOWN_LINES, _basket, _repeated,
                     _render, _units, bp, qty_label)


# ---------- История заказов ----------
@bp.route("/orders", methods=("GET", "POST"))
@needs_phone
def orders():
    from app.web.screens import orders as web_orders

    if request.method == "POST":
        return _repeat(request.form.get("key") or "")

    units = _units()
    current = _basket()
    repeated = _repeated()
    in_basket = repeated.get("key") if current and repeated.get("basket") == int(current["id"]) else None
    # Чеков у владельца больше полутора тысяч: страница со всеми сразу грузилась бы
    # долго и листалась бы бесконечно. Свежие — сразу, остальные — «Показать ещё».
    everything = web_orders.orders()
    limit = max(ORDERS_PAGE, request.args.get("n", type=int) or ORDERS_PAGE)
    shown = []
    for order in everything[:limit]:
        lines = [{"name": ln.get("name") or "—",
                  "qty": qty_label(ln.get("qty") or 0, units.get(ln.get("product_id"))),
                  "total": ln.get("total")} for ln in order["lines"]]
        shown.append({"key": order["key"], "date": order["date"], "day": order["day"],
                      "store_code": order["store_code"],
                      "store_name": order["store_name"], "total": order["total"],
                      "count": len(lines), "head": lines[:SHOWN_LINES],
                      "rest": lines[SHOWN_LINES:], "in_basket": order["key"] == in_basket})
    more = len(everything) - len(shown)
    # Месяц — заголовком над его заказами, как в веб-истории (тот же by_month).
    return _render("orders.html", tab=None, orders=shown, months=web_orders.by_month(shown),
                   more=more, next_n=limit + ORDERS_PAGE, fns=web_orders._fns_state())


def _repeat(key: str):
    """«Повторить»: товары заказа ложатся в новую корзину, она становится текущей."""
    from app.web.screens import orders as web_orders
    from app.web.screens.basket import _remember_open

    order = next((o for o in web_orders.orders() if o["key"] == key), None)
    if order is None:
        return redirect(url_for("phone.orders"), 303)
    qty: dict[int, float] = {}
    for line in order["lines"]:
        if line.get("product_id"):
            pid = int(line["product_id"])
            qty[pid] = qty.get(pid, 0.0) + float(line.get("qty") or 1)
    if not qty:
        return redirect(url_for("phone.orders"), 303)
    basket_id = repo.create_basket(f"Из заказа {order['day']}, {order['store_name']}", source="manual")
    for pid, amount in qty.items():
        repo.set_basket_item(basket_id, pid, amount)
    # Веб-корзина откроет ту же корзину: телефон и компьютер не должны спорить о текущей.
    _remember_open(basket_id)
    repo.set_setting(REPEATED, json.dumps({"key": key, "basket": basket_id}, ensure_ascii=False))
    return redirect(url_for("phone.basket"), 303)
