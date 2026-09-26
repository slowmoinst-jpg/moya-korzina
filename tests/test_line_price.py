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


def _bought_by_weight(shop, pid, raw="СЫР РОССИЙСКИЙ"):
    """Чек с дробным количеством — довод, что товар берут на вес."""
    repo.add_history_row(date="2026-09-01", store_id=shop.id, product_id=pid,
                         raw_name=raw, qty=0.354, unit_price=700.0, total=247.8)


def test_weight_item_is_priced_by_the_packs_that_land_in_the_cart(shop):
    """0,7 кг сыра упаковками по 400 г — две упаковки за 600 ₽: столько и заплатят.

    Раньше цена упаковки шла ценой килограмма (210 ₽), а промежуточная правка
    считала 525 ₽ — меньше, чем стоят две упаковки, которые наряд положит.
    """
    pid = _mapped(shop, "Сыр Российский", "kg", shop_unit="pcs", shop_weight=400,
                  price=300.0, shop_name="Сыр Российский 400 г")
    _bought_by_weight(shop, pid)
    found = service.line_price(pid, shop, 0.7, "kg")
    assert found["value"] == 600.0
    assert "400 г" in found["note"] and "2 упаковки" in found["note"]


def test_a_big_pack_does_not_win_on_a_small_need(shop):
    """0,3 кг из пачки в 1 кг — это вся пачка, а не 30 % её цены."""
    pid = _mapped(shop, "Сыр весовой", "kg", shop_unit="pcs", shop_weight=1000, price=700.0,
                  shop_name="Сыр 1 кг")
    assert service.line_price(pid, shop, 0.3, "kg")["value"] == 700.0


def test_the_pack_wins_over_a_kilo_price_next_to_it(shop):
    """Цена за кг рядом с фасовкой (так бывает в справочнике) не отменяет того,
    что купить можно только пачку: 1,5 кг по 400 г — четыре пачки, как положит наряд."""
    pid = _mapped(shop, "Сыр Российский", "kg", shop_unit="pcs", shop_weight=400, price=300.0,
                  per_kg=700.0, shop_name="Сыр Российский 400 г")
    _bought_by_weight(shop, pid)
    assert service.line_price(pid, shop, 1.5, "kg")["value"] == 1200.0


def test_pack_of_unknown_weight_is_not_a_kilo_price(shop):
    """Упаковка неизвестного веса — сравнить не с чем, и это честнее выдумки."""
    pid = _mapped(shop, "Сыр в упаковке", "kg", shop_unit="pcs", price=300.0)
    _bought_by_weight(shop, pid)
    assert service.line_price(pid, shop, 0.7, "kg") is None


def test_weight_goods_of_the_shop_are_priced_per_kilo(shop):
    pid = _mapped(shop, "Сыр весовой", "kg", shop_unit="kg", price=800.0)
    assert service.line_price(pid, shop, 0.5, "kg")["value"] == 400.0


def test_price_per_kg_from_the_snapshot_wins_for_goods_by_weight(shop):
    """Лента отдаёт у развесного цену порции, а цену килограмма — отдельно: берём её."""
    pid = _mapped(shop, "Сыр весовой", "kg", shop_unit="kg", price=225.0, per_kg=750.0)
    assert service.line_price(pid, shop, 1.5, "kg")["value"] == 1125.0


def _migrate_again():
    """Прогнать разовую миграцию базы ещё раз — как на базе прежней версии."""
    from app.db import get_conn, init_db

    with get_conn() as conn:
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
    init_db()


def test_no_weight_in_the_name_is_not_a_weight_item(shop):
    """«Хлеб Бородинский нарезка» — не весовой, хоть граммовки в названии и нет.

    Разбор названия ставил таким эталонам unit="kg", и честный пересчёт в кило
    делал из буханки за 60 ₽ «150 ₽ за кг», а наряд клал три буханки. Миграция
    базы переводит их в штучные по доводам: сети продают их штукой или пачкой.
    """
    bread = _mapped(shop, "Хлеб БОРОДИНСКИЙ нарезка", "kg", shop_unit="pcs", shop_weight=400,
                    price=60.0, shop_name="Хлеб Бородинский 400 г")
    eggs = _mapped(shop, "Яйцо С1 10шт", "kg", sku="2", shop_unit="pcs", price=120.0)
    bananas = _mapped(shop, "Бананы", "kg", sku="3", shop_unit="kg", price=90.0)
    _migrate_again()
    assert repo.get_product(bread).unit == "pcs" and repo.get_product(eggs).unit == "pcs"
    assert repo.get_product(bananas).unit == "kg", "сеть продаёт на вес — весовой"
    assert service.line_price(bread, shop, 1, "pcs")["value"] == 60.0
    assert service.line_price(eggs, shop, 2, "pcs")["value"] == 240.0


def test_the_migration_takes_false_kilos_off_the_crawled_goods(shop):
    """Сборщики Пятёрочки, Самоката, Fix Price и Дикси ставили «kg» по отсутствию
    граммовки. Миграция снимает его — кроме товаров со словом «весовой»."""
    five = repo.get_store("pyaterochka")
    loaf = repo.upsert_store_product(five.id, "1", "Хлеб Бородинский", unit="kg")
    cucumbers = repo.upsert_store_product(five.id, "2", "Огурцы весовые", unit="kg")
    magnit_meat = repo.upsert_store_product(shop.id, "9", "Креветки Королевские", unit="kg")
    _migrate_again()
    from app.db import get_conn

    with get_conn() as conn:
        units = {r["id"]: r["unit"] for r in conn.execute("SELECT id, unit FROM store_products")}
    assert units[loaf] is None, "«kg» от разбора названия — не единица сети"
    assert units[cucumbers] == "kg"
    assert units[magnit_meat] == "kg", "у Магнита единица шла от самой сети"


def test_the_decision_does_not_depend_on_the_amount(shop):
    """1,9 кг и 2,0 кг — одинаково килограммы: упаковок 4 и 4, а не 4 и 2."""
    pid = _mapped(shop, "Бананы", "kg", shop_unit="pcs", shop_weight=500, price=60.0,
                  shop_name="Бананы 500 г")
    _bought_by_weight(shop, pid, raw="БАНАНЫ ВЕС")
    assert service.line_price(pid, shop, 1.9, "kg")["value"] == 240.0
    assert service.line_price(pid, shop, 2.0, "kg")["value"] == 240.0


def test_receipts_bought_in_pieces_make_a_piece_item(shop):
    """Чеки целыми штуками — довод, что это штуки, как бы ни звался эталон."""
    pid = _mapped(shop, "Картофель", "kg", shop_unit="pcs", shop_weight=2500, price=150.0,
                  shop_name="Картофель сетка 2,5 кг")
    repo.add_history_row(date="2026-09-01", store_id=shop.id, product_id=pid,
                         raw_name="КАРТОФЕЛЬ СЕТКА", qty=1, unit_price=150.0, total=150.0)
    _migrate_again()
    assert repo.get_product(pid).unit == "pcs"
    assert service.line_price(pid, shop, 2, "pcs")["value"] == 300.0, "две сетки, как в чеке"


def test_a_receipt_bought_by_weight_turns_a_piece_item_into_a_weight_one(shop):
    """Заведён штучным по первому чеку, а потом пришёл дробным — значит на вес."""
    from app.importers.ofd_pdf import _ensure_product

    pid, _ = _ensure_product("ПОМИДОРЫ", 1)
    assert repo.get_product(pid).unit == "pcs"
    again, created = _ensure_product("ПОМИДОРЫ", 0.812)
    assert again == pid and not created
    assert repo.get_product(pid).unit == "kg"


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


def test_a_piece_of_unknown_weight_is_not_priced_by_the_kilo(shop):
    """Три лимона по цене трёх килограммов — не цена: веса штуки мы не знаем."""
    pid = _mapped(shop, "Лимоны", "pcs", shop_unit="kg", price=250.0,
                  shop_name="Лимоны весовые")
    assert service.line_price(pid, shop, 3, "pcs") is None


def test_the_piece_rate_is_the_kilo_price_not_the_portion(shop):
    """Штука на весовой полке Ленты: цена килограмма, а не цена её порции."""
    pid = _mapped(shop, "Сыр 200 г", "pcs", weight_g=200, shop_unit="kg", price=225.0,
                  per_kg=750.0, shop_name="Сыр NATURA весовой")
    assert service.line_price(pid, shop, 1, "pcs", 200)["value"] == 150.0


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


def test_the_cart_link_asks_for_the_same_amount_the_calculation_paid_for(shop, monkeypatch):
    """Ссылка на корзину берёт количество по тому же правилу, что и расчёт.

    0,7 кг сыра упаковками по 400 г — две упаковки (Лента клала одну, ВкусВилл —
    «0,7»); весовой товар, который сеть сама продаёт на вес, уезжает килограммами;
    штука на весовой полке — одной штукой, а не «килограммом».
    """
    from app.connectors import lenta

    sent = {}
    monkeypatch.setattr(lenta, "cart_link", lambda items, location=None: sent.setdefault("items", items) and "https://lenta.com/x")
    monkeypatch.setattr(service.location, "for_store", lambda code: None)
    store = repo.get_store("lenta")

    def mapped(name, unit, sku, shop_unit, shop_weight=None):
        pid = repo.upsert_product(Product(id=None, name=name, unit=unit))
        sp = repo.upsert_store_product(store.id, sku, name, weight_g=shop_weight, unit=shop_unit)
        repo.confirm_mapping(pid, sp, confirmed=True)
        return pid

    cheese = mapped("Сыр Российский", "kg", "101", "pcs", 400)
    loose = mapped("Сыр NATURA весовой", "kg", "102", "kg")
    piece = mapped("Сыр Ламбер 230 г", "pcs", "103", "kg")

    class Line:
        def __init__(self, pid, qty):
            self.product_id, self.qty = pid, qty

    assert service.cart_link("lenta", [Line(cheese, 0.7), Line(loose, 0.7), Line(piece, 1)])
    assert sent["items"] == [(101, 2.0, None), (102, 0.7, "kg"), (103, 1.0, None)]
