"""Тесты слоя коннекторов цен. Сеть не трогаем: внутренний API подменяем monkeypatch."""
from __future__ import annotations

import os
import time

import pytest

from app import config
from app.connectors import (
    ConnectorError,
    HistoryConnector,
    LentaConnector,
    MagnitConnector,
    StubConnector,
    VkusvillConnector,
    get_connector,
)
from app.connectors import cache, stub
from app.models import Candidate, Location, PriceSnapshot


@pytest.fixture(autouse=True)
def clean_state():
    cache.cache_clear()
    cache.reset_throttle()
    stub.reload_rows()
    yield
    cache.cache_clear()
    cache.reset_throttle()


@pytest.fixture
def offline(monkeypatch):
    """Магазин «не отвечает» — коннектор обязан уйти в fallback.

    У Магнита путей к сети два: JSON-шлюз magnit.ru/webgate и разбор страницы,
    оставшийся запасным. Глушить надо оба, иначе тест «сети нет» пойдёт в сеть.
    У ВкусВилла и Ленты путь один — вызов MCP.
    """
    from app.connectors import mcp_client

    monkeypatch.setattr(MagnitConnector, "_gateway",
                        lambda self, url, body=None, params=None: (None, 0))
    monkeypatch.setattr(MagnitConnector, "_get_html", lambda self, url, params=None: (None, 0))
    monkeypatch.setattr(mcp_client, "call_tool", lambda *a, **k: None)


# ---------- реестр ----------
def test_get_connector_returns_expected_classes():
    assert isinstance(get_connector("magnit"), MagnitConnector)
    assert isinstance(get_connector("vkusvill"), VkusvillConnector)
    assert isinstance(get_connector("lenta"), LentaConnector)
    assert isinstance(get_connector("stub"), StubConnector)

    # У Пятёрочки и Дикси каталог закрыт — цены берём из собственных чеков
    pyaterochka = get_connector("pyaterochka")
    assert isinstance(pyaterochka, HistoryConnector)
    assert pyaterochka.code == "pyaterochka"
    assert isinstance(get_connector("dixy"), HistoryConnector)
    assert get_connector("magnit").code == "magnit"


def test_get_connector_unknown_store_raises():
    with pytest.raises(ConnectorError):
        get_connector("perekrestok")


# ---------- поиск ----------
def test_stub_search_finds_strachatella():
    conn = get_connector("stub")
    found = conn.search("Страчателла")
    assert found, "заглушка обязана найти Страчателлу в data/fallback_prices.csv"
    assert isinstance(found[0], Candidate)
    assert "страчателла" in found[0].name.lower()
    assert 0 < found[0].score <= 1.0


def test_stub_search_is_case_insensitive_and_respects_limit():
    conn = get_connector("stub")
    assert conn.search("СТРАЧАТЕЛЛА")[0].sku == conn.search("страчателла")[0].sku
    assert len(conn.search("пюре детское", limit=2)) <= 2


# ---------- цены ----------
def test_get_prices_returns_positive_price(offline):
    conn = get_connector("magnit")
    skus = [c.sku for c in conn.search("страчателла")]
    assert skus
    snapshots = conn.get_prices(skus)
    assert snapshots
    snap = snapshots[0]
    assert isinstance(snap, PriceSnapshot)
    assert snap.store_code == "magnit"
    assert snap.price > 0
    assert snap.fetched_at


def test_weight_goods_have_price_per_kg():
    conn = get_connector("vkusvill")
    snapshots = conn.get_prices(["vkusvill-sliva-kg"])
    assert snapshots and snapshots[0].price_per_kg and snapshots[0].price_per_kg > 0


def test_prices_differ_between_stores(offline):
    """Оптимизатору нужно, чтобы магазины реально расходились в ценах."""
    magnit = get_connector("magnit").get_prices(["magnit-strachatella-200"])[0].price
    vkusvill = get_connector("vkusvill").get_prices(["vkusvill-strachatella-200"])[0].price
    assert magnit != vkusvill


def test_unknown_sku_is_skipped_not_raised():
    assert get_connector("magnit").get_prices(["нет-такого-sku"]) == []


# ---------- падение сети ----------
def test_network_failure_falls_back_to_csv(monkeypatch):
    def boom(self, url, params=None, body=None):
        raise RuntimeError("сайт магазина отвалился")

    monkeypatch.setattr(MagnitConnector, "_gateway", boom)
    monkeypatch.setattr(MagnitConnector, "_get_html", boom)
    conn = get_connector("magnit")
    found = conn.search("страчателла")
    assert found, "падение API не должно оставлять расчёт без цен"
    assert found[0].sku.startswith("magnit-")
    assert conn.get_prices([found[0].sku])[0].price > 0


# ---------- кэш ----------
def test_cache_prevents_second_network_call(monkeypatch):
    calls: list[str] = []

    def fake_gateway(self, url, body=None, params=None):
        calls.append(url)
        return {"items": [{"id": "42", "name": "Сыр Страчателла 200 г", "price": 19900,
                           "seoCode": "syr-strachatella"}]}, 200

    monkeypatch.setattr(MagnitConnector, "_gateway", fake_gateway)
    conn = get_connector("magnit")
    first = conn.search("страчателла")
    second = conn.search("страчателла")

    assert len(calls) == 1, "второй поиск обязан прийти из файлового кэша"
    assert first[0].sku == second[0].sku == "42"
    assert first[0].price == 199.0


def test_cache_key_separates_stores(monkeypatch):
    """Цены соседнего города не должны прийти из кэша под видом своих.

    Ловушка тихая: чужая цена ничем не отличается от настоящей, и заметить подмену
    в интерфейсе нельзя. Поэтому магазин обязан входить в ключ кэша.
    """
    seen: list[str] = []

    def fake_gateway(self, url, body=None, params=None):
        store = (body or {}).get("storeCode")
        seen.append(store)
        price = 8499 if store == "992301" else 8999
        return {"items": [{"id": "1899800733", "name": "Молоко Простоквашино 930мл",
                           "price": price}]}, 200

    monkeypatch.setattr(MagnitConnector, "_gateway", fake_gateway)
    krasnodar = get_connector("magnit", Location(store_id="992301")).search("молоко")
    moscow = get_connector("magnit", Location(store_id="264856")).search("молоко")

    assert seen == ["992301", "264856"], "второй магазин обязан сходить за своей ценой"
    assert krasnodar[0].price == 84.99
    assert moscow[0].price == 89.99


def test_cache_get_set_roundtrip():
    assert cache.cache_get("magnit", "k") is None
    cache.cache_set("magnit", "k", {"a": 1})
    assert cache.cache_get("magnit", "k") == {"a": 1}
    cache.cache_set("magnit", "none", None)          # неудачный ответ не кэшируем
    assert cache.cache_get("magnit", "none") is None


# ---------- троттлинг ----------
def test_throttle_pauses_but_does_not_break_call():
    interval = 1.0 / float(config.get("connectors.rate_limit_rps", 1.0))
    cache.throttle("magnit")
    start = time.monotonic()
    cache.throttle("magnit")
    elapsed = time.monotonic() - start
    assert elapsed >= interval * 0.8, "второй запрос к магазину обязан подождать свой слот"

    # троттлинг соседнего магазина не задерживает
    start = time.monotonic()
    cache.throttle("vkusvill")
    assert time.monotonic() - start < interval * 0.5

    assert get_connector("stub").search("хлебцы"), "после паузы вызов живой"


def test_waiting_for_one_store_does_not_hold_another():
    """Пауза Магнита не держит ВкусВилл: ждут вне общего замка.

    Раньше time.sleep стоял под замком, и живой поиск по шести сетям в потоках
    складывал задержки всех сетей в одну очередь.
    """
    import threading

    interval = 1.0 / float(config.get("connectors.rate_limit_rps", 1.0))
    cache.throttle("magnit")
    waiting = threading.Thread(target=cache.throttle, args=("magnit",))
    waiting.start()
    time.sleep(0.05)                       # второй запрос Магнита уже ждёт свой слот
    start = time.monotonic()
    cache.throttle("vkusvill")
    assert time.monotonic() - start < interval * 0.5
    waiting.join()


def test_the_pace_is_shared_between_processes(monkeypatch):
    """Слот сети лежит в файле: другой процесс увидит, что сеть только что спрашивали."""
    pytest.importorskip("fcntl")
    interval = 1.0 / float(config.get("connectors.rate_limit_rps", 1.0))
    assert cache.reserve("lenta", interval) == 0.0
    cache._last_request.clear()            # как будто спрашивает другой процесс
    assert cache.reserve("lenta", interval) > interval * 0.5


def test_a_slot_from_the_future_does_not_put_a_request_to_sleep():
    """Сбитые часы (метка на час вперёд) — не очередь: ждать час запрос не должен."""
    interval = 1.0 / float(config.get("connectors.rate_limit_rps", 1.0))
    cache._last_request["magnit"] = time.time() + 3600
    assert cache.reserve("magnit", interval) < interval + 0.1


def test_stale_cache_files_are_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    cache.cache_set("magnit", "old", {"a": 1})
    cache.cache_set("magnit", "new", {"b": 2})
    old = cache._path("magnit", "old")
    os.utime(old, (time.time() - 10 * 86400, time.time() - 10 * 86400))
    assert cache.prune() == 1
    assert not os.path.exists(old) and cache.cache_get("magnit", "new") == {"b": 2}


def test_fallback_price_is_marked_as_reference():
    """Справочная цена из CSV не должна выглядеть живой."""
    snaps = stub.fallback_prices("pyaterochka", ["pyaterochka-strachatella-200"]) or \
        stub.fallback_prices("pyaterochka", [stub.rows_for("pyaterochka")[0]["sku"]])
    assert snaps and all(s.source == stub.FALLBACK for s in snaps)


def test_magnit_search_card_takes_the_sale_price_first():
    """Зачёркнутая обычная цена в разметке раньше акционной — берётся акционная."""
    page = ('<article class="unit-catalog-product-preview">'
            '<a title="Молоко 930 мл" href="/product/1000-moloko">'
            '<div class="prices__regular"><span>119&#8202;₽</span></div>'
            '<div class="prices__sale"><span>89,99&#8202;₽</span></div></article></main>')
    cards = MagnitConnector()._cards(page)
    assert cards and cards[0].price == 89.99


# ---------- магазин, регион и наличие ----------
def test_magnit_marks_absent_product_instead_of_substituting_price(monkeypatch):
    """404 от страницы Магнита — «в этом магазине такого не продают», а не сбой разбора.

    Раньше на это место молча подставлялась цена из data/fallback_prices.csv, и человек
    получал корзину, которую не смог бы заказать. Теперь позиция помечается отсутствующей.
    """
    monkeypatch.setattr(MagnitConnector, "_gateway",
                        lambda self, url, body=None, params=None: (None, 0))
    monkeypatch.setattr(MagnitConnector, "_get_html", lambda self, url, params=None: (None, 404))
    conn = get_connector("magnit")
    snaps = conn.get_prices(["1000013732"])

    assert len(snaps) == 1
    assert snaps[0].in_stock is False, "товар, которого нет в магазине, обязан быть помечен"


def test_magnit_gateway_reads_price_and_stock(monkeypatch):
    """Шлюз отдаёт и цену, и остаток: наличие больше не додумывается."""
    card = {"id": "1899800733", "name": "Молоко Простоквашино 2.5% 930мл",
            "price": 8999, "quantity": 2}
    monkeypatch.setattr(MagnitConnector, "_gateway",
                        lambda self, url, body=None, params=None: (card, 200))
    snap = get_connector("magnit").get_prices(["1899800733"])[0]

    assert snap.price == 89.99
    assert snap.in_stock is True
    assert snap.name == "Молоко Простоквашино 2.5% 930мл"


def test_magnit_zero_price_and_stock_means_not_sold_here(monkeypatch):
    """Цена 0 при остатке 0 — «товар в сети есть, а в этой точке не продаётся».

    Так шлюз отвечает про молоко «Кубанский молочник» в московском магазине, тогда
    как в краснодарском оно стоит 159 ₽. Принять этот ноль за цену значило бы
    положить в корзину бесплатное молоко, которого человеку не дадут.
    """
    card = {"id": "1000169025", "name": "Молоко Кубанский молочник 1.4кг",
            "price": 0, "quantity": 0}
    monkeypatch.setattr(MagnitConnector, "_gateway",
                        lambda self, url, body=None, params=None: (card, 200))
    snaps = get_connector("magnit").get_prices(["1000169025"])

    assert len(snaps) == 1
    assert snaps[0].in_stock is False, "ноль шлюза не цена, а «здесь не продаётся»"
    assert snaps[0].name is None, "имя из такой карточки не берём — брать нечего"


def test_magnit_out_of_stock_keeps_its_price(monkeypatch):
    """Остаток 0 при живой цене — товар в магазине есть, просто кончился."""
    card = {"id": "1899800733", "name": "Молоко", "price": 8999, "quantity": 0}
    monkeypatch.setattr(MagnitConnector, "_gateway",
                        lambda self, url, body=None, params=None: (card, 200))
    snap = get_connector("magnit").get_prices(["1899800733"])[0]

    assert snap.price == 89.99
    assert snap.in_stock is False


def test_magnit_unknown_sku_answers_not_found(monkeypatch):
    """422 goods_not_found — ответ шлюза, а не обрыв связи: позиция помечается."""
    monkeypatch.setattr(MagnitConnector, "_gateway",
                        lambda self, url, body=None, params=None: (None, 422))
    snaps = get_connector("magnit").get_prices(["9999999999"])

    assert len(snaps) == 1 and snaps[0].in_stock is False


def test_magnit_absent_product_is_not_offered_by_optimizer():
    """Помеченная позиция не должна попасть в этот магазин при разбиении."""
    from app.models import BasketLine, Store
    from app.optimizer.optimizer import _available

    store = Store(id=1, code="magnit", name="Магнит", delivery_fee=0.0, free_delivery_from=0.0)
    line = BasketLine(product_id=1, name="Молоко", unit="pcs", qty=1.0)
    line.prices["magnit"] = 89.0
    line.in_stock["magnit"] = False
    assert _available(line, store) is False


def test_magnit_falls_back_only_when_store_is_unreachable(monkeypatch):
    """Обрыв связи — другое дело: тут справочная цена уместна, товар считаем доступным."""
    monkeypatch.setattr(MagnitConnector, "_get_html", lambda self, url, params=None: (None, 0))
    conn = get_connector("magnit")
    snaps = conn.get_prices(["magnit-ogurcy-450"])

    assert snaps and snaps[0].price > 0
    assert snaps[0].in_stock is True


def test_magnit_selects_store_by_cookies_not_by_query(monkeypatch):
    """Сайт слушает куки shopCode/x_shop_type/nmg_dt; параметры адреса он игнорирует."""
    monkeypatch.setattr(config, "get", lambda key, default=None: {
        "connectors.magnit_shop_code": "019652",
        "connectors.magnit_shop_type": "MM",
        "connectors.magnit_delivery": True,
    }.get(key, default))
    cookies = MagnitConnector()._shop_cookies()

    assert cookies["shopCode"] == '"019652"'
    assert cookies["x_shop_type"] == "MM"
    assert cookies["nmg_dt"] == "DELIVERY_TYPE_DELIVERY"


def test_magnit_reads_price_from_schema_org_markup():
    """Цена берётся из разметки Schema.org: она переживает перерисовку вёрстки."""
    from app.connectors.magnit import _LD_PRICE

    page = '<script type="application/ld+json">{"@type":"Product",' \
           '"offers":{"@type":"Offer","price":175.99,"priceCurrency":"RUB"}}</script>'
    assert _LD_PRICE.search(page).group(1) == "175.99"


def test_magnit_survives_cache_written_by_previous_version():
    """В кэше могли остаться записи прежнего формата — одной строкой вместо пары."""
    from app.connectors.magnit import _unpack

    assert _unpack("<html>страница</html>") == ("<html>страница</html>", 200)
    assert _unpack(("<html>", 200)) == ("<html>", 200)
    assert _unpack([None, 404]) == (None, 404)
    assert _unpack(None) == (None, 0)
