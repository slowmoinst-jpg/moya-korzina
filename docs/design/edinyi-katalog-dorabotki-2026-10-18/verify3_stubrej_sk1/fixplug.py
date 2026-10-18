"""Подмена в памяти (pytest -p fixplug): отказ от сети снимает только подтверждение
НАСТОЯЩЕГО артикула. Код рабочей копии не меняется: подменяется links._do_confirm
копией с одной правкой — условием вокруг repo.clear_rejects."""
import pytest


@pytest.fixture(autouse=True)
def fixed_confirm(monkeypatch):
    from app import repo
    from app.catalog.model import placeholder_sku
    from app.web.screens import links

    def _do_confirm(product, store, sku):
        from flask import flash, session
        from app import matcher

        try:
            matcher.confirm(product.id, store.code, sku)
        except Exception as exc:  # noqa: BLE001
            flash(f"Не получилось связать: {exc}", "bad")
            return
        if not placeholder_sku(sku, store.code):
            repo.clear_rejects(product.id, store.id)
        links._resync(product)
        session.pop(links.CANDIDATES, None)
        flash(f"Готово: «{product.name}» связан с «{store.name}» по артикулу {sku}.", "ok")

    monkeypatch.setattr(links, "_do_confirm", _do_confirm)
    yield
