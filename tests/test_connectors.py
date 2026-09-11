"""Тесты слоя коннекторов цен. Сеть не трогаем: внутренний API подменяем monkeypatch."""
from __future__ import annotations

import time

import pytest

from app import config
from app.connectors import (
    ConnectorError,
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
    """API магазина «не отвечает» — коннектор обязан уйти в fallback."""
    monkeypatch.setattr(MagnitConnector, "_api_get", lambda self, params: None)
    monkeypatch.setattr(VkusvillConnector, "_api_get", lambda self, params: None)


# ---------- реестр ----------
def test_get_connector_returns_expected_classes():
    assert isinstance(get_connector("magnit"), MagnitConnector)
    assert isinstance(get_connector("vkusvill"), VkusvillConnector)
    assert isinstance(get_connector("stub"), StubConnector)

    pyaterochka = get_connector("pyaterochka")
    assert isinstance(pyaterochka, StubConnector)
    assert pyaterochka.code == "pyaterochka"
    assert get_connector("magnit").code == "magnit"


def test_get_connector_unknown_store_raises():
    with pytest.raises(ConnectorError):
        get_connector("lenta")


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
    def boom(self, params):
        raise RuntimeError("API магазина отвалился")

    monkeypatch.setattr(MagnitConnector, "_api_get", boom)
    conn = get_connector("magnit")
    found = conn.search("страчателла")
    assert found, "падение API не должно оставлять расчёт без цен"
    assert found[0].sku.startswith("magnit-")
    assert conn.get_prices([found[0].sku])[0].price > 0


# ---------- кэш ----------
def test_cache_prevents_second_network_call(monkeypatch):
    calls: list[dict] = []

    def fake_api(self, params):
        calls.append(params)
        return {"items": [{"id": "42", "name": "Сыр Страчателла 200 г", "price": 199.0}]}

    monkeypatch.setattr(MagnitConnector, "_api_get", fake_api)
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
