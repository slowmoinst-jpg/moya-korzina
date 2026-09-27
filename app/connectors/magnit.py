"""Коннектор Магнита.

Задокументированного в спецификации эндпоинта `magnit.ru/api/catalog/search` не
существует — он отвечает 404. Зато сайт отдаёт каталог серверной отрисовкой:
страница поиска `/search/?term=...` содержит карточки товаров с ценами, а карточка
товара `/product/<артикул>` открывается по одному артикулу, без слага.
Отсюда и берём данные.

Разметка магазина может смениться в любой момент — это штатная ситуация
(раздел 9 спецификации). Тогда порядок такой: сначала пробуем прочесть страницу
разбором, потом, если включён, запасным разбором языковой моделью
(app/connectors/smart_extract.py), и только в последнюю очередь берём цену из
data/fallback_prices.csv. Расчёт по остальным магазинам при этом не страдает.

ВАЖНО ПРО РЕГИОН. Проверено запросами 12.09.2026: магазин выбирается НЕ параметром
в адресе, а КУКАМИ shopCode / x_shop_type, а способ получения — кукой nmg_dt.
Тот же адрес с разными shopCode в query отдаёт одну и ту же цену; с разными куками —
разные. Из одного и того же адреса: коктейль «Чудо» 960 г — 175,99 ₽ в Краснодаре и
169 ₽ в Зеленограде; молоко «Кубанский молочник» — 89 ₽ при доставке и 89,99 ₽ при
самовывозе. А молока «Кубанский молочник» в московском магазине нет вовсе: страница
отвечает 404.

Отсюда два правила ниже. Первое: куки, а не параметры. Второе: 404 — это НЕ поломка
разбора, а ответ «такого товара в этом магазине не продают»; подставлять на его место
цену из data/fallback_prices.csv нельзя — человек соберёт корзину, которую не сможет
заказать. Такой товар возвращается с in_stock=False, и оптимизатор его в этот магазин
не кладёт.

Свой код магазина можно подсмотреть в адресе magnit.ru после выбора магазина
(параметр shopCode). В config.yaml он остаётся запасным значением; в работе магазин
приходит вместе с местом клиента (app.models.Location).

ТРЕТЬЕ ПРАВИЛО, СЛЕДСТВИЕ ПЕРВЫХ ДВУХ: магазин обязан входить в ключ кэша. Пока
точка была одна на всё приложение, ключа «search:молоко:3» хватало. Как только
адрес стал приходить от клиента, тот же ключ начал бы отдавать зеленоградцу цены
Краснодара — и это не выглядело бы поломкой, потому что чужая цена ничем не
отличается от своей. Поэтому в ключ идёт Location.key.

JSON-ШЛЮЗ ВМЕСТО ВЁРСТКИ, 16.09.2026. Разбор страниц остался, но ушёл на второй
план: цены и остатки приходят из того же шлюза magnit.ru/webgate, которым уже
собирается общий каталог (app/catalog/crawlers/magnit.py). Он лучше вёрстки всем:
магазин задаётся ТЕЛОМ запроса, а не куками, в ответе есть остаток (quantity), и
менять его сеть будет реже, чем разметку. Адреса взяты из кода самого сайта
(magnit.ru/_nuxt/*.js) и проверены живыми запросами 16.09.2026:

    POST /webgate/v2/goods/search            {term, storeCode, storeType, catalogType,
                                              pagination, sort} -> {items: [...]}
    GET  /webgate/v2/goods/{sku}/stores/{код}?storetype=dostavka&catalogtype=3
                                             -> карточка: price (копейки), quantity, name
    POST /webgate/v1/stores-facade/search/detail  {filters: {geo: {typeName: "box", ...}},
                                              pagination} -> {totalCount, data: [магазины]}

Два ответа шлюза значат «в этом магазине не продаётся», и путать их с поломкой
нельзя: 422 goods_not_found — такого артикула нет вовсе, а price 0 при quantity 0 —
товар есть в сети, но не в этой точке. Проверено на молоке «Кубанский молочник»:
в Краснодаре 159 ₽, в московском магазине 0 и 0 — ровно там же, где страница
отвечала 404.

МАГАЗИН ПОДБИРАЕТСЯ К АДРЕСУ, а не настраивается руками. Раньше код магазина был
настройкой установки (connectors.magnit_shop_code), одной на всех: человек из
Москвы видел цены Краснодара и не мог об этом узнать. Теперь адрес переводится в
точку (app/geo.py, coords) и по точке спрашивается справочник магазинов — см.
stores_near и nearest_store ниже. Настройка осталась запасным значением.
"""
from __future__ import annotations

import html
import logging
import math
import re
from typing import Any

from app import config, geo, homeexit
from app.connectors.base import (
    HttpCatalogConnector,
    USER_AGENT,
    api_disabled,
    note_failure,
    note_success,
    register,
    similarity,
)
from app.connectors import smart_extract
from app.connectors.cache import cached_call
from app.models import Candidate, PriceSnapshot

log = logging.getLogger(__name__)

SEARCH_URL = "https://magnit.ru/search/"
PRODUCT_URL = "https://magnit.ru/product/{sku}"

GATEWAY = "https://magnit.ru/webgate"
GOODS_SEARCH_URL = f"{GATEWAY}/v2/goods/search"
GOODS_CARD_URL = GATEWAY + "/v2/goods/{sku}/stores/{store}"
STORES_URL = f"{GATEWAY}/v1/stores-facade/search/detail"

# Пара «каталог + формат», которой шлюз отвечает по коду ЛЮБОГО магазина. Другие
# пары он встречает ошибкой invalid_service_pair, так что подбирать их не нужно.
STORE_TYPE = "dostavka"
CATALOG_TYPE = "3"

# Магазин, чьи цены показываем, когда адреса нет и в config.yaml пусто. Тот же
# краснодарский магазин, на котором стоит общий каталог: у него самая широкая полка.
DEFAULT_STORE = "992301"

# Форматы Магнита с продуктовой полкой. Аптеки (MA), «Заряд», маркетплейс
# (MAGNIT_MARKET) и рестораны-партнёры (RTE_*) сюда не входят: шлюз по их коду
# отвечает, но не тем, за чем к нему идут.
SHELF_FORMATS = ("MM", "ME", "GM", "MK", "MM_MINI")
# Склад доставки. Полка у него уже, чем у магазина (проверено: по «молоко
# простоквашино» даркстор на Хорошёвском отдал один творог, а магазин на Микояна —
# три вида молока), поэтому он идёт после магазинов, но лучше, чем ничего.
HUB_FORMATS = ("DARKSTORE",)
STORE_FORMATS = SHELF_FORMATS + HUB_FORMATS
# Форматы, где у Магнита есть интернет-витрина: из них можно заказать. Замер
# 20.09.2026 — в app/shopbrowser/point.py (SERVED_FORMATS, там же таблица): «Магнит
# у дома мини» карточек не открывает вовсе. Как и приложение сети, точку расчёта
# берём из них, а магазин без витрины — только если других рядом нет.
ONLINE_FORMATS = ("ME", "MM", "GM", "DARKSTORE")

SEARCH_RADIUS_KM = 5.0     # дальше пяти километров «ближайший магазин» уже не ближайший

# карточка товара в выдаче поиска
_ARTICLE = re.compile(
    r'<article class="unit-catalog-product-preview".*?'
    r'(?=<article class="unit-catalog-product-preview"|</main>)',
    re.S,
)
_LINK = re.compile(r'<a title="([^"]*)"[^>]*href="/product/(\d+)-([^"?]*)', re.S)
# Цена в карточке выдачи: сначала акционная, потом обычная. Одним выражением
# «regular|sale» бралось ПЕРВОЕ совпадение в разметке, и зачёркнутая обычная цена,
# стоящая раньше акционной, выдавалась за текущую.
_CARD_SALE = re.compile(
    r'prices__sale"[^>]*>.*?<span[^>]*>([\d\s  ,.]+)&#8202;₽', re.S
)
_CARD_REGULAR = re.compile(
    r'prices__regular"[^>]*>.*?<span[^>]*>([\d\s  ,.]+)&#8202;₽', re.S
)
# цена на странице самого товара: основная разметка и запасной вариант из описания
_PAGE_PRICE = re.compile(
    r'product-details-price(?:-container)?__current"[^>]*>.*?([\d\s  ,.]+)&#8202;₽', re.S
)
_META_PRICE = re.compile(r'<meta name="description" content="[^"]*?за ([\d,.]+)₽')
# разметка Schema.org на странице товара: самый устойчивый источник цены, меняется реже вёрстки
_LD_PRICE = re.compile(r'"offers"\s*:\s*\{[^}]*?"price"\s*:\s*([\d.]+)')
_WEIGHT = re.compile(r"(\d+[.,]?\d*)\s*(г|гр|мл|кг|л)\b", re.I)


def _unpack(value: Any) -> tuple[str | None, int]:
    """Приводит значение из кэша к паре (страница, код ответа).

    В кэше могут лежать записи прежней версии коннектора, когда `_get_html` возвращал
    одну строку. Распаковывать такую запись как пару нельзя — расчёт свалится в резервный
    CSV на ровном месте, и понять почему будет непросто.
    """
    if isinstance(value, (list, tuple)) and len(value) == 2:
        page, status = value
        return (page if isinstance(page, str) else None), int(status or 0)
    if isinstance(value, str):
        return value, 200
    return None, 0


def _to_float(raw: str) -> float | None:
    cleaned = re.sub(r"[^\d,.]", "", raw or "").replace(",", ".")
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def _weight_of(name: str) -> tuple[float | None, str]:
    m = _WEIGHT.search(name or "")
    if not m:
        return None, "pcs"
    value = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    if unit in ("кг", "л"):
        return value * 1000, "pcs"
    return value, "pcs"


# ---------- справочник магазинов ----------
def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние по земле между двумя точками, в метрах."""
    radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def _fetch_stores(lat: float, lon: float, radius_km: float) -> list[dict] | None:
    """Магазины в квадрате вокруг точки, как их отдаёт шлюз. None — шлюз не ответил.

    Квадрат, а не круг: круга справочник не понимает, он принимает две угловые точки.
    Градус широты — это примерно 111 км везде, градус долготы — те же 111 км на экваторе и
    вдвое меньше на широте Москвы, поэтому долгота делится на косинус широты. Иначе
    у северного города квадрат вытянулся бы вдвое, и «ближайший магазин» нашёлся бы
    в соседнем районе.
    """
    try:
        import requests
    except ImportError:  # pragma: no cover
        return None
    dlat = radius_km / 111.0
    dlon = radius_km / max(0.01, 111.0 * math.cos(math.radians(lat)))
    body = {"filters": {"geo": {"typeName": "box",
                                "leftTopPoint": {"latitude": lat + dlat, "longitude": lon - dlon},
                                "rightBottomPoint": {"latitude": lat - dlat, "longitude": lon + dlon}}},
            "pagination": {"offset": 0, "size": 100}}
    timeout = float(config.get("connectors.timeout_sec", 10) or 10)
    proxies = homeexit.requests_proxies("magnit")
    try:
        resp = requests.post(STORES_URL, json=body, timeout=timeout,
                             headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                      "Content-Type": "application/json"},
                             proxies=proxies)
        if resp.status_code != 200 or "json" not in (resp.headers.get("Content-Type") or ""):
            log.warning("магнит: справочник магазинов ответил %s", resp.status_code)
            return None
        return (resp.json() or {}).get("data") or []
    except Exception as exc:  # noqa: BLE001
        log.warning("магнит: справочник магазинов не ответил (%s)", exc)
        return None


def stores_near(lat: float, lon: float, radius_km: float = SEARCH_RADIUS_KM,
                limit: int = 20) -> list[dict]:
    """Магазины Магнита вокруг точки: code, format, address, distance (метры), delivery.

    Порядок не только по расстоянию. Сначала идут магазины с продуктовой полкой, и
    только потом склад доставки: склад бывает ближе, но в нём меньше товаров, и
    молча считать корзину по его узкой полке значило бы показать человеку, что
    половины его покупок у Магнита «нет».

    В ответе справочника лежат и аптеки, и рестораны-партнёры, и маркетплейс — всё,
    что Магнит показывает на своей карте. Продуктовые форматы отбираются здесь.
    """
    key = f"stores:{lat:.4f},{lon:.4f}:{radius_km}"
    raw = cached_call("magnit", key, lambda: _fetch_stores(lat, lon, radius_km))
    out: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        external = item.get("externalId") or {}
        code = str(external.get("storeCode") or "")
        fmt = str(item.get("storeTypeV2") or "")
        if external.get("owner") != "OWNER_MAGNIT" or fmt not in STORE_FORMATS or not code:
            continue
        if item.get("status") not in (None, "STATUS_ACTIVE"):
            continue
        point = item.get("coordinates") or {}
        try:
            distance = _distance_m(lat, lon, float(point["latitude"]), float(point["longitude"]))
        except (KeyError, TypeError, ValueError):
            distance = float("inf")
        out.append({
            "code": code,
            "format": fmt,
            "address": (item.get("address") or "").strip(),
            "distance": distance,
            "delivery": "DELIVERY_TYPE_DELIVERY" in (item.get("deliveryTypeList") or []),
        })
    out.sort(key=lambda s: (0 if s["format"] in SHELF_FORMATS else 1, s["distance"]))
    return out[:limit]


def nearest_store(address: str) -> dict | None:
    """Магазин, чьи цены Магнит покажет человеку по его адресу. None — не подобран.

    None здесь не поломка, а обычный ответ: адрес не разобран, город без Магнита,
    шлюз молчит. Место клиента тогда остаётся пустым, и коннектор берёт запасное
    значение из config.yaml — ровно так же, как до появления адресов.
    """
    point = geo.coords(address)
    if not point:
        log.info("магнит: адрес «%s» не переведён в точку — магазин не подобран", address)
        return None
    found = stores_near(*point)
    if not found:
        log.info("магнит: рядом с адресом «%s» магазинов не нашлось", address)
        return None
    return pick_store(found)


def pick_store(found: list[dict]) -> dict:
    """Какой магазин из ближайших считать точкой клиента — так, как выбрало бы приложение сети.

    Раньше брался просто первый, и им оказывался «Магнит у дома мини»: полка у
    него есть, а интернет-витрины нет. Расчёт показывал экономию, а корзину
    собрать было негде, и человеку советовали сменить адрес руками.

    Порядок: магазин с витриной и доставкой (если считаем цены доставки) →
    магазин с витриной → любой. Внутри — порядок stores_near: полка, потом
    склад, по расстоянию.
    """
    want_delivery = bool(config.get("connectors.magnit_delivery", True))

    def rank(store: dict) -> int:
        online = store.get("format") in ONLINE_FORMATS
        delivers = store.get("delivery", True) or not want_delivery
        return 0 if online and delivers else 1 if online else 2

    return sorted(found, key=rank)[0]


def _money(value: Any) -> float | None:
    """Копейки шлюза в рубли. Не число — None, и решать будет вызывающий."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(value / 100.0, 2)


def _to_candidate(store_code: str, item: dict) -> Candidate | None:
    """Позиция выдачи шлюза. Ни артикула, ни названия — позиции нет."""
    if not isinstance(item, dict):
        return None
    sku = item.get("id") or item.get("productId")
    name = (item.get("name") or "").strip()
    if not sku or not name:
        return None
    weight_g, unit = _weight_of(name)
    seo = item.get("seoCode")
    weighted = (item.get("weighted") or {}) if isinstance(item.get("weighted"), dict) else {}
    return Candidate(
        store_code=store_code,
        sku=str(sku),
        name=name,
        price=_money(item.get("price")),
        weight_g=weight_g,
        unit="kg" if weighted.get("isWeighted") else unit,
        url=f"https://magnit.ru/product/{sku}-{seo}" if seo else f"https://magnit.ru/product/{sku}",
    )


@register("magnit")
class MagnitConnector(HttpCatalogConnector):
    code = "magnit"
    api_url = SEARCH_URL
    site_url = "https://magnit.ru"
    fallback_sku_prefix = "magnit-"

    # --- сеть ---
    def _shop_params(self) -> dict[str, Any]:
        """Параметры адреса. Сервер их не слушает, но с ними ссылка открывается как на сайте."""
        shop = self.location.store_id
        return {"shopCode": shop, "shopType": "dostavka"} if shop else {}

    def _shop_cookies(self) -> dict[str, str]:
        """Куки, которыми сайт на самом деле выбирает магазин и способ получения."""
        shop = self.location.store_id
        if not shop:
            return {}
        return {
            "shopCode": f'"{shop}"',
            "x_shop_type": str(self.location.shop_type or "ME"),
            "nmg_dt": "DELIVERY_TYPE_DELIVERY" if self.location.delivery else "DELIVERY_TYPE_PICKUP",
        }

    def _store_code(self) -> str | None:
        """Код магазина, за чьи цены отвечаем. None — «здесь Магнита нет».

        Три ответа, и средний важнее всех:

            код из места клиента  — магазин подобран по его адресу;
            None                  — адрес известен, а магазина рядом с ним нет;
            запасной код          — адреса нет вовсе (установка «для себя»,
                                    разработка), берём config.yaml или Краснодар.

        Без среднего получалась бы тихая ошибка: человек в городе без Магнита
        видел бы краснодарские цены и заказал бы по ним корзину, которую никто
        не соберёт. Пустой ответ честнее выдуманного.
        """
        if self.location.store_id:
            return str(self.location.store_id)
        if self.location.address:
            return None
        return DEFAULT_STORE

    def _gateway(self, url: str, *, body: dict[str, Any] | None = None,
                 params: dict[str, Any] | None = None) -> tuple[Any | None, int]:
        """Ответ JSON-шлюза и код ответа.

        Код нужен вызывающему ровно по той же причине, что и у страниц: 422
        goods_not_found — это осмысленный ответ «такого товара нет», а не обрыв
        связи, и предохранитель на него реагировать не должен.
        """
        if api_disabled(self.code):
            return None, 0
        try:
            import requests
        except ImportError:  # pragma: no cover
            return None, 0
        timeout = float(config.get("connectors.timeout_sec", 10) or 10)
        headers = {**self._headers(), "Accept": "application/json"}
        proxies = homeexit.requests_proxies(self.code)
        try:
            if body is None:
                resp = requests.get(url, params=params, headers=headers, timeout=timeout, proxies=proxies)
            else:
                resp = requests.post(url, json=body, timeout=timeout,
                                     headers={**headers, "Content-Type": "application/json"},
                                     proxies=proxies)
            if resp.status_code in (404, 422):
                note_success(self.code)          # шлюз ответил, просто товара у него нет
                return None, resp.status_code
            if resp.status_code != 200:
                log.warning("%s: шлюз %s ответил %s", self.code, url, resp.status_code)
                note_failure(self.code)
                return None, resp.status_code
            if "json" not in (resp.headers.get("Content-Type") or ""):
                log.warning("%s: шлюз %s ответил не JSON — похоже на страницу защиты",
                            self.code, url)
                note_failure(self.code)
                return None, resp.status_code
            note_success(self.code)
            return resp.json(), 200
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: запрос к шлюзу %s не удался (%s)", self.code, url, exc)
            note_failure(self.code)
            return None, 0

    def _gateway_search(self, query: str, limit: int) -> list[Candidate] | None:
        """Выдача поиска шлюза. None — шлюз не ответил, пустой список — ничего не нашлось."""
        store = self._store_code()
        if store is None:
            return []
        body = {"term": query, "storeCode": store, "storeType": STORE_TYPE,
                "catalogType": CATALOG_TYPE, "includeAdultGoods": True,
                "pagination": {"offset": 0, "limit": max(limit, 10)},
                "sort": {"order": "desc", "type": "popularity"}}
        payload = cached_call(
            self.code, f"gw-search:{query}:{limit}:{self.location.key}",
            lambda: self._gateway(GOODS_SEARCH_URL, body=body)[0])
        if payload is None:
            return None
        found: list[Candidate] = []
        for item in (payload or {}).get("items") or []:
            candidate = _to_candidate(self.code, item)
            if candidate:
                found.append(candidate)
        return found

    def _gateway_card(self, sku: str) -> dict | None:
        """Карточка товара в нашем магазине. {} — товара здесь нет, None — шлюз молчит."""
        store = self._store_code()
        if store is None:
            return {}                          # Магнита в этом городе нет — товара тем более
        url = GOODS_CARD_URL.format(sku=sku, store=store)
        params = {"storetype": STORE_TYPE, "catalogtype": CATALOG_TYPE}

        def ask() -> dict | None:
            payload, status = self._gateway(url, params=params)
            if status in (404, 422):
                return {}                      # «goods not found» — ответ, а не поломка
            return payload if isinstance(payload, dict) else None

        return cached_call(self.code, f"gw-card:{sku}:{self.location.key}", ask)

    def _get_html(self, url: str, params: dict[str, Any] | None = None) -> tuple[str | None, int]:
        """Страница магазина и код ответа.

        Код нужен вызывающему: 404 — это осмысленный ответ «в этом магазине такого товара
        нет», и путать его с обрывом связи нельзя. Поэтому 404 не считается неудачей
        коннектора и предохранитель на него не реагирует.
        """
        if api_disabled(self.code):
            return None, 0
        try:
            import requests
        except ImportError:  # pragma: no cover
            return None, 0
        timeout = float(config.get("connectors.timeout_sec", 10) or 10)
        proxies = homeexit.requests_proxies(self.code)
        try:
            resp = requests.get(url, params=params, headers=self._headers(),
                                cookies=self._shop_cookies(), timeout=timeout,
                                proxies=proxies)
            if resp.status_code == 404:
                note_success(self.code)          # магазин ответил, просто товара у него нет
                return None, 404
            if resp.status_code != 200:
                log.warning("%s: %s ответил %s — ухожу в fallback", self.code, url, resp.status_code)
                note_failure(self.code)
                return None, resp.status_code
            note_success(self.code)
            return resp.text, 200
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: запрос к %s не удался (%s) — ухожу в fallback", self.code, url, exc)
            note_failure(self.code)
            return None, 0

    # --- разбор ---
    def _cards(self, page: str) -> list[Candidate]:
        out: list[Candidate] = []
        for block in _ARTICLE.finditer(page or ""):
            body = block.group(0)
            link = _LINK.search(body)
            if not link:
                continue
            name = html.unescape(link.group(1)).strip()
            price_match = _CARD_SALE.search(body) or _CARD_REGULAR.search(body)
            weight_g, unit = _weight_of(name)
            out.append(Candidate(
                store_code=self.code,
                sku=link.group(2),
                name=name,
                price=_to_float(price_match.group(1)) if price_match else None,
                weight_g=weight_g,
                unit=unit,
                url=f"{self.site_url}/product/{link.group(2)}-{link.group(3)}",
            ))
        return out

    # --- контракт ---
    def _search(self, query: str, limit: int) -> list[Candidate]:
        if self._store_code() is None:
            log.info("%s: рядом с адресом клиента магазинов нет — цен не будет", self.code)
            return []                     # выдумывать цены чужого города не станем
        found = self._gateway_search(query, limit)
        if found is None:                 # шлюз молчит — пробуем прежний путь по вёрстке
            found = self._html_search(query, limit)
        if not found:
            log.warning("%s: по «%s» ничего не разобрано — беру data/fallback_prices.csv",
                        self.code, query)
            return self._fallback_search(query, limit)
        for candidate in found:
            candidate.score = similarity(query, candidate.name)
        found.sort(key=lambda c: c.score, reverse=True)
        return found[:limit]

    def _html_search(self, query: str, limit: int) -> list[Candidate]:
        """Прежний путь: страница поиска и разбор вёрстки. Остался запасным для шлюза."""
        params = {"term": query, **self._shop_params()}
        page, _ = _unpack(cached_call(self.code, f"search:{query}:{limit}:{self.location.key}",
                                      lambda: self._get_html(SEARCH_URL, params)))
        return self._cards(page) if page else []

    def _out_of_stock(self, skus: list[str]) -> list[PriceSnapshot]:
        """Товары, которых в выбранном магазине нет.

        Цену берём справочную, чтобы экран мог показать порядок величины, но помечаем
        отсутствие: оптимизатор такую позицию в этот магазин не положит.
        """
        known = {snap.sku: snap.price for snap in self._fallback_prices(list(skus))}
        return [PriceSnapshot(store_code=self.code, sku=sku,
                              price=known.get(sku, 0.0), in_stock=False) for sku in skus]

    def _from_card(self, sku: str, card: dict) -> PriceSnapshot | None:
        """Карточка шлюза в снимок цены. None — в этом магазине товар не продаётся.

        Два способа сказать «не продаётся», и оба встречаются живьём:
        пустая карточка (шлюз ответил 422 goods_not_found — такого артикула нет в
        сети вовсе) и карточка с ценой 0 при остатке 0 — товар в сети есть, но не в
        этой точке. Молоко «Кубанский молочник» в московском магазине отвечает
        именно так, а в краснодарском стоит 159 ₽.

        Нулевой остаток при НЕнулевой цене — другое дело: товар в магазине есть,
        просто сейчас кончился. Цену показываем, а отсутствие помечаем — оптимизатор
        такую позицию в этот магазин не положит.
        """
        price = _money(card.get("price")) if card else None
        quantity = card.get("quantity") if card else None
        if price is None or (not price and not quantity):
            return None
        # Весовой товар шлюз называет в рублях за килограмм (тот же признак
        # weighted.isWeighted, по которому каталог ставит unit="kg"). Сказать это
        # снимку явно — значит дать расчёту довод: одна отметка «kg» у товара
        # сети доводом не считается (service._shop_by_weight).
        weighted = card.get("weighted") if isinstance(card.get("weighted"), dict) else {}
        return PriceSnapshot(
            store_code=self.code,
            sku=sku,
            price=price,
            price_per_kg=price if weighted.get("isWeighted") else None,
            in_stock=bool(quantity) if isinstance(quantity, (int, float)) else True,
            name=(card.get("name") or "").strip() or None,
        )

    def _get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        out: list[PriceSnapshot] = []
        missing: list[str] = []        # не дозвонились или не разобрали — берём справочник
        absent: list[str] = []         # магазин ответил «нет такого товара»
        for sku in skus:
            if sku.startswith(self.fallback_sku_prefix):  # синтетический артикул из CSV
                missing.append(sku)
                continue
            card = self._gateway_card(sku)
            if card is not None:
                snapshot = self._from_card(sku, card)
                if snapshot is None:
                    absent.append(sku)
                else:
                    out.append(snapshot)
                continue
            page, status = _unpack(cached_call(
                self.code, f"product:{sku}:{self.location.key}",
                lambda sku=sku: self._get_html(PRODUCT_URL.format(sku=sku), self._shop_params())))
            if status == 404:
                log.info("%s: %s не продаётся в выбранном магазине", self.code, sku)
                absent.append(sku)
                continue
            if not page:
                missing.append(sku)
                continue
            match = _LD_PRICE.search(page) or _PAGE_PRICE.search(page) or _META_PRICE.search(page)
            price = _to_float(match.group(1)) if match else None
            in_stock = True
            per_kg = None
            if price is None:
                # страницу мы получили, а прочесть не смогли: похоже, сменилась вёрстка.
                # прежде чем подсунуть справочную цену, спросим модель — если она включена
                guess = smart_extract.price_from_html(self.code, page, f"артикул {sku}")
                if not guess:
                    missing.append(sku)
                    continue
                price, in_stock = guess["price"], guess["in_stock"]
                # Модель сказала «за кг» — это цена килограмма, и расчёт должен это знать:
                # иначе весовой товар посчитался бы по ней как штучный.
                per_kg = price if guess.get("unit") == "kg" else None
            title = re.search(r"<title>(.*?)(?:\s*–|</title>)", page, re.S)
            out.append(PriceSnapshot(
                store_code=self.code,
                sku=sku,
                price=price,
                price_per_kg=per_kg,
                in_stock=in_stock,
                name=html.unescape(title.group(1)).strip() if title else None,
            ))
        if absent:
            out.extend(self._out_of_stock(absent))
        if missing:
            out.extend(self._fallback_prices(missing))
        return out
