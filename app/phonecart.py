"""Корзина руками телефона: наряд для приложения и его отчёт о том, что легло.

ЗАЧЕМ. Владелец 17.10.2026: повторять корзину в каждой сети, где он вошёл, и
видеть итог, который посчитала сама сеть, — с ценой по карте, личными скидками и
акциями на корзину. Вход владельца живёт на телефоне, а Пятёрочка, Самокат и Дикси
сервер не пускают. Поэтому корзину кладёт приложение (android/app) в окне сайта
сети, а сервер говорит, ЧТО класть, и принимает отчёт.

КАК КЛАДЁТ. Кнопками сайта: открыть карточку, нажать «В корзину» столько раз,
сколько нужно упаковок, потом открыть корзину сети и прочитать её итог. Решение
владельца 17.10.2026: где у сети нет своего запроса корзины, кладём кнопками; где
запрос есть, позже им — по записи корзины (app/cartrecord.py).

ПРАВИЛА ОДНИ С СЕРВЕРНОЙ ПЕРЕДАЧЕЙ. Какая кнопка — кнопка товара, где кончается
блок покупки, что значит «нет в наличии», сколько нажатий у позиции и какой темп —
всё берётся из app/shopbrowser (signals, cart) и уезжает телефону в наряде. В
приложении своих правил нет: иначе телефон и сервер разошлись бы при первой правке.

ЧТО В ОТЧЁТЕ. Что легло и что нет с причиной, сколько позиций показывает счётчик
корзины сети и итог корзины двумя путями: строки «Итого», «Скидка», «Доставка» со
страницы и числа из ответов сайта о корзине. Только суммы и имена полей. Адресов,
имён и телефонов телефон не шлёт, а поля с такими словами отбрасываются здесь ещё
раз тем же правилом, что у записи корзины.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from app import cartrecord, repo

log = logging.getLogger(__name__)

# Сети, чьи сайты приложение открывает у себя. Свои адреса — те же, что у записи.
CHAINS = tuple(cartrecord.CHAIN_HOSTS)

# Куда идти за итогом, если на странице сети не нашлось ссылки на корзину.
# Замерено только у ВкусВилла (docs/design/store-apis.md: «/cart/ работает»).
# У Магнита страницы корзины нет вовсе (замер 20.09.2026): его корзину телефон
# открывает кнопкой «Корзина» на странице. Остальные адреса — догадка по адресам
# ссылок-корзин сетей, и отчёт покажет, верна ли она.
CART_PAGES: dict[str, tuple[str, ...]] = {
    "vkusvill": ("https://vkusvill.ru/cart/",),
    "lenta": ("https://lenta.com/basket/",),
    "pyaterochka": ("https://5ka.ru/cart/",),
    "samokat": ("https://samokat.ru/cart",),
    "dixy": ("https://dixy.ru/cart/",),
    "metro": ("https://online.metro-cc.ru/cart",),
    "perekrestok": ("https://www.perekrestok.ru/cart",),
    # Замер владельца 17.10.2026 в 1.6: кнопка «Корзина» нижнего меню ведёт на
    # magnit.ru/ecom/cart. Сначала приложение всё равно касается кнопки.
    "magnit": ("https://magnit.ru/ecom/cart",),
}

# Строки итога, которые телефон ищет на странице корзины. Порядок не важен:
# у каждой строки свой вид, и человек увидит их все.
KINDS = ("total", "goods", "discount", "delivery", "service", "points", "min")

# Итог из ответа сети о корзине — там, где поля замерены живьём. Магнит, замер
# владельца 17.10.2026: после касания «В корзину» сайт шлёт PUT /webgate/v1/carts/lite,
# и в ответе лежат totalBasePrice, totalFinalPrice и totalGoodsCount. Цены у Магнита
# в копейках (app/catalog/crawlers/magnit.py), отсюда scale.
#
# verified — сверено ли поле с итогом на странице корзины. У Магнита сверено, и
# ответ — НЕ итог. В тот же вечер владелец снял страницу корзины: «Товары на сумму
# 1 748,98 ₽ 1 561,4 ₽, Доставка бесплатно, Сборка и упаковка 39 ₽, Итого 1 600,4 ₽».
# Ответ carts/lite при этом держал 37999 и при 5, и при 11, и при 12 товарах, а у
# пустой корзины — нули. Поэтому число только показывается как есть, итогом сети не
# называется и в строку «Сборки корзин» не идёт. Итог Магнита читается со страницы
# корзины (READ_JS приложения).
#
# А ВОТ ОТВЕТ ОФОРМЛЕНИЯ — ИТОГ, И ОН СВЕРЕН. Тот же вечер, корзина из трёх товаров:
# GET /webgate/v1/checkout/preview отдал summary.totalFinalPrice=51097,
# summary.totalBasePrice=49297, summary.itemsTotalSalePrice=41297, а страница корзины
# показала «Товары на сумму 492,97 ₽ 412,97 ₽, Доставка 59 ₽, Сборка и упаковка 39 ₽,
# Итого 510,97 ₽». Совпало до копейки: totalFinalPrice — итог с доставкой и сборкой,
# totalBasePrice — товары до скидок, itemsTotalSalePrice — товары со скидками, и
# скидка — разница двух последних (80,00 ₽), а не базы и итога: в итоге ещё доставка
# и сборка. Правила проверяются по порядку, сверенное первым.
API_TOTALS: dict[str, list[dict]] = {
    "magnit": [
        {"path": "/webgate/v1/checkout/preview", "total": "summary.totalFinalPrice",
         "base": "summary.totalBasePrice", "sale": "summary.itemsTotalSalePrice",
         "count": "summary.itemsCount", "scale": 0.01, "verified": True},
        {"path": "/webgate/v1/carts/lite", "total": "totalFinalPrice",
         "base": "totalBasePrice", "count": "totalGoodsCount", "scale": 0.01,
         "verified": False},
    ],
}

REPORT_KEY = "phonecart.last."
# Столько незнакомых позиций за один наряд ищем поиском самой сети. Поиск — запрос
# к сети на каждую позицию, а телефон ждёт ответа наряда не дольше минуты.
LIVE_LIMIT = 10
ITEMS_LIMIT = 120
PAGE_LIMIT = 20
API_PATHS_LIMIT = 6
API_FIELDS_LIMIT = 40


def _store(body: dict) -> str:
    code = str(body.get("store") or "").strip().lower()
    if code not in CHAINS:
        raise ValueError(f"сеть «{code or '—'}» приложение не открывает")
    return code


def rules(store: str) -> dict:
    """Чем телефон отличает кнопку товара и с каким темпом жмёт — из серверных правил."""
    from app.shopbrowser import cart, signals

    return {
        "add_name": signals.ADD_NAME,
        "plus_name": signals.PLUS_NAME,
        "gone": signals.GONE,
        "selectors": list(signals.ADD_TO_CART.get(store) or ()),
        "below": cart.BUYBOX_BELOW,
        "pause_ms": int(round(cart._step_pause() * 1000)),
        "click_pause_ms": int(round(cart.CLICK_PAUSE * 1000)),
        # Темп повтора запроса сети «положить в корзину» (приложение 1.8): не чаще
        # connectors.rate_limit_rps — единственное настоящее ограничение (CLAUDE.md).
        "request_pause_ms": _request_pause_ms(),
        "cart_pages": list(CART_PAGES.get(store, ())),
        "hosts": list(cartrecord.CHAIN_HOSTS[store]),
        # Сеть, у которой замерено: нажатие обязано уйти запросом к корзине. Нет
        # запроса — не легло (cart.SENDS_CART, замер Магнита 20.09.2026).
        "sends_cart": store in cart.SENDS_CART,
    }


def _request_pause_ms() -> int:
    """Пауза между запросами к корзине сети, мс: 1 / connectors.rate_limit_rps, не меньше 300."""
    from app import config

    try:
        rps = float(config.get("connectors.rate_limit_rps", 1.0) or 1.0)
    except (TypeError, ValueError):
        rps = 1.0
    return max(300, int(round(1000.0 / rps))) if rps > 0 else 1000


def plan(body: dict) -> dict:
    """Наряд для телефона: вся последняя корзина, переложенная на артикулы этой сети.

    Не только позиции лучшего варианта, а вся корзина: смысл в том, чтобы каждая
    сеть посчитала одну и ту же корзину и итоги можно было сравнить. Вход здесь
    не проверяется (force): вход живёт на телефоне, и сервер о нём может не знать.
    Цены перед нарядом не перезапрашиваются: итог всё равно скажет сеть.
    """
    from app import api, cartplan
    from app.shopbrowser import cart

    store = _store(body)
    picked = {k: v for k, v in body.items() if k in ("basket", "lines")}
    found = {"linked": 0} if picked.get("lines") is not None else _link(picked, store)
    lines, empty_note = api._plan_lines(picked, store)
    built = cartplan.build(store, lines, force=True)
    rows = []
    for line in built.lines[:cart.LINE_LIMIT]:
        clicks, rounded = cart.pieces_of(line)
        row = line.as_dict()
        row.update(clicks=int(clicks), rounding=rounded)
        rows.append(row)
    note = built.note
    if not rows:
        # Пустой наряд должен сказать, ЧТО искали: в окне телефона это единственная
        # подсказка, почему класть нечего (замер владельца 17.10.2026 без неё не читался).
        note = note or empty_note
        if found.get("basket"):
            note = (f"{note} Корзина «{found['basket']}», позиций {found['size']}: этой сети "
                    f"были незнакомы {found['tried']}, в её каталоге нашлось "
                    f"{found['in_catalog']}, поиском самой сети — {found['by_search']} "
                    f"из {found['searched']}.")
    return {"ok": True, "store": store, "lines": rows, "unknown": list(built.unknown),
            "total": built.total, "note": note, "linked": found["linked"], "rules": rules(store)}


def _link(picked: dict, store: str) -> dict:
    """Опознать в едином каталоге позиции корзины, которых эта сеть ещё не знает.

    ЗАЧЕМ. Товар из чека связан только с сетью чека, и наряд в другую сеть выходил
    пустым: первый замер владельца 17.10.2026 на Магните — «класть нечего», хотя
    каталог Магнита знает больше пяти тысяч товаров. Связь ставилась только кнопкой
    на экране «Связи» (app/catalog/refresh.link_products). Здесь то же правило —
    resolve и adopt, найденное подтверждается, — но только для позиций этой корзины
    и только когда единый товар есть в этой сети.

    НЕ НАШЛОСЬ В КАТАЛОГЕ — СПРАШИВАЕМ ПОИСК САМОЙ СЕТИ. Второй замер владельца
    17.10.2026: корзина «Корзина от 16.10.2026», четыре строки из чеков, в каталоге
    Магнита не нашлось ни одной — названия чеков сокращены, и разбор каталога их не
    узнаёт. Поиск сети — то же автосопоставление, что кнопка на «Связях»
    (matcher.auto_match: порог похожести и проверка фасовки, найденное подтверждается).
    Это запрос к сети на позицию, поэтому не больше LIVE_LIMIT за наряд.

    Ответ — что искали: {linked, tried, searched, by_search, basket, size}. Его видит
    человек, когда класть нечего.
    """
    from app import matcher
    from app.catalog import refresh

    shop = repo.get_store(store)
    baskets = repo.list_baskets()
    if shop is None or not baskets:
        return {"linked": 0}
    try:
        basket_id = int(picked["basket"]) if picked.get("basket") is not None else int(baskets[0]["id"])
    except (TypeError, ValueError):
        return {"linked": 0}                       # неверный номер объяснит _plan_lines
    basket = next((b for b in baskets if int(b["id"]) == basket_id), None)
    items = repo.basket_items(basket_id)
    linked = tried = searched = by_search = 0
    for row in items:
        product_id = int(row["product_id"])
        if repo.confirmed_mapping(product_id, shop.id):
            continue
        product = repo.get_product(product_id)
        if product is None:
            continue
        tried += 1
        try:
            found = refresh.resolve(product.name, product.brand, product.weight_g,
                                    product.unit, product.barcode)
            if found and any(r.get("chain") == store for r in found.get("chains") or []):
                refresh.adopt(int(found["id"]), product_id=product_id)
                linked += 1
                continue
        except Exception:  # noqa: BLE001 — каталог не открылся: спросим поиск сети
            log.warning("%s: позиция %s не опозналась в каталоге", store, product_id, exc_info=True)
        if searched >= LIVE_LIMIT:
            continue
        searched += 1
        try:
            if (matcher.auto_match([product_id], [store]) or {}).get("auto"):
                by_search += 1
        except Exception:  # noqa: BLE001 — сеть не ответила: позиция останется незнакомой
            log.warning("%s: поиск сети по позиции %s не удался", store, product_id, exc_info=True)
    log.info("%s: незнакомых позиций корзины %d, в каталоге %d, поиском сети %d из %d",
             store, tried, linked, by_search, searched)
    return {"linked": linked + by_search, "tried": tried, "searched": searched,
            "by_search": by_search, "in_catalog": linked, "size": len(items),
            "basket": str((basket or {}).get("name") or f"№{basket_id}")}


def _text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or abs(number) > 1e9:      # NaN и мусорные величины
        return None
    return round(number, 2)


def _count(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None and number >= 0 else None


def _hidden(key: str) -> bool:
    """Поле со словом из закрытого списка записи корзины: телефон, адрес, карта…"""
    return bool(cartrecord._words(key) & cartrecord.HIDDEN_WORDS)


def _call(value: str) -> str:
    """«PUT /path?x=1 201» → «PUT /path 201»: без параметров и с путём без идентификаторов."""
    parts = _text(value, 200).split(" ")
    if len(parts) >= 2:
        parts[1] = cartrecord.template_path(parts[1].split("?", 1)[0])
    return " ".join(parts)[:120]


def _label(value: Any) -> str:
    """Подпись строки итога. С цифрами не храним: так выглядят адреса и номера."""
    text = _text(value, 30)
    return "" if re.search(r"\d", text) else text


def accept_report(body: dict) -> dict:
    """Принять отчёт телефона о собранной корзине и сохранить последний по сети."""
    store = _store(body)
    items = []
    for raw in (body.get("items") or [])[:ITEMS_LIMIT]:
        if not isinstance(raw, dict):
            continue
        items.append({
            "name": _text(raw.get("name"), 120),
            "sku": _text(raw.get("sku"), 40),
            "ok": bool(raw.get("ok")),
            "why": _text(raw.get("why"), 200),
            "put": _count(raw.get("put")) or 0,
            "times": _count(raw.get("times")) or 0,
            # Чем легла позиция: касанием кнопки сайта или повтором запроса сети.
            "how": raw.get("how") if raw.get("how") in ("tap", "request") else "tap",
            # Какие запросы к корзине ушли после нажатий: «PUT /webgate/v1/carts/lite 201».
            # Путь телефон уже свернул ({id}); здесь сворачиваем ещё раз.
            "calls": [_call(c) for c in (raw.get("calls") or [])[:6] if isinstance(c, str)],
        })

    page = []
    for raw in (body.get("page_totals") or [])[:PAGE_LIMIT]:
        if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
            continue
        amount = _num(raw.get("amount"))
        if amount is None:
            continue
        row = {"kind": raw["kind"], "label": _label(raw.get("label")), "amount": amount}
        # «Товары на сумму 1 748,98 ₽ 1 561,4 ₽» у Магнита: первая сумма — до скидок
        # (замер владельца 17.10.2026). Берётся, только если она не меньше второй.
        base = _num(raw.get("base"))
        if base is not None and base >= amount:
            row["base"] = base
        page.append(row)

    found: dict[str, dict[str, float]] = {}
    raw_api = body.get("api_totals") if isinstance(body.get("api_totals"), dict) else {}
    for path, fields in list(raw_api.items())[:API_PATHS_LIMIT]:
        if not isinstance(fields, dict):
            continue
        clean = {}
        for key, value in list(fields.items())[:API_FIELDS_LIMIT]:
            number = _num(value)
            if number is None or _hidden(str(key)):
                continue
            clean[_text(key, 80)] = number
        if clean:
            found[cartrecord.template_path(_text(path, 200))] = clean

    report = {
        "store": store,
        "at": datetime.now().isoformat(timespec="seconds"),
        "app_version": _text(body.get("app_version"), 20),
        "planned": len(items),
        "landed": sum(1 for item in items if item["ok"]),
        "cart_count": _count(body.get("cart_count")),
        "cart_page": cartrecord.template_path(_text(body.get("cart_page"), 200)),
        "our_total": _num(body.get("our_total")),
        "items": items,
        "page_totals": page,
        "api_totals": found,
    }
    repo.set_setting(REPORT_KEY + store, json.dumps(report, ensure_ascii=False))
    log.info("корзина с телефона, %s: легло %d из %d, итог сети %s, полей из ответов %d",
             store, report["landed"], report["planned"], total_of(report),
             sum(len(v) for v in found.values()))
    _to_row(report)
    return {"ok": True, "store": store, "landed": report["landed"],
            "planned": report["planned"], "total": total_of(report),
            "network": network_total(report)}


def _page(report: dict, kind: str) -> float | None:
    for row in report.get("page_totals") or []:
        if row.get("kind") == kind:
            return row.get("amount")
    return None


def _to_row(report: dict) -> None:
    """Тот же отчёт — в строку сети на экране «Сборка корзин» (app/cartrun.py).

    Итог подписан «посчитала сеть»: его прочитал телефон у самой сети — с её
    страницы корзины или из её ответа. Строка не должна ронять приём отчёта:
    сам отчёт уже сохранён, и человеку важнее он, чем строка экрана.
    """
    try:
        from app import cartrun

        amount = total_of(report)
        network = network_total(report)
        if network is not None and not network["verified"]:
            network = None                         # несверенное число итогом не подаём
        total = None
        if amount is not None:
            goods_row = next((r for r in report.get("page_totals") or [] if r.get("kind") == "goods"), None)
            goods, discount = None, _page(report, "discount")
            if network:
                goods, discount = network["base"], network["discount"]
            elif goods_row:
                goods = goods_row.get("base") or goods_row.get("amount")
                if discount is None and goods_row.get("base") is not None:
                    discount = round(goods_row["base"] - goods_row["amount"], 2)
            total = {"sum": amount, "source": cartrun.NETWORK, "goods": goods,
                     "discount": discount, "delivery": _page(report, "delivery"),
                     "count": network["count"] if network else report.get("cart_count")}
        items = report.get("items") or []
        cartrun.accept({
            "store": report["store"],
            "landed": [item["name"] for item in items if item.get("ok")],
            "missing": [{"name": item["name"], "why": item.get("why") or "не легло"}
                        for item in items if not item.get("ok")],
            "total": total,
            "note": "собрано с телефона кнопками сайта",
        }, by=cartrun.DEVICE)
    except Exception:  # noqa: BLE001
        log.warning("%s: строка «Сборки корзин» не обновилась отчётом телефона",
                    report.get("store"), exc_info=True)


def _field(fields: dict, name: str) -> float | None:
    """Поле ответа по имени: в корне или вложенное («data.summary.totalFinalPrice»)."""
    for key, value in fields.items():
        if key == name or key.endswith("." + name):
            return value
    return None


def network_total(report: dict | None) -> dict | None:
    """Итог корзины из ответа сети, где поля замерены (API_TOTALS): total, base, discount, count.

    Правил у сети может быть несколько. Берётся первое, чей ответ есть в отчёте,
    а правила стоят так, что сверенное идёт первым.
    """
    report = report or {}
    answers = report.get("api_totals") or {}
    for rule in API_TOTALS.get(report.get("store") or "", ()):
        fields = answers.get(rule["path"])
        if fields and _field(fields, rule["total"]) is not None:
            return _from(rule, fields)
    return None


def _from(rule: dict, fields: dict) -> dict:
    total = _field(fields, rule["total"])
    scale = rule["scale"]
    base = _field(fields, rule["base"])
    sale = _field(fields, rule["sale"]) if rule.get("sale") else None
    count = _field(fields, rule["count"])
    out = {"total": round(total * scale, 2),
           "base": round(base * scale, 2) if base is not None else None,
           "count": int(count) if count is not None else None,
           "verified": bool(rule.get("verified"))}
    # Скидка — от товаров до скидок к товарам со скидками, если сеть отдала оба.
    # Иначе — от базы к итогу: так считает ответ без доставки и сборки (carts/lite).
    after = round(sale * scale, 2) if sale is not None else out["total"]
    out["discount"] = round(out["base"] - after, 2) if out["base"] is not None else None
    return out


def total_of(report: dict | None) -> float | None:
    """Итог корзины сети: из её ответа, где поле сверено, иначе строка «Итого» или «Товары»."""
    network = network_total(report)
    if network is not None and network["verified"]:
        return network["total"]
    rows = (report or {}).get("page_totals") or []
    for kind in ("total", "goods"):
        for row in rows:
            if row.get("kind") == kind:
                return row.get("amount")
    return None


def last(store: str) -> dict | None:
    """Последний отчёт телефона по сети в этом рабочем месте. Нет — None."""
    raw = repo.get_setting(REPORT_KEY + store)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


__all__ = ["CHAINS", "CART_PAGES", "KINDS", "API_TOTALS", "rules", "plan", "accept_report",
           "network_total", "total_of", "last"]
