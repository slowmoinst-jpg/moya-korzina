"""Очередь добора подробностей: бюджет ночи должен покупать сравнения сетей.

Замер 19.10.2026: у Ленты 7 380 строк без цены, из них 1 031 имеет родню в других сетях,
а 277 — родню С ЦЕНОЙ. Бюджет добора 6 000 карточек за ночь, и тратить его на случайные
старые строки незачем: цена строки, у которой родня уже с ценой, сразу даёт сравнение
сетей — ради этого единая база и строится.
"""
from __future__ import annotations

import pytest

from app.catalog import store


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


def _item(item_id: int, name: str) -> None:
    """Единый товар: на него ссылаются строки сетей, и без него не вставить строку."""
    with store.connect() as conn:
        conn.execute("insert into catalog_items (id, name, norm, created_at, updated_at)"
                     " values (?,?,?,?,?)",
                     (item_id, name, name.lower(), store.now(), store.now()))
        conn.commit()


def _row(chain: str, sku: str, *, name: str, brand=None, barcode=None, price=None,
         item_id=None, first_seen=None) -> None:
    with store.connect() as conn:
        conn.execute(
            "insert into chain_products (chain, sku, name, brand, barcode, price, item_id,"
            " active, first_seen, last_seen) values (?,?,?,?,?,?,?,1,?,?)",
            (chain, sku, name, brand, barcode, price, item_id,
             first_seen or store.now(), store.now()))
        conn.commit()


def test_rows_whose_item_already_has_a_priced_kin_go_first(base):
    """Лента без цены, у товара есть родня с ценой — цена этой строки даёт сравнение сразу."""
    _item(1, "Сыр Российский 200 г")
    _item(2, "Кефир 1 %")
    _item(3, "Товар без родни")
    base_chain = "perekrestok"
    _row(base_chain, "k1", name="Сыр Российский 200 г", price=199.9, item_id=1)
    _row("lenta", "a", name="Сыр Российский 200 г", item_id=1)          # родня с ценой
    _row("lenta", "b", name="Кефир 1 %", item_id=2)                     # родня без цены
    _row(base_chain, "k2", name="Кефир 1 %", item_id=2)
    _row("lenta", "c", name="Товар без родни", item_id=3)               # родни нет

    order = store.thin_skus("lenta", 10)

    assert order[0] == "a", "первой идёт строка, чья цена сразу даёт сравнение"
    assert order[1] == "b", "потом строка с роднёй без цены"
    assert order[2] == "c", "потом остальные"


def test_a_row_without_price_is_in_the_queue_even_with_brand_and_barcode(base):
    """Карточка отдаёт и цену: строка без цены нуждается в доборе, как бы полна ни была."""
    _row("lenta", "full", name="Сыр с маркой", brand="Российский", barcode="4600000000001")
    _row("lenta", "noBrand", name="Сыр без марки")

    order = store.thin_skus("lenta", 10)

    assert "full" in order, "строка без цены не должна выпадать из очереди"
    assert "noBrand" in order


def test_a_complete_row_is_not_in_the_queue(base):
    """С маркой, штрихкодом и ценой строке добор не нужен: бюджет на неё не тратим."""
    _row("lenta", "done", name="Сыр полный", brand="Российский",
         barcode="4600000000001", price=199.9)

    assert store.thin_skus("lenta", 10) == []


def test_the_limit_is_respected(base):
    for i in range(5):
        _row("lenta", f"s{i}", name=f"Товар {i}")

    assert len(store.thin_skus("lenta", 2)) == 2
