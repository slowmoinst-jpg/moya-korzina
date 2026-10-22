"""Цену, которую видели, нельзя стереть молча — это держит триггер базы.

Замер 20.10.2026: у Самоката price_seen стоял у 1 494 строк, а цена осталась у 884 —
610 значений стёрли, не тронув отметку, и товары выпали из сравнения. Наш код так не
делает (в upsert обновление идёт через COALESCE), но в базу пишут и другие инструменты.
"""
from __future__ import annotations

import pytest

from app.catalog import store


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    with store.connect() as conn:
        conn.execute("insert into chain_products (chain, sku, name, price, price_seen, active,"
                     " first_seen, last_seen) values ('samokat','1','Товар',321.5,?,1,?,?)",
                     (store.now(), store.now(), store.now()))
        conn.commit()
    return store


def _price() -> tuple:
    with store.connect() as conn:
        row = conn.execute("select price, price_seen from chain_products"
                           " where chain='samokat' and sku='1'").fetchone()
    return row["price"], row["price_seen"]


def test_a_lone_price_erasure_is_ignored(base):
    """UPDATE price=NULL без price_seen — запись не проходит, цена остаётся."""
    with store.connect() as conn:
        conn.execute("update chain_products set price=NULL where chain='samokat' and sku='1'")
        conn.commit()

    price, seen = _price()
    assert price == 321.5, "цена, которую видели, не стёрта"
    assert seen is not None


def test_a_new_price_still_lands(base):
    with store.connect() as conn:
        conn.execute("update chain_products set price=400.0 where chain='samokat' and sku='1'")
        conn.commit()

    assert _price()[0] == 400.0


def test_a_lawful_cleanup_together_with_the_mark_still_works(base):
    """Законная чистка гасит цену и отметку ВМЕСТЕ — триггер её не держит.

    Так делает перенос цены Ленты: цена килограмма без фасовки — не цена, и отметку
    «цену видели» оставлять нельзя, иначе перенос в базу человека записал бы ложную.
    """
    with store.connect() as conn:
        conn.execute("update chain_products set price=NULL, price_seen=NULL"
                     " where chain='samokat' and sku='1'")
        conn.commit()

    assert _price() == (None, None)


def test_the_upsert_path_is_not_affected(base):
    """Обычная запись обхода: цена есть — обновилась; цены нет — прежняя осталась."""
    from app.catalog.model import ChainProduct

    store.upsert_products("samokat", [ChainProduct(sku="1", name="Товар", price=350.0)])
    assert _price()[0] == 350.0

    store.upsert_products("samokat", [ChainProduct(sku="1", name="Товар")])
    assert _price()[0] == 350.0, "источник без цены не стирает известную"
