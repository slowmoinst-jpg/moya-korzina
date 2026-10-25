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

САМА, В ФОНЕ (приложение 1.13). Пока человек смотрит корзину на телефоне, приложение
собирает её во всех сетях, где он вошёл, — скрытым окном на сеть, сети параллельно,
внутри сети один поток. Наряд говорит, какую корзину он описывает (basket и её
отпечаток stamp): по отпечатку приложение видит, менялась ли корзина с прошлой сборки
в этой сети, и без перемен не собирает. Ход идёт в progress, итог — в тот же отчёт.

СЛУЖЕБНАЯ СТРОКА — НЕ ТОВАР (21.10.2026). В корзинах из чеков лежат «Аренда
транспортного средства», «Перевозка пассажиров багажа», «Штраф за нарушение условий
договора», «Сборка» (служебная строка приложения). Магазин такого товара не знает:
поиск по этим строкам заведомо пуст, а телефон ждал карточку все 20 с на каждую
позицию. Такие строки не получают адреса поиска, не уходят в живой поиск сети и не
называются в наряде незнакомыми — в note сказано, сколько их пропущено.

ПОВЕРХ ЛЕЖАЩЕГО — ТОЛЬКО НЕДОСТАЮЩЕЕ. Сеть кладёт поверх того, что уже в корзине, и
повторная сборка удвоила бы её. Поэтому приложение помнит, что само положило в каждую
сеть, и докладывает только разницу: позиция, которая уже лежит, в отчёте «kept».
Лишнее (было в корзине человека, теперь нет) приложение не убирает — это решает
человек, — а называет в отчёте (extra). Где сеть отвечает на запись корзиной целиком
(full_cart), её ответ поправляет память телефона: человек мог оформить заказ или
убрать товар сам.
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
    "fixprice": ("https://fix-price.com/cart",),
    "vprok": ("https://www.vprok.ru/cart",),
    "monetka": ("https://monetka.ru/cart",),
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

# Ответы сети на запись корзины, в которых лежит ВСЯ её корзина — у каждой позиции
# артикул и количество. Магнит, замер на эмуляторе 18.10.2026: PUT carts/lite после
# «В корзину» вернул в items и только что положенный товар, и лежавший раньше, с qnty у
# каждого; checkout/preview отдаёт корзину в suborders[].items[]. По такому ответу
# телефон знает, что на самом деле лежит в корзине сети, а не только что он клал сам.
FULL_CART: dict[str, tuple[str, ...]] = {
    "magnit": ("/webgate/v1/carts/lite", "/webgate/v1/checkout/preview"),
}

# Сети, чей запрос записи корзины СТАВИТ количество, а не прибавляет: повтор с тем же
# числом ничего не удваивает, и прибавка к лежащей позиции — тот же запрос с итоговым
# числом. Магнит, замер на эмуляторе 18.10.2026: «В корзину» шлёт PUT carts/lite с одной
# позицией и qnty 1, а «+» на карточке, где Алёнки лежало 2, — ВСЮ корзину с qnty 3 у
# Алёнки и прежними числами у остальных; в ответе Алёнки 3, прочие на месте. Значит,
# перечисленным позициям запрос ставит число, неперечисленные не трогает. Остальным
# сетям, пока не замерено, прибавку телефон делает касаниями «+», по одной упаковке.
QTY_SETS: tuple[str, ...] = ("magnit",)

# Корзина сети пуста — так пишут её страницы. Если приложение помнит, что клало сюда,
# а сеть показывает пустую корзину, человек оформил заказ или очистил её сам.
EMPTY_CART = (r"корзина пуста|в корзине пока|в корзине пусто|в корзине нет товаров|"
              r"в корзине ничего нет|пустая корзина")

HOWS = ("tap", "request", "kept")

REPORT_KEY = "phonecart.last."
# Столько незнакомых позиций за один наряд ищем поиском самой сети. Поиск — запрос
# к сети на каждую позицию, а телефон ждёт ответа наряда не дольше минуты.
LIVE_LIMIT = 10
# Столько незнакомых позиций наряд отдаёт телефону как «найди поиском сети». Приложению
# это ещё предстоит (серверная половина готова): там, где сеть не пускает сервер
# (Пятёрочка, Самокат — замер 18.10.2026), поиск делает телефон, а найденный артикул
# возвращается отчётом и заводится в единой базе (app/catalog/fromurl.py).
SEARCH_LIMIT = 8
# Сколько карточек витрины телефон отдаёт за один заход (accept_cards). Страница
# раздела показывает десятки товаров, и предел отчёта о корзине (ITEMS_LIMIT) её
# обрезал бы; но и заливкой каталога один запрос быть не должен.
CARDS_LIMIT = 150
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
        # Кука входа: по ней приложение само видит, вошёл ли человек в сеть у себя в
        # окне, даже если сервер сохранённого входа не знает (Магнит, 16.10.2026).
        "auth_cookies": list(signals.AUTH_COOKIE.get(store) or ()),
        "full_cart": list(FULL_CART.get(store, ())),
        "qty_sets": store in QTY_SETS,
        "empty_cart": EMPTY_CART,
        # Сигнал бот-проверки (Пятёрочка, Этап 1): паттерн на странице означает «сеть
        # встретила проверкой». При обнаружении приложение делает паузу check_pause_ms
        # и пробует ту же карточку ещё раз. Если вторая попытка тоже упирается в
        # проверку — позиция переносится в missing («check»), и добор следующим прогоном
        # не трогает уже положенное.
        "check_signal": signals.CHECK,
        "check_pause_ms": _check_pause_ms(),
    }


def _request_pause_ms() -> int:
    """Пауза между запросами к корзине сети, мс: 1 / connectors.rate_limit_rps, не меньше 300."""
    from app import config

    try:
        rps = float(config.get("connectors.rate_limit_rps", 1.0) or 1.0)
    except (TypeError, ValueError):
        rps = 1.0
    return max(300, int(round(1000.0 / rps))) if rps > 0 else 1000


def _check_pause_ms() -> int:
    """Пауза после обнаружения бот-проверки, мс: из config.yaml phone_check_pause_ms.

    По умолчанию 5 с — достаточно, чтобы Пятёрочка сняла краткосрочную блокировку,
    но не так много, чтобы сборка корзины длилась целую минуту. Если первая попытка
    после паузы снова упирается в CHECK — позиция помечается «check» и добирается
    следующим прогоном (top_up).
    """
    from app import config

    try:
        ms = int(config.get("phone_check_pause_ms", 5000) or 5000)
    except (TypeError, ValueError):
        ms = 5000
    return max(1000, ms)


def _landed_skus(store: str) -> frozenset[str]:
    """SKU позиций, которые уже лежат в корзине сети по последнему отчёту телефона.

    ЗАЧЕМ (Этап 1, 25.10.2026). Пятёрочка и Самокат пропускают 2–3 позиции за прогон
    из-за бот-защиты и таймаутов. «Добор» — следующий наряд с `top_up=true` содержит
    ТОЛЬКО пропущенные: те, чьего SKU нет среди уже положенных. Это позволяет добрать
    корзину без нажатия кнопки и не удвоить уже лежащее.

    Берём только реально положенные (ok=true), исключая «kept» (они тоже ok, но их
    клал предыдущий прогон, и следующий обязан класть снова только то, чего не хватает).
    Пустое множество — отчёта нет или все позиции не легли; тогда top_up вернёт полный
    наряд, как обычно.
    """
    report = last(store)
    if not report:
        return frozenset()
    return frozenset(
        str(item["sku"])
        for item in (report.get("items") or [])
        if item.get("ok") and item.get("sku") and item.get("how") != "kept"
    )


def plan(body: dict) -> dict:
    """Наряд для телефона: вся последняя корзина, переложенная на артикулы этой сети.

    Не только позиции лучшего варианта, а вся корзина: смысл в том, чтобы каждая
    сеть посчитала одну и ту же корзину и итоги можно было сравнить. Вход здесь
    не проверяется (force): вход живёт на телефоне, и сервер о нём может не знать.
    Цены перед нарядом не перезапрашиваются: итог всё равно скажет сеть.

    TOP_UP (Этап 1). При `top_up=true` возвращает только позиции, которых ещё нет в
    корзине сети по последнему отчёту: SKU, не попавших в landed. Это добор — следующий
    прогон без нажатия, который добирает пропущенные позиции после частичной сборки.
    """
    from app import api, cartplan
    from app.shopbrowser import cart

    store = _store(body)
    top_up = bool(body.get("top_up"))
    picked = {k: v for k, v in body.items() if k in ("basket", "lines")}
    found = {"linked": 0} if picked.get("lines") is not None else _link(picked, store)
    lines, empty_note = api._plan_lines(picked, store)
    # Служебные строки чека («Аренда транспортного средства», «Сборка») — не товар:
    # в наряд и в поиск они не идут. Отсекаем ДО cartplan.build, иначе они попали бы
    # в unknown как «незнакомые позиции», а телефон искал бы их в магазине впустую.
    lines, service_skipped = _without_service_lines(lines)
    basket_id = _basket_id(picked)
    built = cartplan.build(store, lines, force=True)
    # TOP_UP: убираем из наряда позиции, чей SKU уже лежит в корзине сети. Сеть кладёт
    # поверх лежащего, и повторная кладка удвоила бы их. При этом позиции без SKU (ещё
    # не привязанные) остаются: их телефон найдёт поиском, а не нажатием на карточку.
    already: frozenset[str] = _landed_skus(store) if top_up else frozenset()
    rows = []
    by_sku: dict[str, dict] = {}
    for line in built.lines[:cart.LINE_LIMIT]:
        clicks, rounded = cart.pieces_of(line)
        row = line.as_dict()
        row.update(clicks=int(clicks), rounding=rounded)
        sku_key = str(row.get("sku") or "")
        # Два товара корзины на одном артикуле сети (дубль из чека и каталога) — одна
        # позиция с суммой упаковок. Иначе сеть, что ставит количество (QTY_SETS), взяла
        # бы число второй строки вместо суммы: замер на эмуляторе 18.10.2026, Алёнка 3 + 2.
        same = by_sku.get(sku_key) if sku_key else None
        if same is not None:
            same["clicks"] += row["clicks"]
            same["qty"] = round(float(same.get("qty") or 0) + float(row.get("qty") or 0), 3)
            if row.get("price") is not None:
                same["price"] = round(float(same.get("price") or 0) + float(row["price"]), 2)
            continue
        # Top-up: пропускаем позиции, которые уже легли (SKU есть в landed прошлого отчёта).
        if top_up and sku_key and sku_key in already:
            continue
        if sku_key:
            by_sku[sku_key] = row
        rows.append(row)
    note = built.note
    if top_up and already:
        note = (f"{(note or '').strip()} Добор: {len(already)} поз. уже лежит "
                f"в корзине сети с прошлой сборки — их не трогаем.").strip()
    if not rows:
        # Пустой наряд должен сказать, ЧТО искали: в окне телефона это единственная
        # подсказка, почему класть нечего (замер владельца 17.10.2026 без неё не читался).
        note = note or empty_note
        if found.get("basket"):
            note = (f"{note} Корзина «{found['basket']}», позиций {found['size']}: этой сети "
                    f"были незнакомы {found['tried']}, в её каталоге нашлось "
                    f"{found['in_catalog']}, поиском самой сети — {found['by_search']} "
                    f"из {found['searched']}."
                    + (f" Ещё {found['unasked']} не спрошены — предел живого поиска "
                       f"{LIVE_LIMIT} за наряд." if found.get("unasked") else ""))
    elif built.unknown:
        # Наряд не пуст, но часть корзины не опознана: сказать, что по ней сделали.
        # Без этих слов строка про незнакомые позиции читается как «даже не пытались».
        note = (note or "") + _link_note(found)
    if service_skipped:
        # Служебные строки не товар: человек видит, что они не потерялись, а пропущены.
        note = f"{(note or '').strip()} Служебных строк пропущено: {service_skipped}.".strip()
    return {"ok": True, "store": store, "lines": rows, "unknown": list(built.unknown),
            "total": built.total, "note": note, "linked": found["linked"], "rules": rules(store),
            # Позиции, которых сеть не знает: пусть их найдёт телефон поиском самой сети
            # (app/phonecart.py, SEARCH_LIMIT). Для сетей, куда сервер не пускают
            # (Пятёрочка, Самокат), это единственная дорога к настоящему артикулу.
            "search": _search_lines(found.get("unresolved") or [], store),
            # Какую корзину описывает наряд и её отпечаток: по нему телефон узнаёт, менялась
            # ли корзина с прошлой сборки в этой сети, и без перемен её не повторяет.
            "basket": basket_id,
            "stamp": _stamp(basket_id, custom_lines=picked.get("lines")),
            # Флаг добора: сколько позиций уже лежит и пропущено в этом наряде.
            "top_up": top_up,
            "already_landed": len(already)}


def _search_lines(unresolved: list[dict], store: str) -> list[dict]:
    """Что телефону искать самому: позиции, которых сеть не знает, — её же поиском.

    Адрес поиска берётся из config.yaml (`search_url.<сеть>`, проверен живыми запросами
    15.09.2026), а не собирается в коде: у каждой сети он свой, и ошибка в нём — одна
    строка настройки, а не правка приложения.

    Список короткий (SEARCH_LIMIT): поиск — это то, что человек делает руками в сети, и
    десяток запросов за один заход уже много. Пустой список — обычное дело: либо все
    позиции сети знакомы, либо адреса поиска у сети нет.

    СЕТЬ, ГДЕ ВЫДАЧИ НЕТ, ЗАКАЗ НЕ ПОЛУЧАЕТ (`search_off`). Иначе телефон честно ждёт
    карточку все 12 с на каждую позицию и не находит ни одной — у Самоката так, замер
    19.10.2026: страница поиска приходит пустой, а поискового запроса не делает вовсе.
    Время владельца — то, ради чего приложение существует, и тратить его на дорогу,
    которая заведомо никуда не ведёт, нельзя. Появится рабочая дорога — сеть уйдёт из
    `search_off`, и поиск включится сам.

    ВТОРОЙ АДРЕС — КОГДА ПЕРВЫЙ НАШЁЛ НЕ ТОТ ТОВАР (`search_alt`). Живая сборка
    20.10.2026 на боевом сервере положила 0-1 позицию из 4, и один из отчётов был
    «поиск сети нашёл не тот товар». У строки наряда был ОДИН адрес поиска: получив
    по нему чужую карточку, телефон сдавался, хотя второго запроса — короче и
    точнее — хватило бы. Длинное имя из чека («Молоко neo high protein 0,5
    безлактозное ультрапастеризованное 950 г») сеть часто не понимает целиком, а
    первые два-три слова — второй шанс найти нужную полку.

    Строится он ТЕМ ЖЕ путём, что и основной (`_query_name` → `_short_query`), и
    кладётся только когда он правда короче: дубль основного адреса телефону не
    нужен — это лишний запрос в сеть и лишние 12 с ожидания карточки. Нет шаблона
    поиска — нет ни `search`, ни `search_alt`: выдумывать адрес нельзя.
    """
    from urllib.parse import quote_plus

    from app import config

    if _search_off(store):
        return []
    template = config.get(f"search_url.{store}")
    if not isinstance(template, str) or "{q}" not in template:
        return []
    out = []
    # Служебные строки (аренда, перевозка, штраф, «Сборка») — не товар, адрес поиска
    # им не нужен. Отсекаем ДО `_query_name`: он ходит в каталог за именем, а
    # спрашивать там про служебную строку нечего (21.10.2026, task-27).
    candidates = [item for item in (unresolved or [])
                  if not _service_line(str(item.get("name") or "").strip())]
    for item in candidates[:SEARCH_LIMIT]:
        name = str(item.get("name") or "").strip()
        product_id = item.get("product_id")
        if not name:
            continue
        # Ищем полным именем единого товара, если он есть: строка чека — обрубки.
        # Без привязки к единому товару имя тоже чистим: «0 5» — это «0,5».
        query = _query_name(int(product_id), name) if product_id else _clean_query(name)
        row = {"product_id": product_id, "name": name,
               "url": template.replace("{q}", quote_plus(query))}
        short = _short_query(query)
        # Сравниваем без учёта регистра: «Молоко» и «молоко» — один и тот же запрос,
        # и второй адрес тут был бы повтором, а не вторым шансом.
        if short and short.casefold() != query.strip().casefold():
            row["search_alt"] = template.replace("{q}", quote_plus(short))
        out.append(row)
    return out


def _search_off(store: str) -> bool:
    """Сети, где поиск телефону заказывать не надо (config.yaml, `search_off`)."""
    from app import config

    try:
        off = config.get("search_off") or []
    except Exception:  # noqa: BLE001 - настройка не должна валить наряд
        return False
    if isinstance(off, str):
        off = [off]
    return store in [str(x).strip().lower() for x in off]


# Служебные строки корзины: в чеке это «позиция», но товара в магазине у неё нет —
# это услуга, сбор или штраф. Список короткий и нарочно консервативный: обычный
# товар, у которого такое слово стоит в середине названия («Салфетки услуга …»,
# «Молоко доставка …»), служебной строкой не становится.
SERVICE_ROOTS: tuple[str, ...] = (
    "аренда",       # «Аренда транспортного средства»
    "перевозка",    # «Перевозка пассажиров багажа»
    "пассажир",     # та же перевозка в иной редакции
    "штраф",        # «Штраф за нарушение условий договора»
    "услуг",        # «Услуга», «Услуги связи» — только с начала строки
    "сервисн",      # «Сервисный сбор»
    "чаевые",
    "комисси",      # «Комиссия за перевод»
    "такси",
    "taxi",
    "плата за",     # «Плата за доставку»
    "сборка",       # «Сборка» — служебная строка приложения
)


def _service_line(name: str | None) -> bool:
    """Служебная ли это строка: услуга, сбор или штраф, а не товар.

    ЗАЧЕМ. Третий живой прогон 21.10.2026: в корзинах из чеков лежат «Аренда
    транспортного средства», «Перевозка пассажиров багажа», «Штраф за нарушение
    условий договора», «Сборка» (служебная строка приложения). По каждой такой
    строке строился адрес поиска в магазине, и телефон тратил по 20 секунд на
    позицию — время и деньги владельца на поиск товара, которого в магазине нет и
    быть не может.

    ЧЕМ КОНСЕРВАТИВНЕЕ, ТЕМ ЛУЧШЕ. Слово узнаётся, только если стоит НАЧАЛОМ
    строки: «Салфетки услуга влажные» и «Молоко доставка на дом» остаются товарами,
    «Сборная солянка» — тоже (сравнение идёт с началом строки, а не с подстрокой в
    середине). Регистр, лишние пробелы и «ё» не важны: «ЧАЁВЫЕ» — те же «чаевые».
    """
    text = re.sub(r"\s+", " ", str(name or "").casefold().replace("ё", "е")).strip()
    return any(text.startswith(root) for root in SERVICE_ROOTS)


def _without_service_lines(lines) -> tuple[list, int]:
    """Отделить служебные строки от товаров: им не место ни в наряде, ни в поиске.

    Возвращает оставшиеся строки и сколько служебных пропущено — это число уходит
    человеку в note, чтобы пропажа позиций из корзины не выглядела поломкой.
    """
    kept, skipped = [], 0
    for line in lines or []:
        name = getattr(line, "name", None) or getattr(line, "product_name", None)
        if _service_line(name):
            skipped += 1
            continue
        kept.append(line)
    return kept, skipped


def _has_cyrillic(text: str | None) -> bool:
    """Есть ли в строке русские буквы.

    Нужно, чтобы отличить имя, которое поймёт витрина, от латинской транслитерации.
    Замер 21.10.2026 на боевой базе владельца: каноническим именем молочки стала
    строка «moloko ultrapasterizovannoe parmalat pitevoe proteinovoe bezlaktoznoe
    18 bez zmzh 1l rossiya» (транслитерация из sitemap-сетей), и Магнит, Лента и
    Пятёрочка на неё отвечали пустой выдачей на всех позициях.
    """
    return bool(re.search(r"[А-Яа-яЁё]", text or ""))


def _query_name(product_id: int, fallback: str) -> str:
    """Чем искать товар в сети: имя из единой базы, а не обрубки чека.

    ЗАЧЕМ. Чек пишет «Dirol жев рез арб дын кок 13.6 г», и поиск сети по такой строке не
    найдёт ничего: это не слова, а обрубки. У товара, привязанного к единой базе, есть
    полное имя («Жевательная резинка Dirol Арбузно-дынный коктейль, 13,6 г») — им сеть
    ищет нормально. Замер 19.10.2026 на товарах владельца: из 301 купленного и ни к одной
    сети не привязанного товара Самокат и Пятёрочка знают единый товар у 43.

    С 20.10.2026 порядок такой: привязка → похожее имя из единой базы → аккуратная чистка
    обрубка. Повод: живая сборка на боевом сервере, у 3 сетей из 4 не легло ни одной
    позиции, и причина в отчётах — «поиск сети ничего не нашёл за 12 с»: в поиск уходило
    «Молоко neo high protein 0 5 безлактозное ультрапастеризованное 950 г» (это «0,5 %»).

    Нет ни привязки, ни похожего имени — ищем как написан товар: выдумывать имя по обрубкам
    нельзя, по выдуманному слову сеть вернёт чужой товар, а приложение нажмёт ему
    «В корзину».

    ПО-РУССКИ, ЕСЛИ ЧЕК ПО-РУССКИ (21.10.2026). Имя единого товара — это ещё и запрос в
    витрину, а витрины Магнита, Ленты и Пятёрочки латинскую транслитерацию не находят.
    Поэтому имя без кириллицы не отдаём в поиск, когда обрубок чека русский: чистим
    обрубок и ищем им. Товар, у которого и чек латиницей (иностранные марки), не трогаем —
    его латинское имя, возможно, единственное, что сеть знает.
    """
    from app.catalog import store

    try:
        master = repo.master_of(int(product_id))
        if master and master.get("master_id"):
            item = store.item(int(master["master_id"])) or {}
            full = str(item.get("name") or "").strip()
            if full and len(full) > len(fallback):
                if _has_cyrillic(full) or not _has_cyrillic(fallback):
                    return full
                return _clean_query(fallback)
    except Exception:  # noqa: BLE001 — каталог не открылся: ищем как написан товар
        log.info("имя для поиска не подобралось у товара %s", product_id, exc_info=True)
    found = _catalog_name(fallback)
    if found:
        if _has_cyrillic(found) or not _has_cyrillic(fallback):
            return found
        return _clean_query(fallback)
    return _clean_query(fallback)


# Сколько слов имени берём, когда полного имени не нашлось. Три — это обычно
# «что это» (молоко + марка + тип); дальше идут прилагательные, которых в поиске сети нет.
# Число одно на два дела, и это нарочно: им пробует каталог (`_catalog_name`) и им же
# строится второй, короткий запрос телефону (`_short_query`) — иначе телефон искал бы
# не тем, чем сервер уже научился искать, и они разошлись бы при первой правке.
SHORT_QUERY_WORDS = 3


def _clean_query(text: str) -> str:
    """Починить обрубок чека так, чтобы сеть его поняла: «0 5» — это «0,5».

    ЗАМЕР 20.10.2026 на живых отчётах: позиции в корзинах владельца записаны как
    «Молоко neo high protein 0 5 безлактозное ультрапастеризованное 950 г». Сеть по
    «0 5» не ищет ничего: для неё это два числа.

    Два случая, и оба — про одно и то же:
      - жирность «0 5» идёт БЕЗ единицы измерения, сразу за ней слово;
      - объём «0 95 л» — с единицей.
    Склеиваем только там, где это очевидно: первое число 0 или 1 (жирность продукта),
    второе — одна-две цифры. «2 500 г» под правило не попадает и остаётся как есть.
    """
    import re

    fixed = (text or "").replace("\u00a0", " ")
    # «0 5 безлактозное», «1 8 ультрапастеризованное», «2 5%»
    fixed = re.sub(r"(?<![\d,.])\b([01])\s+(\d{1,2})(?![\d])\s*(?=[^\d]|$)", r"\1,\2 ", fixed)
    # «0 95 л», «1 8 кг» — число с единицей
    fixed = re.sub(r"(?<![\d,.])\b(\d)\s+(\d{1,2})\s*(?=(%|г|гр|кг|мл|л|шт)\b)", r"\1,\2", fixed)
    fixed = re.sub(r"\s+", " ", fixed).strip()
    return fixed or text


def _short_query(text: str) -> str:
    """Короткий запрос для второго адреса поиска: первые `SHORT_QUERY_WORDS` слов.

    ЗАЧЕМ. Основной запрос — имя товара целиком (из единой базы, если оно там есть):
    по нему сеть находит товар, когда понимает все слова. Живой отчёт 20.10.2026:
    «поиск сети нашёл не тот товар» — и виновато не всегда имя: у длинного имени
    сеть может зацепиться за одно слово и отдать соседнюю полку. Второй адрес с
    двумя-тремя первыми словами — второй шанс для телефона, и он дешевле, чем ещё
    одна сборка целиком.

    «СМЫСЛОВОЕ СЛОВО» здесь то же, что в `_catalog_name`: длиннее двух знаков. Так
    одиночные «0,5», «л» и «г» не становятся запросом, а «Молоко neo high» остаётся.
    Числа вроде «0,5» под это правило попадают (три знака) и, если стоят в первых
    трёх словах, остаются в запросе: жирность — часть названия, и терять её в
    коротком запросе незачем.

    Пусто — второй адрес не нужен: имя и так короткое, либо в нём нечего взять.
    Тогда у строки наряда остаётся один `search`, как было до 21.10.2026.
    """
    fixed = _clean_query(text or "")
    words = [word for word in fixed.split() if len(word) > 2]
    if len(words) <= SHORT_QUERY_WORDS:
        return ""
    return " ".join(words[:SHORT_QUERY_WORDS])


def _catalog_name(text: str) -> str | None:
    """Найти каноническое имя в единой базе по обрубку чека. None — не нашлось.

    Проверяем аккуратно: имя берём, только если сходится фасовка (вес или объём в
    пределах 10 %) и совпадает первое смысловое слово. Иначе сеть получит запрос про
    другой товар — а это хуже, чем не найти ничего.

    Латинское имя при русском обрубке не берём (21.10.2026): в единой базе владельца
    каноническим именем молочки стала транслитерация из sitemap-сетей, а русские
    витрины её не находят. Пропускаем такого кандидата и смотрим дальше — среди
    похожих может найтись русское имя; не нашлось ни одного — None, и `_query_name`
    почистит обрубок чека.
    """
    from app.catalog import store
    from app.matcher.normalize import parse_weight

    raw = (text or "").strip()
    if len(raw) < 4:
        return None
    russian = _has_cyrillic(raw)
    grams, _unit = parse_weight(raw)
    words = [w for w in raw.split() if len(w) > 2]
    attempts = [raw]
    if len(words) > SHORT_QUERY_WORDS:
        attempts.append(" ".join(words[:SHORT_QUERY_WORDS]))
    for attempt in attempts:
        try:
            hits = store.search_items(attempt, limit=3)
        except Exception:  # noqa: BLE001 — каталог не ответил: не выдумываем
            return None
        for hit in hits:
            name = str(hit.get("name") or "").strip()
            if not name or len(name) <= len(raw):
                continue
            hit_grams, _hit_unit = parse_weight(name)
            if grams and hit_grams and abs(hit_grams - grams) > grams * 0.1:
                continue
            if words and words[0].lower() not in name.lower():
                continue
            if russian and not _has_cyrillic(name):
                continue
            return name
    return None


def _basket_id(picked: dict) -> int | None:
    """Номер корзины наряда: названная или самая свежая. Присланный список — не корзина."""
    if picked.get("lines") is not None:
        return None
    if picked.get("basket") is not None:
        try:
            return int(picked["basket"])
        except (TypeError, ValueError):
            return None
    baskets = repo.list_baskets()
    return int(baskets[0]["id"]) if baskets else None


def _stamp(basket_id: int | None, custom_lines: list | None = None) -> str:
    import hashlib
    from app import cartrun

    if custom_lines is not None:
        pairs = []
        for ln in custom_lines:
            pid = getattr(ln, "product_id", None) or (ln.get("product_id") if isinstance(ln, dict) else 0)
            qty = getattr(ln, "qty", None) or (ln.get("qty") if isinstance(ln, dict) else 1.0)
            name = getattr(ln, "name", None) or (ln.get("name") if isinstance(ln, dict) else "")
            pairs.append((int(pid or 0), str(name), round(float(qty or 0), 3)))
        pairs.sort()
        return hashlib.sha1(repr(pairs).encode("utf-8")).hexdigest()[:12]
    return cartrun.stamp_of(basket_id) if basket_id is not None else ""


def _link(picked: dict, store: str) -> dict:
    """Опознать в едином каталоге позиции корзины, которых эта сеть ещё не знает.

    ЗАЧЕМ. Товар из чека связан только с сетью чека, и наряд в другую сеть выходил
    пустым: первый замер владельца 17.10.2026 на Магните — «класть нечего», хотя
    каталог Магнита знает больше пяти тысяч товаров. Связь ставилась только кнопкой
    на экране «Связи» (app/catalog/refresh.link_products). Здесь то же правило —
    resolve и adopt, найденное подтверждается, — но только для позиций этой корзины
    и только когда единый товар есть в этой сети.

    ЗАГЛУШКА — НЕ «СЕТЬ ЗНАЕТ ТОВАР». Замер 18.10.2026 на рабочем месте demo: в корзине
    16 позиций, четыре с настоящими артикулами Магнита, а двенадцать — только с
    заглушками (`magnit-sliva-kg`, `magnit-ikra-180` и подобными). Заглушка — источник
    цены, а не артикул сети (app/repo.real_sku_sql, докстринг confirmed_mapping): по ней
    в корзину сети ничего не положить. Пока здесь стояло ЛЮБОЕ подтверждённое
    сопоставление, эти двенадцать не шли ни в наряд, ни в поиск — 75 % корзины молча
    выпадало, а наряд оставался из четырёх строк. Поэтому пропускаем только настоящую
    связь (`real_only=True`), а позиция с одной заглушкой идёт тем же путём, что
    незнакомая.

    НЕ НАШЛОСЬ В КАТАЛОГЕ — СПРАШИВАЕМ ПОИСК САМОЙ СЕТИ. Второй замер владельца
    17.10.2026: корзина «Корзина от 16.10.2026», четыре строки из чеков, в каталоге
    Магнита не нашлось ни одной — названия чеков сокращены, и разбор каталога их не
    узнаёт. Поиск сети — то же автосопоставление, что кнопка на «Связях»
    (matcher.auto_match: порог похожести и проверка фасовки, найденное подтверждается).
    Это запрос к сети на позицию, поэтому не больше LIVE_LIMIT за наряд, и о нехватке
    предела говорится вслух (`unasked`): молчание читалось бы как «спросили всё».

    Ответ — что искали: {linked, tried, searched, by_search, in_catalog, unasked,
    basket, size}. Его видит человек, когда класть нечего.
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
    linked = tried = searched = by_search = skipped = 0
    # Позиции, которые так и остались незнакомыми: их отдаём телефону как «найди
    # поиском самой сети» (plan → search). Собираем после всех попыток, иначе в список
    # попали бы и те, что нашлись в каталоге или живым поиском.
    unresolved: list[dict] = []
    for row in items:
        product_id = int(row["product_id"])
        # Служебная строка чека (аренда, перевозка, штраф, «Сборка») — не товар: ни
        # каталог, ни живой поиск сети по ней не спрашиваем. Это экономит запросы к
        # сети и время владельца, которое уходило на заведомо пустой поиск (task-27).
        if _service_line(row.get("name")):
            skipped += 1
            continue
        # Только настоящая связь: заглушка сеть товара не знает, и «уже связано» по ней
        # означало «не класть и не спрашивать» — так двенадцать позиций demo исчезали.
        if repo.confirmed_mapping(product_id, shop.id, real_only=True):
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
                # Находкой считаем только то, что пригодится наряду: настоящий артикул.
                # Поиск сети может «подтвердить» и ту же заглушку снова (замер 18.10.2026,
                # app/matcher: у Пятёрочки и Самоката живой поиск отдаёт hist-), а
                # cartplan берёт только настоящий артикул — счётчик обещал бы находку,
                # которой в корзине не будет.
                if repo.confirmed_mapping(product_id, shop.id, real_only=True):
                    by_search += 1
        except Exception:  # noqa: BLE001 — сеть не ответила: позиция останется незнакомой
            log.warning("%s: поиск сети по позиции %s не удался", store, product_id, exc_info=True)
        if not repo.confirmed_mapping(product_id, shop.id, real_only=True):
            unresolved.append({"product_id": product_id, "name": product.name})
    # Сколько осталось не спрошенным: предел живого поиска объясняет разницу между
    # «искали все» и «искали десять», и наряд говорит это человеку.
    unasked = max(0, tried - linked - searched)
    log.info("%s: незнакомых позиций корзины %d, в каталоге %d, поиском сети %d из %d "
             "(не спрошено %d: предел %d), телефону искать %d, служебных пропущено %d",
             store, tried, linked, by_search, searched, unasked, LIVE_LIMIT, len(unresolved),
             skipped)
    return {"linked": linked + by_search, "tried": tried, "searched": searched,
            "by_search": by_search, "in_catalog": linked, "unasked": unasked,
            "unresolved": unresolved, "skipped": skipped,
            "size": len(items),
            "basket": str((basket or {}).get("name") or f"№{basket_id}")}


def _link_note(found: dict) -> str:
    """Что сказать человеку о поиске незнакомых позиций — честно и с пределом.

    Предел LIVE_LIMIT бережёт сеть (не чаще connectors.rate_limit_rps), и молчать о нём
    нельзя: без этих слов наряд обещает, что незнакомые позиции были поисканы, а часть
    их просто не спросили — на замере 18.10.2026 двенадцать позиций при пределе десять.
    """
    tried = int(found.get("tried") or 0)
    if not tried:
        return ""
    text = (f" Незнакомых спрашивали: в каталоге нашлось {found.get('in_catalog') or 0}, "
            f"живым поиском сети — {found.get('by_search') or 0} "
            f"из {found.get('searched') or 0} спрошенных")
    unasked = int(found.get("unasked") or 0)
    if unasked:
        text += (f", ещё {unasked} не спрошены — предел живого поиска {LIVE_LIMIT} "
                 "за наряд (сеть бережём)")
    return text + "."


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


# Наклейки витрины в начале названия товара. Их снимает и приложение
# (MainActivity.CARDS_JS), но база не должна зависеть от версии приложения: строки
# Пятёрочки, пришедшие 18–19.10.2026, лежали как «-22% 4,95 Томатная паста
# Помидорка 250мл 250 мл 205 00 ₽» — по такому названию ни показать человеку, ни
# сопоставить. Правила те же, что в приложении, и сняты с живой витрины.
_CARD_BADGES = (
    r"^[-\u2212]?\d+(?:[.,]\d+)?\s*%",
    r"^%",
    r"^на\s+\d+\s*[а-яa-z]+",
    r"^от\s+\d+\s*шт\.?",
    r"^Нет оценок",
    r"^Комбо\s+\d[.,]\d{1,2}",
    r"^\d+[.,]\d{1,2}",
    r"^(?:Хит|Новинка|Выгодно)\s*",
    # «1 2 3 4 5 6 7 8» — переключатель страниц выдачи, стоящий внутри карточки
    # Самоката (замер 19.10.2026: имя приходило как «1 2 3 4 5 6 7 8 Больше нет
    # Омлет-ролл Creative Kitchen, …»). И слово витрины о том, что товара нет.
    r"^(?:\d+\s+){2,}",
    r"^(?:Больше нет|Нет в наличии)\s*",
)
# Цена в тексте карточки: «205 00 ₽», «299.99 руб». Название кончается там, где она
# начинается, — всё после неё (скидки, «В корзину») к имени товара не относится.
_CARD_PRICE = re.compile(r"\d[\d\s]{0,8}(?:[.,]\d{1,2})?\s*(?:₽|руб)", re.I)


def _card_name(raw: Any) -> str:
    """Название товара из текста карточки: без наклеек витрины и без хвоста с ценой.

    Пусто — карточка без имени: сервер её не примет, и это честнее выдуманного имени
    (по выдуманному потом не сопоставить).
    """
    text = " ".join(str(raw or "").split())
    cut = _CARD_PRICE.search(text)
    if cut:
        text = text[:cut.start()]
    for _ in range(6):
        before = text
        for pattern in _CARD_BADGES:
            text = re.sub(pattern, "", text, count=1, flags=re.I).strip()
        if text == before:
            break
    return _text(text, 120)


def accept_report(body: dict) -> dict:
    """Принять отчёт телефона о собранной корзине и сохранить последний по сети."""
    store = _store(body)
    items = []
    for raw in (body.get("items") or [])[:ITEMS_LIMIT]:
        if not isinstance(raw, dict):
            continue
        item = {
            # Название чистим тем же `_card_name`, что и карточки: отчёт приходит и из
            # инструментов, которые кладут туда текст карточки целиком. Замер 20.10.2026:
            # у Пятёрочки 25 строк с «-22% 4,95 Томатная паста … 205 00 ₽» в имени —
            # они пришли ОТЧЁТОМ, а не карточками, и мимо чистки.
            "name": _card_name(raw.get("name")),
            "sku": _text(raw.get("sku"), 40),
            # Адрес карточки, которую телефон открывал. По нему сервер достаёт настоящий
            # артикул сети и заводит товар в единой базе (app/catalog/fromurl.py) — это
            # единственная дорога для Пятёрочки и Самоката, куда сервер не пускают.
            "url": _text(raw.get("url"), 200),
            "ok": bool(raw.get("ok")),
            # Цена, которую сеть показала за эту позицию. Для Перекрёстка, Vprok и Монетки
            # корзина человека — единственное место, где цена вообще видна: их обход
            # отдаёт только названия (в базе ноль цен на 194 тысячи строк).
            "price": _num(raw.get("price")),
            "why": _text(raw.get("why"), 200),
            "put": _count(raw.get("put")) or 0,
            "times": _count(raw.get("times")) or 0,
            # Чем легла позиция: касанием кнопки сайта, повтором запроса сети или уже
            # лежала в корзине сети с прошлой сборки (kept) — тогда её не трогали.
            "how": raw.get("how") if raw.get("how") in HOWS else "tap",
            # Какие запросы к корзине ушли после нажатий: «PUT /webgate/v1/carts/lite 201».
            # Путь телефон уже свернул ({id}); здесь сворачиваем ещё раз.
            "calls": [_call(c) for c in (raw.get("calls") or [])[:6] if isinstance(c, str)],
        }
        # Пометка строки с телефона: «нашёл по короткому запросу» — позиция нашлась
        # вторым, коротким запросом поиска сети. Храним рядом с остальными полями
        # пункта; пустое не пишем, чтобы отчёт не обрастал пустыми ключами.
        note = _text(raw.get("note"), 200)
        if note:
            item["note"] = note
        # ИМЯ С КАРТОЧКИ СЕТИ, ОТДЕЛЬНО ОТ НАШЕГО (21.10.2026). `name` выше — название
        # позиции из наряда; телефон кладёт имя найденной карточки сети в `found_name`,
        # и `_learn` учит каталог сети по нему. Дикси: «Туалетная бумага Papia Балийский
        # цветок» у сети против нашего «Papia Bali Flower» — по нашему имени база решила
        # бы, что сеть называет товар так же. Пустое не пишем: старое приложение поля не
        # присылает, и выбор имени остаётся прежним.
        found_name = _text(raw.get("found_name"), 120)
        if found_name:
            item["found_name"] = found_name
        # НАЛИЧИЕ С КАРТОЧКИ, ОТДЕЛЬНО ОТ `ok` (приложение 1.13, Z7h). Телефон видит
        # «Нет в наличии/Больше нет» на карточке и присылает это в `in_stock`; позиция
        # при этом могла не лечь по другой причине («кнопки не нашлось»), и прежний
        # вывод наличия из `ok`/`why` (refresh.note_device_stock) терял такой товар.
        # Берём только настоящий bool: строка «true» и число 1 наличием не считаются —
        # иначе сервер выдумал бы то, чего телефон не говорил. Нет поля (старые
        # отчёты) — ключа нет, и наличие считается по-прежнему по `ok`/`why`.
        if isinstance(raw.get("in_stock"), bool):
            item["in_stock"] = bool(raw["in_stock"])
        items.append(item)

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

    extra = []
    for raw in (body.get("extra") or [])[:ITEMS_LIMIT]:
        if isinstance(raw, dict) and _text(raw.get("name"), 120):
            extra.append({"name": _text(raw.get("name"), 120), "sku": _text(raw.get("sku"), 40),
                          "more": _count(raw.get("more")) or 1})

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
        # Сама, в скрытом окне, пока человек смотрел корзину, — или кнопкой «Собрать».
        "background": bool(body.get("background")),
        "basket": _count(body.get("basket")),
        "stamp": _text(body.get("stamp"), 40),
        "extra": extra,
        "seconds": _count(body.get("seconds")),
    }
    repo.set_setting(REPORT_KEY + store, json.dumps(report, ensure_ascii=False))
    log.info("корзина с телефона, %s: легло %d из %d, итог сети %s, полей из ответов %d",
             store, report["landed"], report["planned"], total_of(report),
             sum(len(v) for v in found.values()))
    _learn(report)
    _stock(report)
    _to_row(report)
    # Отметка «телефон принёс отчёт о корзине» — это и есть доказательство, что фоновая
    # сборка работает. Проверка базы показывает её и ругается, если телефон молчит
    # больше недели (store.mark_device, app/catalog/checkup.py).
    try:
        from app.catalog import store as catalog_store

        catalog_store.mark_device(store, "report", int(report.get("landed") or 0),
                                  int(report.get("planned") or 0))
    except Exception:  # noqa: BLE001 — отметка не должна ломать приём отчёта
        log.exception("не смог отметить приход отчёта для %s", store)
    return {"ok": True, "store": store, "landed": report["landed"],
            "planned": report["planned"], "total": total_of(report),
            "network": network_total(report)}


def accept_cards(body: dict) -> dict:
    """Карточки витрины, которые телефон прочитал на странице сети, — в единую базу.

    ЗАЧЕМ. Цель — разобрать товары ВСЕХ сетей для сопоставления. Девять сетей сервер
    обходит сам, а Пятёрочку и Самокат не пускает: витрина отвечает проверкой и с
    серверного адреса, и с домашнего (замеры 18–19.10.2026). Телефон человека эти
    витрины видит; приложение читает карточки со страницы раздела и присылает их
    сюда. Одна карточка — один товар: адрес даёт настоящий артикул сети
    (app/catalog/fromurl.py), название и цена приходят с витрины, наличие — с неё же.

    ЧТО ЭТО НЕ ТАК. Это не заливка каталога: принимаем только карточки, у которых
    вышел артикул по правилу ЭТОЙ сети и адрес принадлежит её хостам, и не больше
    CARDS_LIMIT за заход. Заглушки (model.placeholder_sku) и строки без названия
    отбрасываются: по ним в корзину сети ничего не положить. Название и марка у уже
    известной строки остаются сборщика (refresh.learn_device_rows), а ЦЕНА и НАЛИЧИЕ
    из карточки обновляются и у знакомых строк: у Перекрёстка, Vprok и Монетки обход
    отдаёт одни названия, и цена человека — единственная, что у них есть.

    Форма запроса: {store, cards: [{url, name, price, in_stock}], page?}.
    """
    from app.catalog import fromurl, refresh
    from app.catalog import store as catalog_store

    store = _store(body)
    raw_cards = body.get("cards")
    if not isinstance(raw_cards, list):
        raise ValueError("В запросе нет списка cards")
    cards = [c for c in raw_cards[:CARDS_LIMIT] if isinstance(c, dict)]

    rows = []
    stock = []
    no_sku = no_name = 0
    priced = 0
    for card in cards:
        url = _text(card.get("url"), 200)
        name = _card_name(card.get("name"))
        if not name:
            no_name += 1
            continue
        sku = fromurl.sku_of(store, url) if url else None
        if not sku:
            no_sku += 1
            continue
        price = _num(card.get("price"))
        priced += 1 if price is not None else 0
        rows.append({"sku": sku, "name": name, "url": url, "price": price,
                     "in_stock": card.get("in_stock") if isinstance(card.get("in_stock"), bool)
                     else None})
        if isinstance(card.get("in_stock"), bool):
            stock.append({"sku": sku, "ok": bool(card["in_stock"]),
                          "why": "" if card["in_stock"] else "нет в наличии"})

    learned = refresh.learn_device_rows(store, rows, limit=CARDS_LIMIT) if rows else 0
    stocked = refresh.note_device_stock(store, stock) if stock else 0
    # ЦЕНА — И УЖЕ ЗНАКОМЫМ СТРОКАМ. learn_device_rows заводит только незнакомые
    # артикулы, а у Перекрёстка (158 698 строк), Vprok (27 551) и Монетки (7 772) цены
    # нет ни у одной: их обход отдаёт только названия. Человек открывает раздел — и цены
    # тех товаров, что он видит, приходят в базу. Без этого шага чтение раздела этим трём
    # сетям не давало ничего: все артикулы уже известны, и строки пропускались.
    priced_rows = [(row["price"], row["sku"]) for row in rows if row.get("price")]
    repriced = catalog_store.set_prices(store, priced_rows) if priced_rows else 0
    log.info("карточки с телефона, %s: принято %d, с артикулом %d, заведено %d, "
             "наличие обновилось у %d, цен у %d", store, len(cards), len(rows), learned,
             stocked, repriced)
    # Отметка для проверки базы: когда телефон последний раз приносил этой сети карточки.
    # Без неё не ответить на вопрос «а телефон-то ещё работает?» — а Пятёрочка и Самокат
    # живут только им (app/catalog/checkup.py показывает это в отчёте).
    try:
        catalog_store.mark_device(store, "cards", len(cards), learned)
    except Exception:  # noqa: BLE001 — отметка не должна ломать приём
        log.exception("не смог отметить приход карточек для %s", store)
    return {"ok": True, "store": store, "got": len(cards), "with_sku": len(rows),
            "learned": learned, "stocked": stocked, "priced": priced, "repriced": repriced,
            # Что не взяли и почему — телефону это нужно, чтобы не повторять однажды
            # отвергнутое и видеть, работает ли правило артикула у этой сети.
            "no_sku": no_sku, "no_name": no_name,
            "sku_rule": bool(fromurl.RULES.get(store))}


def progress(body: dict) -> dict:
    """Ход сборки телефона в одной сети — в строку «Сборки корзин» (cartrun.progress).

    Телефон шлёт его, начиная сборку, на каждой позиции и когда сборка кончилась без
    отчёта («класть нечего», сеть увела на страницу входа). Форма: {store, basket,
    stamp, planned, at, now, state, note}. Сеть, куда сейчас кладёт сервер, отвечает
    отказом — ValueError, и телефон отступает.
    """
    from app import cartrun

    store = _store(body)
    row = cartrun.progress({
        "store": store, "basket": body.get("basket"), "stamp": _text(body.get("stamp"), 40),
        "planned": _count(body.get("planned")) or 0, "at": _count(body.get("at")) or 0,
        "now": _text(body.get("now"), 160), "state": _text(body.get("state"), 10),
        "note": _text(body.get("note"), 400),
    }, by=cartrun.DEVICE)
    return {"ok": True, "store": store, "state": row.state}


def _page(report: dict, kind: str) -> float | None:
    for row in report.get("page_totals") or []:
        if row.get("kind") == kind:
            return row.get("amount")
    return None


def _delivery(report: dict, total: float) -> float | None:
    """Доставка со страницы корзины: строка «Доставка», которая меньше итога.

    Замер на эмуляторе 23.10.2026, Перекрёсток: кнопка оформления («826,35 ₽ Доставка
    сегодня Оформить заказ») читается страницей как строка доставки с суммой итога.
    Доставка не бывает не меньше итога заказа, и такая строка — это сам итог.
    """
    for row in report.get("page_totals") or []:
        amount = row.get("amount")
        if row.get("kind") == "delivery" and amount is not None and amount < total:
            return amount
    return None


def _learn(report: dict) -> None:
    """Донести до единой базы товар, который человек сам нашёл в телефоне.

    ЗАЧЕМ. Пятёрочку и Самокат сервер не пускает ни с серверного адреса, ни с домашнего
    (замер 18.10.2026), поэтому их каталога в единой базе нет и сопоставлять их строки не
    с чем. Приложение присылает адрес карточки у легшей позиции; из адреса берётся
    настоящий артикул сети (app/catalog/fromurl.py), и товар заводится в общей базе
    (refresh.learn_device_rows). Со следующего наряда эта сеть знает товар, а ночное
    сопоставление получает строку, которой ему не хватало.

    КЛАДКА — БОЛЬШЕ НЕ УСЛОВИЕ (приложение 1.14, Z11). Телефон присылает настоящий артикул
    сети из её же поиска (item["sku"]) и у НЕ легших позиций: живой случай 21.10.2026 —
    Дикси, карточка отвечает 403, все позиции ok=false, а артикул 2000642072 в отчёте
    есть, и в каталоге за час не появилось ни одной строки Дикси. Учить такую строку
    безопасно: в каталог СЕТИ уходят её артикул и её имя, наш товар не затрагивается —
    связь с ним эта строка сама не создаёт, сопоставление идёт своей дорогой
    (app/catalog/link.py: кандидат или подтверждение по чеку и штрихкоду). Поэтому ok
    больше не обязателен.

    Адрес остаётся обязательным и только своим: без него не проверить, что артикул и имя
    принадлежат этой сети, — поэтому пункт без адреса пропускается, даже когда артикул в
    отчёте есть (безопасность важнее полноты).

    Имя — с карточки сети (`item["found_name"]`), а наше название позиции из наряда
    (`item["name"]`) — только запасной путь: иначе база приписала бы сети наше имя.

    Цена и наличие относятся к тому же артикулу сети, что и `sku`, поэтому передаются
    вместе со строкой.

    Отчёт от этого не падает никогда: товар в базе — прибыль, а не условие приёма отчёта.
    """
    from app.catalog import fromurl, model, refresh

    chain = str(report.get("store") or "")
    hosts = tuple(cartrecord.CHAIN_HOSTS.get(chain) or ())
    rows = []
    # Известные строки, которые телефон видел: оживляются отдельно (ниже, `_revive_seen`).
    seen: list[str] = []
    for item in report.get("items") or []:
        url = str(item.get("url") or "")
        # Адрес — только этой сети: чужой (или подделанный) в базу не пускаем, и без
        # адреса не проверить, что артикул и имя — этой сети.
        if not url or not hosts or not any(host in url for host in hosts):
            continue
        # Артикул из поиска сети (item["sku"]) главнее адреса: телефон присылает его и
        # тогда, когда карточка не открылась. Адрес — прежний путь для отчётов, где
        # артикул не прислали.
        sku = str(item.get("sku") or "").strip() or fromurl.sku_of(chain, url)
        # ЦЕНА — ДОКАЗАТЕЛЬСТВО, ЧТО КАРТОЧКА ОТРИСОВАЛАСЬ. Без неё (403, бот-защита,
        # пустая страница) цену не прочитать, и это не наблюдение: оживлять строку
        # нельзя — иначе `last_seen` обновился бы без основания. Наблюдение собирается
        # из тех же строк своей сети, что и учёба, но не зависит от неё: известный
        # артикул `learn_device_rows` пропустит, а оживить его всё равно надо.
        price = _num(item.get("price"))
        if price is not None and price > 0 and sku and not model.placeholder_sku(sku, chain):
            if sku not in seen:
                seen.append(sku)
        # Имя — с карточки сети (item["found_name"]); наше название позиции из наряда —
        # только запасной путь для старых отчётов, где поля нет. Иначе база приписала бы
        # сети наше название (Дикси: «Туалетная бумага Papia Балийский цветок» против
        # нашего «Papia Bali Flower»).
        name = _text(item.get("found_name") or item.get("name"), 120)
        # Заглушка — не артикул сети (model.placeholder_sku): по ней в корзину сети
        # ничего не положить. Пустое имя строке каталога тоже не годится.
        if not sku or not name or model.placeholder_sku(sku, chain):
            continue
        row = {"sku": sku, "name": name, "url": url}
        # Цена и наличие — того же артикула сети, что и sku: телефон прочитал их на
        # открытой карточке, и строка заводится сразу с ними. Цена — только
        # положительное число (ноль, строка и мусор ценой не считаются), наличие —
        # только настоящий bool («true» строкой наличием не считается).
        if price is not None and price > 0:
            row["price"] = price
        if isinstance(item.get("in_stock"), bool):
            row["in_stock"] = bool(item["in_stock"])
        rows.append(row)
    if not rows:
        # Новых строк нет — но наблюдение телефона не пропадает: известные строки
        # оживают и здесь. Ранний return не должен съесть этот шаг.
        _revive_seen(chain, seen)
        return
    learned = 0
    try:
        learned = refresh.learn_device_rows(chain, rows)
    except Exception:  # noqa: BLE001 — база не приняла: отчёт всё равно сохранён
        log.warning("%s: товары с телефона не завелись в единой базе", chain, exc_info=True)
    # Наблюдение — ПОСЛЕ учёбы: `learn_device_rows` заводит только НОВЫЕ артикулы, а
    # известные пропускает; оживить их — отдельный шаг (`store.touch_seen`).
    _revive_seen(chain, seen)
    if learned:
        log.info("%s: единая база пополнилась с телефона — %d товар(ов)", chain, learned)


def _revive_seen(chain: str, seen: list[str]) -> int:
    """Оживить известные строки каталога, карточку которых телефон видел своими глазами.

    ЗАЧЕМ. Живость строки ведёт обход, но если человек открыл карточку товара, которого
    обход в перечне не увидел, строка считается мёртвой (`active=0`) — и сопоставление
    её не видит, хотя товар в сети есть. Наблюдение человека — доказательство жизни, и
    `learn_device_rows` его не записывает: он заводит только новые артикулы, известные
    пропускает. Поэтому шаг отдельный.

    ЧТО ЗДЕСЬ НЕ ОШИБКА. `touch_seen` появляется в app/catalog/store.py отдельной
    правкой; пока её нет, приём отчёта обязан работать как раньше — поэтому отсутствие
    функции (`AttributeError`) даёт ноль, но всё равно пишет предупреждение: молчаливый
    пропуск — это ровно тот способ незаметно потерять данные, каким чуть не потерялся
    `found_name` (21.10.2026). Любая другая беда — тоже только запись в журнал: отчёт
    о корзине уже сохранён, и товар в базе — прибыль, а не условие приёма (то же
    правило, что у учёбы).
    """
    if not seen:
        return 0
    try:
        from app.catalog import store

        touched = store.touch_seen(chain, seen)
    except AttributeError:
        log.warning("%s: store.touch_seen отсутствует — известные строки не оживлены", chain)
        return 0
    except Exception:  # noqa: BLE001 — приёмка отчёта не должна падать
        log.warning("%s: наблюдение телефона не оживило строки каталога", chain, exc_info=True)
        return 0
    if touched:
        log.info("%s: строк оживлено наблюдением телефона — %d", chain, touched)
    return touched


def _stock(report: dict) -> None:
    """Наличие и цены из корзины сети — в единую базу (решение владельца 18.10.2026).

    Для Пятёрочки и Самоката корзина человека — единственный источник наличия,
    для Перекрёстка, Vprok и Монетки — ещё и единственный источник цены: сервер к ним
    не пробивается либо получает только названия (замеры 18–19.10.2026).
    """
    from app.catalog import refresh

    chain = str(report.get("store") or "")
    try:
        refresh.note_device_stock(chain, report.get("items") or [])
    except Exception:  # noqa: BLE001 — наличие не прибыль отчёта: он уже сохранён
        log.warning("%s: наличие с телефона не записалось", chain, exc_info=True)
    try:
        refresh.note_device_prices(chain, report.get("items") or [])
    except Exception:  # noqa: BLE001 — цена тоже не прибыль отчёта
        log.warning("%s: цены с телефона не записались", chain, exc_info=True)


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
            # «Посчитала сеть» — только то, что сеть назвала к оплате: сверенный ответ или
            # строка итога. Нет её, и total_of взяла «Товары»: это сумма товаров без скидок
            # и сборки, и итогом сети она не подаётся (замер на эмуляторе 23.10.2026:
            # итог сети — товары − скидки + доставка + сборка, app/netprice.py).
            paid = network is not None or any(
                r.get("kind") == "total" for r in report.get("page_totals") or [])
            total = {"sum": amount, "source": cartrun.NETWORK if paid else cartrun.OURS,
                     "goods": goods, "discount": discount, "delivery": _delivery(report, amount),
                     "count": network["count"] if network else report.get("cart_count")}
        items = report.get("items") or []
        kept = sum(1 for item in items if item.get("ok") and item.get("how") == "kept")
        note = ("собрано с телефона само, пока была открыта корзина" if report.get("background")
                else "собрано с телефона кнопками сайта")
        if kept:
            note += f"; {kept} поз. уже лежали в корзине сети с прошлой сборки — их не трогали"
        if report.get("extra"):
            note += ("; в корзине сети осталось и прежнее, чего в вашей корзине теперь нет — "
                     "приложение его не убирает")
        # ПОЧЕМУ НЕ ВЫШЛО — В САМУ СТРОКУ, А НЕ ТОЛЬКО В ПОЗИЦИИ. Замер 20.10.2026 на экране
        # «Корзина»: четыре сети показывали «не вышло», а причина (стена «Выключите VPN»,
        # «нет входа», «нет в наличии») лежала только внутри позиций. Человек видел «не
        # вышло» и не знал, что делать: ждать, входить или менять адрес.
        whys: list[str] = []
        for item in items:
            if item.get("ok"):
                continue
            why = " ".join(str(item.get("why") or "").split())[:80]
            if why and why not in whys:
                whys.append(why)
        if whys:
            note += (f" — {whys[0]}" if len(whys) == 1
                     else f" — причины разные: «{whys[0]}», «{whys[1]}»")
        # ЭТАП 2: РАСПОЗНАВАНИЕ «ВЫБЕРИТЕ МАГАЗИН» И «НУЖЕН ВХОД» ПО ПРИЧИНАМ ПОЗИЦИЙ.
        # Магнит возвращает «выберите магазин» при отсутствии выбранной точки, Дикси —
        # «войдите заново» при протухшей сессии. Если хотя бы одна пропущенная
        # позиция несёт такую причину — строка сети переходит в LOGIN с понятным действием
        # (store: «Выбрать магазин», again: «Войти заново»), а не в FAILED:
        # экран покажет конкретную кнопку действия вместо тихого «легло 0».
        store_detected = any(
            cartrun.STORE_WHY.search(str(item.get("why") or ""))
            for item in items
            if not item.get("ok")
        )
        login_detected = any(
            cartrun.LOGIN_WHY.search(str(item.get("why") or ""))
            for item in items
            if not item.get("ok")
        )
        reply = {
            "store": report["store"],
            "landed": [item["name"] for item in items if item.get("ok")],
            "missing": [{"name": item["name"], "why": item.get("why") or "не легло"}
                        for item in items if not item.get("ok")],
            "total": total,
            "note": note,
            "extra": report.get("extra") or [],
        }
        if store_detected:
            reply["state"] = cartrun.LOGIN
            reply["login"] = "store"
        elif login_detected:
            reply["state"] = cartrun.LOGIN
            reply["login"] = "again"
        if report.get("basket"):
            reply["basket"] = report["basket"]
        if report.get("stamp"):
            reply["stamp"] = report["stamp"]
        cartrun.accept(reply, by=cartrun.DEVICE)
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


__all__ = ["CHAINS", "CART_PAGES", "KINDS", "API_TOTALS", "FULL_CART", "QTY_SETS", "EMPTY_CART",
           "rules", "plan", "accept_report", "accept_cards", "progress", "network_total",
           "total_of", "last"]
