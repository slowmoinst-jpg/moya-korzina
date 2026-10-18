"""Связи: один товар — разные названия в сетях.

САМЫЙ ОТВЕТСТВЕННЫЙ ЭКРАН ПРИЛОЖЕНИЯ, и это не преувеличение. Подтверждённое здесь
сопоставление определяет, что человек потом увидит в корзине и за что заплатит. Ошибка
не выглядит ошибкой: «Страчателла 200 г», подтверждённая мороженым «Maxibuo страчателла
92 г», даёт красивую цену и неправильный товар в заказе. Поэтому:

  · рядом с каждым кандидатом написано, ЧТО именно будет подтверждено — название сети
    целиком, артикул, цена и похожесть, а не одна кнопка «Подтвердить» в конце строки;
  · возражения матчера (app/matcher/quality.doubts) показаны ДО подтверждения, а не
    после. На прежнем экране они появлялись только у уже подтверждённой связи — то есть
    ровно тогда, когда предупреждать поздно;
  · «Убрать связь» спрятана в раскрывающийся блок с объяснением последствий. Не ради
    лишнего нажатия: снятая связь тихо выкидывает товар из расчёта, и человек узнаёт об
    этом по тому, что корзина подешевела «сама».

ТРИ УРОВНЯ, И У КАЖДОГО СВОЙ АДРЕС. `/links` — очередь: у каких товаров какие сети ещё
не опознаны. `/links?product=12` — один товар и все сети. `/links?product=12&store=lenta` —
работа над одной парой. В Streamlit это были два выпадающих списка, и очереди не было
видно вовсе: человек выбирал товар наугад и не знал, много ли осталось.

СЕТЬ ДЁРГАЕТСЯ ТОЛЬКО НАЖАТИЕМ. Подбор и опрос цен — это минуты и сотни запросов;
открытие страницы читает только базу.

ПОЧЕМУ КАНДИДАТЫ ЖИВУТ В СЕССИИ. matcher.find_candidates сохраняет найденное в базу как
неподтверждённые сопоставления, но НЕ сохраняет похожесть — она считается на лету и
нигде не лежит. Прочитать кандидатов из базы значило бы показать их без главного числа,
а спросить магазин заново — сходить в сеть на открытии страницы. Поэтому список едет
через сессию от нажатия до следующей страницы, ровно как он ехал через session_state
прежнего экрана. Это передача результата действия, а не состояние экрана: фильтр и
выбранная пара по-прежнему целиком в адресе.
"""
from __future__ import annotations

import logging

from flask import flash, redirect, render_template, request, session
from urllib.parse import urlencode

from app import repo
from app.web.screens.products import num, rub, unit_label
from app.web.views import SCREEN_BY_KEY

log = logging.getLogger(__name__)

CANDIDATES = "links_candidates"
NAME_LIMIT = 200          # кука подписанной сессии мала: длинное название в ней не нужно


def _back(product_id=None, store_code: str | None = None, query: str = "") -> str:
    """Адрес возврата после действия. Собирается заново, а не берётся из формы.

    Взять его из скрытого поля было бы удобнее и опаснее: подставленный кем угодно
    чужой адрес превратил бы форму подтверждения в дорогу на подделку, причём для
    человека это выглядело бы как продолжение работы.
    """
    params = {k: v for k, v in (("product", product_id), ("store", store_code), ("q", query)) if v}
    path = SCREEN_BY_KEY["links"].path
    return f"{path}?{urlencode(params)}" if params else path


# ---------- что показать ----------
def _queue(products, stores, matrix, query: str) -> list[dict]:
    """Очередь: у каждого товара — сколько сетей опознано и каких не хватает.

    Три состояния пары, и различать их обязательно: подтверждена; искали, но человек
    ещё не выбрал; не искали вовсе. Слить последние два в «не опознано» значило бы
    отправить человека искать там, где уже искали и не нашли ничего подходящего.
    """
    out = []
    for product in products:
        if query and query not in (product.name or "").lower():
            continue
        marks = []
        done = 0
        for store in stores:
            state = matrix.get((product.id, store.id))
            done += 1 if state else 0
            marks.append({"code": store.code, "name": store.name,
                          "state": "done" if state else ("seen" if state is False else "new")})
        out.append({"id": product.id, "name": product.name, "marks": marks,
                    "done": done, "total": len(stores), "left": len(stores) - done})
    # Незаконченные — наверх: экран про работу, и работа должна быть первой на глазах.
    out.sort(key=lambda r: (r["left"] == 0, -r["left"], r["name"] or ""))
    return out


def _pair(product, store) -> dict:
    """Всё, что известно про одну пару «товар — сеть».

    base — кандидаты единой базы (слабая связь): база нашла товар, но сама в нём не
    уверена, и в корзину он уедет только после подтверждения здесь.
    """
    # «Подтверждено» — только настоящий артикул сети. Заглушка (цена из чека или
    # прайса) показывается отдельно: в корзину сети по ней ничего не положить, и
    # кандидаты базы с поиском остаются на виду (ревью 18.10.2026).
    mapping = repo.confirmed_mapping(product.id, store.id, real_only=True)
    if not mapping:
        stub = repo.confirmed_mapping(product.id, store.id)
        base = [{"sku": c.get("sku"), "name": (c.get("raw_name") or "")[:NAME_LIMIT],
                 "weight_g": c.get("weight_g"),
                 "doubts": _doubts(product, c.get("raw_name"), c.get("weight_g"))}
                for c in repo.base_candidates(product.id, store.id)]
        return {"confirmed": None, "stub": stub, "doubts": [], "base": base,
                "price": repo.latest_price(stub["id"]) if stub else None}
    return {"confirmed": mapping, "stub": None, "doubts": _doubts(product, mapping.get("raw_name"),
                                                                 mapping.get("weight_g")),
            "price": repo.latest_price(mapping["id"])}


def _doubts(product, raw_name: str | None, weight_g=None) -> list[str]:
    """Почему это сопоставление может быть неверным. Пустой список — возражений нет.

    Подтверждённое не значит проверенное: половина связей заводится автоматически.
    Разошлись родовое слово, марка или граммовка — человек должен увидеть это здесь,
    а не догадаться потом по странной цене в корзине.
    """
    try:
        from app.matcher.normalize import similarity
        from app.matcher.quality import doubts
    except Exception as exc:  # noqa: BLE001
        log.warning("проверка сомнений недоступна (%s)", exc)
        return []
    raw = raw_name or ""
    flags = list(doubts(product, raw, weight_g))
    if similarity(product.name, raw) < 0.75:
        flags.append("похожесть названий низкая")
    return flags


def _stashed(product_id: int, store_code: str) -> list[dict]:
    """Кандидаты последнего подбора — только если они про эту самую пару."""
    saved = session.get(CANDIDATES)
    if not isinstance(saved, dict):
        return []
    if saved.get("product") != product_id or saved.get("store") != store_code:
        return []
    return list(saved.get("items") or [])


# ---------- действия ----------
def _do_link_catalog(products) -> None:
    """Опознать товары в едином каталоге сетей: артикулы всех сетей разом, без поиска.

    Каталог (app/catalog) знает товары уже сопоставленными между собой, поэтому одно
    попадание закрывает сразу все сети. Живой подбор ниже остаётся для того, чего в
    каталоге нет.
    """
    try:
        from app.catalog.refresh import link_products
    except Exception as exc:  # noqa: BLE001
        flash(f"Единый каталог сейчас недоступен ({exc}). Остальное на экране работает.", "warn")
        return
    if not products:
        flash("Опознавать пока нечего — список товаров пуст.", "info")
        return
    from app import users
    from app.catalog import link

    phone = users.current()
    if link.running(phone):
        # Фоновый проход этого рабочего места уже идёт (вход, загрузка чеков): второй
        # параллельный делал бы ту же работу. Нажатие превращается в сверку сразу
        # после него (ревью 18.10.2026).
        link.link_soon(phone, sync=True)
        flash("Сверка с единым каталогом уже идёт фоном — очередь ниже обновится, когда она"
              " закончится.", "info")
        return
    try:
        found = link_products()
    except Exception as exc:  # noqa: BLE001
        log.exception("опознание в едином каталоге не удалось")
        flash(f"Опознание не удалось: {exc}. Остальное на экране работает.", "bad")
        return
    if found.get("failed"):
        flash(f"Не записано из-за сбоя — {found['failed']}: эти товары пройдут при следующей"
              " сверке, можно нажать ещё раз.", "warn")
    if found["linked"]:
        weak = found.get("weak") or 0
        doubtful = found.get("doubtful") or 0
        extra = ""
        if weak:
            extra += (f" Под вопросом — {weak}: артикулы записаны кандидатами и уедут в"
                      " корзину, когда вы подтвердите их у товара по сети.")
        if doubtful:
            extra += f" Не уверены — {doubtful}: подходят несколько товаров, угадывать не стали."
        flash(f"Опознано {found['linked']} из {found['tried']}: артикулы сетей записаны, "
              f"цены по адресу спросит «Обновить цены».{extra}", "ok")
    elif found.get("doubtful"):
        flash(f"Не уверены — {found['doubtful']}: подходят несколько товаров каталога,"
              " угадывать не стали. Ниже — живой подбор.", "info")
    elif not found.get("failed"):
        flash(f"Ни один из {found['tried']} товаров в каталоге не опознан — либо каталог "
              "ещё не собран, либо названия расходятся. Ниже — живой подбор.", "info")


def _do_auto_match(product_ids, store_codes) -> None:
    from app import matcher

    if not product_ids:
        flash("Связывать пока нечего — список товаров пуст.", "info")
        return
    try:
        report = matcher.auto_match(product_ids, store_codes)
    except Exception as exc:  # noqa: BLE001
        log.exception("подбор не удался")
        flash(f"Подбор не удался: {exc}. Остальное на экране работает.", "bad")
        return
    left = len(report.get("need_review") or [])
    # Список «требуют проверки» не пересказываем: это ровно то, что показывает очередь
    # ниже, и она читается из базы. Две копии одного списка разошлись бы на первом же
    # подтверждении, и одна из них молча врала бы.
    flash(f"Подтверждено автоматически: {report.get('auto', 0)}. Требуют ручной "
          f"проверки: {left} — они в очереди ниже.", "ok")


def _do_refresh_prices(product_ids, store_codes) -> None:
    from app import matcher

    if not product_ids:
        flash("Список товаров пуст.", "info")
        return
    try:
        result = matcher.refresh_prices(product_ids, store_codes)
    except Exception as exc:  # noqa: BLE001
        log.exception("обновление цен не удалось")
        flash(f"Не удалось обновить цены: {exc}. Остальное на экране работает.", "bad")
        return
    flash(f"Обновлено цен: {result.get('updated', 0)}.", "ok")
    for error in (result.get("errors") or []):
        flash(str(error), "warn")


def _do_find(product, store) -> None:
    from app import matcher

    try:
        found = matcher.find_candidates(product.id, store.code, 3)
    except Exception as exc:  # noqa: BLE001
        log.exception("поиск кандидатов не удался")
        flash(f"Поиск в магазине не удался: {exc}. Остальное на экране работает.", "bad")
        return
    items = [{
        "sku": getattr(c, "sku", "") or "",
        "name": (getattr(c, "name", "") or "")[:NAME_LIMIT],
        "price": getattr(c, "price", None),
        "score": getattr(c, "score", 0),
        "weight_g": getattr(c, "weight_g", None),
    } for c in (found or [])]
    session[CANDIDATES] = {"product": product.id, "store": store.code, "items": items}
    if not items:
        flash("Ничего не нашли. Попробуйте название покороче — например, без граммовки.", "warn")


def _resync(product) -> None:
    """Сверить товар с единым каталогом сразу после решения человека.

    Выбор в одной сети — мост к другим, отказ — повод снять то, что на нём держалось.
    Ждать ночной пересборки незачем: это доли секунды на один товар (ревью 18.10.2026).
    Само решение человека при сбое сверки остаётся в силе.
    """
    try:
        from app.catalog import link

        link.link_products([product.id], sync=True)
    except Exception:  # noqa: BLE001
        log.exception("сверка товара с единым каталогом после решения человека не удалась")


def _do_confirm(product, store, sku: str) -> None:
    from app import matcher

    try:
        matcher.confirm(product.id, store.code, sku)
    except Exception as exc:  # noqa: BLE001
        log.exception("подтверждение не прошло")
        flash(f"Не получилось связать: {exc}", "bad")
        return
    repo.clear_rejects(product.id, store.id)        # «до нового подтверждения» — оно пришло
    _resync(product)
    session.pop(CANDIDATES, None)
    flash(f"Готово: «{product.name}» связан с «{store.name}» по артикулу {sku}.", "ok")


def _do_drop(product, store) -> None:
    repo.drop_mapping(product.id, store.id)
    _resync(product)
    session.pop(CANDIDATES, None)
    flash(f"Связь «{product.name}» с «{store.name}» убрана. До нового подтверждения этот "
          "товар в расчёт по этой сети не попадёт.", "info")


def page():
    products = repo.list_products(active_only=False)
    stores = repo.list_stores()
    query = (request.args.get("q") or "").strip()

    if request.method == "POST":
        action = request.form.get("action")
        product = next((p for p in products if str(p.id) == request.form.get("product")), None)
        store = next((s for s in stores if s.code == request.form.get("store")), None)
        active_ids = [p.id for p in products if p.active]
        codes = [s.code for s in stores]

        if action == "link_catalog":
            _do_link_catalog(products)
        elif action == "auto_match":
            _do_auto_match(active_ids, codes)
        elif action == "refresh_prices":
            _do_refresh_prices(active_ids, codes)
        elif product is None or store is None:
            flash("Товар или магазин не найдены — возможно, их успели изменить.", "warn")
        elif action == "find":
            _do_find(product, store)
        elif action == "confirm":
            _do_confirm(product, store, request.form.get("sku") or "")
        elif action == "drop":
            _do_drop(product, store)

        return redirect(_back(request.form.get("product"), request.form.get("store"),
                              request.form.get("q") or ""))

    # Подтверждённой считается связь с настоящим артикулом: заглушка строки чека в
    # корзину сети не уедет, и пара с ней ещё ждёт работы.
    matrix = repo.mapping_matrix(real_only=True)
    product = next((p for p in products if str(p.id) == request.args.get("product")), None)
    store = next((s for s in stores if s.code == request.args.get("store")), None)

    chosen = None
    if product is not None:
        chosen = {
            "product": product,
            "unit": unit_label(product.unit),
            "stores": [{"code": s.code, "name": s.name,
                        "state": "done" if matrix.get((product.id, s.id))
                                 else ("seen" if matrix.get((product.id, s.id)) is False else "new")}
                       for s in stores],
        }

    pair = None
    if product is not None and store is not None:
        pair = _pair(product, store)
        pair["store"] = store
        pair["candidates"] = _stashed(product.id, store.code)
        # Возражения к каждому кандидату считаются теми же правилами, что и к
        # подтверждённой связи: предупреждать после подтверждения поздно.
        for candidate in pair["candidates"]:
            candidate["doubts"] = _doubts(product, candidate.get("name"),
                                          candidate.get("weight_g"))

    done = sum(1 for p in products for s in stores if matrix.get((p.id, s.id)))
    total = len(products) * len(stores)

    return render_template(
        "links.html",
        screen=SCREEN_BY_KEY["links"],
        rub=rub, num=num,
        query=query,
        stats=[("Товаров", str(len(products))),
               ("Связок подтверждено", f"{done} из {total}")],
        queue=_queue(products, stores, matrix, query.lower()),
        chosen=chosen,
        pair=pair,
        back=_back(product.id if product else None, store.code if store else None, query),
    )


# Действия экрана живут на его же адресе — см. пояснение в app/web/screens/products.py.
page.methods = ("GET", "POST")

__all__ = ["page"]
