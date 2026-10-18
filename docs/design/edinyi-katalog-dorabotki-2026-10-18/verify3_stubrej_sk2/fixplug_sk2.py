"""Плагин pytest: исправление скептика в памяти — отказ снимает только настоящий артикул.

Подключение: PYTHONPATH=<эта папка> python -m pytest -p fixplug_sk2 ...
Рабочая копия не меняется: подменяется функция уже импортированного модуля.
"""
from __future__ import annotations


def pytest_configure(config):  # noqa: ARG001
    from flask import flash, session

    from app import repo
    from app.catalog.model import placeholder_sku
    from app.web.screens import links as screen

    def _do_confirm(product, store, sku: str) -> None:
        from app import matcher

        try:
            matcher.confirm(product.id, store.code, sku)
        except Exception as exc:  # noqa: BLE001
            screen.log.exception("подтверждение не прошло")
            flash(f"Не получилось связать: {exc}", "bad")
            return
        if not placeholder_sku(sku, store.code):
            repo.clear_rejects(product.id, store.id)
        screen._resync(product)
        session.pop(screen.CANDIDATES, None)
        flash(f"Готово: «{product.name}» связан с «{store.name}» по артикулу {sku}.", "ok")

    screen._do_confirm = _do_confirm
