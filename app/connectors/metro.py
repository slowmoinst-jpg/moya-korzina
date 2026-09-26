"""METRO: цены по конкретному торговому центру, остаток числом и штрихкоды.

ПОЧЕМУ ЭТА СЕТЬ ЦЕННЕЕ ОСТАЛЬНЫХ. Из всех шести с лишним сетей, до которых мы
дотянулись, METRO единственная отдаёт сразу четыре вещи: цену по конкретной точке,
ОТДЕЛЬНО цену на полке (`prices.offline.price`), остаток числом и **несколько
штрихкодов на товар**. Штрихкод здесь важнее цены: это единственный надёжный ключ,
которым строка чека сшивается с карточкой каталога. У Магнита его нет вовсе, у
ВкусВилла в карточке нет, у Ленты один.

Замер 18–19.09.2026 с боевого сервера: в петербургской точке 32 552 товара,
652 страницы по 50, то есть полный обход одной точки при одном запросе в секунду —
около одиннадцати минут. Столько же стоит обход одного магазина Магнита.

ПРО ТОКЕН, И ЭТО НАДО ЧИТАТЬ ДО ТОГО, КАК ОПЕРЕТЬСЯ НА МОДУЛЬ. Адрес метода в
спецификации сети выглядит так:

    /api/v1/{api_token}/{store_id}/products

Спецификация выложена публично и отдаётся без ключа — это сделано намеренно. А вот
то, что запросы проходят и БЕЗ сегмента токена, в спецификации не описано и похоже
на недосмотр маршрутизации, а не на приглашение. Плюс `robots.txt` этого хоста
запрещает обход целиком, а условий использования нигде не опубликовано.

Поэтому модуль устроен так, чтобы честная дорога была первой: есть настройка
`connectors.metro_api_token`, и если она задана, токен уходит в адрес, как и велит
спецификация. Пока её нет, адрес собирается без токена, и это ЗАПАСНОЙ путь,
а не основной. Получить именной токен можно через форму METRO для бизнеса с темой
«Запросы на сотрудничество» — до тех пор на этот канал нельзя закладывать
производство.

ЧЕГО ЗДЕСЬ НЕТ. Текстового поиска у сети нет вовсе: товары берутся по артикулам
(`articles[]`) или по категории, а параметра со строкой запроса в спецификации не
существует. Поэтому `_search` ищет не в сети, а в НАШЕМ едином каталоге среди
строк METRO, которые туда положил сборщик `app/catalog/crawlers/metro.py`. Это не
обход и не хитрость: сеть просто не умеет искать словами, а мы умеем.

Записи в чужую корзину здесь тоже нет. В спецификации METRO метод наполнения
корзины есть, но это отдельное решение владельца, и в модуле цен ему не место.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Iterable

import requests

from app import config
from app.connectors.base import Connector, register
from app.connectors.cache import cached_call
from app.models import Candidate, Location, PriceSnapshot

log = logging.getLogger(__name__)

HOST = "https://api.metro-cc.ru/api/v1"
SITE = "https://online.metro-cc.ru"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

# Сколько артикулов спрашиваем одним запросом. Метод принимает массив, и пачка
# вдесятеро дешевле десяти отдельных походов; сотня — чтобы не упереться в длину адреса.
BATCH = 50
PAGE = 50            # столько товаров на страницу отдаёт сама сеть, меньше просить нельзя


def _token_part() -> str:
    """Сегмент токена в адресе. Пусто — идём запасным путём, см. шапку модуля."""
    token = str(config.get("connectors.metro_api_token") or "").strip()
    return f"{token}/" if token else ""


def _url(tail: str) -> str:
    return f"{HOST}/{_token_part()}{tail.lstrip('/')}"


def _get(tail: str, params: list[tuple[str, Any]] | None = None, timeout: float = 45.0) -> dict:
    response = requests.get(_url(tail), params=params or [], timeout=timeout,
                            headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError(f"METRO ответила отказом: {str(data.get('errors'))[:200]}")
    return data if isinstance(data, dict) else {"data": data}


def _rows(payload: dict) -> list[dict]:
    """Список товаров из ответа. Сеть заворачивает его в data.data и рядом кладёт страницы."""
    data = payload.get("data")
    if isinstance(data, dict):
        inner = data.get("data")
        return [r for r in (inner or []) if isinstance(r, dict)]
    return [r for r in (data or []) if isinstance(r, dict)]


def _page_info(payload: dict) -> dict:
    data = payload.get("data")
    return {k: v for k, v in data.items() if k != "data"} if isinstance(data, dict) else {}


# ---------- торговые центры ----------
def tradecenters() -> list[dict]:
    """Все торговые центры сети: код точки, адрес, город, координаты.

    Ответ один на страну и меняется редко, поэтому идёт через общий кэш коннекторов.
    """
    def run() -> list[dict]:
        payload = _get("tradecenters")
        data = payload.get("data")
        rows = data if isinstance(data, list) else (data or {}).get("data") or []
        return [r for r in rows if isinstance(r, dict)]

    return cached_call("metro", "tradecenters", run) or []


def _distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Расстояние по большому кругу. Нужно только для сравнения точек между собой."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def _center_point(center: dict) -> tuple[float, float] | None:
    """Координаты центра. Сеть кладёт их ВЛОЖЕННЫМ объектом `coordinates`.

    Это выяснилось живой проверкой 19.09.2026: сперва здесь искали latitude и
    longitude на верхнем уровне, и подбор ближайшего центра молча возвращал «не
    нашёл» при полном списке из 91 центра. Верхний уровень оставлен запасным —
    сети случается менять форму ответа, а падать на этом незачем.
    """
    sources = [center]
    nested = center.get("coordinates")
    if isinstance(nested, dict):
        sources.insert(0, nested)
    for source in sources:
        for lat_key, lon_key in (("latitude", "longitude"), ("lat", "lon"), ("lat", "lng")):
            lat, lon = source.get(lat_key), source.get(lon_key)
            try:
                if lat is not None and lon is not None:
                    return float(lat), float(lon)
            except (TypeError, ValueError):
                continue
    return None


# Дальше этого центр METRO уже не «ваш»: доставка и самовывоз у сети по городу и
# области. Без потолка человек из города без METRO получал цены центра за сотни
# километров — правдоподобные и ничем не отличимые от своих.
MAX_KM = 60.0


def nearest_store(address: str) -> dict | None:
    """Ближайший к адресу торговый центр. None — адрес не разобрался, центров нет
    или ближайший дальше connectors.metro_max_km.

    У METRO центров на всю страну меньше сотни, и в городе их единицы: в Петербурге
    три. Поэтому «ближайший» здесь честно означает ближайший, а не хаб доставки,
    как у Ленты.
    """
    from app import geo

    point = geo.coords(address) if address else None
    centers = tradecenters()
    if not centers:
        return None
    if not point:
        return None
    best, best_km = None, None
    for center in centers:
        here = _center_point(center)
        if not here:
            continue
        km = _distance_km(point, here)
        if best_km is None or km < best_km:
            best, best_km = center, km
    if best is None:
        return None
    try:
        limit = float(config.get("connectors.metro_max_km") or MAX_KM)
    except (TypeError, ValueError):
        limit = MAX_KM
    if best_km is not None and best_km > limit:
        log.info("metro: ближайший центр к «%s» в %.0f км — дальше %.0f км, это не ваш",
                 address, best_km, limit)
        return None
    return {"code": str(best.get("store_id") or best.get("id") or ""),
            "address": best.get("address") or best.get("name") or "",
            "city": best.get("city") or "", "distance_km": round(best_km or 0.0, 2)}


def _store_id(location: Location | None) -> str | None:
    """Код точки для запроса: из места клиента, иначе запасной из config.yaml.

    Адрес известен, а центра рядом нет — это ответ «METRO здесь нет», а не повод
    взять запасной петербургский центр: его цены и наличие выглядели бы своими.
    """
    if location and location.store_id:
        return str(location.store_id)
    if location and location.address:
        found = nearest_store(location.address)
        return found["code"] if found and found["code"] else None
    fallback = config.get("connectors.metro_store_id")
    return str(fallback) if fallback else None


# ---------- разбор карточки ----------
def _price(row: dict) -> tuple[float | None, float | None, bool]:
    """Цена витрины, цена на полке и признак акции.

    Сеть кладёт обе цены рядом: `price` — то, что человек заплатит в этом канале,
    `offline.price` — то, что стоит на полке. Различие между ними и есть наценка
    канала, и врать про неё нельзя: она бывает в оба конца.
    """
    prices = row.get("prices") or {}
    if not isinstance(prices, dict):
        return None, None, False
    def num(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None
    offline = prices.get("offline") or {}
    return (num(prices.get("price")),
            num(offline.get("price")) if isinstance(offline, dict) else None,
            bool(prices.get("is_promo")))


def _stock(row: dict) -> tuple[bool, float | None]:
    stock = row.get("stock") or {}
    if not isinstance(stock, dict):
        return True, None
    try:
        value = float(stock.get("value")) if stock.get("value") is not None else None
    except (TypeError, ValueError):
        value = None
    return (value is None or value > 0), value


def _barcode(row: dict) -> str | None:
    """Первый штрихкод товара. Их бывает несколько — берём первый, остальные равноценны."""
    codes = row.get("barcodes")
    if isinstance(codes, list):
        for code in codes:
            text = str(code or "").strip()
            if text.isdigit():
                return text
    return None


def _url_of(row: dict) -> str:
    slug = str(row.get("slug") or "").strip()
    return f"{SITE}/products/{slug}" if slug else SITE


# ---------- коннектор ----------
@register("metro")
class MetroConnector(Connector):
    """Цены METRO по точке человека. Поиск словами — по нашему каталогу, см. шапку."""

    code = "metro"
    site_url = SITE
    fallback_sku_prefix = "metro-"

    def products_by_articles(self, articles: Iterable[str], store: str) -> list[dict]:
        """Карточки по артикулам, пачками. Пустой ответ — товара в этой точке нет."""
        out: list[dict] = []
        batch: list[str] = []

        def flush() -> None:
            if not batch:
                return
            key = f"articles:{store}:{','.join(sorted(batch))}"

            def run() -> list[dict]:
                params = [("articles[]", a) for a in batch]
                return _rows(_get(f"{store}/products", params))

            out.extend(cached_call(self.code, key, run) or [])
            batch.clear()

        for article in articles:
            text = str(article or "").strip()
            if not text:
                continue
            batch.append(text)
            if len(batch) >= BATCH:
                flush()
        flush()
        return out

    def _candidate(self, row: dict) -> Candidate | None:
        article = str(row.get("article") or "").strip()
        name = str(row.get("name") or "").strip()
        if not article or not name:
            return None
        price, _offline, _promo = _price(row)
        return Candidate(store_code=self.code, sku=article, name=name, price=price,
                         url=_url_of(row), ean=_barcode(row))

    def _search(self, query: str, limit: int) -> list[Candidate]:
        """Поиск словами идёт по нашему каталогу: у сети текстового поиска нет.

        Каталог наполняет app/catalog/crawlers/metro.py. Пока обхода не было,
        строк METRO там нет, и честный ответ — пусто, а не выдуманный кандидат.
        """
        from app.catalog import store as catalog

        try:
            items = catalog.search_items(query, limit * 4)
        except Exception as exc:  # noqa: BLE001 — каталога может не быть вовсе
            log.info("metro: каталог не спросился (%s)", exc)
            return []
        articles: list[str] = []
        for row in items:
            # Единый товар знает, какими артикулами он зовётся в каждой сети.
            card = catalog.item(int(row.get("id"))) if row.get("id") else None
            for chain_row in (card or {}).get("chains") or []:
                if str(chain_row.get("chain")) == self.code and chain_row.get("sku"):
                    articles.append(str(chain_row["sku"]))
                    break
            if len(articles) >= limit:
                break
        if not articles:
            return []
        store = _store_id(self.location)
        if not store:
            log.warning("metro: не задана точка — цены спрашивать негде")
            return []
        return [c for c in (self._candidate(r)
                            for r in self.products_by_articles(articles, store)) if c]

    def _search_barcode(self, barcode: str) -> Candidate | None:
        """Штрихкод ищется в нашем каталоге: у сети поиска по нему тоже нет."""
        from app.catalog import store as catalog

        try:
            rows = [r for r in catalog.products_of_chain(self.code)
                    if str(r.get("barcode") or "") == barcode]
        except Exception:  # noqa: BLE001
            return None
        if not rows:
            return None
        store = _store_id(self.location)
        if not store:
            return None
        cards = self.products_by_articles([str(rows[0].get("sku"))], store)
        return self._candidate(cards[0]) if cards else None

    def _get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        store = _store_id(self.location)
        if not store:
            log.warning("metro: не задана ни точка, ни запасная в config.yaml — "
                        "цены спрашивать негде")
            return []
        snapshots: list[PriceSnapshot] = []
        for row in self.products_by_articles(skus, store):
            article = str(row.get("article") or "").strip()
            price, _offline, _promo = _price(row)
            if not article or price is None:
                continue
            in_stock, _value = _stock(row)
            snapshots.append(PriceSnapshot(store_code=self.code, sku=article, price=price,
                                           in_stock=in_stock,
                                           name=str(row.get("name") or "") or None))
        return snapshots

    def shelf_prices(self, skus: list[str]) -> dict[str, float]:
        """Цены НА ПОЛКЕ по артикулам. Отдельно от витринных — это разные числа.

        Нужны затем, что приложение обещает считать честно: цена доставки и цена
        полки различаются, и у Ленты, например, на четырнадцать процентов. METRO
        единственная, кто называет обе сразу, и это грех не использовать.
        """
        store = _store_id(self.location)
        if not store:
            return {}
        out: dict[str, float] = {}
        for row in self.products_by_articles(skus, store):
            article = str(row.get("article") or "").strip()
            _price_now, offline, _promo = _price(row)
            if article and offline is not None:
                out[article] = offline
        return out
