"""Тесты слоя коннекторов цен. Сеть не трогаем: внутренний API подменяем monkeypatch."""
from __future__ import annotations

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
from app.models import Candidate, PriceSnapshot


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

    У Магнита это разбор HTML, у ВкусВилла и Ленты — вызов MCP: глушим оба пути.
    """
    from app.connectors import mcp_client

    monkeypatch.setattr(MagnitConnector, "_api_get", lambda self, params: None)
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
    def boom(self, url, params=None):
        raise RuntimeError("сайт магазина отвалился")

    monkeypatch.setattr(MagnitConnector, "_get_html", boom)
    conn = get_connector("magnit")
    found = conn.search("страчателла")
    assert found, "падение API не должно оставлять расчёт без цен"
    assert found[0].sku.startswith("magnit-")
    assert conn.get_prices([found[0].sku])[0].price > 0


# ---------- кэш ----------
def test_cache_prevents_second_network_call(monkeypatch):
    calls: list[dict] = []

    def fake_page(self, url, params=None):
        calls.append(params or {})
        return (
            '<article class="unit-catalog-product-preview">'
            '<a title="Сыр Страчателла 200 г" href="/product/42-syr-strachatella">'
            '<span class="pl-text unit-catalog-product-preview-prices__regular">'
            '<span>199&#8202;₽</span></span></a></article></main>'
        )

    monkeypatch.setattr(MagnitConnector, "_get_html", fake_page)
    conn = get_connector("magnit")
    first = conn.search("страчателла")
    second = conn.search("страчателла")

    assert len(calls) == 1, "второй поиск обязан прийти из файлового кэша"
    assert first[0].sku == second[0].sku == "42"
    assert first[0].price == 199.0


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


# ---------- магазин, регион и наличие ----------
def test_magnit_marks_absent_product_instead_of_substituting_price(monkeypatch):
    """404 от Магнита — это «в этом магазине такого не продают», а не сбой разбора.

    Раньше на это место молча подставлялась цена из data/fallback_prices.csv, и человек
    получал корзину, которую не смог бы заказать. Теперь позиция помечается отсутствующей.
    """
    monkeypatch.setattr(MagnitConnector, "_get_html", lambda self, url, params=None: (None, 404))
    conn = get_connector("magnit")
    snaps = conn.get_prices(["1000013732"])

    assert len(snaps) == 1
    assert snaps[0].in_stock is False, "товар, которого нет в магазине, обязан быть помечен"


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
