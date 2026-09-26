"""Коннекторы поверх MCP: ВкусВилл и Лента. Сеть не трогаем — подменяем call_tool.

Здесь проверяется не «работает ли сервер магазина» (это не наше дело и это меняется),
а наш разбор его ответов: что мы считаем наличием, что ценой за килограмм, и что
делаем, когда магазин говорит «такого у нас нет».
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config  # noqa: E402
from app.connectors import cache, get_connector, lenta, mcp_client, stub, vkusvill  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    cache.cache_clear()
    cache.reset_throttle()
    stub.reload_rows()
    yield
    cache.cache_clear()
    cache.reset_throttle()


def fake_tool(answers: dict):
    """Подменяет call_tool: имя инструмента -> готовый ответ (или функция от аргументов)."""
    def call(url, store_code, tool, arguments, cache_key=None):
        value = answers.get(tool)
        return value(arguments) if callable(value) else value
    return call


# ---------- транспорт ----------
def test_mcp_reads_both_plain_json_and_sse_frames():
    """Сервер вправе ответить и голым JSON, и кадрами SSE — понимаем оба."""
    assert mcp_client._decode('{"jsonrpc":"2.0","id":1,"result":{"a":1}}')["result"] == {"a": 1}
    sse = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"a":2}}\n\n'
    assert mcp_client._decode(sse)["result"] == {"a": 2}
    assert mcp_client._decode("не json") is None


def test_mcp_ok_payload_rejects_failed_answers():
    assert mcp_client.ok_payload({"ok": True, "data": {"x": 1}}) == {"x": 1}
    assert mcp_client.ok_payload({"ok": False, "error": "внутренняя ошибка"}) is None
    assert mcp_client.ok_payload("строка") is None


# ---------- ВкусВилл ----------
VV_SEARCH = {"ok": True, "data": {"items": [
    {"id": 609, "xml_id": 609, "name": "Огурцы короткоплодные",
     "price": {"current": 215, "currency": "RUB"}, "unit": "кг"},
    {"id": 28740, "xml_id": 28740, "name": 'Огурцы "Корнишоны", 300&nbsp;г',
     "price": {"current": 209, "old": 240, "discount_percent": 13}, "unit": "шт"},
]}}


def test_vkusvill_search_unescapes_names_and_maps_units(monkeypatch):
    monkeypatch.setattr(mcp_client, "call_tool", fake_tool({"vkusvill_products_search": VV_SEARCH}))
    found = get_connector("vkusvill").search("огурцы", limit=5)

    assert [c.sku for c in found] == ["609", "28740"]
    assert found[0].unit == "kg" and found[1].unit == "pcs"
    assert "&nbsp;" not in found[1].name and "300 г" in found[1].name


def test_vkusvill_weight_goods_get_price_per_kg(monkeypatch):
    answer = {"ok": True, "data": {"id": 609, "name": "Огурцы короткоплодные",
                                   "price": {"current": 215}, "unit": "кг"}}
    monkeypatch.setattr(mcp_client, "call_tool", fake_tool({"vkusvill_product_details": answer}))
    snap = get_connector("vkusvill").get_prices(["609"])[0]

    assert snap.price == 215.0 and snap.price_per_kg == 215.0


def test_vkusvill_does_not_pretend_to_know_stock(monkeypatch):
    """Остатков ВкусВилл не отдаёт. Значит и мы про наличие ничего не выдумываем."""
    answer = {"ok": True, "data": {"id": 1, "name": "Молоко", "price": {"current": 100}, "unit": "шт"}}
    monkeypatch.setattr(mcp_client, "call_tool", fake_tool({"vkusvill_product_details": answer}))
    assert get_connector("vkusvill").get_prices(["1"])[0].in_stock is True


def test_vkusvill_cart_link_respects_limit_of_twenty(monkeypatch):
    """Ограничение объявил сам ВкусВилл: больше двадцати позиций в одну ссылку не кладём."""
    sent = {}

    def call(url, store_code, tool, arguments, cache_key=None):
        sent.update(arguments)
        return {"ok": True, "data": {"link": "https://vkusvill.ru/?share_basket=1"}}

    monkeypatch.setattr(mcp_client, "call_tool", call)
    link = vkusvill.cart_link([(i, 1) for i in range(1, 31)])

    assert link == "https://vkusvill.ru/?share_basket=1"
    assert len(sent["products"]) == vkusvill.CART_LIMIT == 20


def test_vkusvill_cart_link_clamps_quantity_into_allowed_range(monkeypatch):
    sent = {}

    def call(url, store_code, tool, arguments, cache_key=None):
        sent.update(arguments)
        return {"ok": True, "data": {"link": "https://vkusvill.ru/?share_basket=2"}}

    monkeypatch.setattr(mcp_client, "call_tool", call)
    vkusvill.cart_link([(1, 0.0), (2, 999.0), (3, 0.4)])

    assert [p["q"] for p in sent["products"]] == [vkusvill.MIN_QTY, vkusvill.MAX_QTY, 0.4]


def test_vkusvill_cart_link_is_never_cached(monkeypatch):
    """Каждый вызов создаёт новую ссылку — кэшировать её нельзя."""
    seen = []

    def call(url, store_code, tool, arguments, cache_key=None):
        seen.append(cache_key)
        return {"ok": True, "data": {"link": "https://vkusvill.ru/?share_basket=3"}}

    monkeypatch.setattr(mcp_client, "call_tool", call)
    vkusvill.cart_link([(1, 1)])
    assert seen == [None]


def test_vkusvill_cart_link_survives_refusal(monkeypatch):
    monkeypatch.setattr(mcp_client, "call_tool", fake_tool({"vkusvill_cart_link_create": None}))
    assert vkusvill.cart_link([(1, 1)]) is None


# ---------- Лента ----------
@pytest.fixture
def lenta_address(monkeypatch):
    real = config.get
    monkeypatch.setattr(config, "get", lambda key, default=None: (
        "Москва, Ходынский бульвар 4" if key == "connectors.lenta_address"
        else None if key == "connectors.lenta_store_id" else real(key, default)))


def _details(price, stock, name="Молоко пастеризованное 2,5%, 930мл"):
    return {"ok": True, "data": {"storeId": 291, "item": {
        "id": 80424, "name": name, "price": price, "priceRegular": 124.99, "stock": stock}}}


def test_lenta_reads_stock_as_real_availability(monkeypatch, lenta_address):
    monkeypatch.setattr(mcp_client, "call_tool", fake_tool({"storefront_product_details": _details(90.99, 119)}))
    snap = get_connector("lenta").get_prices(["80424"])[0]

    assert snap.price == 90.99 and snap.in_stock is True


def test_lenta_zero_price_and_stock_means_not_sold_here(monkeypatch, lenta_address):
    """У Ленты пара «цена 0 + остаток 0» — это «в этой точке такого нет», а не сбой."""
    monkeypatch.setattr(mcp_client, "call_tool", fake_tool({"storefront_product_details": _details(0, 0)}))
    snap = get_connector("lenta").get_prices(["80424"])[0]

    assert snap.in_stock is False, "позицию, которой в точке нет, обязаны пометить"


def test_lenta_out_of_stock_is_not_offered_by_optimizer(monkeypatch, lenta_address):
    from app.models import BasketLine, Store
    from app.optimizer.optimizer import _available

    store = Store(id=9, code="lenta", name="Лента")
    line = BasketLine(product_id=1, name="Молоко", unit="pcs", qty=1.0)
    line.prices["lenta"], line.in_stock["lenta"] = 90.99, False
    assert _available(line, store) is False


def test_lenta_weight_goods_get_price_per_kg(monkeypatch, lenta_address):
    answer = _details(50.0, 5, name="Огурцы короткоплодные грунтовые, весовые")
    monkeypatch.setattr(mcp_client, "call_tool", fake_tool({"storefront_product_details": answer}))
    snap = get_connector("lenta").get_prices(["11993"])[0]

    assert snap.price_per_kg == 50.0


def test_lenta_weight_pack_price_is_not_a_kilo_price(monkeypatch, lenta_address):
    """У развесного товара Лента отдаёт цену ФАСОВКИ: 300 г сыра за 150 ₽ — 500 ₽/кг.

    Раньше цена фасовки уходила ценой килограмма, и сыр считался втрое дешевле.
    """
    answer = _details(150.0, 5, name="Сыр NATURA сливочный 45%, весовой")
    answer["data"]["item"].update({"isWeight": True, "weightGrams": 300})
    monkeypatch.setattr(mcp_client, "call_tool", fake_tool({"storefront_product_details": answer}))
    snap = get_connector("lenta").get_prices(["11993"])[0]

    assert snap.price_per_kg == 500.0


def test_lenta_without_address_does_not_ask_for_prices(monkeypatch):
    """Без адреса цена у Ленты бессмысленна: у каждой точки она своя."""
    called = []
    monkeypatch.setattr(config, "get", lambda key, default=None: None if "lenta" in key else default)
    monkeypatch.setattr(mcp_client, "call_tool", lambda *a, **k: called.append(a) or None)
    snaps = get_connector("lenta").get_prices(["lenta-nope"])

    assert called == [], "без адреса к магазину ходить незачем"
    assert snaps == [] or all(s.store_code == "lenta" for s in snaps)


def test_lenta_search_takes_prices_from_the_search_itself(monkeypatch, lenta_address):
    """Поиск Ленты отдаёт цену сразу — запись «price всегда 0» устарела 16.09.2026.

    Ответ по «молоко» на Ходынском бульваре: те же поля, что у карточки, и у всех
    десяти позиций цена больше нуля. Значит кандидату цена достаётся без второго
    хода за карточкой.
    """
    answer = {"ok": True, "data": {"storeId": 291, "channel": "lo", "page": 1, "items": [
        {"id": 671969, "name": "Молоко пастеризованное ЛЕНТА 2,5%, без змж, 900мл", "price": 91.99,
         "priceRegular": 106.99, "discountPercent": 14, "stock": 64, "package": "900мл",
         "slug": "moloko-pasterizovannoe-25-pet-bez-zmzh-rossiya-900ml",
         "url": "https://lenta.com/product/moloko-pasterizovannoe-25-pet-bez-zmzh-rossiya-900ml-671969/"},
    ]}}
    monkeypatch.setattr(mcp_client, "call_tool", fake_tool({"storefront_products_search": answer}))
    found = get_connector("lenta").search("молоко", limit=3)

    assert found[0].sku == "671969" and found[0].price == 91.99 and found[0].unit == "pcs"
    assert found[0].url == "https://lenta.com/product/moloko-pasterizovannoe-25-pet-bez-zmzh-rossiya-900ml-671969/"


def test_lenta_zero_price_in_search_is_no_price_but_candidate_stays(monkeypatch, lenta_address):
    """Ноль в цене поиска значит «в этой точке не продаётся»: цены нет, кандидат остаётся.

    Кандидат нужен ради id и единицы («весовые» значит килограммы): по нему потом
    спросят карточку в другой точке или найдут товар в резервном CSV.
    """
    answer = {"ok": True, "data": {"storeId": 291, "items": [
        {"id": 11993, "name": "Огурцы короткоплодные грунтовые, весовые", "price": 0, "stock": 0},
    ]}}
    monkeypatch.setattr(mcp_client, "call_tool", fake_tool({"storefront_products_search": answer}))
    found = get_connector("lenta").search("огурцы", limit=3)

    assert found[0].sku == "11993" and found[0].price is None and found[0].unit == "kg"


def test_lenta_resolves_address_once_and_asks_by_hub_code(monkeypatch, lenta_address):
    """Адрес разрешается в хаб доставки, а поиск и его кэш живут по коду хаба.

    Проверено 16.09.2026: по адресу «Москва, Ходынский бульвар 4» resolve_store
    называет хабом доставки ТК291 (aliasId 291), и поиск по storeId 291 отвечает
    тем же, что по адресу. Ключ кэша поиска строится по коду: два написания одного
    адреса и два соседа с одним хабом делят один кэш.
    """
    seen: list = []

    def call(url, store_code, tool, arguments, cache_key=None):
        seen.append((tool, arguments, cache_key))
        if tool == "storefront_resolve_store":
            return {"ok": True, "data": {
                "suggested": {"delivery": {"id": 62, "aliasId": 291, "name": "ТК291", "shopType": "HM"}},
                "hubs": [{"id": 3272, "aliasId": 1537, "name": "ТК1537", "shopType": "DY", "distance": 194}]}}
        if tool == "storefront_products_search":
            return {"ok": True, "data": {"storeId": 291, "items": [
                {"id": 671969, "name": "Молоко пастеризованное ЛЕНТА 2,5%, 900мл", "price": 91.99, "stock": 64}]}}
        return None

    monkeypatch.setattr(mcp_client, "call_tool", call)
    get_connector("lenta").search("молоко", limit=3)

    resolve, search = seen
    assert resolve[0] == "storefront_resolve_store" and resolve[1] == {"address": "Москва, Ходынский бульвар 4"}
    assert search[0] == "storefront_products_search"
    assert search[1] == {"query": "молоко", "page": 1, "storeId": 291, "channel": "lo"}
    assert "291" in search[2] and "Ходынский" not in search[2], "кэш поиска должен строиться по коду хаба"


def test_lenta_cart_link_sends_items_in_lentas_own_shape(monkeypatch):
    """У Ленты свои имена полей: items/id/quantity, а не products/xml_id/q ВкусВилла."""
    sent = {}

    def call(url, store_code, tool, arguments, cache_key=None):
        sent.update({"tool": tool, "args": arguments, "cache": cache_key})
        return {"ok": True, "data": {"shareId": "d265f624", "link": "https://lenta.com/basket/?share_id=d265f624"}}

    monkeypatch.setattr(mcp_client, "call_tool", call)
    link = lenta.cart_link([(80424, 2)])

    assert link == "https://lenta.com/basket/?share_id=d265f624"
    assert sent["tool"] == "storefront_cart_link_create"
    assert sent["args"] == {"items": [{"id": 80424, "quantity": 2}]}
    assert sent["cache"] is None, "каждый вызов создаёт новую ссылку — кэшировать нельзя"


def test_lenta_cart_link_rounds_fractional_quantity_up(monkeypatch):
    """Лента принимает только целое. 0,4 кг сыра — это одна упаковка, а не ноль."""
    sent = {}

    def call(url, store_code, tool, arguments, cache_key=None):
        sent.update(arguments)
        return {"ok": True, "data": {"link": "https://lenta.com/basket/?share_id=x"}}

    monkeypatch.setattr(mcp_client, "call_tool", call)
    lenta.cart_link([(1, 0.4), (2, 1.2), (3, 0.0), (4, 3)])

    assert [p["quantity"] for p in sent["items"]] == [1, 2, 1, 3]


def test_lenta_cart_link_respects_limit_of_hundred(monkeypatch):
    sent = {}

    def call(url, store_code, tool, arguments, cache_key=None):
        sent.update(arguments)
        return {"ok": True, "data": {"link": "https://lenta.com/basket/?share_id=y"}}

    monkeypatch.setattr(mcp_client, "call_tool", call)
    lenta.cart_link([(i, 1) for i in range(1, 140)])

    assert len(sent["items"]) == lenta.CART_LIMIT == 100


def test_lenta_cart_link_survives_refusal(monkeypatch):
    monkeypatch.setattr(mcp_client, "call_tool", fake_tool({"storefront_cart_link_create": None}))
    assert lenta.cart_link([(1, 1)]) is None


# ---------- сервис ----------
def test_service_offers_cart_link_only_where_store_supports_it():
    from app import service

    assert service.cart_link("magnit", []) is None
    assert service.cart_link("dixy", []) is None
    assert set(service.CART_LINK_STORES) == {"vkusvill", "lenta"}


def test_service_builds_lenta_link_through_lentas_own_connector(monkeypatch):
    """Магазинов со ссылкой стало два — проверяем, что сервис зовёт коннектор нужного."""
    from app import repo, service

    called = {}

    def fake_link(items, location=None):
        # Вторым аргументом едет точка человека: без неё Лента не отдаёт карточку,
        # а без карточки неизвестен размер фасовки развесного товара.
        called["items"] = items
        called["location"] = location
        return "https://lenta.com/x"

    from app import location
    from app.models import Store

    # База здесь не поднимается: магазин и место клиента подставлены, как и
    # сопоставление. Без этого тест зависел от того, какой тест шёл перед ним.
    monkeypatch.setattr(repo, "get_store", lambda code: Store(id=1, code=code, name=code))
    monkeypatch.setattr(location, "for_store", lambda code: None)
    monkeypatch.setattr(repo, "confirmed_mapping", lambda product_id, store_id: {"sku": "80424"})
    monkeypatch.setattr(lenta, "cart_link", fake_link)

    class Line:
        product_id, qty = 1, 2

    assert service.cart_link("lenta", [Line()]) == "https://lenta.com/x"
    # Третьим элементом едет единица измерения из сопоставления. Без неё Лента
    # читала бы развесное количество как ГРАММЫ: 0,7 кг сыра уезжали как «1»,
    # то есть один грамм (описание storefront_cart_link_create, 19.09.2026).
    assert called["items"] == [(80424, 2.0, None)]


# ---------- магазины без каталога: цены из чеков ----------
@pytest.fixture
def receipts(tmp_path, monkeypatch):
    """Два чека Пятёрочки в разные дни: свежий должен победить старый."""
    from app import repo
    from app.db import init_db
    from app.models import Product

    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "hist.db"))
    init_db()
    store = repo.get_store("pyaterochka")
    milk = repo.upsert_product(Product(None, "Молоко 1 л", unit="pcs"))
    apples = repo.upsert_product(Product(None, "Яблоки", unit="kg"))
    repo.add_history_row("2026-07-10", store.id, milk, "Молоко", 1, 79.90, 79.90)
    repo.add_history_row("2026-09-05", store.id, milk, "Молоко", 2, 84.50, 169.00)
    repo.add_history_row("2026-09-05", store.id, apples, "Яблоки", 1.5, 150.0, 225.0)
    return {"milk": milk, "apples": apples}


def test_history_connector_takes_the_freshest_receipt(receipts):
    snap = get_connector("pyaterochka").get_prices([f"hist-{receipts['milk']}"])[0]

    assert snap.price == 84.50, "цена должна быть из сентябрьского чека, а не из июльского"
    assert snap.fetched_at == "2026-09-05", "дату чека обязаны донести — по ней видно, насколько цена свежая"


def test_history_connector_marks_weight_goods(receipts):
    snap = get_connector("pyaterochka").get_prices([f"hist-{receipts['apples']}"])[0]
    assert snap.price_per_kg == 150.0


def test_history_connector_finds_by_name(receipts):
    found = get_connector("pyaterochka").search("молоко", limit=3)
    assert found and found[0].sku == f"hist-{receipts['milk']}"
    assert found[0].price == 84.50


def test_history_connector_without_receipts_falls_back(tmp_path, monkeypatch):
    """Пока чеков нет, магазин не выдумывает цены, а честно уходит в справочник."""
    from app.db import init_db

    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "empty.db"))
    init_db()
    assert get_connector("dixy").get_prices(["hist-404"]) == []


# ---------- развесное у Ленты считается фасовками ----------
#
# САМАЯ ДОРОГАЯ ИЗ ТИХИХ ОШИБОК, НАЙДЕННЫХ 19.09.2026, И ПОЧИНЕНА ОНА НЕ С ПЕРВОГО
# РАЗА. Описание инструмента у Ленты говорит «для развесных quantity — целые
# граммы»; по нему и была написана первая правка. Замер на семи настоящих товарах
# показал обратное: saleLimit у картофеля {min 1, max 15, step 1} при weightGrams
# 1000 — в граммах это «не больше пятнадцати граммов картофеля», бессмыслица, а в
# фасовках всё сходится. Значит quantity — число фасовок по weightGrams, и первая
# правка отправила бы 0,7 кг картофеля как СЕМЬСОТ фасовок.
#
# Исходная же ошибка была в другую сторону: округление до единицы отдавало одну
# фасовку независимо от её размера — 0,7 кг сыра превращались в 500 г.

def test_lenta_counts_weighted_goods_in_packs_of_its_own_size(monkeypatch):
    """0,7 кг сыра при фасовке 500 г — это две фасовки, а не одна и не 700."""
    sent = {}

    def tool(url, code, name, args, **kw):
        if name == "storefront_product_details":
            return {"item": {"isWeight": True, "weightGrams": 500,
                             "saleLimit": {"minGrams": 1, "maxGrams": 10, "stepGrams": 1}}}
        sent["items"] = args["items"]
        return {"link": "https://lenta.com/x"}

    monkeypatch.setattr(mcp_client, "call_tool", tool)
    monkeypatch.setattr(mcp_client, "ok_payload", lambda a: a)
    monkeypatch.setattr(lenta, "_where", lambda loc=None: {"storeId": 291})

    lenta.cart_link([(600223, 0.7, "kg")])

    assert sent["items"] == [{"id": 600223, "quantity": 2}]


def test_a_kilogram_pack_takes_one_pack_per_kilogram(monkeypatch):
    """Картофель фасуется по килограмму: 2 кг — две фасовки, а не две тысячи."""
    sent = {}

    def tool(url, code, name, args, **kw):
        if name == "storefront_product_details":
            return {"item": {"isWeight": True, "weightGrams": 1000,
                             "saleLimit": {"minGrams": 1, "maxGrams": 15, "stepGrams": 1}}}
        sent["items"] = args["items"]
        return {"link": "https://lenta.com/x"}

    monkeypatch.setattr(mcp_client, "call_tool", tool)
    monkeypatch.setattr(mcp_client, "ok_payload", lambda a: a)
    monkeypatch.setattr(lenta, "_where", lambda loc=None: {"storeId": 291})

    lenta.cart_link([(300886, 2.0, "kg")])

    assert sent["items"] == [{"id": 300886, "quantity": 2}]


def test_the_ceiling_of_the_network_is_respected(monkeypatch):
    """Выше потолка сети не просим: она всё равно не примет."""
    sent = {}

    def tool(url, code, name, args, **kw):
        if name == "storefront_product_details":
            return {"item": {"isWeight": True, "weightGrams": 300,
                             "saleLimit": {"minGrams": 1, "maxGrams": 1, "stepGrams": 1}}}
        sent["items"] = args["items"]
        return {"link": "https://lenta.com/x"}

    monkeypatch.setattr(mcp_client, "call_tool", tool)
    monkeypatch.setattr(mcp_client, "ok_payload", lambda a: a)
    monkeypatch.setattr(lenta, "_where", lambda loc=None: {"storeId": 291})

    lenta.cart_link([(720097, 2.0, "kg")])

    assert sent["items"] == [{"id": 720097, "quantity": 1}], "просили 7 фасовок, потолок 1"


def test_a_piece_good_is_still_counted_in_pieces(monkeypatch):
    """Штучное осталось штучным: правило про фасовки не должно расползтись на всё."""
    sent = {}

    def tool(url, code, name, args, **kw):
        if name == "storefront_product_details":
            raise AssertionError("у штучного товара карточку не спрашивают")
        sent["items"] = args["items"]
        return {"link": "https://lenta.com/x"}

    monkeypatch.setattr(mcp_client, "call_tool", tool)
    monkeypatch.setattr(mcp_client, "ok_payload", lambda a: a)

    lenta.cart_link([(80424, 2, "pcs")])

    assert sent["items"] == [{"id": 80424, "quantity": 2}]


def test_a_good_the_network_calls_piecewise_is_not_repacked(monkeypatch):
    """Наша единица сказала «кг», а сеть говорит isWeight:false — верим СЕТИ.

    Наш `_unit` выводит вес из слова «весов» в названии, и это догадка.
    """
    sent = {}

    def tool(url, code, name, args, **kw):
        if name == "storefront_product_details":
            return {"item": {"isWeight": False}}
        sent["items"] = args["items"]
        return {"link": "https://lenta.com/x"}

    monkeypatch.setattr(mcp_client, "call_tool", tool)
    monkeypatch.setattr(mcp_client, "ok_payload", lambda a: a)
    monkeypatch.setattr(lenta, "_where", lambda loc=None: {"storeId": 291})

    lenta.cart_link([(1, 0.7, "kg")])

    assert sent["items"] == [{"id": 1, "quantity": 1}]


def test_when_the_card_does_not_come_nothing_is_invented(monkeypatch):
    """Карточка не пришла — считаем как раньше, по одной фасовке на килограмм.

    Выдумать размер фасовки нельзя: у картофеля он 1000 г, у сыра 300. Ошибиться
    в НЁМ — значит ошибиться в количестве в разы, а прежнее поведение хотя бы
    предсказуемо и уже знакомо человеку.
    """
    sent = {}

    def tool(url, code, name, args, **kw):
        if name == "storefront_product_details":
            raise RuntimeError("сеть не ответила")
        sent["items"] = args["items"]
        return {"link": "https://lenta.com/x"}

    monkeypatch.setattr(mcp_client, "call_tool", tool)
    monkeypatch.setattr(mcp_client, "ok_payload", lambda a: a)
    monkeypatch.setattr(lenta, "_where", lambda loc=None: {"storeId": 291})

    lenta.cart_link([(1, 0.7, "kg")])

    assert sent["items"] == [{"id": 1, "quantity": 1}]


def test_vkusvill_takes_the_unit_without_choking_on_it(monkeypatch):
    """ВкусВиллу единица не нужна, но принять её он обязан — иначе ссылки не будет."""
    sent = {}

    def tool(url, code, name, args, **kw):
        sent["products"] = args["products"]
        return {"link": "https://vkusvill.ru/x"}

    monkeypatch.setattr(mcp_client, "call_tool", tool)
    monkeypatch.setattr(mcp_client, "ok_payload", lambda a: a)

    vkusvill.cart_link([(52, 0.7, "kg")])

    assert sent["products"] == [{"xml_id": 52, "q": 0.7}], "дробное ВкусВилл берёт как есть"
