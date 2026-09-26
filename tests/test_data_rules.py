"""Правила данных на сервере (docs/design/nazvaniya-2026-09-26.md, «Требования к данным»).

1. Цена на экране — только свежая; устаревшая не показывается и не считается.
2. Экономия — по сравнению с тем, сколько человек платил раньше (его чеки).
3. Перед оформлением цены и наличие проверяются ещё раз.
4. Заказами становятся только чеки сетей, где мы собираем заказ.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app import cartplan, chains, config, freshness, repo, service
from app.db import init_db
from app.models import Product


def _ago(**kw) -> str:
    return (datetime.now() - timedelta(**kw)).isoformat(timespec="seconds")


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "rules.db"))
    init_db()
    return tmp_path


def _mapped(store_code: str, name: str, sku: str) -> tuple[int, int]:
    store = repo.get_store(store_code)
    pid = repo.upsert_product(Product(id=None, name=name, unit="pcs"))
    sp = repo.upsert_store_product(store.id, sku, name)
    repo.confirm_mapping(pid, sp, confirmed=True)
    return pid, sp


# ---------- 1. свежесть ----------
def test_fresh_window_follows_the_setting(monkeypatch):
    monkeypatch.setattr(config, "get", lambda path, default=None: 2 if path == "prices.fresh_hours" else default)
    assert freshness.is_fresh(_ago(hours=1))
    assert not freshness.is_fresh(_ago(hours=3))


def test_unreadable_date_is_not_fresh():
    assert not freshness.is_fresh(None)
    assert not freshness.is_fresh("вчера")


def test_calculation_ignores_a_stale_price(db):
    pid, sp = _mapped("magnit", "Молоко 2,5%", "m-1")
    repo.save_price(sp, 80.0, fetched_at=_ago(days=3))
    store = repo.get_store("magnit")

    assert service._price_for_line(pid, store, 1, "pcs") == (None, False)

    repo.save_price(sp, 84.0, fetched_at=_ago(minutes=2))
    assert service._price_for_line(pid, store, 2, "pcs") == (168.0, True)


# ---------- 2. экономия по своим чекам ----------
def test_baseline_is_what_the_person_paid_before(db):
    pid, sp = _mapped("magnit", "Кефир 1%", "m-2")
    base_pid, base_sp = _mapped("pyaterochka", "Кефир 1%", "p-2")
    repo.save_price(base_sp, 70.0, fetched_at=_ago(minutes=1))
    repo.add_history_row(date="2026-09-01", store_id=repo.get_store("magnit").id, product_id=pid,
                         raw_name="Кефир 1%", qty=1, unit_price=95.0, total=95.0, receipt_key="k1")
    basket = repo.create_basket("проба")
    repo.set_basket_item(basket, pid, 2)

    assert service.baseline_total(basket) == 190.0, "база — его цена из чека, а не цена Пятёрочки"


# ---------- 3. проверка перед оформлением ----------
def test_verify_updates_price_and_drops_what_is_gone(db, monkeypatch):
    monkeypatch.setattr(cartplan, "available", lambda code: True)
    milk, milk_sp = _mapped("magnit", "Молоко", "m-10")
    eggs, eggs_sp = _mapped("magnit", "Яйца", "m-11")
    bread, bread_sp = _mapped("magnit", "Хлеб", "m-12")

    class Line:
        def __init__(self, pid, name, price):
            self.product_id, self.name, self.qty, self.price, self.store_code = pid, name, 1, price, "magnit"

    lines = [Line(milk, "Молоко", 84.0), Line(eggs, "Яйца", 119.0), Line(bread, "Хлеб", 55.0)]

    def refresh(ids, stores):   # магазин ответил: молоко подорожало, яиц нет, хлеб прежний
        repo.save_price(milk_sp, 89.0)
        repo.save_price(eggs_sp, 119.0, in_stock=False)
        repo.save_price(bread_sp, 55.0)

    plan = cartplan.build("magnit", lines)
    cartplan.verify(plan, refresh=refresh)

    assert plan.checked
    assert [ln.name for ln in plan.lines] == ["Молоко", "Хлеб"]
    assert plan.gone == ["Яйца"]
    assert plan.changes == [{"name": "Молоко", "was": 84.0, "now": 89.0}]
    assert plan.total == 144.0


def test_verify_does_not_pass_an_unconfirmed_price(db, monkeypatch):
    monkeypatch.setattr(cartplan, "available", lambda code: True)
    pid, sp = _mapped("magnit", "Сыр", "m-20")
    repo.save_price(sp, 219.0, fetched_at=_ago(days=2))

    class Line:
        product_id, name, qty, price, store_code = pid, "Сыр", 1, 219.0, "magnit"

    def refresh(ids, stores):
        raise RuntimeError("магазин не ответил")

    plan = cartplan.verify(cartplan.build("magnit", [Line()]), refresh=refresh)
    assert plan.lines == [] and plan.gone == ["Сыр"]


# ---------- 4. отсев чеков ----------
@pytest.mark.parametrize("seller, code", [
    ("Пятёрочка", "pyaterochka"),
    ('ООО "Агроторг"', "pyaterochka"),
    ('АО "ТАНДЕР"', "magnit"),
    ("ВкусВилл", "vkusvill"),
    ("ООО «Умный ритейл»", "samokat"),
    ("Перекрёсток", "perekrestok"),
    ("Лента Строй", None),
    ("Аптека «Ригла»", None),
])
def test_chain_is_recognised_by_brand_or_legal_name(seller, code):
    assert chains.resolve(seller) == code


def test_only_orderable_chains_become_orders(db):
    from app.importers.bundle import store_receipts
    from app.importers.ofd_pdf import Receipt, ReceiptRow

    def cheque(seller, day):
        return {"key": None, "receipt": Receipt(date=day, store_name=seller, total=100.0,
                                                rows=[ReceiptRow(raw_name="Товар " + seller, qty=1,
                                                                 unit_price=100.0, total=100.0)])}

    got = store_receipts([cheque('ООО "Агроторг"', "2026-09-20"),
                          cheque("Аптека «Ригла»", "2026-09-21"),
                          cheque("Перекрёсток", "2026-09-22")], source="lkdr")

    assert got["receipts"] == 1
    assert {r["store"] for r in got["other"]} == {"Аптека «Ригла»", "Перекрёсток"}
    assert len(repo.list_history()) == 1, "чеки других магазинов не пишутся в историю"
    assert len(repo.other_store_receipts()) == 2
    assert repo.pending_receipts() == [], "отложенный чек — не «осталось загрузить»"

    again = store_receipts([cheque("Аптека «Ригла»", "2026-09-21")], source="lkdr")
    assert again["receipts"] == 0 and again["skipped"] == 1, "второй раз не разбирается"


def test_usual_basket_skips_old_purchases_from_other_chains(db):
    from app import baskets

    milk, _ = _mapped("magnit", "Молоко", "m-30")
    coffee, _ = _mapped("perekrestok", "Кофе", "x-30")
    for day in ("2026-09-01", "2026-09-08"):
        repo.add_history_row(date=day, store_id=repo.get_store("magnit").id, product_id=milk,
                             raw_name="Молоко", qty=1, unit_price=84, total=84, receipt_key="a" + day)
        repo.add_history_row(date=day, store_id=repo.get_store("perekrestok").id, product_id=coffee,
                             raw_name="Кофе", qty=1, unit_price=399, total=399, receipt_key="b" + day)

    assert [r["product_id"] for r in baskets.regular_purchases()] == [milk]
