"""Цена позиции корзины: килограмм не из цены фасовки, фасовки — по весу.

Ошибки здесь самые дорогие из найденных ревизией 26.09.2026: они не роняют
экран, а тихо делают выигравшим не тот магазин. Сыр 400 г за 300 ₽ без цены
за кг считался как 300 ₽ за КИЛОГРАММ — на 0,7 кг выходило 210 ₽ вместо
525 ₽, и «экономия» завышалась на 315 ₽.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import repo, service, users  # noqa: E402
from app.models import Product  # noqa: E402

PHONE = "79990000088"


@pytest.fixture
def shop(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.open_workspace(PHONE)
    yield repo.get_store("magnit")
    users.deactivate()


def _mapped(store, name: str, unit: str, weight_g=None, sku="1", shop_unit=None,
            shop_weight=None, price=100.0, per_kg=None, fetched_at=None,
            shop_name=None) -> int:
    pid = repo.upsert_product(Product(id=None, name=name, unit=unit, weight_g=weight_g))
    sp = repo.upsert_store_product(store.id, sku, shop_name or name, weight_g=shop_weight,
                                   unit=shop_unit)
    repo.confirm_mapping(pid, sp, confirmed=True)
    repo.save_price(sp, price, per_kg, fetched_at=fetched_at)
    return pid


def test_weight_item_is_priced_by_the_packs_that_land_in_the_cart(shop):
    """0,7 кг сыра упаковками по 400 г — две упаковки за 600 ₽: столько и заплатят.

    Раньше цена упаковки шла ценой килограмма (210 ₽), а промежуточная правка
    считала 525 ₽ — меньше, чем стоят две упаковки, которые наряд положит.
    """
    pid = _mapped(shop, "Сыр Российский 400 г", "kg", shop_unit="pcs", shop_weight=400,
                  price=300.0)
    found = service.line_price(pid, shop, 0.7, "kg")
    assert found["value"] == 600.0
    assert "2 уп. по 400 г" in found["note"]


def test_a_big_pack_does_not_win_on_a_small_need(shop):
    """0,3 кг из пачки в 1 кг — это вся пачка, а не 30 % её цены."""
    pid = _mapped(shop, "Сыр весовой", "kg", shop_unit="pcs", shop_weight=1000, price=700.0,
                  shop_name="Сыр 1 кг")
    assert service.line_price(pid, shop, 0.3, "kg")["value"] == 700.0


def test_pack_of_unknown_weight_is_not_a_kilo_price(shop):
    """Упаковка неизвестного веса — сравнить не с чем, и это честнее выдумки."""
    pid = _mapped(shop, "Сыр в упаковке", "kg", shop_unit="pcs", price=300.0)
    assert service.line_price(pid, shop, 0.7, "kg") is None


def test_weight_goods_of_the_shop_are_priced_per_kilo(shop):
    pid = _mapped(shop, "Сыр весовой", "kg", shop_unit="kg", price=800.0)
    assert service.line_price(pid, shop, 0.5, "kg")["value"] == 400.0


def test_price_per_kg_from_the_snapshot_wins(shop):
    pid = _mapped(shop, "Сыр 400 г", "kg", shop_unit="pcs", shop_weight=400, price=300.0,
                  per_kg=700.0)
    assert service.line_price(pid, shop, 1.5, "kg")["value"] == 1050.0


def test_no_weight_in_the_name_is_not_a_weight_item(shop):
    """«Хлеб Бородинский нарезка» — не весовой, хоть граммовки в названии и нет.

    Разбор названия ставил таким товарам unit="kg", и честный пересчёт в кило
    делал из буханки за 60 ₽ «150 ₽ за кг», а наряд клал три буханки.
    """
    bread = _mapped(shop, "Хлеб БОРОДИНСКИЙ нарезка", "kg", shop_unit="pcs", shop_weight=400,
                    price=60.0, shop_name="Хлеб Бородинский 400 г")
    assert service.line_price(bread, shop, 1, "kg")["value"] == 60.0
    eggs = _mapped(shop, "Яйцо С1 10шт", "kg", sku="2", shop_unit="pcs", price=120.0)
    assert service.line_price(eggs, shop, 2, "kg")["value"] == 240.0, "магазин не выпадает"


def test_receipts_bought_by_weight_make_a_weight_item(shop):
    """Дробное количество в чеках — довод: этот товар человек берёт на вес."""
    pid = _mapped(shop, "Картофель", "kg", shop_unit="pcs", shop_weight=2500, price=150.0,
                  shop_name="Картофель сетка 2,5 кг")
    assert service.line_price(pid, shop, 2, "kg")["value"] == 300.0, "без довода — штуками"
    repo.add_history_row(date="2026-09-01", store_id=shop.id, product_id=pid,
                         raw_name="КАРТОФЕЛЬ ВЕС", qty=1.734, unit_price=40.0, total=69.36)
    assert service.line_price(pid, shop, 2, "kg")["value"] == 150.0, "2 кг — одна сетка"


def test_different_packs_are_compared_by_weight(shop):
    """800 г за 90 ₽ против эталона 1 кг: платят 90, а сравнивают как 112,5."""
    pid = _mapped(shop, "Гречка 1 кг", "pcs", weight_g=1000, shop_unit="pcs",
                  shop_weight=800, price=90.0)
    found = service.line_price(pid, shop, 1.0, "pcs", 1000)
    assert found["value"] == 90.0, "в оплату — цена полки"
    assert found["adjust"] == 22.5, "в выбор — разница с приведённой к килограмму"
    assert "800" in found["note"] and "1000" in found["note"]


def test_same_pack_keeps_the_shelf_price(shop):
    """930 мл против 0,93 л — одна фасовка, цену полки не трогаем."""
    pid = _mapped(shop, "Молоко 930 мл", "pcs", weight_g=930, shop_unit="pcs",
                  shop_weight=930, price=89.0)
    found = service.line_price(pid, shop, 2.0, "pcs", 930)
    assert found["value"] == 178.0
    assert found["note"] is None and found["adjust"] == 0.0


def test_piece_sold_by_weight_in_the_shop(shop):
    pid = _mapped(shop, "Колбаса 300 г", "pcs", weight_g=300, shop_unit="kg", price=900.0,
                  shop_name="Колбаса докторская весовая")
    assert service.line_price(pid, shop, 1.0, "pcs", 300)["value"] == 270.0


def test_a_kilo_mark_without_a_word_is_not_a_kilo_price(shop):
    """unit="kg" у товара сети без слова «весовой» — не довод: так метили сборщики
    любую строку без граммовки. Цена буханки не делится на 0,4 кг."""
    pid = _mapped(shop, "Хлеб 400 г", "pcs", weight_g=400, shop_unit="kg", price=60.0,
                  shop_name="Хлеб Бородинский")
    assert service.line_price(pid, shop, 1.0, "pcs", 400)["value"] == 60.0


def test_old_price_is_marked_stale(shop):
    old = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    pid = _mapped(shop, "Кефир", "pcs", price=80.0, fetched_at=old)
    found = service.line_price(pid, shop, 1.0, "pcs")
    assert found["stale"] is True
    fresh = _mapped(shop, "Ряженка", "pcs", sku="2", price=80.0)
    assert service.line_price(fresh, shop, 1.0, "pcs")["stale"] is False


def test_stale_price_reaches_the_variant(shop):
    old = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    pid = _mapped(shop, "Кефир", "pcs", price=80.0, fetched_at=old)
    basket = repo.create_basket("проверка")
    repo.set_basket_item(basket, pid, 1)
    variants, _base = service.calculate(basket, refresh=False)
    stale = variants[0].stale_lines
    assert [ln.product_name for ln in stale] == ["Кефир"]
    assert stale[0].stale_since == old[:10]


def test_pieces_are_whole_in_the_basket(shop):
    """Штучный товар — целыми штуками: цена ×1,5 не сходилась с корзиной сети."""
    pid = _mapped(shop, "Батон", "pcs", price=50.0)
    kg = _mapped(shop, "Картофель", "kg", sku="2", shop_unit="kg", price=40.0)
    basket = repo.create_basket("проверка")
    repo.set_basket_item(basket, pid, 1.5)
    repo.set_basket_item(basket, kg, 1.5)
    qty = {row["product_id"]: row["qty"] for row in repo.basket_items(basket)}
    assert qty[pid] == 2.0
    assert qty[kg] == 1.5, "весовой товар округлять нельзя"
    repo.set_basket_item(basket, pid, 0.3)
    assert {r["product_id"]: r["qty"] for r in repo.basket_items(basket)}[pid] == 1.0
    assert repo.whole_pieces(1.3) == 1.0


def test_reference_price_is_never_taken_for_a_live_one(shop):
    """Справочная цена из CSV помечается в расчёте, какой бы свежей ни была дата."""
    pid = repo.upsert_product(Product(id=None, name="Хлебцы", unit="pcs"))
    sp = repo.upsert_store_product(shop.id, "magnit-hlebcy", "Хлебцы")
    repo.confirm_mapping(pid, sp, confirmed=True)
    repo.save_price(sp, 55.0, source="fallback")
    found = service.line_price(pid, shop, 1.0, "pcs")
    assert found["stale"] is True and found["stale_label"] == "справочная цена"


def test_absent_in_the_baseline_store_is_not_a_zero_baseline(shop):
    """Товара нет в базовом магазине — его ноль не база: иначе экономия занижена."""
    from app import config

    base = repo.get_store(config.get("baseline_store", "pyaterochka"))
    pid = repo.upsert_product(Product(id=None, name="Сыр Кубанский", unit="pcs"))
    sp = repo.upsert_store_product(base.id, "absent", "Сыр Кубанский")
    repo.confirm_mapping(pid, sp, confirmed=True)
    repo.save_price(sp, 0.0, in_stock=False)
    other = repo.upsert_store_product(shop.id, "7", "Сыр Кубанский")
    repo.confirm_mapping(pid, other, confirmed=True)
    repo.save_price(other, 300.0)
    basket = repo.create_basket("проверка")
    repo.set_basket_item(basket, pid, 1)
    assert service.baseline_by_product(basket)[pid] == 300.0


def test_a_name_without_weight_is_not_a_weight_word():
    """«На вес» — только по слову, а не по отсутствию граммовки в названии."""
    from app.matcher.normalize import sold_by_weight, unit_from_name

    assert unit_from_name("Хлеб БОРОДИНСКИЙ нарезка") is None
    assert unit_from_name("Яйцо С1 10шт") is None
    assert unit_from_name("Мука 1 кг") == "pcs", "«1 кг» — фасовка, а не развес"
    assert unit_from_name("Бананы, кг") == "kg"
    assert sold_by_weight("ЯБЛОКИ ГАЛА ВЕС") and sold_by_weight("Огурцы весовые")


def test_receipts_make_weight_items_only_by_evidence(shop):
    """Чек заводит весовой эталон по дробному количеству или слову, а не по молчанию."""
    from app.importers.ofd_pdf import _ensure_product

    bread, _ = _ensure_product("Хлеб БОРОДИНСКИЙ нарезка", 1)
    apples, _ = _ensure_product("ЯБЛОКИ ГАЛА ВЕС", 1)
    cheese, _ = _ensure_product("Сыр Российский", 0.354)
    milk, _ = _ensure_product("Молоко 930 мл", 2)
    units = {pid: repo.get_product(pid).unit for pid in (bread, apples, cheese, milk)}
    assert units == {bread: "pcs", apples: "kg", cheese: "kg", milk: "pcs"}


def test_pyaterochka_adds_known_goods_from_a_real_base(shop):
    """Добор товаров Пятёрочки из чеков — на НАСТОЯЩЕЙ базе, а не подменённой.

    Запрос ссылался на колонку is_current, которой в store_prices нет, и падал;
    тест подменял соединение целиком и этого не видел.
    """
    from app.catalog.crawlers.pyaterochka import PyaterochkaCrawler

    five = repo.get_store("pyaterochka")
    sp = repo.upsert_store_product(five.id, "111", "Хлеб Бородинский")
    repo.save_price(sp, 45.0, fetched_at="2026-09-01T10:00:00")
    repo.save_price(sp, 49.0, fetched_at="2026-09-20T10:00:00")
    got = list(PyaterochkaCrawler(sections=[])._from_store_products())
    assert [(p.sku, p.price, p.unit) for p in got] == [("111", 49.0, None)]
