"""Корзина METRO: единственная сеть, которая сама описала, как её наполнять.

ПОЧЕМУ ЭТОТ МОДУЛЬ ОТДЕЛЬНЫЙ И ПОЧЕМУ ОН ВАЖНЕЕ ОСТАЛЬНЫХ СПОСОБОВ ПЕРЕДАЧИ.

Все прежние ступени передачи (app/handover.py) упираются в одно: сеть не объявила
способа положить товар в чужую корзину, и мы либо шлём ссылку, либо ведём человека
по карточкам, либо водим браузер по её страницам и нажимаем «в корзину» за него
(app/shopbrowser/cart.py). Шестнадцать позиций — это шестнадцать загрузок страниц
и минуты ожидания.

METRO — исключение. Она выложила спецификацию своего интерфейса публично
(api.metro-cc.ru/docs, OpenAPI 3.0), и в ней есть ровно то, чего нет больше ни у
кого: метод наполнения корзины.

    GET    /{api_token}/{store_id}/eshop/basket/?user_hash=…   что сейчас в корзине
    POST   …/eshop/basket/?user_hash=…    {"articles":[{"article":N,"count":K}]}
    PUT    …/eshop/basket/?user_hash=…    {"article":N,"count":K}
    DELETE …/eshop/basket/?user_hash=…&eshop_products_id[][eshop_product_id]=…

Шестнадцать позиций уходят ОДНИМ запросом. Не «быстрее», а качественно иначе: нет
ни разбора чужой вёрстки, ни зависимости от того, переехала ли у сети кнопка.

ЧЕЙ ЭТО ХЕШ — ГЛАВНЫЙ ВОПРОС, И ОН ИЗМЕРЕН 19.09.2026 С БОЕВОГО СЕРВЕРА.

Спецификация зовёт `user_hash` «уникальным хешем пользователя, владеющего
корзиной». Замер показал, чем он является на деле:

    первый GET без всего      → 200, user_hash=5502aed9…, корзина пустая
    сеть ставит куку          → metro_user_id = тот же 5502aed9…, плюс metro_api_session
    второй GET той же сессией → хеш ТОТ ЖЕ
    GET с хешем в параметре   → хеш принят, отдана та же корзина

То есть `metro_user_id` — это и есть `user_hash`, а корзина находится по нему.

ОСТАВАЛСЯ ОДИН ВОПРОС, И ОН ТОЖЕ ЗАКРЫТ ЗАМЕРОМ: тот ли это хеш, что у человека.
Обычным запросом витрина online.metro-cc.ru куку не ставит — её ставит скрипт,
сходивший к интерфейсу, — и по этому ответу можно было решить, что у витрины хеш
свой. Проверено настоящим браузером на сервере (app/shopbrowser, 19.09.2026):

    окно открыло витрину, сеть поставила 48 кук, среди них metro_user_id =
    e3b63cd2e222822c002cd29a0603cf70; ТОТ ЖЕ хеш интерфейс принял как свой и отдал
    по нему корзину.

Значит наполнить можно ТУ САМУЮ корзину, которую человек видит у себя: его хеш
лежит в его же сохранённом входе (app/shopbrowser/store.py), откуда `hash_of` его
и достаёт. Своего, серверного хеша мы человеку не навязываем — чужая корзина ему
бесполезна, и подменять её мы не станем.

ВХОД ДЛЯ ЭТОГО НЕ ОБЯЗАТЕЛЕН, и это не поблажка, а свойство сети: корзину METRO
заводит и гостю. Вход добавляет к ней цены по карте клиента и историю, но чтобы
позиции легли, довольно открытого в окне магазина.

ЧЕГО ЗДЕСЬ НЕТ. Оформления заказа, оплаты, адреса доставки. Модуль кончается
наполненной корзиной; кнопку «заказать» нажимает человек — то же решение, что и в
app/cartplan.py, и по той же причине: это его деньги.

БЕЗ ИМЕННОГО ТОКЕНА ЗАПИСЬ НЕ РАБОТАЕТ. ЭТО ИЗМЕРЕНО, А НЕ ПРЕДПОЛОЖЕНО.

Чтение корзины проходит и без токена, и первое время казалось, что запись пройдёт
тоже. Проверка 19.09.2026 показала обратное, и показала жёстко:

    POST с настоящим кодом точки (store_id=10, снят из перечня самой сети),
    настоящим артикулом в наличии (117189, сахар aro, остаток 7463) и с хешем,
    который сеть выдала НАШЕМУ ЖЕ окну, — 400 {"errors":"Basket not found"}.
    Метода «создать корзину» в спецификации нет вовсе.
    POST .../eshop/basket/set-delivery-type без токена — 404.

Почему так, видно по тому, чем живёт сама витрина. Её страница ходит к сети
адресом вида `api.metro-cc.ru/api/v1/{ТОКЕН}/10/…` и обновляет вход через
`api.metro-cc.ru/auth/api/v1/refresh`. То есть настоящий маршрут — ТОТ, ЧТО
ОПИСАН В СПЕЦИФИКАЦИИ, с сегментом токена; бестокенный путь отдаёт чтение и не
отдаёт запись. Наш прежний вывод «похоже на недосмотр маршрутизации» подтвердился
с той стороны, с которой было неприятнее: недосмотр кончается там, где начинается
запись.

ЧУЖОЙ ТОКЕН МЫ НЕ БЕРЁМ. Токен витрины виден в её собственных запросах, и
соблазн подставить его очевиден. Не подставляем: он выдан не нам, и вся эта
дорога с самого начала строится на том, что честный путь идёт первым. Именной
токен получают через форму METRO для бизнеса, тема «Запросы на сотрудничество»;
до тех пор `fill` отказывается ДО запроса и говорит, чего не хватает.

ЧТО ЭТО ЗНАЧИТ ДЛЯ ЧЕЛОВЕКА. Пока токена нет, METRO остаётся на ступени карточек
(app/handover.KIND_BY_STORE), как Магнит и Дикси: ссылки на товары открываются, а
корзина собирается нажатиями. Появится токен в `connectors.metro_api_token` —
канал включится сам, без единой правки кода: вся дорога до него уже проверена.

ЧТО ИЗМЕРЕНО, А ЧТО ВЗЯТО ИЗ СПЕЦИФИКАЦИИ. Измерены живьём: чтение корзины,
выдача хеша, куки, приём хеша параметром, совпадение хеша окна с хешем
интерфейса, отказ записи без токена. Из спецификации взята ФОРМА запросов записи
(тело, параметры, метод) — с токеном её ещё никто не выполнял, поэтому разбор
ответа написан так, чтобы не рухнуть на неожиданной форме.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

import requests

from app import config
from app.connectors.metro import USER_AGENT, _url

log = logging.getLogger(__name__)

# Кука, в которой сеть держит хеш владельца корзины. Имя СНЯТО ЖИВЬЁМ 19.09.2026,
# а не выведено: в ответе api.metro-cc.ru она пришла со значением, побайтно равным
# полю user_hash. Выдуманное имя здесь было бы хуже отсутствующего — оно молча
# наполняло бы чужую корзину.
HASH_COOKIE = "metro_user_id"
HASH_HOST = "api.metro-cc.ru"

# Хеш — тридцать два знака шестнадцатеричного вида. Проверяем форму, потому что в
# куке с этим именем однажды окажется что-то другое, и уехать с мусором в адресе
# хуже, чем честно сказать «хеша нет».
HASH_LEN = 32

BASKET = "eshop/basket/"

# Больше этого в корзину за раз не кладём. Число то же, что у наряда
# (app/shopbrowser/cart.LINE_LIMIT): длиннее корзины у человека не бывает, а
# запрос на тысячу артикулов — это ошибка вызывающего, а не большая покупка.
LINE_LIMIT = 60
TIMEOUT = 45.0


@dataclass
class Basket:
    """Корзина глазами сети. `raw` оставлен нарочно: разбор может отстать от сети."""

    user_hash: str | None = None
    lines: list[dict] = field(default_factory=list)
    unavailable: list[dict] = field(default_factory=list)
    total: float | None = None
    minimal: float | None = None
    raw: dict = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.lines)


def _basket(payload: dict) -> Basket:
    """Ответ сети -> корзина. Чужая форма, поэтому каждое поле необязательно."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = payload if isinstance(payload, dict) else {}

    def num(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return Basket(
        user_hash=str(data.get("user_hash") or "") or None,
        lines=[r for r in (data.get("articles") or []) if isinstance(r, dict)],
        unavailable=[r for r in (data.get("unavailable_articles") or []) if isinstance(r, dict)],
        total=num(data.get("total_cost")),
        minimal=num(data.get("minimal_cost")),
        raw=data,
    )


def _call(method: str, store_id: str, user_hash: str | None,
          *, params: list[tuple[str, Any]] | None = None,
          body: dict | None = None, timeout: float = TIMEOUT) -> Basket:
    """Один запрос к корзине. Отказ сети поднимаем, а не прячем за пустой корзиной.

    Тихий провал здесь стоил бы дороже всего: человек увидел бы «передано» и пошёл
    бы к пустой корзине. Поэтому «не получилось» всегда громкое.
    """
    query: list[tuple[str, Any]] = list(params or [])
    if user_hash:
        query.append(("user_hash", user_hash))
    response = requests.request(
        method, _url(f"{store_id}/{BASKET}"), params=query, json=body, timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"METRO ответила не JSON на {method} корзины: "
                           f"{response.text[:200]}") from exc
    if isinstance(payload, dict) and payload.get("success") is False:
        raise RuntimeError(f"METRO отказала на {method} корзины: "
                           f"{json.dumps(payload.get('errors'), ensure_ascii=False)[:300]}")
    return _basket(payload)


def writable() -> bool:
    """Можно ли вообще писать в корзину этой сети прямо сейчас.

    Один вопрос: есть ли именной токен. Без него сеть отвечает на запись 400
    «Basket not found» — замер 19.09.2026, подробности в шапке. Спрашивать это
    ДО запроса важнее, чем кажется: иначе человек нажимает «Передать», ждёт, и
    получает отказ сети вместо понятного «не хватает настройки».
    """
    return bool(str(config.get("connectors.metro_api_token") or "").strip())


def hash_of(state: dict | None) -> str | None:
    """Хеш владельца корзины из сохранённого входа человека. Нет — значит нет.

    Берём ТОЛЬКО куку нужного имени и только с хоста интерфейса: кука с тем же
    именем, пришедшая откуда-то ещё, наполнила бы корзину неизвестно чью. Форму
    тоже проверяем — см. HASH_LEN.
    """
    for cookie in (state or {}).get("cookies") or []:
        if not isinstance(cookie, dict) or cookie.get("name") != HASH_COOKIE:
            continue
        domain = str(cookie.get("domain") or "").lstrip(".")
        if domain and not (domain == HASH_HOST or HASH_HOST.endswith("." + domain)
                           or domain.endswith(".metro-cc.ru") or domain == "metro-cc.ru"):
            continue
        value = str(cookie.get("value") or "").strip()
        if len(value) == HASH_LEN and all(c in "0123456789abcdefABCDEF" for c in value):
            return value.lower()
        log.warning("METRO: кука %s есть, но её значение не похоже на хеш", HASH_COOKIE)
    return None


def read(store_id: str, user_hash: str | None = None) -> Basket:
    """Что сейчас в корзине. Без хеша сеть заведёт новую и скажет её хеш."""
    return _call("GET", str(store_id), user_hash)


def fill(store_id: str, user_hash: str, lines: Iterable) -> Basket:
    """Положить позиции в корзину человека одним запросом.

    `lines` — что угодно с полями `sku` и `qty`: строка наряда (app/cartplan.py),
    строка корзины, словарь. Артикул METRO — число, и нецелые сюда не доходят:
    строка, которую сеть не поймёт, отсеивается здесь, а не превращается молча в
    ноль. Количество округляется ВВЕРХ до штуки: корзина сети считает штуками, и
    «0,4 кг» в ней — это одна упаковка, а не ноль.
    """
    if not writable():
        raise ValueError(
            "METRO принимает корзину только по именному токену: без него сеть отвечает "
            "«Basket not found» (замер 19.09.2026). Токен получают через форму METRO для "
            "бизнеса и кладут в настройку connectors.metro_api_token.")
    if not user_hash:
        raise ValueError("METRO: наполнять нечего — неизвестно, чья это корзина. "
                         "Откройте METRO в окне магазина, чтобы сеть выдала хеш.")
    articles: list[dict] = []
    skipped: list[str] = []
    for line in lines:
        sku = str(getattr(line, "sku", None) or (line.get("sku") if isinstance(line, dict) else "")).strip()
        raw_qty = getattr(line, "qty", None)
        if raw_qty is None and isinstance(line, dict):
            raw_qty = line.get("qty")
        if not sku.isdigit():
            skipped.append(sku or "—")
            continue
        try:
            qty = float(raw_qty if raw_qty is not None else 1)
        except (TypeError, ValueError):
            qty = 1.0
        count = max(1, int(qty) if float(qty).is_integer() else int(qty) + 1)
        articles.append({"article": int(sku), "count": count})
    if skipped:
        log.warning("METRO: %d позиций без числового артикула не уехали: %s",
                    len(skipped), ", ".join(skipped[:8]))
    if not articles:
        raise ValueError("METRO: ни одной позиции с числовым артикулом — класть нечего.")
    if len(articles) > LINE_LIMIT:
        raise ValueError(f"METRO: {len(articles)} позиций за раз — столько в корзине не бывает.")
    return _call("POST", str(store_id), user_hash, body={"articles": articles})


def set_count(store_id: str, user_hash: str, article: int | str, count: int) -> Basket:
    """Поменять количество одной позиции. Ноль сюда не передают — для этого drop."""
    if int(count) < 1:
        raise ValueError("METRO: количество меньше единицы — это удаление, а не правка.")
    return _call("PUT", str(store_id), user_hash,
                 body={"article": int(article), "count": int(count)})


def drop(store_id: str, user_hash: str, eshop_product_ids: Iterable) -> Basket:
    """Убрать позиции из корзины. Нужен идентификатор СТРОКИ корзины, не артикул."""
    params = [("eshop_products_id[][eshop_product_id]", str(i)) for i in eshop_product_ids if i]
    if not params:
        raise ValueError("METRO: не сказано, что убирать.")
    return _call("DELETE", str(store_id), user_hash, params=params)


__all__ = ["Basket", "HASH_COOKIE", "HASH_HOST", "LINE_LIMIT",
           "writable", "hash_of", "read", "fill", "set_count", "drop"]
