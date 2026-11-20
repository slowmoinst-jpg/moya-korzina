"""Тесты Этапа 2 (приложение 1.19): drop в plan и data-order у формы «Заказать»."""
from __future__ import annotations

import html
import json
import re

import pytest

from app import cartrun, db, location, phonecart, repo, store_accounts, users
from app.models import Product
from app.web import create_app


PHONE = "+79991112233"


def test_phonecart_plan_drop_skus(tmp_path, monkeypatch):
    """plan() с параметром drop возвращает url и name по store_products."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))     # не трогать базу в data/
    db.init_db()
    store = repo.get_store("magnit")
    assert store is not None

    sp1 = repo.upsert_store_product(
        store.id, "drop-sku-1", "Товар с ссылкой", url="https://magnit.ru/product/drop1"
    )
    sp2 = repo.upsert_store_product(
        store.id, "drop-sku-2", "Товар без ссылки", url=None
    )

    body = {
        "store": "magnit",
        "drop": ["drop-sku-1", "drop-sku-2", "unknown-sku-999"],
    }
    res = phonecart.plan(body)

    assert res["ok"] is True
    assert "drop" in res
    drops = {item["sku"]: item for item in res["drop"]}

    assert "drop-sku-1" in drops
    assert drops["drop-sku-1"]["name"] == "Товар с ссылкой"
    assert drops["drop-sku-1"]["url"] == "https://magnit.ru/product/drop1"

    assert "drop-sku-2" in drops
    assert drops["drop-sku-2"]["name"] == "Товар без ссылки"
    assert "url" not in drops["drop-sku-2"]

    assert "unknown-sku-999" in drops
    assert drops["unknown-sku-999"]["name"] == ""
    assert "url" not in drops["unknown-sku-999"]

    # Пустой drop
    res_empty = phonecart.plan({"store": "magnit", "drop": []})
    assert res_empty["ok"] is True
    assert res_empty.get("drop") == []

    # Уборка после «Заказать» в невыбранной сети: пустой список позиций — пустой наряд,
    # а не вся корзина, и адреса карточек своего для снятия.
    res_clean = phonecart.plan({"store": "magnit", "lines": [], "drop": ["drop-sku-1"]})
    assert res_clean["lines"] == [] and res_clean["search"] == []
    assert res_clean["drop"] == [{"sku": "drop-sku-1", "name": "Товар с ссылкой",
                                  "url": "https://magnit.ru/product/drop1"}]


