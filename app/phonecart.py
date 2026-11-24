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
import threading
import time
from datetime import datetime
from typing import Any

from app import cartrecord, config, repo

log = logging.getLogger(__name__)

# Сети, чьи сайты приложение открывает у себя. Свои адреса — те же, что у записи.
CHAINS = tuple(cartrecord.CHAIN_HOSTS)

# Повторные попытки поиска незнакомых позиций корзины не чаще RETRY_SECONDS.
# Ключ: (вид, путь базы, сеть, product_id) -> time.time().
RETRY_SECONDS = 6 * 3600
LINK_BUDGET_SECONDS = 2.5
_TRIED: dict[tuple[str, str, str, int], float] = {}
_TRIED_LOCK = threading.Lock()


def _tried_mark(kind: str, db_path: str, store: str, product_id: int) -> None:
    with _TRIED_LOCK:
        _TRIED[(str(kind), str(db_path), str(store), int(product_id))] = time.time()


def _is_tried(kind: str, db_path: str, store: str, product_id: int) -> bool:
    with _TRIED_LOCK:
        ts = _TRIED.get((str(kind), str(db_path), str(store), int(product_id)))
    return bool(ts and (time.time() - ts) < RETRY_SECONDS)


# (база, сеть, товар) → когда за него взялся фоновый поиск сети (_ask_later): через секунды сеть может его
# узнать. Поиск идёт секунды; запись старше ASKING_SECONDS — поток завис или не стартовал, товар спрошен.
_ASKING: dict[tuple[str, str, int], float] = {}
ASKING_SECONDS = 120


def _settled(db_path: str, store: str, product_id: int) -> bool:
    """Сервер с незнакомым товаром закончил: каталог и поиск сети спрошены, фоновый поиск не идёт."""
    # Порядок чтения обратен порядку записи в _link (сначала _ASKING, потом «спрошен»): увидев «спрошен»,
    # запись о начатом поиске уже не пропустим.
    if not _is_tried("link", db_path, store, product_id):
        return False
    asked = _ASKING.get((str(db_path), str(store), int(product_id)))
    return asked is None or time.monotonic() - asked > ASKING_SECONDS


# Каталог отвечает на вопрос одинаково для всех сетей, а наряды сетей после правки спрашивают его разом.
# Каждый вопрос — проход по всей таблице каталога, около 0,1 с. Замер 13.11.2026: наряд на новый товар шёл
# 3,1–3,5 с вместо 0,4 с, и Магнит не уложился в 5 с от касания. Ответ помним: вопрос → (когда, ответ).
_ANSWERS: dict[tuple, tuple[float, Any]] = {}
_ANSWER_GATES: dict[tuple, threading.Lock] = {}
ANSWERS_KEEP = 2000
ANSWER_WAIT_SECONDS = 3     # дольше чужой ответ не ждём: каталог завис — спрашиваем сами, как до этой памяти
RESOLVE_SECONDS = 60        # опознание товара: на время одной правки, дальше его не зовёт пометка «спрошен»


def _asked_once(key: tuple, seconds: float, ask):
    """Ответ каталога на вопрос: свежий — из памяти, иначе спрашивает один поток, остальные ждут его.

    Ответ общий для всех потоков и людей: менять его нельзя. Отказ каталога (исключение) не запоминается.
    """
    gate = _ANSWER_GATES.setdefault(key, threading.Lock())
    got = gate.acquire(timeout=ANSWER_WAIT_SECONDS)
    try:
        hit = _ANSWERS.get(key)
        if hit and time.monotonic() - hit[0] < seconds:
            return hit[1]
        if len(_ANSWER_GATES) > ANSWERS_KEEP:       # замок есть и у вопроса без ответа: считаем их
            _ANSWERS.clear()
            _ANSWER_GATES.clear()
        answer = ask()
        _ANSWERS[key] = (time.monotonic(), answer)
        return answer
    finally:
        if got:
            gate.release()


# Наряды телефона идут раньше фонового счёта. Наряд — 330–660 коротких запросов к базе, и рядом со счётным
# потоком того же процесса (расчёт «Где дешевле», пересчёт цен после правки) каждый запрос ждёт GIL. Замер
# 13.11.2026: шесть нарядов разом — 0,45 с без помех и больше 8 с рядом со счётом; на телефоне наряд на
# новый товар шёл 3,1–3,3 с, и три сети из пяти не уложились в 5 с от касания.
_PLANS_LOCK = threading.Lock()
_PLANS = {"now": 0, "until": 0.0}   # сколько нарядов считается; до какого времени (monotonic) их ещё ждём
PLANS_AFTER_EDIT_SEC = 2.0          # телефон спрашивает наряды через 0,3–1,7 с после правки
PLANS_GAP_SEC = 1.0                 # наряды сетей приходят не разом: новому соединению нужно до секунды
PLANS_WAIT_SEC = 3.0                # дольше счёт не ждёт: расчёт «Где дешевле» стартует не позже 5 с от правки


def plans_expected() -> None:
    """Корзину только что правили: телефон вот-вот спросит наряды сетей, фоновый счёт их пропустит."""
    with _PLANS_LOCK:
        _PLANS["until"] = max(_PLANS["until"], time.monotonic() + PLANS_AFTER_EDIT_SEC)


def wait_for_plans() -> None:
    """Фоновый счёт ждёт, пока наряды телефона считаются или вот-вот придут, но не дольше PLANS_WAIT_SEC."""
    end = time.monotonic() + PLANS_WAIT_SEC
    while time.monotonic() < end:
        with _PLANS_LOCK:
            if not _PLANS["now"] and time.monotonic() >= _PLANS["until"]:
                return
        time.sleep(0.05)


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

# Страницы корзины, где копейки стоят вплотную к рублям: «1 541» и «87» телефон читает как 54187
# (Пятёрочка, замер 11.11.2026: строка сети показала 54 187 ₽ за три товара). Суммам такой
# страницы не верим: итог сети есть только в отчёте запросами cartapi.js. Ответ сайта о заказе
# (/api/orders/v11/orders/<номер>/, basket.final_sum и basket.total_sum в рублях) сторож
# приложения тоже передаёт, но хранит его между сборками, и свежесть серверу не проверить.
# shortcut: лечится разбором страницы в приложении (READ_JS) или меткой свежести у сторожа;
# починят — убрать сеть отсюда.
GLUED_PAGES: tuple[str, ...] = ("pyaterochka",)

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

# Телефон кладёт эти сети запросами из cartapi.js (APK 1.24, Магнит — с 1.26, METRO — с 1.27, Самокат — с 1.29,
# Впрок — с 1.62: старые версии адреса их окна не знают и кладут касаниями); убрать сеть из кортежа — вернуть
# её на касания без выпуска APK.
API_CARTS: tuple[str, ...] = ("perekrestok", "pyaterochka", "dixy", "magnit", "metro", "samokat", "vprok")

# С какой версии приложения адаптер сети в cartapi.js годен. Самокат из 1.28 брал витрину по точке
# из кода и считал копейки сети рублями: до 1.29 сеть остаётся на касаниях. Версию приложение шлёт
# в запросе наряда с 1.29, у прежних её там нет. Впрок: окно сети с запросами появилось в 1.62.
API_SINCE: dict[str, tuple[int, ...]] = {"samokat": (1, 29), "vprok": (1, 62)}

# Корзина сети пуста — так пишут её страницы. Если приложение помнит, что клало сюда,
# а сеть показывает пустую корзину, человек оформил заказ или очистил её сам.
EMPTY_CART = (r"корзина пуста|в корзине пока|в корзине пусто|в корзине нет товаров|"
              r"в корзине ничего нет|пустая корзина")

HOWS = ("tap", "request", "kept")

REPORT_KEY = "phonecart.last."
# Журнал времени правок «от касания до отчёта»: строка на отчёт, последние LAGS_KEEP. Цель
# владельца — не дольше 5 с в каждой сети, а строки журнала сервера стирает каждая выкладка.
# Восьмым в строке, если приложение его прислало (1.44), — сколько сборка ждала наряд, мс.
LAGS_KEY = "phonecart.lags"
LAGS_KEEP = 300
_LAGS_LOCK = threading.Lock()   # отчёты сетей приходят разом, журнал читается и пишется целиком
# Журнал отказов: последние FAILS_KEEP отчётов, где легло не всё, с причинами. Следующий отчёт стирает
# прошлый (REPORT_KEY) за секунды, а телефон владельца отладчиком не посмотреть: 13.11.2026 Дикси и
# Пятёрочка ответили «легло 0 из 11», и причины ушли вместе с отчётами.
FAILS_KEY = "phonecart.fails"
FAILS_KEEP = 40
# Хроника приложения (1.54): последние строки его журнала при отчёте. 14.11.2026 Пятёрочка не собралась на пяти
# нажатиях подряд (три другие сети уложились в 3,2 с), а чем занималось окно сети, серверу не видно:
# отчёт один на сборку, а нажатия, пропущенные до сборки, отчёта не дают вовсе. Хранится отдельно от отчёта:
# следующий отчёт той же сети его стёр бы.
TRACES_KEY = "phonecart.traces"
TRACES_KEEP = 6
TRACE_LINES = 160
TRACE_WIDTH = 220
# Хроника без отчёта (1.55): сборка идёт дольше 30 с, а отчёта нет — приложение шлёт журнал само. 15.11.2026 строки
# Магнита и Пятёрочки провисели «идёт» две с лишним минуты после запуска приложения, и хроника, привязанная к отчёту,
# не доехала: отчёта у такой сборки не бывает. Отдельный журнал: частые нажатия не вытесняют хроники отчётов.
SLOW_KEY = "phonecart.slowtraces"
SLOW_KEEP = 8
# Так приложение называет в раннем отчёте позицию, которую поищет в сети после знакомых (finishRun).
LATER_WHY = "ищу в сети после знакомых"
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
# Артикулов «снять своё» за один наряд (приложение 1.19): своего в сети не больше корзины.
DROP_LIMIT = ITEMS_LIMIT
PAGE_LIMIT = 20
API_PATHS_LIMIT = 6
API_FIELDS_LIMIT = 40


def _store(body: dict) -> str:
    code = str(body.get("store") or "").strip().lower()
    if code not in CHAINS:
        raise ValueError(f"сеть «{code or '—'}» приложение не открывает")
    return code


def _version(text) -> tuple[int, ...]:
    """«1.29» → (1, 29); пусто или не числа через точку → ()."""
    parts = str(text or "").strip().split(".")
    ok = all(p.isascii() and p.isdigit() and len(p) <= 6 for p in parts)
    return tuple(int(p) for p in parts) if ok else ()


def rules(store: str, app_version: str | None = None) -> dict:
    """Чем телефон отличает кнопку товара и с каким темпом жмёт — из серверных правил."""
    from app.shopbrowser import cart, signals

    return {
        "add_name": signals.ADD_NAME,
        "plus_name": signals.PLUS_NAME,
        "minus_name": signals.MINUS_NAME,
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
        # Телефон кладёт эти сети запросами из cartapi.js (APK 1.24): Перекрёсток, Пятёрочка, Дикси.
        "api": store in API_CARTS and _version(app_version) >= API_SINCE.get(store, ()),
        "api_burst": 3,
        "empty_cart": EMPTY_CART,
        # Сигнал бот-проверки (Пятёрочка, Этап 1): паттерн на странице означает «сеть
        # встретила проверкой». При обнаружении приложение делает паузу check_pause_ms
        # и пробует ту же карточку ещё раз. Если вторая попытка тоже упирается в
        # проверку — позиция переносится в missing («check»), и добор следующим прогоном
        # не трогает уже положенное.
        "check_signal": signals.CHECK,
        "check_pause_ms": _check_pause_ms(),
        "api_total": _api_total(store),
        # Точка человека для адаптера сети (APK 1.39): корзина собирается по ЕГО адресу, а не по
        # тому, что сайт сети выбрал сам.
        "place": _place(store),
        # Артикул из адреса карточки — правило сервера (app/catalog/fromurl.py) строкой для телефона.
        # Товар, найденный поиском сети, приложение по нему числит своим и снимает, когда его убрали
        # из корзины (APK 1.43): до этого артикул знал только сервер, и товар оставался в корзине сети.
        "sku_url": _sku_url(store),
    }


def _sku_url(store: str) -> str:
    """Правило «адрес карточки → артикул сети» для телефона; у сети без правила — пустая строка."""
    from app.catalog import fromurl

    rule = fromurl.RULES.get(store)
    if rule is None:
        return ""
    # Флаг IGNORECASE (Дикси) — записью (?i): её одинаково понимают Python и Java.
    return ("(?i)" if rule.flags & re.IGNORECASE else "") + rule.pattern


def _api_total(store: str) -> dict | None:
    """Сверенное правило итога из ответа сети (API_TOTALS, verified): его телефон спрашивает сам.

    ЗАЧЕМ (08.11.2026). Страница корзины сети — 3–5 с каждой фоновой сборки, а ответ сети с
    тем же итогом — доли секунды. Несверенное правило (carts/lite Магнита) итогом не является
    и телефону не отдаётся.
    """
    for rule in API_TOTALS.get(store, ()):
        if not rule.get("verified"):
            continue
        out = {}
        for key in ("path", "total", "base", "sale", "count", "scale"):
            val = rule.get(key)
            if val is not None:
                out[key] = val
        return out if out else None
    return None


# Точка человека для адаптера сети в телефоне (rules.place). Подбор ходит в сеть — геокодер,
# справочник магазинов, — поэтому ответ помним в настройках человека: наряд ждёт подбора раз в
# PLACE_TTL_SEC (первый подбор — 0,45 с, замер 12.11.2026), а не при каждом запросе.
PLACE_KEY = "phonecart.place."
PLACE_TTL_SEC = 6 * 3600      # как кэш справочника магазинов (connectors.cache_ttl_hours)
PLACE_RETRY_SEC = 600         # не подобралась — спросим снова через десять минут
PLACE_KEEP_SEC = 24 * 3600    # столько прежняя точка живёт без подтверждения: сбой службы пережидаем, закрытый магазин — нет


def _place(store: str) -> dict | None:
    """Точка человека для адаптера сети; None — сеть берёт то, что выбрано на её сайте.

    ЗАЧЕМ (12.11.2026). Сайт сети выбирает адрес сам: Самокат — последний добавленный (у владельца
    это был адрес в другом городе), Магнит определяет город по сети и ставит самовывоз. Корзина,
    собранная не по адресу человека, не годится ни для заказа, ни для сравнения цен. Самокату
    отдаём координаты адреса из настроек, Магниту — магазин, по которому считаем его цены.
    Пятёрочке (APK 1.63) — те же координаты: адаптер сверяет с ними адрес корзины сети и в корзину
    по чужому адресу («Тверская улица, 1», 16.11.2026) не пишет, а просит выбрать свой.

    shortcut: смена адреса доходит до телефона с новым нарядом — до 6 ч (PLAN_MS приложения);
    нужна сразу — добавить адрес в отпечаток корзины (cartrun.stamp_of).
    """
    if store not in ("magnit", "samokat", "pyaterochka"):
        return None
    import hashlib
    from app import location

    try:
        addr = (location.address() or "").strip()
        if not addr:
            return None
        mark = hashlib.sha1(addr.casefold().encode("utf-8")).hexdigest()[:12]
        kept = json.loads(repo.get_setting(PLACE_KEY + store) or "null")
        if not isinstance(kept, dict) or kept.get("for") != mark:
            kept = {}
        now = time.time()
        # Точка Магнита, запомненная до 1.55, перечня deliver не знает: подбираем заново, а не ждём шесть часов.
        old_format = store == "magnit" and isinstance(kept.get("place"), dict) and "deliver" not in kept["place"]
        if kept and not old_format and now - float(kept.get("at") or 0) < (
                PLACE_TTL_SEC if kept.get("place") else PLACE_RETRY_SEC):
            return kept.get("place")
        try:
            found = _find_place(store, addr)
        except Exception as exc:  # noqa: BLE001 — геокодер или справочник молчат
            log.warning("%s: точка человека для телефона не подобрана (%s)", store, type(exc).__name__)
            found = None
        seen = now if found else float(kept.get("ok") or 0)      # когда точку подобрали в последний раз
        if found or not kept.get("place") or now - seen > PLACE_KEEP_SEC:
            at, place = now, found
        else:
            # Прежняя точка того же адреса лучше никакой; спросим снова через PLACE_RETRY_SEC.
            at, place = now - PLACE_TTL_SEC + PLACE_RETRY_SEC, kept["place"]
        repo.set_setting(PLACE_KEY + store, json.dumps({"for": mark, "at": at, "ok": seen, "place": place}))
        return place
    except Exception as exc:  # noqa: BLE001 — наряд важнее точки: без неё телефон кладёт, как раньше
        log.warning("%s: точка человека для телефона не прочитана (%s)", store, type(exc).__name__)
        return None


def _find_place(store: str, addr: str) -> dict | None:
    """Точка по адресу: координаты для Самоката и Пятёрочки, магазин расчёта и магазины вокруг — для Магнита."""
    from app import geo, location

    if store in ("samokat", "pyaterochka"):
        spot = geo.coords(addr) or location.point()
        return {"lat": round(spot[0], 6), "lon": round(spot[1], 6)} if spot else None
    from app.connectors import magnit
    from app.shopbrowser import point

    place = location.for_store("magnit")
    spot = geo.coords(addr)
    if not place or not place.store_id or not spot or not point.served("magnit", place):
        return None
    # Магазины у адреса: магазин, выбранный на сайте, из этого перечня телефон не меняет.
    stores = magnit.stores_near(*spot, limit=500)
    near = [str(s["code"]) for s in stores]
    if str(place.store_id) not in near:
        return None       # справочник не ответил: с пустым перечнем телефон менял бы любой выбор человека
    # Доставку отдают не все магазины у адреса (замер 15.11.2026: из 15 у адреса владельца — один склад, остальные
    # «Магнит у дома» и мини только на самовывоз). Кука сайта с таким магазином и доставкой — не «Экспресс»:
    # телефон принимает свой выбор человека, только если магазин в deliver (1.55).
    return {"shop": str(place.store_id), "type": str(place.shop_type or "ME"), "delivery": bool(place.delivery),
            "near": near, "deliver": [str(s["code"]) for s in stores if s.get("delivery")]}


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
    после паузы снова упирается в CHECK — позиция помечается «check», её возьмёт
    следующая сборка.
    """
    from app import config

    try:
        ms = int(config.get("phone_check_pause_ms", 5000) or 5000)
    except (TypeError, ValueError):
        ms = 5000
    return max(1000, ms)


# Наряды-близнецы. Приложение с 1.44 повторяет запрос наряда, не дождавшись ответа за 1,8 с (новое соединение
# изредка открывается 3–17 с), и помечает оба запроса одним числом ask. Если медлит не дорога, а сервер,
# повтор ждёт ответ первого: наряд не считается второй раз рядом с первым, и новый товар не ищется в сети
# дважды. (база, сеть, ask) → [готово, ответ] наряда, который считается сейчас.
_TWINS: dict[tuple, list] = {}
TWIN_WAIT_SEC = 3.0                 # дольше повтор не ждёт: первый завис — считает сам


def plan(body: dict) -> dict:
    """Наряд для телефона (собирает _plan). Пока он считается, фоновый счёт ждёт: wait_for_plans."""
    began = time.monotonic()
    ask = body.get("ask")               # время работы телефона в мс: _count отбросил бы всё больше 1e9
    key = (str(config.db_path()), str(body.get("store")), ask) if isinstance(ask, int) else None
    mine: list = [threading.Event(), None]
    joined = False
    with _PLANS_LOCK:
        _PLANS["now"] += 1
        first = _TWINS.setdefault(key, mine) if key else mine
    try:
        if first is not mine and first[0].wait(TWIN_WAIT_SEC) and first[1] is not None:
            joined = True
            return first[1]
        mine[1] = _plan(body)           # сюда же идёт повтор, чей первый запрос упал или завис
        return mine[1]
    finally:
        with _PLANS_LOCK:
            _PLANS["now"] -= 1
            _PLANS["until"] = max(_PLANS["until"], time.monotonic() + PLANS_GAP_SEC)
            if key and _TWINS.get(key) is mine:
                del _TWINS[key]
        mine[0].set()
        # Сколько наряд занял здесь, и при ошибке тоже. Телефон ждал дольше — остальное ушло на дорогу, вход
        # в рабочее место или очередь Waitress («Task queue depth» в журнале). Замер 13.11.2026: Самокат ждал
        # 16,9 с, и по журналу было не отличить сервер от сети.
        log.info("наряд %.40r: %.2f с%s", body.get("store"), time.monotonic() - began,
                 ", ответ первого запроса" if joined else "")


def _plan(body: dict) -> dict:
    """Наряд для телефона: вся последняя корзина, переложенная на артикулы этой сети.

    Не только позиции лучшего варианта, а вся корзина: смысл в том, чтобы каждая
    сеть посчитала одну и ту же корзину и итоги можно было сравнить. Вход здесь
    не проверяется (force): вход живёт на телефоне, и сервер о нём может не знать.
    Цены перед нарядом не перезапрашиваются: итог всё равно скажет сеть.
    """
    from app import api, cartplan
    from app.shopbrowser import cart

    store = _store(body)
    background = bool(body.get("background"))
    picked = {k: v for k, v in body.items() if k in ("basket", "lines")}
    found = {"linked": 0, "recent": 0, "later": 0} if picked.get("lines") is not None else _link(picked, store, live=not background)
    lines, empty_note = api._plan_lines(picked, store)
    # Служебные строки чека («Аренда транспортного средства», «Сборка») — не товар:
    # в наряд и в поиск они не идут. Отсекаем ДО cartplan.build, иначе они попали бы
    # в unknown как «незнакомые позиции», а телефон искал бы их в магазине впустую.
    lines, service_skipped = _without_service_lines(lines)
    basket_id = _basket_id(picked)
    built = cartplan.build(store, lines, force=True)
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
            # Слитую позицию телефон по касанию не правит (APK 1.27): чьи в ней штуки, видно только здесь.
            same.setdefault("product_ids", [same.get("product_id")]).append(row.get("product_id"))
            continue
        if sku_key:
            by_sku[sku_key] = row
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
                    f"из {found['searched']}."
                    + (f" Ещё {found['unasked']} не спрошены — предел живого поиска "
                       f"{LIVE_LIMIT} за наряд." if found.get("unasked") else "")
                    + (f" Ещё {found['recent']} спрашивали недавно." if found.get("recent") else ""))
    elif built.unknown:
        # Наряд не пуст, но часть корзины не опознана: сказать, что по ней сделали.
        # Без этих слов строка про незнакомые позиции читается как «даже не пытались».
        note = (note or "") + _link_note(found)
    if service_skipped:
        # Служебные строки не товар: человек видит, что они не потерялись, а пропущены.
        note = f"{(note or '').strip()} Служебных строк пропущено: {service_skipped}.".strip()
    db_path = str(config.db_path())
    # Фоновой сборке (после каждой правки корзины) поиск по названию не заказываем. Замер 15.11.2026 на телефоне
    # владельца: знакомые товары ложатся за 1–3 с, а хвост поиска держал строку сети ещё 60–137 с и не находил
    # почти ничего (за всё время две связи method='phone'). Незнакомый сети товар строка показывает сразу
    # (app/web/phone/basket.py, _absent); искать его остаётся кнопке «Собрать» в окне сети и серверу (_ask_later).
    all_search = [] if background else _search_lines(found.get("unresolved") or [], store)
    search_lines: list[dict] = []
    search_recent: list[dict] = []
    for s_line in all_search:
        pid = s_line.get("product_id")
        if pid and _is_tried("search", db_path, store, int(pid)):
            search_recent.append(s_line)
        else:
            if pid:
                _tried_mark("search", db_path, store, int(pid))
            search_lines.append(s_line)
    # У товара с запасным строка наряда несёт номер запасного, а сам товар остаётся в поиске. Его правку
    # телефон (APK 1.37) не выводит: по «+» строка запасного осталась бы с прежним числом.
    own = {line.product_id for line in lines}
    for s_line in all_search:
        if s_line.get("product_id") not in own:
            s_line["swapped"] = True
    # Товары, которых сеть сейчас не получит ни запросом, ни поиском: она их не знает, искать их телефону
    # нечем (нет адреса поиска, search_off, сверх SEARCH_LIMIT), и сервер с ними закончил (_settled). Их
    # правка наряд не меняет, и телефон (APK 1.37) выводит его сам, не дожидаясь сервера. Не спрошенный
    # товар и тот, кого ещё ищет фоновый поиск сети, сюда не идут: через секунды сеть может его узнать, а
    # телефон держал бы наряд без него до 6 ч. Товара с запасным здесь тоже нет: его номера нет в lines.
    # Привязку, которую фоновый поиск успел поставить, пока считался этот наряд, перепроверяем: такой товар
    # сеть уже знает, и его правке нужен свежий наряд.
    told = {line.product_id for line in built.lines} | {s.get("product_id") for s in all_search}
    shop = repo.get_store(store)
    rest = []
    if shop and len(built.lines) + len(built.unknown) == len(lines):
        rest = [int(line.product_id) for line in lines
                if getattr(line, "product_id", None) and line.product_id not in told
                and _settled(db_path, store, line.product_id)
                and not repo.confirmed_mapping(line.product_id, shop.id, real_only=True)]
    res = {"ok": True, "store": store, "lines": rows, "unknown": list(built.unknown), "rest": rest,
           "total": built.total, "note": note, "linked": found["linked"],
           "rules": rules(store, body.get("app_version")),
           # Позиции, которых сеть не знает: пусть их найдёт телефон поиском самой сети
           # (app/phonecart.py, SEARCH_LIMIT). Для сетей, куда сервер не пускают
           # (Пятёрочка, Самокат), это единственная дорога к настоящему артикулу.
           "search": search_lines,
           "search_recent": search_recent,
           # Все товары корзины, о которой наряд. Найденное поиском сети для товара из этого списка телефон
           # (APK 1.43) не снимает и без строки поиска: искали недавно, сверх SEARCH_LIMIT, rest.
           "pids": sorted({int(line.product_id) for line in lines if getattr(line, "product_id", None)}),
           # Какую корзину описывает наряд и её отпечаток: по нему телефон узнаёт, менялась
           # ли корзина с прошлой сборки в этой сети, и без перемен её не повторяет.
           "basket": basket_id,
           "stamp": _stamp(basket_id, custom_lines=picked.get("lines"))}
    if "drop" in body:
        # Своё, что телефон снимет из корзины сети (1.19): карточка и название по артикулу.
        st = repo.get_store(store)
        res["drop"] = []
        for sku in [str(x).strip() for x in (body.get("drop") or [])[:DROP_LIMIT] if str(x).strip()]:
            sp = repo.store_product_by_sku(st.id, sku) if st else None
            item = {"sku": sku, "name": (sp or {}).get("raw_name") or ""}
            if sp and sp.get("url"):
                item["url"] = sp["url"]
            res["drop"].append(item)
    return res


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
        if item.get("pack_g"):      # фасовка товара (_unknown): телефон сверит её с карточкой
            row["pack_g"] = item["pack_g"]
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

# Фирменный пакет сети с кассы: в чеке «Пакет ПЯТЕРОЧКА 65х40 см», а у товара в корзине «Пятерочка 65х40см» —
# слово «Пакет» при разборе чека теряется, и корень «пакет» его не поймал бы. Здесь только название сети и размер
# в сантиметрах: обычный товар так не называется, а «Пакеты для мусора 30 л» или «Салфетки 20х20 см» не он.
_CHECKOUT_BAG = re.compile(r"^(?:пакет )?(?:пятерочка|перекресток|магнит|лента|дикси|вкусвилл|самокат|metro|метро)"
                           r" \d+ ?[xх] ?\d+ ?см$")


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
    return any(text.startswith(root) for root in SERVICE_ROOTS) or bool(_CHECKOUT_BAG.match(text))


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
    try:
        found = _asked_once(("name", fallback), RETRY_SECONDS, lambda: _catalog_name(fallback))
    except Exception:  # noqa: BLE001 — каталог не ответил: не выдумываем, и отказ не запоминаем
        log.info("каталог не ответил об имени для поиска", exc_info=True)
        found = None
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
    from app.catalog.match import ordered_words
    from app.matcher.normalize import parse_weight

    raw = (text or "").strip()
    if len(raw) < 4:
        return None
    # Один вид продукта с фасовкой не определяет марку, даже если вес похож.
    if len(ordered_words(raw)) < 2:
        return None
    russian = _has_cyrillic(raw)
    grams, _unit = parse_weight(raw)
    words = [w for w in raw.split() if len(w) > 2]
    attempts = [raw]
    if len(words) > SHORT_QUERY_WORDS:
        attempts.append(" ".join(words[:SHORT_QUERY_WORDS]))
    for attempt in attempts:
        hits = store.search_items(attempt, limit=3)      # каталог не ответил — решает зовущий (_query_name)
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


def _ask_later(product_ids: list[int], store: str) -> None:
    """Спросить живой поиск сети в фоне, не задерживая наряд.

    ЗАЧЕМ. Фоновая сборка не ждёт поиска сети, найденное возьмёт следующий наряд.
    При фоновой сборке телефон должен быстро собрать знакомые товары и вернуть
    отчёт за считанные секунды для пересчёта «Где дешевле». Живой поиск сети
    (до 2,5 с на позицию) вынесен в daemon-поток: он находит артикулы в фоне,
    учит единую базу, и следующий наряд получит эти позиции уже прямым запросом.
    """
    from app import users

    # Пока поиск идёт, товар в rest наряда не попадает (_settled): телефон не должен считать его чужим сети.
    # Запись ставит ещё _link, до пометки «спрошен»; здесь она снимается на каждом выходе.
    asking = [(str(config.db_path()), str(store), int(pid)) for pid in product_ids]
    for key in asking:
        _ASKING.setdefault(key, time.monotonic())

    def asked():
        for key in asking:
            _ASKING.pop(key, None)

    phone = users.current()
    if not phone:
        asked()
        return

    def _worker():
        from app import matcher, users

        try:
            users.open_workspace(phone)
            try:
                matcher.auto_match(product_ids, [store])
            finally:
                users.deactivate()
        except Exception:  # noqa: BLE001 — фоновый поиск не должен ронять процесс
            log.warning("фоновый поиск позиций %s в сети %s не удался", product_ids, store, exc_info=True)
        finally:
            asked()

    worker = threading.Thread(target=_worker, name=f"link-{store}", daemon=True)
    try:
        worker.start()
    except Exception:  # noqa: BLE001 — сбой старта потока не должен ронять наряд
        asked()
        log.warning("поток фонового поиска link-%s не запустился", store, exc_info=True)


def _unknown(product_id: int, product: Any) -> dict:
    """Товар, которого сеть не знает, — строкой для поиска телефоном (_search_lines).

    С фасовкой: телефон сверит её с карточкой до касания «В корзину» (packWhy, приложение 1.48). Замер
    13.11.2026: «хлопья №3 350 г» легли карточкой «…№2 500 г» — веса у строки поиска не было. Весовому
    товару фасовка не нужна: он продаётся на развес, и сверка только мешала бы.
    """
    row = {"product_id": product_id, "name": product.name}
    if product.weight_g and product.unit != "kg":
        row["pack_g"] = float(product.weight_g)
    return row


def _link(picked: dict, store: str, live: bool = True) -> dict:
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
        return {"linked": 0, "recent": 0, "later": 0}
    try:
        basket_id = int(picked["basket"]) if picked.get("basket") is not None else int(baskets[0]["id"])
    except (TypeError, ValueError):
        return {"linked": 0, "recent": 0, "later": 0}                       # неверный номер объяснит _plan_lines
    basket = next((b for b in baskets if int(b["id"]) == basket_id), None)
    items = repo.basket_items(basket_id)
    linked = tried = searched = by_search = skipped = recent = 0
    ask_later_ids: list[int] = []
    db_path = str(config.db_path())
    t0 = time.monotonic()
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
        # Позиция уже исследовалась недавно — не тратим время на повторный поиск
        if _is_tried("link", db_path, store, product_id):
            recent += 1
            unresolved.append(_unknown(product_id, product))
            continue

        tried += 1
        try:
            about = (product.name, product.brand, product.weight_g, product.unit, product.barcode)
            found = _asked_once(("resolve", *about), RESOLVE_SECONDS, lambda: refresh.resolve(*about))
            if found and any(r.get("chain") == store for r in found.get("chains") or []):
                refresh.adopt(int(found["id"]), product_id=product_id)
                linked += 1
                _tried_mark("link", db_path, store, product_id)
                continue
        except Exception:  # noqa: BLE001 — каталог не открылся: спросим поиск сети
            log.warning("%s: позиция %s не опозналась в каталоге", store, product_id, exc_info=True)
        if (searched + len(ask_later_ids)) >= LIVE_LIMIT or (time.monotonic() - t0) >= LINK_BUDGET_SECONDS:
            continue
        if not live:
            ask_later_ids.append(product_id)
            # До пометки «спрошен»: наряд соседнего запроса не должен увидеть товар спрошенным и не ищущимся.
            _ASKING[(db_path, str(store), product_id)] = time.monotonic()
            _tried_mark("link", db_path, store, product_id)
            unresolved.append(_unknown(product_id, product))
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
        _tried_mark("link", db_path, store, product_id)
        if not repo.confirmed_mapping(product_id, shop.id, real_only=True):
            unresolved.append(_unknown(product_id, product))

    if ask_later_ids:
        _ask_later(ask_later_ids, store)

    later = len(ask_later_ids)
    # Сколько осталось не спрошенным: предел живого поиска объясняет разницу между
    # «искали все» и «искали десять», и наряд говорит это человеку.
    unasked = max(0, tried - linked - searched - later)
    log.info("%s: незнакомых позиций корзины %d, в каталоге %d, поиском сети %d из %d "
             "(не спрошено %d: предел %d), позже %d, недавних %d, телефону искать %d, служебных пропущено %d",
             store, tried, linked, by_search, searched, unasked, LIVE_LIMIT, later, recent, len(unresolved),
             skipped)
    return {"linked": linked + by_search, "tried": tried, "searched": searched,
            "by_search": by_search, "in_catalog": linked, "unasked": unasked,
            "recent": recent, "later": later,
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
    recent = int(found.get("recent") or 0)
    if not tried and not recent:
        return ""
    text = ""
    if tried:
        text = (f" Незнакомых спрашивали: в каталоге нашлось {found.get('in_catalog') or 0}, "
                f"живым поиском сети — {found.get('by_search') or 0} "
                f"из {found.get('searched') or 0} спрошенных")
        unasked = int(found.get("unasked") or 0)
        if unasked:
            text += (f", ещё {unasked} не спрошены — предел живого поиска {LIVE_LIMIT} "
                     "за наряд (сеть бережём)")
    if recent:
        if text:
            text += f", ещё {recent} спрашивали недавно"
        else:
            text = f" Незнакомых: ещё {recent} спрашивали недавно"
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
    except (TypeError, ValueError, OverflowError):   # OverflowError — целое из сотен цифр
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


def accept_report(body: dict, device: str = "") -> dict:
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
        # Цена в хвосте названия — признак текста карточки сети: так шлют отчёт инструменты
        # (замер 20.10.2026), а в название позиции из наряда цена не попадает никогда.
        if not found_name and _CARD_PRICE.search(str(raw.get("name") or "")):
            found_name = item["name"]
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

    # Отчёт запросами несёт адрес ответа сети («/api/…»): его суммы — её числа, а не разбор страницы.
    if store in GLUED_PAGES and not _text(body.get("cart_page"), 200).startswith("/api/"):
        page = []

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
        # От касания «+» или «−» до этого отчёта, мс (приложение 1.29): цель владельца — не дольше 5 с.
        "lag_ms": _count(body.get("lag_ms")),
        # Сколько из них сборка ждала наряд, мс (приложение 1.44): сервер это или дорога к нему.
        "plan_ms": _count(body.get("plan_ms")),
    }
    trace = _trace(body.get("trace"))
    repo.set_setting(REPORT_KEY + store, json.dumps(report, ensure_ascii=False))
    if trace:
        note_trace(report, trace, device)
    lag, waited = report["lag_ms"], report["plan_ms"]
    touch = ""
    if lag is not None:
        row = [report["at"], store, report["landed"], report["planned"], lag, report["app_version"],
               _text(device, 40)]
        note_lag(row if waited is None else row + [waited])
        touch = f", от касания {lag / 1000:.1f} с" + ("" if waited is None else f" (наряд ждали {waited / 1000:.1f} с)")
    if report["landed"] < report["planned"]:
        note_fail(report, device)
    log.info("корзина с телефона, %s: легло %d из %d за %s с%s, итог сети %s, полей из ответов %d",
             store, report["landed"], report["planned"], report["seconds"], touch, total_of(report),
             sum(len(v) for v in found.values()))
    _learn(report)
    bound = 0
    try:
        bound = _bind(report)
    except Exception:  # noqa: BLE001 — артикул товару — прибыль, а не условие приёма отчёта
        log.warning("%s: товар не связался с найденной карточкой", store, exc_info=True)
    _stock(report)
    try:
        _cart_prices(report)
    except Exception:  # noqa: BLE001
        log.warning("%s: цены корзины не записались в рабочее место", store, exc_info=True)
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
    # bound — скольким товарам этот отчёт дал артикул сети: наряд в памяти телефона ещё ищет их
    # касаниями, и приложение (1.48) берёт новый.
    return {"ok": True, "store": store, "landed": report["landed"],
            "planned": report["planned"], "total": total_of(report),
            "network": network_total(report), "bound": bound}


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

    Имя — с карточки сети (`item["found_name"]`), а если его нет — из адреса карточки
    (`fromurl.name_of`): чужие или наши названия позиций из наряда в каталог сети больше
    не попадают, чтобы не портить каталог (Lay's с солью вместо сметаны и лука).

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
        # Имя — с карточки сети (item["found_name"]), а если его нет — из адреса карточки
        # (fromurl.name_of). Наше название позиции из наряда больше не пишем: так в каталог
        # Пятёрочки попали строки под чужим именем (Lay's с солью как «рифлёные сметана и лук»).
        name = _text(item.get("found_name") or fromurl.name_of(url, sku), 120)
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


BOUND = "phone"     # product_mapping.method: связь доказал телефон — нашёл карточку поиском сети и положил


def _bind(report: dict) -> int:
    """Связать товар корзины с карточкой сети, которую телефон сам нашёл и положил.

    ЗАЧЕМ. `_learn` заводит найденную карточку в каталоге сети, а связь с товаром корзины оставляет
    сопоставлению по названиям — строку чека оно не узнаёт. Замер 13.11.2026, корзина из чека
    Пятёрочки: 7 товаров, настоящих артикулов 0, и каждая правка искала их касаниями заново
    (96–135 с), хотя адрес найденной карточки приходил в отчёте. Здесь товар получает артикул
    сети, и следующую правку сеть принимает запросом.

    Товар узнаётся по названию позиции в корзине отчёта: номера товара в отчёте нет. Связь
    пишется, только когда позиция легла и карточка прошла сверку фасовки, чисел и слов
    (x5.found_by_phone): в том же замере «…хлопья овсян №3 350 г» легли карточкой «…2-500g».
    Товар с настоящим артикулом сети не трогаем, от сети, где человек связь убирал, отступаем.
    """
    from app.catalog import fromurl, model, x5

    chain = str(report.get("store") or "")
    shop = repo.get_store(chain)
    hosts = tuple(cartrecord.CHAIN_HOSTS.get(chain) or ())
    if shop is None or not hosts or not report.get("basket"):
        return 0
    names: dict[str, int | None] = {}
    for row in repo.basket_items(int(report["basket"])):
        # Название в отчёте прошло чистку _card_name (accept_report) — ключ чистим так же. Одно
        # название у двух товаров корзины: чей артикул, по отчёту не понять.
        name = _card_name(row["name"])
        names[name] = None if name in names else int(row["product_id"])
    bound = 0
    for item in report.get("items") or []:
        url = str(item.get("url") or "")
        pid = names.get(str(item.get("name") or ""))
        if not item.get("ok") or pid is None or not any(host in url for host in hosts):
            continue
        sku = str(item.get("sku") or "").strip() or fromurl.sku_of(chain, url)
        if (not sku or model.placeholder_sku(sku, chain) or repo.chain_rejected(pid, shop.id)
                or repo.confirmed_mapping(pid, shop.id, real_only=True)):
            continue
        card = _text(item.get("found_name") or fromurl.name_of(url, sku), 120)
        product = repo.get_product(pid)
        if product is None or not x5.found_by_phone(product, card):
            continue
        known = repo.store_product_by_sku(shop.id, sku)      # известной строке название не портим
        sp = known["id"] if known else repo.upsert_store_product(
            shop.id, sku, card, weight_g=x5.weight_of(card, None), url=url, keep_known=True)
        repo.confirm_mapping(pid, sp, confirmed=True, method=BOUND)
        # Запись молча не проходит, когда человек убирал у товара заглушку этой сети («stub:»).
        if (repo.confirmed_mapping(pid, shop.id, real_only=True) or {}).get("sku") != sku:
            continue
        item["sku"] = sku       # цену карточки запишет _cart_prices из этого же отчёта
        bound += 1
    if bound:
        log.info("%s: телефон нашёл и положил — артикул сети получили товаров: %d", chain, bound)
    return bound


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


def _cart_prices(report: dict) -> int:
    """Цены и наличие из корзины сети — в цены рабочего места (решение 01.11.2026)."""
    from app import location
    from app.catalog import model

    store = repo.get_store(report.get("store") or "")
    if not store:
        return 0

    code = store.code
    pk = location.point_key()
    saved = 0

    for item in report.get("items") or []:
        sku = str(item.get("sku") or "").strip()
        if not sku:
            continue
        if model.placeholder_sku(sku, code):
            continue

        sp = repo.store_product_by_sku(store.id, sku)
        if not sp:
            continue
        sp_id = sp["id"]
        if not repo.is_mapping_confirmed(sp_id):
            continue

        price_val = item.get("price")
        price = float(price_val) if price_val is not None and float(price_val) > 0 else None

        if item.get("in_stock") is False:
            p = price
            if p is None:
                snap = repo.latest_price(sp_id, point_key=pk)
                if snap and snap.get("price") is not None and float(snap["price"]) > 0:
                    p = float(snap["price"])
            if p is not None:
                repo.save_price(sp_id, p, in_stock=False, source="cart", point_key=pk)
                saved += 1
        elif item.get("ok") and price is not None:
            unit = str(sp.get("unit") or "").strip().lower()
            if unit in ("kg", "кг"):
                continue
            repo.save_price(sp_id, price, in_stock=True, source="cart", point_key=pk)
            saved += 1

    log.info("%s: цен из корзины сети записано %d", code, saved)
    return saved


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


def lags() -> list[list]:
    """Журнал времени правок этого рабочего места: [когда, сеть, легло, всего, мс от касания,
    версия приложения, устройство] и, если приложение прислало (1.44), мс ожидания наряда; старые сверху."""
    try:
        rows = json.loads(repo.get_setting(LAGS_KEY) or "[]")
    except ValueError:
        return []
    return rows if isinstance(rows, list) else []


def note_lag(row: list) -> None:
    """Дописать строку в журнал времени правок (формат — в lags). Пишет и сервер: ссылку-корзину
    сетей «ссылкой» (basket_link) он делает сам, устройство у такой строки — «ссылка»."""
    try:
        with _LAGS_LOCK:
            repo.set_setting(LAGS_KEY, json.dumps((lags() + [row])[-LAGS_KEEP:], ensure_ascii=False))
    except Exception:  # noqa: BLE001 — журнал не должен ломать то, что он замеряет
        log.warning("время правки не записалось в журнал: %s", row[1:2], exc_info=True)


def fails() -> list[dict]:
    """Журнал отказов этого рабочего места, старые сверху: когда, сеть, легло и всего, секунды сборки,
    мс от касания, версия, устройство, корзина, причины с числом позиций (до четырёх, частые первыми;
    пометка позиции — в скобках), запросы первой не лёгшей позиции, у которой они есть, и times —
    сколько отчётов подряд слилось в строку (тогда first — время первого)."""
    try:
        rows = json.loads(repo.get_setting(FAILS_KEY) or "[]")
    except ValueError:
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def note_fail(report: dict, device: str = "") -> None:
    """Дописать в журнал отказов отчёт, где легло не всё (формат — в fails). Отчёт той же сети с теми же
    причинами строку не добавляет, а обновляет: серия касаний иначе вытеснила бы первый отказ."""
    try:
        whys: dict[str, int] = {}
        calls: list = []
        for item in report["items"]:
            # Отложенная позиция раннего отчёта — не отказ: о ней доложит хвост. С пометкой — отказ запросов
            # сети (приложение кладёт его причину в каждую позицию раннего отчёта), его пишем.
            if item["ok"] or (item["why"] == LATER_WHY and not item.get("note")):
                continue
            why = (item["why"] or "без причины")[:160] + (f" ({item['note']})" if item.get("note") else "")
            whys[why] = whys.get(why, 0) + 1
            calls = calls or item["calls"]
        if not whys:
            return
        top = sorted(whys.items(), key=lambda pair: -pair[1])[:4]
        row = {"at": report["at"], "store": report["store"], "landed": report["landed"],
               "planned": report["planned"], "seconds": report["seconds"], "lag_ms": report["lag_ms"],
               "app_version": report["app_version"], "device": _text(device, 40), "basket": report["basket"],
               "whys": top, "calls": calls, "times": 1}
        with _LAGS_LOCK:
            rows = fails()
            same = next((old for old in reversed(rows) if old.get("store") == row["store"]), None)
            if same is not None and {pair[0] for pair in same.get("whys") or []} == {why for why, _ in top}:
                row["times"] = int(same.get("times") or 1) + 1
                row["first"] = same.get("first") or same.get("at")
                rows.remove(same)
            repo.set_setting(FAILS_KEY, json.dumps((rows + [row])[-FAILS_KEEP:], ensure_ascii=False))
    except Exception:  # noqa: BLE001 — журнал не должен ломать приём отчёта
        log.warning("отказ не записался в журнал: %s", report.get("store"), exc_info=True)


def _trace(raw: Any) -> list[str]:
    """Хроника приложения из отчёта: последние TRACE_LINES строк, каждая не шире TRACE_WIDTH; не список — пусто."""
    if not isinstance(raw, list):
        return []
    lines = (_text(line, TRACE_WIDTH) for line in raw[-TRACE_LINES:])
    return [line for line in lines if line]


def traces(key: str = TRACES_KEY) -> list[dict]:
    """Хроники приложения при отчётах этого рабочего места (key=SLOW_KEY — без отчёта, 1.55), старые сверху:
    когда, сеть, легло и всего, версия, устройство, trace — строки журнала приложения с метками времени
    телефона (1.54) — и reason, если приложение назвало, почему прислало хронику."""
    try:
        rows = json.loads(repo.get_setting(key) or "[]")
    except ValueError:
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def note_trace(report: dict, trace: list[str], device: str = "", key: str = TRACES_KEY,
               keep: int = TRACES_KEEP) -> None:
    """Дописать хронику приложения к журналу (формат — в traces); хранится последних keep."""
    try:
        row = {"at": report["at"], "store": report["store"], "landed": report["landed"],
               "planned": report["planned"], "app_version": report["app_version"],
               "device": _text(device, 40), "trace": trace}
        if report.get("reason"):
            row["reason"] = report["reason"]
        with _LAGS_LOCK:
            repo.set_setting(key, json.dumps((traces(key) + [row])[-keep:], ensure_ascii=False))
    except Exception:  # noqa: BLE001 — хроника не должна ломать приём отчёта
        log.warning("хроника приложения не записалась: %s", report.get("store"), exc_info=True)


def accept_trace(body: dict, device: str = "") -> dict:
    """Хроника без отчёта (1.55): {store, reason, app_version, trace} — сборка идёт долго, и отчёта к ней может не
    быть. Пустую хронику или сеть без названия не берём: ответ 400, приложение не повторяет."""
    store = _text(body.get("store"), 24)
    lines = _trace(body.get("trace"))
    if not store or not lines:
        raise ValueError("Хроника пуста или не названа сеть")
    note_trace({"at": datetime.now().isoformat(timespec="seconds"), "store": store, "landed": 0, "planned": 0,
                "app_version": _text(body.get("app_version"), 16), "reason": _text(body.get("reason"), 120)},
               lines, device, SLOW_KEY, SLOW_KEEP)
    return {"ok": True, "lines": len(lines)}


__all__ = ["CHAINS", "CART_PAGES", "KINDS", "API_TOTALS", "FULL_CART", "QTY_SETS", "API_CARTS",
           "EMPTY_CART", "rules", "plan", "accept_report", "accept_cards", "progress",
           "network_total", "total_of", "last", "lags", "note_lag", "fails", "note_fail",
           "traces", "note_trace", "accept_trace"]
