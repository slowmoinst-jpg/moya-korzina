from __future__ import annotations

import logging

from flask import redirect, request, url_for

from app import cartrun, faster, freshness, location, netprice, repo
from app.optimizer import rank_key
from app.web import device
from app.web.auth import needs_phone

from .common import WHOM, _basket, _login, _render, bp, deliveries, goods, where

log = logging.getLogger(__name__)


# ---------- Где дешевле ----------
@bp.route("/cheaper", methods=("GET", "POST"))
@needs_phone
def cheaper():
    from app.web.screens import result

    current = _basket()
    if current is None:
        return _render("cheaper.html", tab=None, basket=None)
    basket_id = int(current["id"])
    if request.method == "POST":
        return _order(basket_id)

    bundle = result._bundle(basket_id)
    variants = bundle["variants"]
    if not variants:
        return _render("cheaper.html", tab=None, basket=current, variants=[])

    # Итоги кабинетов читаются один раз на страницу: они лежат одной настройкой
    # (app/cartrun), и спрашивать их на каждое место значило бы разбирать её заново.
    placed, best_n = _ranked(basket_id, bundle)
    every = [p.variant for p in placed]
    best = every[best_n]
    picked = request.args.get("pick", type=int)
    on_n = picked if picked is not None and 0 <= picked < len(every) else best_n
    chosen = every[on_n]

    # Товары корзины и точка человека — для подписи «витрина у адреса»: цена должна
    # быть снята по ЭТИМ товарам и у ЭТОЙ точки, а не вообще когда-то в сети.
    product_ids = [int(item["product_id"]) for item in repo.basket_items(basket_id)]
    point_key = location.point_key()
    order = list(range(len(placed)))
    if any(p.net for p in placed):
        # Итоги сетей сдвинули цены: порядок на экране — по новым, а номер места (pick)
        # остаётся индексом в _places: по нему понимает «Заказать» (_order).
        order.sort(key=lambda n: (len(every[n].missing_products), float(every[n].total)))
    places = []
    for n in order:
        variant = every[n]
        size = len(getattr(variant, "stores", None) or [])
        diff = round(float(variant.total) - float(best.total), 2)
        missing = len(getattr(variant, "missing_products", None) or [])
        if missing:
            sub = f"{deliveries(size)} · нет {goods(missing)}"
        elif variant is not best and diff < 0:
            sub = f"дешевле на {result.rub(-diff)}, но {deliveries(size)}"
        else:
            sub = deliveries(size)
        places.append({"n": n, "title": _title(variant), "total": variant.total, "sub": sub,
                       "codes": [s.store_code for s in variant.stores],
                       "source": _source(placed[n], product_ids, point_key),
                       "best": n == best_n, "on": n == on_n})

    figures = result._figures(chosen, bundle["baseline"])
    parts = []
    for store in (getattr(chosen, "stores", None) or []):
        parts.append(_part(store))
    shown = placed[on_n]
    return _render(
        "cheaper.html", tab=None, basket=current, variants=variants, places=places,
        fast=_fast(every, bundle.get("positions") or 0),
        nets=any(p.net for p in placed),
        chosen={"title": _title(chosen), "total": chosen.total, "best": on_n == best_n,
                # Итог сети, по которому назначена цена выбранного места: экран
                # называет его, а не наши «товары, скидка, доставка» (netprice.apply).
                "net": ({"sum": shown.net.sum, "discount": shown.net.discount,
                         "delivery": shown.net.delivery, "cashback": shown.cashback}
                        if shown.net else None),
                "codes": [s.store_code for s in chosen.stores],
                # Вся корзина в одну сеть — телефон может положить её сам («Собрать»
                # в окне сети кладёт корзину целиком). Долю разбивки он так не положит.
                "whole": len(chosen.stores) == 1,
                "figures": figures,
                "base": result._base_words(chosen, bundle.get("from_receipts")),
                "complete": result._completeness(chosen, bundle["positions"]),
                "missing": list(getattr(chosen, "missing_products", None) or []),
                "subtotal": sum(float(s.subtotal or 0) for s in chosen.stores),
                "delivery": sum(float(s.delivery or 0) for s in chosen.stores),
                "discount": sum(float(s.discount or 0) for s in chosen.stores),
                "parts": parts},
        on_phone=device.on_phone(),
        sent=request.args.get("sent"), why=request.args.get("why"),
        why_store=request.args.get("store"), pick=picked,
        # Сколько позиций осталось вне корзины сети — вместе с честным «собрано».
        left=request.args.get("left"))


def _places(basket_id: int, bundle: dict) -> list:
    """Каждое место, где можно взять корзину: расчёт «Результата» плюс все сети целиком.

    Расчёт отдаёт три лучших варианта вперемешку, а владелец просил видеть, «сколько
    корзина стоит в каждом месте». Поэтому к ним добавляется вся корзина в каждой
    сети — тем же оптимизатором и на тех же входах, что в service.calculate, только
    одним магазином. Сеть, где нет ни одного товара корзины, местом не считается.
    Список кэшируется вместе с расчётом «Результата» и сбрасывается вместе с ним.
    """
    if "phone_places" in bundle:
        return bundle["phone_places"]
    from app import config, handover, service
    from app.optimizer import optimize

    places = list(bundle["variants"])
    try:
        stores = repo.list_stores()
        by_product = service.baseline_by_product(basket_id)
        singles = optimize(
            lines=service.build_basket_lines(basket_id), stores=stores,
            offers=service.offers_map(), baseline=round(sum(by_product.values()), 2),
            baseline_lines=by_product,
            penalty=float(config.get("extra_order_penalty_rub", 150.0)),
            top_n=len(stores), max_stores=1, handover=handover.penalty_by_store())
        service._resolve_card_names(singles)
        seen = {tuple(sorted(s.store_code for s in v.stores)) for v in places}
        positions = int(bundle.get("positions") or 0)
        for variant in singles:
            key = tuple(sorted(s.store_code for s in variant.stores))
            if key in seen or len(variant.missing_products) >= positions:
                continue
            seen.add(key)
            places.append(variant)
    except Exception:  # noqa: BLE001 — без досчёта остаются варианты «Результата»
        log.warning("итог по каждой сети не посчитался", exc_info=True)
    # Сначала полные, потом с недостающими; внутри — от дешёвого к дорогому.
    places.sort(key=lambda v: (len(v.missing_products), float(v.total)))
    bundle["phone_places"] = places
    return places


def _fast(every: list, positions: int) -> dict:
    """Блок «Быстрее»: самый дешёвый вариант, где вся корзина в одной сети.

    Своего правила здесь нет — оно живёт в app/faster.py и проверяется без браузера.
    Здесь только слова для экрана: как называется сеть, что сказать про срок и что
    про самовывоз. Срок не обещаем ни в каком варианте: его у нас нет ни от одной
    сети, и выдуманные минуты были бы обещанием, которого мы не выполним.
    """
    found = faster.fastest(every, positions)
    variant = found.get("variant")
    if variant is None:
        return found
    store = (getattr(variant, "stores", None) or [None])[0]
    code = str(getattr(store, "store_code", "") or "") if store is not None else ""
    found["code"] = code
    found["title"] = _title(variant)
    found["pickup"] = faster.pickup_line(code)
    found["note"] = "без обещания срока — когда привезёт, решает сама сеть"
    return found


def _cabinets(basket_id: int) -> dict:
    """Итоги кабинетов ТЕКУЩЕЙ корзины по сетям: суммы, которые посчитала сама сеть.

    Это самый достоверный источник на экране: сеть написала итог своей корзины с
    учётом карты и её скидок (app/carttotal.Total.source). Но строка сборки живёт
    в настройке рабочего места и переживает правку корзины: без проверки «та ли
    корзина и тот ли её состав» подпись «корзина кабинета» показывала бы итог
    ПРЕЖНЕЙ корзины как текущий. Правило то же, что у экрана «Корзина»
    (app/web/phone/basket.py, _run_view): basket_id и stamp обязаны совпасть.
    Нет строки — нет и подписи; досчитывать за сеть здесь нечего.
    """
    try:
        stamp = cartrun.stamp_of(basket_id)
        return {row.code: row for row in cartrun.rows()
                if row.state == cartrun.DONE
                and row.basket_id == basket_id
                and row.stamp == stamp}
    except Exception:  # noqa: BLE001 — без итогов кабинетов остаются прочие подписи
        log.warning("итоги кабинетов не прочитались", exc_info=True)
        return {}


def _ranked(basket_id: int, bundle: dict) -> tuple[list[netprice.Place], int]:
    """Места экрана с ценой сети там, где она годится, и номер того, что советуем.

    Номер места — его индекс в `_places`: по нему экран и «Заказать» понимают друг друга
    (pick), и он не должен прыгать оттого, что итог сети переставил места на экране.
    Рекомендуем тем же правилом, что и без итогов сетей (result._chosen), но среди
    мест с пересчитанными ценами: итог сети мог сделать дешёвым то место, что в тройку
    расчёта не попало.
    """
    from app.web.screens import result

    every = _places(basket_id, bundle)
    placed = netprice.apply(every, _cabinets(basket_id), int(bundle.get("positions") or 0),
                            float(bundle.get("baseline") or 0.0))
    if any(p.net for p in placed):
        pool = sorted((p.variant for p in placed), key=rank_key)
    else:
        pool = bundle["variants"]               # итогов сетей нет — совет, как был
    best, _switch = result._chosen(pool, None)
    return placed, next((n for n, p in enumerate(placed) if p.variant is best), 0)


def _source(place: netprice.Place, product_ids: list[int] | None = None,
            point_key: str | None = None) -> str:
    """Откуда цена этого места и когда она снята — одной подписью под ценой.

    Честность по убыванию, как в плане Z3+4: сумма из кабинета сети (её писала сама
    сеть) важнее витрины у адреса, витрина важнее кэша. Чего нет — о том и говорим:
    «свежесть неизвестна» или «проверяется телефоном», а не пустое место.

    «Корзина кабинета» — только когда цена места ВЗЯТА из итога сети (netprice.apply).
    Итог, который в цену не пошёл (легло не всё, в корзине лишнее, устарел), цену не
    подписывает: под нашей ценой стоит «витрина» или «кэш», а рядом — почему итог сети
    не взят.

    product_ids/point_key — про какую корзину и какую точку спрашивать момент цены:
    витрина у адреса это снимки товаров корзины у пина человека (repo.last_price_moment).
    """
    if place.net is not None:
        when = _moment_text(place.net.at)
        return f"корзина кабинета, {when}" if when else "корзина кабинета, свежесть неизвестна"
    parts: list[str] = []
    for store in (getattr(place.variant, "stores", None) or []):
        code = str(getattr(store, "store_code", "") or "")
        if not code:
            continue
        line = _one_source(code, product_ids, point_key)
        if line and line not in parts:      # две сети с одной подписью — не дважды
            parts.append(line)
    if place.why:
        parts.append(f"итог сети не взят: {place.why}")
    return " · ".join(parts)


def _one_source(code: str, product_ids: list[int] | None = None,
                point_key: str | None = None) -> str:
    """Одна сеть: чья это цена и когда снята. Момент не выдумываем — его нет, так и скажем."""
    stamp = _price_moment(code, product_ids, point_key)
    if not stamp:
        return "проверяется телефоном" if _by_phone(code) else "свежесть неизвестна"
    if code in location.ADDRESS_STORES:
        when = _moment_text(stamp)
        return f"витрина у адреса, {when}" if when else "свежесть неизвестна"
    day = _day_text(stamp)
    return f"кэш, {day}" if day else "свежесть неизвестна"


def _price_moment(code: str, product_ids: list[int] | None = None,
                  point_key: str | None = None) -> str | None:
    """Когда сеть последний раз отдавала ЖИВУЮ цену по товарам корзины у этой точки.

    None — живых цен у сети нет (справочник CSV не в счёт), товаров корзины среди
    них нет или сети нет в базе. point_key=None — снимки без точки: цену чужого
    магазина за свою не выдаём (app/repo.last_price_moment).
    """
    store = repo.get_store(code)
    if store is None:
        return None
    try:
        return repo.last_price_moment(int(store.id), product_ids=product_ids,
                                      point_key=point_key)
    except Exception:  # noqa: BLE001 — подпись свежести не повод ронять экран
        log.warning("%s: момент последней цены не прочитался", code, exc_info=True)
        return None


def _moment_text(stamp) -> str | None:
    """«21.10 02:14» из отметки времени. Нет отметки — None, а не «сейчас»."""
    moment = freshness.moment(stamp) if stamp else None
    return moment.strftime("%d.%m %H:%M") if moment else None


def _day_text(stamp) -> str | None:
    """«21.10» — для кэша: у него важна дата, а не минута."""
    moment = freshness.moment(stamp) if stamp else None
    return moment.strftime("%d.%m") if moment else None


def _by_phone(code: str) -> bool:
    """Сеть, до витрины которой сервер не доходит: цену приносит только телефон.

    Списка сетей здесь нет нарочно: правило одно с передачей корзины (app/cartrun,
    road -> PHONE) — это Пятёрочка, Самокат, Дикси и Перекрёсток из
    store_accounts.ABILITIES, где наполнение корзины с сервера недоступно.
    """
    try:
        return cartrun.road(code) == cartrun.PHONE
    except Exception:  # noqa: BLE001 — незнакомая сеть не повод ронять экран
        return False


def _title(variant) -> str:
    """«Всё во ВкусВилле» или «Часть в Магните, часть во ВкусВилле»."""
    parts = list(getattr(variant, "stores", None) or [])
    if len(parts) == 1:
        return "Всё " + where(parts[0].store_code, parts[0].store_name)
    if len(parts) == 2:
        return (f"Часть {where(parts[0].store_code, parts[0].store_name)}, "
                f"часть {where(parts[1].store_code, parts[1].store_name)}")
    return " + ".join(p.store_name for p in parts)


def _part(store) -> dict:
    """Одна сеть выбранного варианта: сколько и как туда отдать корзину."""
    from app import handover as ho

    code = store.store_code
    kind = ho.KIND_BY_STORE.get(code, ho.LIST)
    by_link = kind == ho.LINK
    login = "" if by_link else _login(code)
    return {"code": code, "name": store.store_name, "where": where(code, store.store_name),
            "whom": WHOM.get(code) or store.store_name,
            "total": store.total, "count": len(getattr(store, "lines", None) or []),
            "by_link": by_link,
            # Ссылкой сеть отдаёт корзину и без входа; остальным нужен вход человека.
            "needs_login": bool(login), "again": login == "again"}


def _order(basket_id: int):
    """«Заказать»: положить корзину в магазин и открыть её там.

    Три дороги — те же, что у веб-«Результата» (app/web/screens/result.py, _act):
    ссылка сети на готовую корзину (ВкусВилл, Лента); корзина под сохранённым входом
    человека, которую кладёт браузер сервера; вход, если его ещё нет. Внутри
    приложения на телефоне сеть без ссылки открывается у него самого (окно сети,
    кнопка «Собрать») — это делает скрипт страницы до отправки формы, а сюда такой
    запрос приходит только из браузера или из старой сборки приложения.

    ПУСТОЙ НАРЯД — ОТДЕЛЬНЫЙ ОТВЕТ. «Не собралась» и «класть нечего» — разное, и
    человеку нужно второе: `why=gone`, если товаров нет в наличии, `why=unknown`,
    если сеть их не знает (app/cartplan.build). `why=busy` остаётся только для
    честного «передача уже идёт» и сбоя (ревью 18.10.2026, находка №12).
    """
    from app import cartplan
    from app import handover as ho
    from app.shopbrowser import cart
    from app.shopbrowser import store as shopstore
    from app.web import auth
    from app.web.screens import result

    code = (request.form.get("store") or "").strip()
    pick = request.form.get("pick", type=int)
    back = url_for("phone.cheaper", **({"pick": pick} if pick is not None else {}))
    bundle = result._bundle(basket_id)
    variants = bundle["variants"]
    if not variants or not code:
        return redirect(back, 303)
    # То же место, что на экране: без pick «Заказать» относится к рекомендуемому, а оно
    # зависит от итогов сетей (_ranked), не только от нашего расчёта.
    placed, best_n = _ranked(basket_id, bundle)
    chosen = placed[pick if pick is not None and 0 <= pick < len(placed) else best_n].variant
    lines = next((s.lines for s in chosen.stores if s.store_code == code), None)
    if not lines:
        return redirect(back, 303)

    sep = "&" if "?" in back else "?"
    if ho.KIND_BY_STORE.get(code, ho.LIST) == ho.LINK:
        try:
            plan = ho.for_store(code, lines)
            if plan is not None and plan.link:
                return redirect(plan.link)
        except Exception:  # noqa: BLE001 — сеть не ответила; скажем об этом на экране
            log.warning("ссылка на корзину %s не собралась", code, exc_info=True)
        return redirect(f"{back}{sep}why=link&store={code}", 303)

    if shopstore.load(code) is None:
        return redirect(url_for("phone.settings", connect=code), 303)
    why_not = cartplan.why_not(code)
    if why_not:
        return redirect(f"{back}{sep}why=closed&store={code}", 303)
    try:
        plan = cartplan.build(code, lines, force=True, verify_prices=True)
        if not plan.lines:
            # Пустой наряд — не «корзина уже собирается или не собралась». Класть
            # нечего, и человеку нужно именно это: «нет в наличии» или «товары сети
            # незнакомы». Прежний ответ отправлял пустой наряд в why=busy и врал о
            # том, что корзина где-то собирается (ревью 18.10.2026, находка №12).
            #
            # «Нет в наличии» — только когда ВСЕ позиции ушли в gone: в наряде может
            # быть и то и другое разом, и тогда текст про наличие врал бы про
            # незнакомые позиции (adversarial-проверка 18.10.2026, T11).
            why = "gone" if plan.gone and not plan.unknown else "unknown"
            return redirect(f"{back}{sep}why={why}&store={code}", 303)
        if cart.start(code, auth.current_phone() or "", plan):
            sent = f"{back}{sep}sent={code}"
            if plan.unknown or plan.gone:
                # Часть позиций в корзину не уехала — сказать об этом, а не молчать:
                # человек иначе ищет их в магазине и не находит.
                sent += f"&left={len(plan.unknown) + len(plan.gone)}"
            return redirect(sent, 303)
    except Exception:  # noqa: BLE001
        log.warning("корзина в %s не ушла", code, exc_info=True)
    return redirect(f"{back}{sep}why=busy&store={code}", 303)
