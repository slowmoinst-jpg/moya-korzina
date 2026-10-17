"""Сборка корзин: по строке на сеть — ход, что легло, чего нет в наличии, итог.

ЗАЧЕМ ОТДЕЛЬНЫЙ ЭКРАН. На «Результате» — ответ «где брать и сколько стоит», на
«Кабинетах» — вход и передача в одну сеть. Здесь одна корзина уезжает во все сети
сразу: нажатие пускает поток на каждую сеть (app/cartrun.py), а экран показывает,
что из этого вышло, сетью за сетью, и итог каждой с пометкой, чей он.

ДО НАЖАТИЯ ВИДНО, КУДА КОРЗИНА УЕДЕТ. Строка каждой сети рисуется сразу: «уедет, 12
поз.», «нужен вход», «приложение сюда не кладёт — и почему». Нажатие без этого было
бы ставкой вслепую: человек ждал бы корзину в Пятёрочке, куда сервер не пускают.

ПОЧЕМУ У ХОДА СВОЙ МАЛЕНЬКИЙ ОТВЕТ. Тот же приём, что на «Кабинетах»: страница
собирает строку каждой сети по корзине и справочникам, а спрашивают её раз в пару
секунд, пока идёт сборка. Поэтому опрос — тот же адрес с «?state=1», и отвечает он
строками из настройки и ходом передач, без нарядов.

ЧЕГО ЗДЕСЬ НЕТ. Кнопки «заказать» и оплаты: сборка кончается наполненной корзиной, а
заказ человек оформляет в кабинете сети сам, своим аккаунтом и своей картой.
"""
from __future__ import annotations

import logging

from flask import jsonify, redirect, render_template, request

from app import cartrun, repo
from app.web import auth
from app.web.screens.basket import num, plural_items, rub
from app.web.views import SCREEN_BY_KEY

log = logging.getLogger(__name__)

PATH = SCREEN_BY_KEY["carts"].path
ACCOUNTS = SCREEN_BY_KEY["accounts"].path

STATE_WORDS = {
    cartrun.IDLE: "ещё не собирали",
    cartrun.READY: "уедет по нажатию",
    cartrun.GOING: "собираю",
    cartrun.DONE: "собрано",
    cartrun.FAILED: "не легло",
    cartrun.REFUSED: "сеть отказала",
    cartrun.LOGIN: "нужен вход",
    cartrun.SENT: "уже передано",
    cartrun.CANT: "приложение сюда не кладёт",
    cartrun.EMPTY: "класть нечего",
}

# Цвет плашки состояния. Зелёный в этом интерфейсе значит выгоду и удачу — «собрано»;
# красный — только то, что требует внимания человека прямо сейчас.
STATE_TONE = {
    cartrun.READY: "info", cartrun.GOING: "info", cartrun.DONE: "ok",
    cartrun.FAILED: "bad", cartrun.REFUSED: "bad", cartrun.LOGIN: "warn",
    cartrun.SENT: "info",
}

# «Открыть корзину Лента» — то, что выходит, если клеить кнопку из справочника.
# Родительный падеж у сетей свой, и правило сюда не годится (несклоняемые «Дикси» и
# METRO), поэтому список — тот же приём, что INTO на «Кабинетах».
OF = {
    "magnit": "Магнита", "pyaterochka": "Пятёрочки", "samokat": "Самоката",
    "lenta": "Ленты", "vkusvill": "ВкусВилла", "dixy": "Дикси", "metro": "METRO",
    "perekrestok": "Перекрёстка",
}

ROAD_WORDS = {
    cartrun.LINK: "ссылка-корзина от сети",
    cartrun.BROWSER: "браузер приложения под вашим входом",
    cartrun.API: "метод корзины самой сети",
    cartrun.PHONE: "витрина закрыта для сервера",
}


def _when(stamp: str | None) -> str:
    """«2026-10-17T14:05:09» → «17.10 в 14:05»."""
    text = str(stamp or "")
    if len(text) < 16:
        return ""
    return f"{text[8:10]}.{text[5:7]} в {text[11:16]}"


def _total(total) -> dict | None:
    if total is None:
        return None
    return {"sum": total.sum, "network": total.source == cartrun.NETWORK,
            "source": "посчитала сеть" if total.source == cartrun.NETWORK else "наш расчёт",
            "at": _when(total.at), "goods": total.goods, "discount": total.discount,
            "delivery": total.delivery, "minimal": total.minimal, "count": total.count}


def _button(row) -> dict | None:
    """Кнопка у строки сети: собрать её одну. Ни одной — там, где собирать нечем.

    У сети, которую собирает только телефон, кнопки нет: сервер туда не кладёт, и
    нажатие кончилось бы словами «приложение сюда не кладёт» вместо её итога.
    """
    code = row.code
    if row.road == cartrun.PHONE:
        return None
    if row.state in (cartrun.SENT,) or (row.state == cartrun.DONE and row.road != cartrun.LINK):
        # Сеть кладёт поверх лежащего — целиком ещё раз только после очистки корзины.
        return {"do": f"again:{code}", "label": "Корзину в сети очистил — положить ещё раз"}
    if row.state == cartrun.DONE and row.road == cartrun.LINK:
        return {"do": f"again:{code}", "label": "Новая ссылка"}
    if row.state in (cartrun.FAILED, cartrun.REFUSED):
        return {"do": f"one:{code}", "label": "Собрать ещё раз"}
    return None


def _view(row) -> dict:
    """Строка сети словами для шаблона."""
    going = row.state == cartrun.GOING
    pulse = cartrun.live(row) if going else None
    missing = list(row.missing or [])
    return {
        "code": row.code, "name": row.name or row.code, "road": row.road,
        "of": OF.get(row.code, row.name or row.code),
        "road_words": ROAD_WORDS.get(row.road, ""),
        "state": row.state, "state_words": STATE_WORDS.get(row.state, row.state),
        "tone": STATE_TONE.get(row.state, ""),
        "note": row.note, "planned": row.planned,
        "landed": list(row.landed or []), "missing": missing,
        "gone": sum(1 for item in missing if item.get("gone")),
        "unknown": list(row.unknown or []), "changes": list(row.changes or []),
        "total": _total(row.total), "link": row.link,
        "by_phone": row.by == cartrun.DEVICE,
        "when": _when(row.finished_at or row.started_at),
        "going": going, "live": pulse,
        "login": row.login, "button": _button(row),
        "accounts": ACCOUNTS,
    }


# Чем кончилась сборка — это ответ, и он держится, пока его не сменит следующая
# сборка. «Нужен вход», «класть нечего», «уже передано» — не ответ, а условие, и оно
# меняется без нас: человек вошёл на «Кабинетах» — и строка обязана это увидеть, а не
# звать его «Подключить» второй раз.
ANSWERS = (cartrun.DONE, cartrun.FAILED, cartrun.REFUSED)


def _merge(basket_id: int) -> list:
    """Строка на сеть: идущая и ответ последней сборки этой корзины — как есть, прочие — что будет."""
    stored = {row.code: row for row in cartrun.rows()}
    ahead = {row.code: row for row in cartrun.preview(basket_id)}
    out = []
    for code in cartrun.networks():
        mine = stored.get(code)
        if mine is not None and (cartrun.alive(mine) or (
                mine.state in ANSWERS and mine.basket_id == basket_id)):
            out.append(mine)
        elif code in ahead:
            out.append(ahead[code])
        elif mine is not None:
            out.append(mine)
    return out


def _state_answer():
    """Ответ на «?state=1»: ход сборки без перерисовки страницы."""
    rows = []
    for row in cartrun.rows():
        pulse = cartrun.live(row) if row.state == cartrun.GOING else None
        rows.append({"code": row.code, "going": row.state == cartrun.GOING,
                     "state": row.state, "planned": row.planned, **(pulse or {})})
    return jsonify({"ok": True, "going": any(r["going"] for r in rows), "rows": rows})


def page():
    if request.method == "POST":
        return _act()
    if request.args.get("state"):
        return _state_answer()

    baskets = repo.list_baskets()
    basket_id = request.args.get("basket", type=int)
    if not basket_id and baskets:
        basket_id = int(baskets[0]["id"])
    basket = next((b for b in baskets if int(b["id"]) == (basket_id or 0)), None)
    if basket is None:
        return render_template("carts.html", screen=SCREEN_BY_KEY["carts"], basket=None,
                               basket_path=SCREEN_BY_KEY["basket"].path)

    rows = [_view(row) for row in _merge(int(basket["id"]))]
    ready = [r for r in rows if r["state"] == cartrun.READY]
    return render_template(
        "carts.html", screen=SCREEN_BY_KEY["carts"], basket=basket,
        positions=len(repo.basket_items(int(basket["id"]))),
        rows=rows, ready=ready, going=any(r["going"] for r in rows),
        result_path=SCREEN_BY_KEY["result"].path,
        basket_path=SCREEN_BY_KEY["basket"].path,
        rub=rub, num=num, plural_items=plural_items)


def _act():
    """Нажатия: собрать во всех, собрать одну сеть, положить ещё раз после очистки."""
    basket_id = request.form.get("basket", type=int)
    if not basket_id:
        return redirect(PATH)
    do = request.form.get("do") or ""
    what, _, code = do.partition(":")
    phone = auth.current_phone() or ""
    if what == "all":
        cartrun.start(phone, basket_id)
    elif what in ("one", "again") and code in cartrun.networks():
        cartrun.start(phone, basket_id, only=code, again=what == "again")
    return redirect(f"{PATH}?basket={basket_id}")


# Действия живут на адресе экрана (тот же приём, что в app/web/screens/result.py).
page.methods = ("GET", "POST")

__all__ = ["page", "PATH"]
