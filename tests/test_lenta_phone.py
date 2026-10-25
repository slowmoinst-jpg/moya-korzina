"""Проверка телефонного маршрута для Ленты (Этап 3, docs/plans/korzina-vo-vseh-setyah-2026-10-24.md).

Лента переведена с гостевой ссылки на телефонный маршрут:
- cartrun.road('lenta') == 'phone';
- signals.ADD_TO_CART['lenta'] содержит рабочие селекторы кнопки «в корзину»;
- phonecart.rules('lenta') отдаёт селекторы и страницу корзины https://lenta.com/basket/;
- телефон собирает позиции под личным входом владельца (с картой лояльности), читает «Итого»
  со страницы корзины через page_totals;
- accept_report переводит строку в cartrun.DONE с by='phone' и сохраняет сетевой итог (Total.source == NETWORK);
- humansteps.board() видит дорогу phone и сетевой итог.
"""
from __future__ import annotations

import pytest

from app import cartrun, carttotal, config, humansteps, phonecart, repo
from app.db import init_db
from app.models import Product
from app.shopbrowser import signals


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "test.db"))
    init_db()


@pytest.fixture
def basket(db):
    """Корзина с позициями для проверки наряда Ленты."""
    b_id = repo.create_basket("Тест Лента")
    p1 = repo.upsert_product(Product(id=None, name="Молоко Простоквашино 2.5% 930 мл", unit="pcs"))
    p2 = repo.upsert_product(Product(id=None, name="Хлеб Бородинский 400 г", unit="pcs"))
    repo.set_basket_item(b_id, p1, 2.0)
    repo.set_basket_item(b_id, p2, 1.0)
    store = repo.get_store("lenta")
    if store:
        sp1 = repo.upsert_store_product(store.id, "101001", "Молоко Простоквашино 2.5% 930 мл",
                                        url="https://lenta.com/product/101001")
        sp2 = repo.upsert_store_product(store.id, "101002", "Хлеб Бородинский 400 г",
                                        url="https://lenta.com/product/101002")
        repo.confirm_mapping(p1, sp1, confirmed=True)
        repo.confirm_mapping(p2, sp2, confirmed=True)
        repo.save_price(sp1, 95.0)
        repo.save_price(sp2, 45.0)
    return b_id


def test_lenta_road_is_phone():
    """Лента собирается телефоном владельца под его входом, а не гостевой ссылкой."""
    assert cartrun.road("lenta") == cartrun.PHONE


def test_lenta_rules_and_selectors():
    """Селекторы и параметры страницы Ленты описаны в правилах телефона."""
    rules = phonecart.rules("lenta")
    assert rules["selectors"], "селекторы кнопки «в корзину» у Ленты обязаны быть"
    assert any("add-to-cart" in sel or "корзин" in sel or "sku" in sel for sel in rules["selectors"])
    assert "https://lenta.com/basket/" in rules["cart_pages"]
    assert "lenta.com" in rules["hosts"]


def test_lenta_plan_contains_lines_and_rules(basket):
    """Наряд для Ленты строится с позициями корзины и серверными правилами."""
    plan = phonecart.plan({"store": "lenta", "basket": basket})
    assert plan["ok"] is True
    assert plan["store"] == "lenta"
    assert len(plan["lines"]) >= 2
    assert plan["rules"]["selectors"] == list(signals.ADD_TO_CART["lenta"])
    assert "https://lenta.com/basket/" in plan["rules"]["cart_pages"]


def test_lenta_accept_report_saves_network_total_and_done(basket):
    """Отчёт телефона о корзине Ленты сохраняет сетевой итог с картой лояльности."""
    report = {
        "store": "lenta",
        "basket": basket,
        "stamp": "stamp_lenta_1",
        "items": [
            {"sku": "101001", "name": "Молоко Простоквашино 2.5% 930 мл", "ok": True, "how": "tap"},
            {"sku": "101002", "name": "Хлеб Бородинский 400 г", "ok": True, "how": "tap"},
        ],
        "page_totals": [
            {"kind": "goods", "label": "Товары", "amount": 220.0},
            {"kind": "discount", "label": "Скидка по карте Лента", "amount": 35.0},
            {"kind": "delivery", "label": "Доставка", "amount": 0.0},
            {"kind": "total", "label": "Итого к оплате", "amount": 185.0},
        ],
        "cart_count": 2,
    }

    phonecart.accept_report(report)

    row = cartrun.get("lenta")
    assert row is not None
    assert row.state == cartrun.DONE
    assert row.by == cartrun.DEVICE
    assert len(row.landed) == 2
    assert "Молоко Простоквашино 2.5% 930 мл" in row.landed
    assert "Хлеб Бородинский 400 г" in row.landed
    assert row.total is not None
    assert row.total.source == carttotal.NETWORK
    assert row.total.sum == 185.0
    assert row.total.discount == 35.0
    assert row.total.delivery == 0.0


def test_lenta_visible_in_humansteps_board(basket):
    """Сводка по 11 сетям видит Ленту на дороге phone и фиксирует сетевой итог."""
    test_lenta_accept_report_saves_network_total_and_done(basket)

    board = humansteps.board()
    lenta_entry = next((item for item in board if item["code"] == "lenta"), None)
    assert lenta_entry is not None
    assert lenta_entry["road"] == cartrun.PHONE
    assert lenta_entry["state"] == cartrun.DONE
    assert lenta_entry["network_total"] is True
    assert lenta_entry["landed"] == 2
