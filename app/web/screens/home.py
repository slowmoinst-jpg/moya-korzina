"""Главная: четыре шага настройки и состояние каждого.

Форма взята из выбранного направления «Цвет магазина»: не витрина с обещаниями, а
чек-лист. Человек открывает приложение и сразу видит, чего ему не хватает, чтобы
расчёт стал правдой, — и в каком шаге он застрял.

Порядок шагов не случаен и переставлять его нельзя: адрес задаёт цены, без цен
нечего считать; чеки дают, ЧТО считать; карты и акции — это последняя добавка к
сумме, и без первых двух она бессмысленна.
"""
from __future__ import annotations

import logging

from flask import render_template, request

from app import location as client_place
from app import repo
from app.web.views import SCREEN_BY_KEY

log = logging.getLogger(__name__)


def _loading_note(address: str | None) -> dict | None:
    """Идёт ли сейчас загрузка цен по адресу. None — сказать нечего.

    Молчать тут нельзя: обход точки — это минуты, и без строки человек видит
    пустые цены и решает, что приложение сломано, хотя оно ровно в эту секунду
    работает на него.
    """
    if not address:
        return None
    try:
        from app.catalog import store as catalog_store
        state = catalog_store.address_status(address)
    except Exception:  # noqa: BLE001 — очередь не повод ронять главную
        return None
    if not state:
        return None
    if state["status"] in ("waiting", "running"):
        return {"kind": "info", "text": f"Цены по адресу «{address}» сейчас загружаются. "
                                        "Это несколько минут."}
    if state["status"] == "failed":
        return {"kind": "warn", "text": f"Загрузка цен по адресу «{address}» не удалась: "
                                        f"{state.get('note') or 'без пояснения'}."}
    return None


def _distance(hub: dict) -> float:
    """Расстояние до магазина. Без него магазин уходит в конец, а не в начало.

    Сети отдают магазины не по близости: ответ Ленты по Екатеринбургу начинался
    с 9 110 м, а 394 м лежали третьими. Магазин без расстояния — не ближайший, и
    ставить его первым было бы хуже, чем не показывать вовсе.
    """
    try:
        return float(hub.get("distance"))
    except (TypeError, ValueError):
        return float("inf")


def _distance_text(hub: dict) -> str:
    """«394 м» и «9,1 км»: девять тысяч метров человек читает дольше, чем нужно."""
    metres = _distance(hub)
    if metres == float("inf"):
        return ""
    if metres < 1000:
        return f"{round(metres)} м"
    return f"{metres / 1000:.1f} км".replace(".", ",")


def _check(address: str) -> list[dict]:
    """Магазины рядом с адресом — проверка, что адрес вообще понят.

    Зачем это отдельным действием, а не при открытии страницы. Опечатка в адресе
    не видна: сеть просто не разберёт его, цены придут пустыми, и человек решит,
    что приложение сломано. Список рядом стоящих магазинов снимает этот вопрос до
    расчёта. Но спрашивать сети на каждом открытии главной нельзя — это секунды
    сети на ровном месте, поэтому проверка нажимается.

    Пустой ответ — тоже ответ, и важный: адрес не разобран или сети тут нет.
    """
    found: list[dict] = []
    for name, call in (("Лента", client_place.nearby),
                       ("Магнит", client_place.nearby_magnit)):
        try:
            near = call(address) or []
        except Exception as exc:  # noqa: BLE001 — молчание сети не повод ронять экран
            log.warning("%s: магазины рядом с «%s» не найдены (%s)", name, address, exc)
            near = []
        if not near:
            found.append({"chain": name, "shop": None, "far": ""})
            continue
        best = min(near, key=_distance)
        found.append({"chain": name,
                      "shop": best.get("address") or best.get("name") or "—",
                      "far": _distance_text(best)})
    return found


def page():
    address = client_place.address()
    receipts = len(repo.list_history() or [])
    products = len(repo.list_products() or [])
    cards = len(repo.list_cards() or [])

    steps = [
        {"n": 1, "title": "Куда везём", "done": bool(address),
         "now": address or "Адрес не указан",
         "why": "Адрес задаёт цены и наличие во всех сетях: без него цифры будут "
                "чужого города.",
         "href": "#address"},
        {"n": 2, "title": "Что покупаем", "done": receipts > 0,
         "now": f"{receipts} покупок в истории" if receipts else "Покупок пока нет",
         "why": "Чеки говорят, что именно вы берёте. По ним корзина собирается сама, "
                "а не вводится руками.",
         "href": SCREEN_BY_KEY["receipts"].path},
        {"n": 3, "title": "Товары опознаны", "done": products > 0,
         "now": f"{products} товаров" if products else "Товаров пока нет",
         "why": "Один и тот же творог называется в сетях по-разному. Пока товар не "
                "опознан, сравнивать нечего.",
         "href": SCREEN_BY_KEY["products"].path},
        {"n": 4, "title": "Карты и акции", "done": cards > 0,
         "now": f"{cards} карт" if cards else "Карт пока нет",
         "why": "Скидка по карте меняет ответ: бывает выгоднее взять дороже там, где "
                "выходит минимальный чек.",
         "href": SCREEN_BY_KEY["cards"].path},
    ]

    # Проверка адреса ходит в сети, поэтому идёт только по нажатию: ?check=1.
    checked = _check(address) if (address and request.args.get("check") == "1") else None

    return render_template(
        "home.html",
        screen=SCREEN_BY_KEY["home"],
        address=address,
        checked=checked,
        steps=steps,
        ready=all(s["done"] for s in steps),
        note=_loading_note(address),
        saved=request.args.get("saved") == "1",
    )


__all__ = ["page"]
