from __future__ import annotations

import json

from flask import Blueprint, render_template

from app import repo

bp = Blueprint("phone", __name__, url_prefix="/phone")

# «Где?» — для «Всё во ВкусВилле», «Заказать в Магните». Падеж у названий сетей свой,
# и собирать его правилом значило бы однажды написать «в ВкусВилле».
WHERE = {
    "magnit": "в Магните", "vkusvill": "во ВкусВилле", "pyaterochka": "в Пятёрочке",
    "lenta": "в Ленте", "dixy": "в Дикси", "samokat": "в Самокате",
    "metro": "в METRO", "perekrestok": "в Перекрёстке",
}

# «Кого?» — для «Подключить Пятёрочку». Остальные названия не склоняются.
WHOM = {"pyaterochka": "Пятёрочку", "lenta": "Ленту"}

# Сколько товаров заказа видно сразу; остальное — «Ещё N товаров» (вариант Б).
SHOWN_LINES = 5

# Сколько заказов на странице истории; дальше — «Показать ещё».
ORDERS_PAGE = 20

# Какой заказ сейчас лежит в корзине: {"key": ключ заказа, "basket": номер корзины}.
# Нужен ровно для пометки «В корзине» в истории — по одному имени корзины этого не
# понять: две покупки в один день и в одной сети назывались бы одинаково.
REPEATED = "phone:repeated"


# ---------- слова и числа ----------
def where(code: str | None, name: str | None = None) -> str:
    return WHERE.get(code or "") or f"в {name or code or 'магазине'}"


def plural(n: int, one: str, few: str, many: str) -> str:
    tail = n % 100
    if 11 <= tail <= 14:
        return many
    last = n % 10
    return one if last == 1 else few if 2 <= last <= 4 else many


def goods(n: int) -> str:
    return f"{n} {plural(n, 'товар', 'товара', 'товаров')}"


def deliveries(n: int) -> str:
    return f"{n} {plural(n, 'доставка', 'доставки', 'доставок')}"


def qty_label(qty, unit: str | None) -> str:
    from app.web.screens.basket import num, unit_label

    return f"{num(qty)} {unit_label(unit)}"


def _step(unit: str | None) -> float:
    """Шаг «−» и «+»: штука для штучного, сто граммов для весового — как в веб-корзине."""
    return 0.1 if (unit or "pcs") == "kg" else 1.0


def _units() -> dict[int, str]:
    try:
        return {p.id: p.unit for p in repo.list_products(active_only=False) if p.id}
    except Exception:  # noqa: BLE001 — подпись единиц не повод ронять экран
        return {}


def _basket() -> dict | None:
    """Текущая корзина: та же, что открыта в вебе, иначе самая свежая.

    Веб помнит, какую корзину человек открыл последней (app/web/screens/basket.py,
    LAST_OPEN = «basket:last»), и голый /basket на компьютере показывает её. Телефон
    раньше брал только самую свежую: после «Повторить» прежняя корзина пропадала с
    экрана, хотя в вебе оставалась открытой, и телефон с компьютером могли считать
    разные корзины. Теперь источник один — настройка basket:last.

    Настройка могла остаться от удалённой корзины: тогда, как и в вебе, молча
    возвращаемся к самой свежей, а не показываем пустой экран.
    """
    baskets = repo.list_baskets()
    if not baskets:
        return None

    from app.web.screens.basket import LAST_OPEN

    try:
        last = int(repo.get_setting(LAST_OPEN) or 0)
    except (TypeError, ValueError):
        last = 0
    if last:
        remembered = next((b for b in baskets if int(b["id"]) == last), None)
        if remembered is not None:
            return remembered
    return baskets[0]


def _repeated() -> dict:
    try:
        data = json.loads(repo.get_setting(REPEATED) or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _render(template: str, **values):
    from app.web.screens.basket import rub

    return render_template(f"phone/{template}", rub=rub, goods=goods, where=where,
                           qty_label=qty_label, deliveries=deliveries, **values)


def _rub():
    from app.web.screens.basket import rub

    return rub


# ---------- вход в сеть ----------
def _login(code: str) -> str:
    """Годится ли вход в сеть: «» — да, «again» — войти снова, «connect» — не входили.

    Берём у «Сборки корзин» (app/cartrun.py), а не store_accounts.connection: флаг
    подключения там ставится отчётом о сборе и чаще погашен, чем нет (см.
    app/web/screens/stores.py, _saved_logins). Сеть без сохранённого входа корзину
    не примет, сколько бы ни горела галочка.
    """
    from app import cartrun

    try:
        return cartrun._login(code)
    except Exception:  # noqa: BLE001 — состояние входа не повод ронять экран
        return "connect"
