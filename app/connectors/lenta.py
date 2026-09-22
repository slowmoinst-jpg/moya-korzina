"""Коннектор Ленты поверх её MCP-сервера: https://mcp.lenta.com/mcp.

Лента — единственная из проверенных сетей, которая отдаёт ОСТАТОК ЧИСЛОМ. Поэтому
здесь in_stock не догадка, а факт: «stock: 119» значит сто девятнадцать штук на
витрине выбранной точки, «stock: 0» — что этой позиции там нет.

Три особенности, которые определяют устройство модуля.

Первая: сервер не хранит сессию и требует адрес или storeId В КАЖДОМ вызове. И это
не помеха, а ровно то, что нужно продукту: адрес приходит ОТ КЛИЕНТА и едет
параметром запроса (app.models.Location), никакой учётной записи для этого не надо.
Запасное значение в config.yaml остаётся для установки «для себя», где адрес и
правда один. Без адреса цены спрашивать бессмысленно — они у каждой точки свои.
Проверено 13.09.2026: молоко Простоквашино 2,5 % стоит 90,99 ₽ на Ходынском
бульваре в Москве (обычная 124,99, скидка 27 %) и 91,99 ₽ в Екатеринбурге (обычная
108,99, скидка 16 %). Различается не только цена, но и глубина акции.

При этом адрес разрешается ОДИН РАЗ. storefront_resolve_store отдаёт по адресу
магазины рядом и отдельно называет, какой из них Лента берёт хабом доставки
(suggested.delivery). Его aliasId и есть storeId витрины — так написано в схеме
самих инструментов («aliasId из storefront_resolve_store, не Monolith id»), и так
проверено 16.09.2026: поиск «молоко» по адресу «Москва, Ходынский бульвар 4» и по
storeId 291 отдал одни и те же десять позиций с теми же ценами, а в ответе на
поиск по адресу сервер сам вернул storeId 291. Поэтому _where() спрашивает адрес
у resolve_store (ответ лежит в файловом кэше), а поиск и карточки ходят по коду
хаба: ключи их кэша строятся по storeId, и два написания одного адреса не
удваивают запросов. Не разрешился адрес — уходит сам адрес, витрина принимает и
его. Подводные камни кодов — в _where().

Вторая: поиск ОТДАЁТ ЦЕНУ. Запись от 13.09 «у всех найденных позиций price = 0»
устарела: 16.09.2026 storefront_products_search по «молоко» вернул у всех десяти
позиций price, priceRegular, discountPercent, stock, package, slug, url (например
id 671969: 91,99 ₽ при обычной 106,99, скидка 14 %, остаток 64) — ровно те же
поля, что у storefront_product_details. Двухходовка «поиск, потом карточка» ради
цены не нужна: карточка остаётся инструментом для уже известного id (get_prices),
а ноль в цене поиска значит то же, что и в карточке, — «в этой точке не продаётся».

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


def _code(value) -> int | None:
    """Код точки как число: aliasId приходит числом, из config.yaml может прийти строкой."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def resolve_address(address: str) -> dict:
    """Ответ storefront_resolve_store целиком: addressUsed, latitude, longitude, hubs, suggested.

    Один сетевой вызов на адрес: ответ ложится в файловый кэш, и из него читают и
    nearest_stores (проверка адреса на экране), и delivery_hub (код для цен).
    Пустой словарь — сервер не ответил или адрес не разобран.
    """
    address = (address or "").strip()
    if not address:
        return {}
    answer = mcp_client.call_tool(MCP_URL, "lenta", "storefront_resolve_store",
                                  {"address": address}, cache_key=f"stores:{address}")
    return mcp_client.ok_payload(answer) or {}


def delivery_hub(address: str) -> int | None:
    """Код хаба доставки для адреса: suggested.delivery.aliasId из resolve_store.

    Единственный код, который стоит отдавать витрине как storeId (почему — в _where).
    None — адрес не разобран или сервер молчит; тогда спрашивают самим адресом.
    """
    suggested = resolve_address(address).get("suggested")
    delivery = suggested.get("delivery") if isinstance(suggested, dict) else None
    return _code(delivery.get("aliasId")) if isinstance(delivery, dict) else None


def _where(location: Location | None = None) -> dict:
    """Чем спрашиваем витрину: кодом хаба доставки, а адресом — только если код не добыть.

    storeId витрины — это aliasId магазина из storefront_resolve_store (номер ТК),
    а не внутренний id. Так написано в схеме инструментов, и так отвечает сервер.
    Проверено 16.09.2026 по адресу «Москва, Ходынский бульвар 4»:

    * хаб доставки из ответа (suggested.delivery: ТК291, id 62, aliasId 291) — поиск
      по storeId 291 отдаёт те же десять позиций с теми же ценами, что по адресу;
    * aliasId ДРУГОГО магазина из того же ответа (4537, формат AL, 3,6 км) — витрина
      отвечает, но его ассортиментом и его ценами;
    * внутренний id вместо aliasId — либо пусто (5344), либо чужой магазин, у
      которого такой aliasId (62: молоко по 74,99 вместо 91,99);
    * aliasId магазина без доставки (202138 — ТК в 394 м от «Екатеринбург, улица
      Щербакова 4», с него началась запись 15.09) — поиск отдаёт пустой список,
      карточка — «товар не найден».

    Отсюда правило: код берём только тот, что Лента сама назвала хабом доставки для
    этого адреса, и не подбираем магазин по расстоянию сами. Ближайший к Ходынскому
    ТК1537 стоит в 194 м, а хаб доставки — в 11 км: как говорит схема поиска, «если
    ближайший магазин пустой на доставке — берётся следующий хаб с ассортиментом».
    Чужой код не падает — он тихо показывает чужие цены или пустой магазин.

    Порядок:
    1. есть адрес — разрешаем его в хаб (ответ resolve_store в кэше) и уходим кодом;
    2. адрес есть, а хаба не добыть (сервер молчит, город не обслуживается) — уходит
       сам адрес: витрина подберёт хаб сама, ключ кэша будет по адресу;
    3. адреса нет, но есть код (lenta_store_id в config.yaml) — уходит код как есть,
       без сети: это и есть «уже разрешённое» место;
    4. ничего нет — пустой словарь, коннектор берёт data/fallback_prices.csv.

    Запись от 15.09 «коды из resolve_store витрина не принимает» была неверна:
    тогда проверяли aliasId ближайшего физического магазина, а не хаба доставки.
    """
    location = location or Location()
    address = str(location.address or "").strip()
    if address:
        hub = delivery_hub(address)
        if hub is not None:
            return {"storeId": hub, "channel": DEFAULT_CHANNEL}
        log.info("lenta: адрес «%s» в хаб доставки не разрешился — спрашиваю адресом", address)
        return {"address": address, "channel": DEFAULT_CHANNEL}
    if location.store_id:
        code = _code(location.store_id)
        if code is not None:
            return {"storeId": code, "channel": DEFAULT_CHANNEL}
        log.warning("lenta: код точки %r не число — спрашивать нечем", location.store_id)
    return {}


def _weight_rules(sku: int, location: Location | None) -> dict | None:
    """Как считается количество у развесного товара. None — сеть не ответила.

    ОПИСАНИЕ ИНСТРУМЕНТА У ЛЕНТЫ И ЕЁ ЖЕ ДАННЫЕ ПРОТИВОРЕЧАТ ДРУГ ДРУГУ, И ЭТО
    НАДО ЗНАТЬ ДО ТОГО, КАК ЧИТАТЬ КОД НИЖЕ.

    Описание storefront_cart_link_create говорит: «для развесных quantity — целые
    граммы, кратные saleLimit.stepGrams». Звучит однозначно. Вот что сеть при этом
    отдаёт на настоящих товарах (замер 19.09.2026, точка «Москва, Ходынский 4»):

        Картофель, весовой      weightGrams 1000, saleLimit {min 1, max 15,  step 1}
        Картофель красный       weightGrams 1000, saleLimit {min 1, max 127, step 1}
        Сыр ЛАМБЕР полшара      weightGrams  500, saleLimit {min 1, max 10,  step 1}
        Сыр NATURA весовой      weightGrams  300, saleLimit {min 1, max 14,  step 1}

    Если бы quantity был в граммах, «max 15» означало бы, что картофеля продают не
    больше пятнадцати ГРАММОВ. Это бессмыслица. А в фасовках всё сходится: 15 раз
    по килограмму — пятнадцать килограммов, десять полшаров сыра — пять кило.
    Проверено на семи товарах подряд, у всех stepGrams = 1.

    Значит quantity — ЧИСЛО ФАСОВОК по weightGrams, а не граммы, и поля названы
    неудачно. Верим данным, а не описанию: описание не проверить, а данные сходятся
    на каждом товаре и согласуются с ценой (price = pricePerKg × weightGrams / 1000).

    Возвращаем то, из чего считается количество, а не готовое число: решение
    «сколько фасовок» принимает cart_link, и ему нужны и границы тоже.
    """
    where = _where(location)
    if not where:
        return None
    try:
        answer = mcp_client.call_tool(MCP_URL, "lenta", "storefront_product_details",
                                      {"id": int(sku), **where},
                                      cache_key=f"weight:{sku}:{sorted(where.items())}")
    except Exception as exc:  # noqa: BLE001 — сеть не ответила: это не повод падать
        log.info("lenta: карточка %s для развеса не пришла (%s)", sku, exc)
        return None
    item = (mcp_client.ok_payload(answer) or {})
    item = item.get("item") if isinstance(item.get("item"), dict) else item
    if not isinstance(item, dict):
        return None
    limits = item.get("saleLimit") if isinstance(item.get("saleLimit"), dict) else {}
    pack = _number(item.get("weightGrams")) or 0
    return {
        "weight": bool(item.get("isWeight")),
        "pack_g": int(pack) if pack > 0 else 0,
        "min": int(_number(limits.get("minGrams")) or 1),
        "max": int(_number(limits.get("maxGrams")) or 0),
    }


def cart_link(items, location: Location | None = None) -> str | None:
    """Ссылка на корзину Ленты Онлайн по списку позиций.

    Позиция — `(id, количество)` или `(id, количество, единица)`. Единица нужна
    ради развесных товаров, и вот почему.

    РАЗВЕСНОЙ ТОВАР СЧИТАЕТСЯ ФАСОВКАМИ, И ЭТО СТОИЛО ПОЛКИЛО СЫРА.

    Здесь было написано: «целое количество означает, что развесной товар так не
    передать: 0,4 кг сыра превратятся в одну упаковку», — и дробное просто
    округлялось вверх до единицы. Единица — это ОДНА ФАСОВКА, и фасовка у каждого
    товара своя: у картофеля 1000 г, у полшара ЛАМБЕРа 500 г, у NATURA 300 г
    (`weightGrams`, замер 19.09.2026). То есть 0,7 кг сыра уезжали как 500 г, а
    2 кг картофеля — как 1 кг. Молча и в деньгах: корзина выглядит собранной, а
    сумма не сходится с расчётом.

    Теперь количество считается от фасовки: сколько раз по `weightGrams` нужно
    взять, чтобы покрыть заказанные килограммы. Округляем ВВЕРХ — недодать
    человеку хуже, чем положить чуть больше, и то же правило уже принято в наряде
    (app/shopbrowser/cart._pieces). Границы `min`/`max` сети соблюдаем: выше
    потолка сеть всё равно не примет, а упереться в него молча хуже, чем сказать.

    ПОЧЕМУ НЕ ГРАММЫ, ХОТЯ ОПИСАНИЕ ГОВОРИТ «ГРАММЫ» — разобрано в `_weight_rules`
    выше: описание и данные сети противоречат друг другу, и мы верим данным.

    Прочие отличия от ВкусВилла, ради которых это отдельная функция: ключ items
    вместо products, поле quantity вместо q, потолок в сто позиций вместо двадцати.

    Кэшировать ссылку нельзя: каждый вызов создаёт новую.
    """
    products = []
    for item in items[:CART_LIMIT]:
        sku, qty = item[0], item[1]
        unit = item[2] if len(item) > 2 else None
        try:
            amount = float(qty)
        except (TypeError, ValueError):
            continue
        count = max(int(math.ceil(amount)), 1)
        if str(unit or "").lower() in ("kg", "кг"):
            rules = _weight_rules(int(sku), location)
            if rules and rules["weight"] and rules["pack_g"]:
                pack = rules["pack_g"]
                count = max(int(math.ceil(amount * 1000 / pack)), rules["min"] or 1)
                if rules["max"] and count > rules["max"]:
                    log.info("lenta: %s — %g кг это %d фасовок по %d г, а сеть даёт максимум %d",
                             sku, amount, count, pack, rules["max"])
                    count = rules["max"]
        products.append({"id": int(sku), "quantity": count})
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
    """Магазины Ленты рядом с адресом: id, aliasId, name, address, shopType, distance.

    Нужна как ПРОВЕРКА адреса на экране: нашла Лента рядом свои магазины — адрес
    понят. Выбирать из списка точку для цен не нужно и вредно: хаб доставки Лента
    называет сама (suggested.delivery, см. delivery_hub), и он бывает не ближайшим —
    у Ходынского бульвара ближайший ТК1537 стоит в 194 м, а хаб ТК291 в 11 км.
    Список и код читаются из одного кэшированного ответа: адрес разрешается один раз.
    """
    hubs = resolve_address(address).get("hubs") or []
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
            price=_number(item.get("price")) or None,   # 0 у Ленты — «в этой точке не продаётся», а не цена
            unit=_unit(name),
            url=item.get("url") or f"{SITE_URL}/product/{item.get('slug', '')}-{sku}/",
        )

    def _search(self, query: str, limit: int) -> list[Candidate]:
        where = _where(self.location)
        if not where:
            log.warning("%s: не задан адрес клиента и нет запасного в config.yaml — "
                        "без адреса цены спрашивать нечего", self.code)
            return self._fallback_search(query, limit)
        # where — код хаба (или адрес, если хаб не добыт), поэтому и ключ кэша по нему:
        # два клиента с одним хабом делят кэш, а смена адреса чужой кэш не подхватит.
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
