"""Что экран «Магазины» говорит о наполнении: с чем пришли строки сети.

Владелец просил проверить, что «наполнение единой базы работает». Число строк само по
себе этого не говорит: строка может прийти без цены (Перекрёсток, Впрок, Монетка), без
наличия (ВкусВилл) или без марки и штрихкода (как было у Ленты). Поэтому counts по сети
отдают и то, с чем строка пришла, — и это видно на экране «Магазины», а не только в журнале.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import store  # noqa: E402
from app.catalog.model import ChainProduct  # noqa: E402


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


def test_counts_say_what_each_row_came_with(base):
    store.upsert_products("lenta", [
        ChainProduct(sku="1", name="Горбуша натуральная, 240г", price=196.99, in_stock=True,
                     brand="PL Lenta TempSubst", barcode="14607019900708"),
        ChainProduct(sku="2", name="vino marques de riscal 750ml"),
    ])

    counts = store.chain_counts()["lenta"]

    assert counts["products"] == 2
    assert counts["priced"] == 1
    assert counts["stocked"] == 1
    assert counts["branded"] == 1
    assert counts["barcoded"] == 1


def test_an_empty_catalog_is_still_reported(base):
    """Сеть, у которой каталога нет вовсе, обязана быть видна как ноль, а не пропасть."""
    store.upsert_products("samokat", [])

    assert "samokat" not in store.chain_counts() or store.chain_counts()["samokat"]["products"] == 0
    assert store.chain_counts().get("lenta") is None
