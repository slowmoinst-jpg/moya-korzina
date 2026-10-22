"""История цены: сколько товар стоил раньше и дорого ли брать сейчас.

Замер 19.10.2026: в chain_prices у каждого (сеть, артикул, точка) была ровно ОДНА строка
при 18–24 ночных снимках — прежняя цена затиралась. Ответить «сейчас дороже обычного,
подожди» было нечем.
"""
from __future__ import annotations

import pytest

from app.catalog import store
from app.catalog.model import ChainProduct


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


def _write(chain: str, sku: str, price: float, point: str = "1") -> None:
    store.upsert_products(chain, [ChainProduct(sku=sku, name=f"Товар {sku}", price=price,
                                               point=point)])


def test_a_price_change_is_remembered(base):
    _write("magnit", "100", 199.0)
    _write("magnit", "100", 249.0)

    history = base.price_history("magnit", "100")

    assert [row["price"] for row in history] == [249.0, 199.0], "новые первыми"


def test_the_same_price_twice_is_not_written_twice(base):
    """Строка на ИЗМЕНЕНИЕ, а не на обход: иначе память растёт с каждой ночью."""
    _write("magnit", "100", 199.0)
    _write("magnit", "100", 199.0)
    _write("magnit", "100", 199.0)

    assert len(base.price_history("magnit", "100")) == 1


def test_the_usual_price_is_the_median_not_the_last(base):
    """Акция на день не должна становиться «обычной ценой» — считаем по медиане."""
    for price in (100.0, 100.0, 100.0, 200.0, 50.0):
        _write("lenta", "77", price)

    assert base.usual_price("lenta", "77") == 100.0


def test_no_history_means_no_answer(base):
    """Нечего сравнивать — молчим, а не выдумываем обычную цену."""
    assert base.usual_price("lenta", "нет-такого") is None


def test_history_is_per_point(base):
    """У сети цена своя в каждом магазине: история одной точки не путается с другой."""
    _write("magnit", "100", 199.0, point="A")
    _write("magnit", "100", 260.0, point="B")

    assert [row["price"] for row in base.price_history("magnit", "100", "A")] == [199.0]
    assert [row["price"] for row in base.price_history("magnit", "100", "B")] == [260.0]


def test_a_chain_without_points_keeps_history_too(base):
    """Замер 20.10.2026: история писалась только в ветке с точкой.

    За сутки в ней оказались лишь magnit (970 строк) и metro (135) — у остальных сетей
    точки нет (каталог один на страну), и «обычная цена» была бы мертва для восьми
    сетей из одиннадцати.
    """
    from app.catalog.model import ChainProduct

    for price in (199.0, 249.0):
        base.upsert_products("lenta", [ChainProduct(sku="777", name="Молоко 1 л", price=price)])

    rows = base.price_history("lenta", "777")
    assert [row["price"] for row in rows] == [249.0, 199.0], "новые первыми"
    assert base.usual_price("lenta", "777") == 249.0
    assert base.usual_trusted("lenta", "777") is False, "двух свежих точек мало"

    base.upsert_products("lenta", [ChainProduct(sku="777", name="Молоко 1 л", price=229.0)])
    assert base.usual_trusted("lenta", "777") is True, "три изменения — уже довод"


def test_the_same_price_without_points_is_not_written_twice(base):
    from app.catalog.model import ChainProduct

    for _ in range(3):
        base.upsert_products("vkusvill", [ChainProduct(sku="9", name="Сыр", price=500.0)])

    assert len(base.price_history("vkusvill", "9")) == 1
