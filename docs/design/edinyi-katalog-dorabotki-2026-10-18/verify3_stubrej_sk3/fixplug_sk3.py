"""Подмена в памяти для прогона наборов тестов: links._do_confirm снимает отказ от сети
только подтверждением НАСТОЯЩЕГО артикула (не hist-, не list-, не «<сеть>-…»).

Подключение: PYTHONPATH=<эта папка> python -m pytest -p fixplug_sk3 ...
"""
import pytest


@pytest.fixture(autouse=True)
def stub_confirm_keeps_refusal(monkeypatch):
    from app.catalog.model import placeholder_sku
    from app.web.screens import links

    def _do_confirm(product, store, sku: str) -> None:
        from app import matcher

        try:
            matcher.confirm(product.id, store.code, sku)
        except Exception as exc:  # noqa: BLE001
            links.log.exception("подтверждение не прошло")
            links.flash(f"Не получилось связать: {exc}", "bad")
            return
        if not placeholder_sku(sku, store.code):
            links.repo.clear_rejects(product.id, store.id)
        links._resync(product)
        links.session.pop(links.CANDIDATES, None)
        links.flash(f"Готово: «{product.name}» связан с «{store.name}» по артикулу {sku}.", "ok")

    monkeypatch.setattr(links, "_do_confirm", _do_confirm)
    yield
