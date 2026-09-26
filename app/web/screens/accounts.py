"""Кабинеты магазинов: человек входит в свои аккаунты — и корзина уезжает туда сама.

ЗАЧЕМ ОТДЕЛЬНЫЙ ЭКРАН. На «Магазинах» написано, откуда приложение берёт цены; это
разговор про источники. Здесь разговор другой и про другое: чей это аккаунт, что
даёт вход именно в него и куда поедет посчитанная корзина. Мешать их в одном
экране значило бы прятать вторую половину работы за первой.

ГДЕ ПРОИСХОДИТ ВХОД — решение владельца 17.09.2026. Он происходит В ПРИЛОЖЕНИИ:
кнопка открывает экран «Кабинет» (app/web/screens/cabinet.py), где показана
настоящая страница сети, поднятая браузером на нашем сервере. Человек нажимает по
ней как обычно, сам проходит проверку «я не робот», если она появится, и сам
вводит код из СМС.

ПОЧЕМУ НЕ ФОРМА «ТЕЛЕФОН И КОД» ПРЯМО ЗДЕСЬ. Потому что войти ею мы всё равно не
сможем: у Магнита запрос кода уходит телом {phoneNumber, captchaToken, aud} и без
пройденной капчи не уходит вовсе, а Пятёрочку и Самокат закрывает ServicePipe.
Капчу проходит человек — и лучшее, что можно для него сделать, это показать её
там, где он уже находится, вместо того чтобы отправлять его на чужой сайт.

ЧТО ХРАНИТСЯ ПОСЛЕ ВХОДА. Куки и localStorage сети — в рабочем месте человека,
тем же решением, которым туда лёг ключ кабинета ФНС (app/shopbrowser/store.py).
Ни пароля, ни кода из СМС: они набираются в чужой странице и до нас в разобранном
виде не доходят.

ЧТО ЗДЕСЬ МОЖНО СДЕЛАТЬ РУКАМИ. Четыре вещи, и все они обратимы:

    войти          — кнопка открывает кабинет сети внутри приложения;
    передать       — посчитанная корзина уезжает в этот магазин: сетям, дающим
                     ссылку на готовую корзину, — ссылкой; остальным — тем самым
                     браузером на сервере, под сохранённым входом;
    повторить      — доложить ТОЛЬКО то, что в прошлый раз не легло;
    забыть         — приложение забывает и подключение, и сохранённый вход. Доступ
                     это НЕ отзывает: пока сеть помнит вход, куки действуют, и
                     отзывают его выходом из аккаунта в самом магазине. Так и
                     сказано на экране.

ПОЧЕМУ У ХОДА ПЕРЕДАЧИ СВОЙ МАЛЕНЬКИЙ ОТВЕТ. Передача идёт минуту и больше, и
экран обязан показывать её вживую: «кладу 7 из 16: творог Простоквашино».
Перерисовывать ради этой строки всю страницу нельзя — она собирает наряд по всем
шести сетям и трижды ходит в справочники, а спрашивают её раз в две секунды.
Поэтому у хода отдельный адрес — тот же /accounts, но с «?going=магнит», — и
отвечает он не страницей, а тремя числами и списком позиций.

ПОЧЕМУ ВТОРАЯ ПЕРЕДАЧА НЕ ПУСКАЕТСЯ. Она положила бы всё в корзину второй раз.
Погашенной кнопки мало: экран мог быть открыт до нажатия или открыт дважды —
проверка стоит и здесь, у самой двери, и в пускателе (app/shopbrowser/cart.start).

ЧЕГО ЗДЕСЬ НЕТ И НЕ ПОЯВИТСЯ. Кнопки «заказать». Передача кончается наполненной
корзиной, а заказ оформляет человек — своим аккаунтом и своей картой. Это его
деньги, и последнее слово должно оставаться за ним; заодно любая наша ошибка в
расчёте остаётся видимой ошибкой, а не списанием.
"""
from __future__ import annotations

import logging

from datetime import datetime

from flask import jsonify, redirect, render_template, request

from app import cartplan, collector, repo, store_accounts
from app.shopbrowser import cart, collect
from app.shopbrowser import store as shopstore
from app.web import auth
from app.web.screens.basket import num, unit_label
from app.web.views import SCREEN_BY_KEY

log = logging.getLogger(__name__)

PATH = "/accounts"

# «Войти в Пятёрочка» — не опечатка, а то, что получается, если склеивать кнопку
# из названия справочника. Названия сетей склоняются, и винительный падеж у них
# разный: «в Ленту», но «в Магнит» и «в Дикси». Правило «женский род на -а меняет
# окончание» сюда не годится: под него попал бы и несклоняемый «Дикси». Поэтому
# список, а не правило, — шесть строк дешевле одного неверного обобщения.
INTO = {
    "magnit": "Магнит",
    "pyaterochka": "Пятёрочку",
    "samokat": "Самокат",
    "lenta": "Ленту",
    "vkusvill": "ВкусВилл",
    "dixy": "Дикси",
}


def _when(stamp: str | None) -> str | None:
    """«2026-09-17T13:19:53» → «17.09 в 13:19». Год и секунды человеку тут не нужны.

    Всё, что показывает этот экран, случилось на этой неделе или не случилось
    вовсе, а машинная метка читается как след отладки, а не как ответ.
    """
    if not stamp:
        return None
    text = str(stamp)
    try:
        return f"{text[8:10]}.{text[5:7]} в {text[11:16]}"
    except Exception:  # noqa: BLE001 — непонятная метка лучше пустоты
        return text


def _lines(store_code: str):
    """Позиции последней корзины, оценённые в этой сети.

    Берём тот же источник, что и дверь расширения (app/api._plan_lines): человек
    видит на экране «Корзина» ровно эту корзину, и наряд, собранный из другой,
    расходился бы с тем, что у него перед глазами.
    """
    from app import api

    try:
        lines, _note = api._plan_lines({}, store_code)
        return lines
    except Exception as exc:  # noqa: BLE001 — пустой наряд не повод ронять экран
        log.warning("позиции для наряда в %s не собрались (%s)", store_code, exc)
        return []


def _last(store_code: str) -> dict | None:
    """Отчёт о прошлой передаче с человеческой меткой времени."""
    got = collector.handover_report(store_code)
    if not got:
        return None
    return {**got, "at": _when(got.get("at"))}


def _going(code: str) -> dict | None:
    """Идёт ли передача прямо сейчас. Без этого экран выглядит неподвижным.

    Человек нажал «Передать корзину», ничего не изменилось — и он нажимает ещё
    раз, то есть кладёт всё в корзину дважды.

    Живой ход берём у cart.running, а не по одному только `finished_at`: сервер
    мог перезапуститься посреди наряда, и незакрытая отметка гасила бы кнопку
    вечно (см. cart.STALE_AFTER).
    """
    if not cart.running(code):
        return None
    got = cart.progress(code) or {}
    return _pulse(got, True)


def _pulse(got: dict, live: bool) -> dict:
    """Ход одной передачи числами: которую кладём, сколько разобрано, из скольких."""
    total = int(got.get("total") or 0)
    at = int(got.get("at") or 0)
    return {"at": min(at, total) if total else at,
            "done": int(got.get("done") or 0),
            "total": total,
            "now": got.get("now") or "",
            "note": got.get("note") or "",
            "going": bool(live),
            "since": _when(got.get("started_at"))}


def _rows(got: dict) -> list[dict]:
    """Отчёт по позициям человеческими словами: что легло, что нет и почему.

    Отдельно от отчёта коллектора (`_last`) нарочно. Тот помнит СОБЫТИЕ — «17.09
    в 18:42 легло 14, две нет» — и знает только артикулы; здесь же лежат названия,
    количества и заметка об округлении, то есть ровно то, по чему человек узнаёт
    свой творог. Одних артикулов на экране мало: «magnit-8841» ему не говорит
    ничего.
    """
    out = []
    for item in (got.get("items") or []):
        if not isinstance(item, dict):
            continue
        qty = item.get("qty")
        out.append({
            "sku": item.get("sku") or "",
            "name": item.get("name") or item.get("sku") or "—",
            "qty": (f"{num(qty)} {unit_label(item.get('unit'))}" if qty is not None else ""),
            "ok": bool(item.get("ok")),
            "why": item.get("why") or "",
            "note": item.get("note") or "",
            "earlier": bool(item.get("earlier")),
            # «Не знаю, легло или нет» — третий ответ, и рисовать его крестиком
            # значило бы сказать человеку «не легло» там, где оно, возможно, лежит.
            "unknown": bool(item.get("unknown")),
        })
    return out


def _report(code: str) -> dict | None:
    """Чем кончилась передача — по позициям. Ещё ни одной не было — None.

    ПУСТОЙ СПИСОК ПОЗИЦИЙ — ЭТО НЕ «НЕЧЕГО ПОКАЗАТЬ». Раньше отчёт без строк
    отбрасывался целиком, а вместе с ним и `note` — единственное место, где живёт
    причина. Между тем именно так передача и кончается в самых вероятных случаях:
    «сеть больше не считает вас вошедшим», «сеть встретила проверкой», «передача
    оборвалась на полуслове» — ни одной строки и одно объяснение. Человек нажимал
    «Передать корзину», видел зелёное «передача пошла», через пару секунд экран
    сам возвращался — и молчал: кнопка снова нажимается, корзина пуста, причины
    нет. Следующий шаг при этом известен и написан прямо в note.
    """
    got = cart.progress(code)
    if not got:
        return None
    rows = _rows(got)
    note = got.get("note") or ""
    if not rows and not note:
        return None
    ok = sum(1 for r in rows if r["ok"])
    return {"rows": rows, "ok": ok, "failed": len(rows) - ok,
            "note": note,
            "at": _when(got.get("finished_at") or got.get("started_at"))}


def _going_answer(code: str):
    """Ответ на «?going=магнит»: ход передачи без перерисовки всей страницы.

    Сюда экран стучится раз в пару секунд, пока идёт передача. Собирать ради
    этого целую страницу нельзя: она строит наряд по всем шести сетям и ходит в
    справочник товаров — на таком опросе сервер ляжет раньше, чем наполнится
    корзина.
    """
    if code not in store_accounts.ABILITIES:
        return jsonify({"ok": False, "going": False,
                        "note": "Эта сеть приложению незнакома."}), 400
    got = cart.progress(code) or {}
    answer = _pulse(got, cart.running(code))
    answer["ok"] = True
    answer["rows"] = _rows(got)
    return jsonify(answer)


def _collecting(code: str) -> dict | None:
    """Идёт ли сбор цен прямо сейчас."""
    got = collect.progress(code)
    if not got or got.get("finished_at"):
        return None
    return {"done": got.get("done") or 0, "total": got.get("total") or 0,
            "note": got.get("note") or ""}


def _row(store, conn) -> dict:
    """Одна сеть: состояние кабинета, что даёт вход и что готово уехать."""
    from app import handover as ho

    code = store.code
    by_link = ho.KIND_BY_STORE.get(code) == ho.LINK
    waiting = cartplan.get_pending(code)
    lines = _lines(code)

    plan = None
    built = None
    if not by_link:
        try:
            built = cartplan.build(code, lines, force=True)
            plan = {"count": len(built.lines), "total": built.total,
                    "unknown": len(built.unknown), "note": built.note,
                    # Округление показываем ДО нажатия, а не только в отчёте:
                    # человек, увидевший «0,7 кг → 1 упаковка» заранее, поправит
                    # количество сам; узнавший из чека — уже нет.
                    "rounded": [f"{ln.name}: {mark}" for ln in built.lines
                                if (mark := cart.rounding_of(ln))]}
        except Exception:  # noqa: BLE001 — наряд не повод ронять экран
            log.warning("наряд для %s не собрался", code, exc_info=True)

    return {
        "code": code,
        "name": store.name,
        "into": INTO.get(code, store.name),
        # Кнопку показываем, только когда есть что передавать: кнопка, которая
        # всегда кончается объяснением «передавать нечего», — это не кнопка.
        "can_send": bool(lines) if by_link else bool(plan and plan["count"]),
        "connected": conn.connected,
        "account": conn.account,
        "points": conn.points,
        "since": _when(conn.connected_at),
        "seen": _when(conn.last_seen_at),
        "gives": conn.gives if conn.connected else (),
        "promise": store_accounts.describe(code),
        "entry": store_accounts.entry_url(code),
        "coupons": collector.coupons(code) or [],
        "by_link": by_link,
        "plan": plan,
        "waiting": bool(waiting),
        # Вход живёт в окне магазина внутри приложения (app/web/screens/cabinet.py),
        # а сохранённый вход — в рабочем месте человека.
        "cabinet": f"/cabinet?store={code}",
        "login": shopstore.about(code),
        "going": _going(code),
        # Цены этих двух сетей сервер сам не видит: их собирает браузер в кабинете
        # человека (app/shopbrowser/collect.py). У остальных кнопки нет — там цены
        # приложение спрашивает у сети напрямую.
        "can_collect": code in collect.CHAINS,
        "collecting": _collecting(code),
        # Чем кончилась прошлая передача. Без неё человек нажал «Передать корзину»
        # и не узнал в приложении ничего: отчёт расширения уходил в журнал сервера.
        "last": _last(code),
        # Отчёт по позициям последней передачи — с названиями, а не артикулами.
        "report": _report(code),
        # Повторить можно только то, что не легло, и только когда ничего не идёт.
        # Считаем ТЕМ ЖЕ пересечением, каким потом отбирает _act: корзину после
        # передачи могли поправить, и кнопка «Повторить только их (3)» уводила бы
        # на «повторять нечего» или повторяла одну — то есть врала в лицо числом.
        "retry": len(_repeatable(code, built)),
    }


def _repeatable(code: str, built) -> list[str]:
    """Что из непроложенного ещё есть в сегодняшней корзине — и потому повторимо."""
    have = {str(line.sku) for line in (getattr(built, "lines", None) or [])}
    return [sku for sku in cart.failed_skus(code) if sku in have]


def page():
    if request.method == "POST":
        return _act()

    # Ход передачи спрашивают часто и коротко — отвечаем до того, как начнём
    # собирать страницу: наряды по шести сетям на каждый опрос никому не нужны.
    going = (request.args.get("going") or "").strip().lower()
    if going:
        return _going_answer(going)

    known = {s.code: s for s in repo.list_stores()}
    rows = []
    for conn in store_accounts.connections():
        store = known.get(conn.store_code)
        if store is None:
            continue
        rows.append(_row(store, conn))

    from app.shopbrowser import driver

    return render_template(
        "accounts.html",
        screen=SCREEN_BY_KEY["accounts"],
        rows=rows,
        ability_names=store_accounts.ABILITY_NAMES,
        have_browser=driver.available(),
        sent=request.args.get("sent"),
        prices=request.args.get("prices"),
        trouble=request.args.get("trouble"),
        busy=request.args.get("busy"),
        gone=request.args.get("gone"),
        resend=_resend(request.args.get("resend")),
    )


def _resend(code: str | None) -> dict | None:
    """Что сказать, когда человек просит положить уже положенное ещё раз."""
    code = (code or "").strip().lower()
    if code not in store_accounts.ABILITIES:
        return None
    sent = cart.already_sent(code)
    if not sent:
        return None
    known = {s.code: s.name for s in repo.list_stores()}
    return {"code": code, "name": known.get(code, code),
            "at": sent["finished_at"][11:16], "landed": sent["landed"],
            "retry": len(cart.failed_skus(code))}


def _act():
    """Нажатия: передать корзину, доложить непроложенное или забыть подключение."""
    from app import handover as ho

    do = request.form.get("do") or ""
    what, _, code = do.partition(":")
    if code not in store_accounts.ABILITIES:
        return redirect(PATH)

    if what == "forget":
        store_accounts.forget(code)
        # И сам сохранённый вход: оставить куки сети, погасив подключение, значило
        # бы держать ключ от кабинета, о котором приложение уже говорит «не подключён».
        shopstore.forget(code)
        cartplan.clear_pending(code)
        return redirect(PATH)

    if what == "prices":
        if shopstore.load(code) is None:
            return redirect(f"/cabinet?store={code}")
        collect.start(code, auth.current_phone() or "")
        return redirect(f"{PATH}?prices={code}")

    if what not in ("send", "retry"):
        return redirect(PATH)

    by_link = ho.KIND_BY_STORE.get(code) == ho.LINK
    if what == "retry" and by_link:
        # Сеть, принимающая корзину ссылкой, наряда не исполняет — повторять у
        # неё нечего, и кнопки такой на экране нет. Значит, форма пришла не оттуда.
        return redirect(PATH)

    # Вторая передача, пока идёт первая, кладёт всё в корзину ВТОРОЙ РАЗ. Экран
    # кнопку гасит, но гашение живёт в той странице, которую человек уже открыл, —
    # а открыть её он мог до нажатия или сразу в двух вкладках. Настоящая проверка
    # только здесь и в самом пускателе.
    if cart.running(code):
        return redirect(f"{PATH}?busy={code}")

    lines = _lines(code)
    if not lines:
        return redirect(f"{PATH}?trouble={code}")

    # Сеть, принимающая корзину одной ссылкой, получает её ссылкой: это быстрее
    # наряда и не листает чужую витрину.
    if by_link:
        try:
            plan = ho.for_store(code, lines)
            if plan is not None and plan.link:
                return redirect(plan.link)
        except Exception:  # noqa: BLE001 — молчание сети не повод ронять экран
            log.warning("корзину по прямой ссылке в %s собрать не вышло", code, exc_info=True)
        return redirect(f"{PATH}?trouble={code}")

    # Остальным корзину кладёт браузер на нашем сервере, под сохранённым входом.
    try:
        built = cartplan.build(code, lines, force=True)
    except Exception:  # noqa: BLE001
        log.warning("наряд для %s не собрался", code, exc_info=True)
        return redirect(f"{PATH}?trouble={code}")

    if not built.lines:
        return redirect(f"{PATH}?trouble={code}")

    if shopstore.load(code) is None:
        # Без сохранённого входа класть некуда: корзина у невошедшего своя на
        # каждое устройство и живёт до закрытия вкладки. Ведём в кабинет, а не
        # пишем «не получилось»: человеку нужен следующий шаг, а не диагноз.
        return redirect(f"/cabinet?store={code}")

    # Полная передача после удачной — та же беда, что и двойное нажатие: сеть кладёт
    # поверх лежащего. Спрашиваем, а не отказываем: человек мог очистить корзину
    # сам и хотеть положить заново. Решает он, ответом «да, ещё раз».
    again = bool(request.form.get("again"))
    if what == "send" and not again and cart.already_sent(code):
        return redirect(f"{PATH}?resend={code}")

    only = None
    if what == "retry":
        # Только то, что не легло, и только то, что ещё есть в сегодняшней корзине.
        # Прогнать наряд целиком — значит положить удавшееся второй раз, то есть
        # своими руками испортить человеку корзину. Отбор тот же, что и у числа на
        # кнопке (_repeatable), — иначе кнопка обещала бы одно, а делала другое.
        only = _repeatable(code, built)
        if not only:
            return redirect(f"{PATH}?gone={code}")

    if not cart.start(code, auth.current_phone() or "", built, only=only, again=again):
        return redirect(f"{PATH}?busy={code}")
    return redirect(f"{PATH}?sent={code}")




# Действия живут на адресе экрана (тот же приём, что в app/web/screens/result.py):
# Flask читает методы с самой view-функции, а functools.wraps в auth.needs_phone
# проносит атрибут через обёртку — карта страниц ради форм не растёт.
page.methods = ("GET", "POST")

__all__ = ["page", "PATH"]
