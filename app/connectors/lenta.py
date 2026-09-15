"""Коннектор Ленты поверх её MCP-сервера: https://mcp.lenta.com/mcp.

Лента — единственная из проверенных сетей, которая отдаёт ОСТАТОК ЧИСЛОМ. Поэтому
здесь in_stock не догадка, а факт: «stock: 119» значит сто девятнадцать штук на
витрине выбранной точки, «stock: 0» — что этой позиции там нет.

Две особенности, которые определяют устройство модуля.

Первая: сервер не хранит сессию и требует адрес или storeId В КАЖДОМ вызове. И это
не помеха, а ровно то, что нужно продукту: адрес приходит ОТ КЛИЕНТА и едет
параметром запроса (app.models.Location), никакой учётной записи для этого не надо.
Запасное значение в config.yaml остаётся для установки «для себя», где адрес и
правда один. Без адреса цены спрашивать бессмысленно — они у каждой точки свои.
Проверено 13.09.2026: молоко Простоквашино
2,5 % стоит 90,99 ₽ на Ходынском бульваре в Москве (обычная 124,99, скидка 27 %) и
91,99 ₽ в Екатеринбурге (обычная 108,99, скидка 16 %). Различается не только цена,
но и глубина акции.

Вторая: поиск цены НЕ отдаёт — у всех найденных позиций price = 0. Цена и остаток
приходят только из карточки товара. Отсюда та же двухходовка, что у Магнита: поиск
находит идентификаторы, карточка даёт цифры.

Третья появилась 15.09.2026: storefront_cart_link_create, ссылка на готовую корзину
(см. cart_link ниже). Ещё 13.09 инструментов было четыре, теперь пять — значит Лента
стала второй сетью после ВкусВилла, куда корзина уезжает целиком, а не по одной
карточке.
"""
from __future__ import annotations

import logging
import math
import re

from app.connectors import mcp_client
from app.connectors.base import Connector, register, similarity
from app.models import Candidate, Location, PriceSnapshot

log = logging.getLogger(__name__)

MCP_URL = "https://mcp.lenta.com/mcp"
SITE_URL = "https://lenta.com"
DEFAULT_CHANNEL = "lo"                       # витрина «Лента онлайн»; ещё бывают utk, b2b, ozn
CART_LIMIT = 100                             # потолок объявлен самой Лентой
_WEIGHT_WORD = re.compile(r"\bвесов\w*", re.I)


def _unit(name: str | None) -> str:
    """У Ленты весовой товар помечен словом в названии: «Огурцы …, весовые»."""
    return "kg" if _WEIGHT_WORD.search(name or "") else "pcs"


def _number(value) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _where(location: Location | None = None) -> dict:
    """Адрес — то, без чего Лента не отвечает ценами. Код точки идёт вторым, и вот почему.

    ПРОВЕРЕНО 15.09.2026, И РЕЗУЛЬТАТ ОБРАТЕН ОЖИДАЕМОМУ. Казалось, что код точки
    надёжнее адреса: строку сервер разбирает сам и может выбрать не тот магазин.
    На деле коды, которые отдаёт storefront_resolve_store, витрина не принимает
    вовсе. По адресу «Екатеринбург, улица Щербакова 4» поиск даёт 10 позиций, а по
    коду ближайшей к нему точки (id 1278, она же aliasId 202138, ТК202138 в 394 м)
    — ноль. То же самое у всех остальных точек того же ответа и на всех каналах:
    lo, cc, utk, ozn. Карточка товара по коду отвечает не отказом, а ценой 0 и
    остатком 0 — что коннектор обязан читать как «этой позиции здесь нет».

    Отсюда вывод: resolve_store перечисляет ФИЗИЧЕСКИЕ магазины сети (форматы MM,
    SM, HM — у дома, супермаркет, гипермаркет), а витрина доставки работает по
    другим точкам, наружу не названным. Поэтому спрашиваем адресом.

    Код оставлен запасным путём на случай, когда адреса нет, а код откуда-то есть.
    Ошибиться им тихо и дорого: неверный код не падает, он делает вид, что в
    магазине пусто.
    """
    location = location or Location()
    if location.address:
        return {"address": str(location.address), "channel": DEFAULT_CHANNEL}
    if location.store_id:
        try:
            return {"storeId": int(location.store_id), "channel": DEFAULT_CHANNEL}
        except (TypeError, ValueError):
            log.warning("lenta: код точки %r не число — спрашивать нечем", location.store_id)
    return {}


def cart_link(items: list[tuple[int, float]]) -> str | None:
    """Ссылка на корзину Ленты Онлайн по списку (id товара, количество).

    Появилось 15.09.2026: ещё 13.09 у сервера было четыре инструмента, теперь пять,
    и пятый — storefront_cart_link_create. Значит Лента перешла из «по ссылке на
    каждый товар» в «вся корзина одной ссылкой», как ВкусВилл.

    Отличия от ВкусВилла, ради которых это отдельная функция, а не общая:
    ключ items вместо products, поле quantity вместо q, ЦЕЛОЕ количество вместо
    дробного и потолок в сто позиций вместо двадцати. Адрес здесь не нужен —
    единственный вызов Ленты, который без него работает.

    Целое количество означает, что развесной товар так не передать: 0,4 кг сыра
    превратятся в одну упаковку. Поэтому дробное округляем вверх — лучше показать
    человеку в корзине больше, чем он собирался, чем молча недодать.

    Кэшировать нельзя: каждый вызов создаёт новую ссылку.
    """
    products = []
    for sku, qty in items[:CART_LIMIT]:
        try:
            number = int(math.ceil(float(qty)))
        except (TypeError, ValueError):
            continue
        products.append({"id": int(sku), "quantity": max(number, 1)})
    if not products:
        return None
    if len(items) > CART_LIMIT:
        log.info("lenta: в ссылку влезает %d позиций из %d — остальные придётся отдать второй ссылкой",
                 CART_LIMIT, len(items))
    answer = mcp_client.call_tool(MCP_URL, "lenta", "storefront_cart_link_create",
                                  {"items": products})
    data = mcp_client.ok_payload(answer) or {}
    link = data.get("link")
    return link if isinstance(link, str) and link.startswith("http") else None


def nearest_stores(address: str) -> list[dict]:
    """Ближайшие точки Ленты по адресу: id, aliasId, name, address, shopType, distance.

    Нужна дважды: при настройке установки «для себя» (человек указывает адрес, а в
    config.yaml попадает код точки) и в работе с клиентом — его адрес разрешается
    в код точки один раз, дальше запросы идут по коду, который однозначен.
    """
    answer = mcp_client.call_tool(MCP_URL, "lenta", "storefront_resolve_store",
                                  {"address": address}, cache_key=f"stores:{address}")
    data = answer if isinstance(answer, dict) else {}
    hubs = data.get("hubs") or (data.get("data") or {}).get("hubs") or []
    return [h for h in hubs if isinstance(h, dict)]


@register("lenta")
class LentaConnector(Connector):
    code = "lenta"
    site_url = SITE_URL
    fallback_sku_prefix = "lenta-"

    def _to_candidate(self, item: dict) -> Candidate | None:
        sku, name = item.get("id"), (item.get("name") or "").strip()
        if not sku or not name:
            return None
        return Candidate(
            store_code=self.code,
            sku=str(sku),
            name=name,
            price=_number(item.get("price")) or None,   # поиск цен не отдаёт, это ожидаемо
            unit=_unit(name),
            url=item.get("url") or f"{SITE_URL}/product/{item.get('slug', '')}-{sku}/",
        )

    def _search(self, query: str, limit: int) -> list[Candidate]:
        where = _where(self.location)
        if not where:
            log.warning("%s: не задан адрес клиента и нет запасного в config.yaml — "
                        "без адреса цены спрашивать нечего", self.code)
            return self._fallback_search(query, limit)
        answer = mcp_client.call_tool(MCP_URL, self.code, "storefront_products_search",
                                      {"query": query, "page": 1, **where},
                                      cache_key=f"search:{query}:{sorted(where.items())}")
        data = mcp_client.ok_payload(answer) or {}
        found = [c for c in (self._to_candidate(i) for i in data.get("items") or []) if c]
        if not found:
            log.warning("%s: по «%s» MCP ничего не дал — беру data/fallback_prices.csv", self.code, query)
            return self._fallback_search(query, limit)
        for candidate in found:
            candidate.score = similarity(query, candidate.name)
        found.sort(key=lambda c: c.score, reverse=True)
        return found[:limit]

    def _get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        where = _where(self.location)
        if not where:
            return self._fallback_prices(list(skus))
        out: list[PriceSnapshot] = []
        missing: list[str] = []
        for sku in skus:
            if sku.startswith(self.fallback_sku_prefix) or not str(sku).isdigit():
                missing.append(sku)
                continue
            answer = mcp_client.call_tool(MCP_URL, self.code, "storefront_product_details",
                                          {"id": int(sku), **where},
                                          cache_key=f"product:{sku}:{sorted(where.items())}")
            data = mcp_client.ok_payload(answer) or {}
            item = data.get("item") if isinstance(data.get("item"), dict) else data
            price = _number(item.get("price"))
            stock = _number(item.get("stock")) or 0.0
            if price is None:
                missing.append(sku)
                continue
            name = (item.get("name") or "").strip() or None
            if price <= 0 and stock <= 0:
                # Лента говорит прямо: этой позиции в выбранной точке нет.
                # Цену показываем справочную, но помечаем отсутствие — оптимизатор не положит.
                known = {s.sku: s.price for s in self._fallback_prices([sku])}
                out.append(PriceSnapshot(store_code=self.code, sku=str(sku),
                                         price=known.get(sku, 0.0), in_stock=False, name=name))
                continue
            out.append(PriceSnapshot(
                store_code=self.code,
                sku=str(sku),
                price=price,
                price_per_kg=price if _unit(name) == "kg" else None,
                in_stock=stock > 0,
                name=name,
            ))
        if missing:
            out.extend(self._fallback_prices(missing))
        return out
