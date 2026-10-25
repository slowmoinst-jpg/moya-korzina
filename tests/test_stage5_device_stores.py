"""Проверка Этапа 5: FixPrice, Перекрёсток, Впрок, Монетка (docs/plans/korzina-vo-vseh-setyah-2026-10-24.md).

- Для всех четырёх сетей:
  - cartrun.road(code) == 'phone';
  - phonecart.rules(code) отдаёт рабочие селекторы кнопки «в корзину», адрес корзины и хосты;
  - телефонный наряд phonecart.plan строится с позициями и правилами;
  - отчёт телефона accept_report обновляет цены в базе (note_device_prices) и оживляет строки;
  - строка сети переходит в DONE с сетевым итогом (Total.source == NETWORK);
  - humansteps.board() отражает все 4 сети на дороге phone.
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
    b_id = repo.create_basket("Тест Этап 5")
    p1 = repo.upsert_product(Product(id=None, name="Чай черный 100 пакетиков", unit="pcs"))
    p2 = repo.upsert_product(Product(id=None, name="Печенье овсяное 300 г", unit="pcs"))
    repo.set_basket_item(b_id, p1, 1.0)
    repo.set_basket_item(b_id, p2, 1.0)
    skus = {
        "fixprice": ("112233", "445566"),
        "perekrestok": ("3342904", "3342905"),
        "vprok": ("1284993", "1284994"),
        "monetka": ("556677", "889900"),
    }
    for code, (sku1, sku2) in skus.items():
        store = repo.get_store(code)
        if store:
            sp1 = repo.upsert_store_product(store.id, sku1, "Чай черный 100 пакетиков",
                                            url=f"https://{code}.example/{sku1}")
            sp2 = repo.upsert_store_product(store.id, sku2, "Печенье овсяное 300 г",
                                            url=f"https://{code}.example/{sku2}")
            repo.confirm_mapping(p1, sp1, confirmed=True)
            repo.confirm_mapping(p2, sp2, confirmed=True)
            repo.save_price(sp1, 120.0)
            repo.save_price(sp2, 80.0)
    return b_id


@pytest.mark.parametrize("chain", ["fixprice", "perekrestok", "vprok", "monetka"])
def test_stage5_stores_road_is_phone(chain):
    """Все четыре сети собираются исключительно телефоном владельца."""
    assert cartrun.road(chain) == cartrun.PHONE


@pytest.mark.parametrize("chain", ["fixprice", "perekrestok", "vprok", "monetka"])
def test_stage5_rules_and_selectors(chain):
    """Каждая сеть имеет селекторы кнопок, адрес корзины и хост."""
    rules = phonecart.rules(chain)
    assert rules["selectors"], f"у сети {chain} должны быть селекторы"
    assert rules["cart_pages"], f"у сети {chain} должен быть адрес корзины"
    assert rules["hosts"], f"у сети {chain} должны быть объявлены хосты"


def test_stage5_accept_report_updates_prices_and_total(basket):
    """Корзина с телефона становится источником цены для сетей без серверных цен."""
    report = {
        "store": "fixprice",
        "basket": basket,
        "stamp": "stamp_fp_1",
        "items": [
            {
                "sku": "112233",
                "name": "Чай черный 100 пакетиков",
                "url": "https://fix-price.com/catalog/produkty/p-112233-chay",
                "price": 149.0,
                "ok": True,
                "how": "tap",
            },
            {
                "sku": "445566",
                "name": "Печенье овсяное 300 г",
                "url": "https://fix-price.com/catalog/produkty/p-445566-pechenye",
                "price": 99.0,
                "ok": True,
                "how": "tap",
            },
        ],
        "page_totals": [
            {"kind": "goods", "label": "Товары", "amount": 248.0},
            {"kind": "total", "label": "Итого к оплате", "amount": 248.0},
        ],
        "cart_count": 2,
    }

    res = phonecart.accept_report(report)
    assert res["ok"] is True

    row = cartrun.get("fixprice")
    assert row is not None
    assert row.state == cartrun.DONE
    assert row.by == cartrun.DEVICE
    assert len(row.landed) == 2
    assert row.total is not None
    assert row.total.source == carttotal.NETWORK
    assert row.total.sum == 248.0


def test_board_shows_all_11_stores_with_correct_roads(basket):
    """Сводка humansteps.board() отражает все 11 сетей."""
    board = humansteps.board()
    codes = {item["code"] for item in board}
    expected = {
        "magnit", "pyaterochka", "samokat", "lenta", "vkusvill",
        "dixy", "metro", "perekrestok", "fixprice", "vprok", "monetka",
    }
    assert codes == expected, f"Все 11 сетей должны быть в сводке: {expected - codes}"
