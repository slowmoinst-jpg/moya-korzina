"""Тесты живой полосы трёх лучших мест и цен строк корзины (прогон 2).

Проверяется (бриф .omc/briefs/light-tablo-2.md):
1. top3 без свёртка возвращает None и зовёт warm; со свёртком без phone_places — тоже;
2. со свёртком другого состава корзины (stamp не тот) — None и warm;
3. с годным свёртком — не больше трёх мест, первое рекомендуемое, цены — по строкам рекомендуемого места; warm не зовётся;
4. wait=True без свёртка считает сразу и warm не зовёт;
5. /basket/top: без расчёта {"ready": false}; с расчётом ready: true, в html есть class="live" и сегмент win, в prices — строка с U+202F перед ₽;
6. страница корзины с годным расчётом показывает полосу и цену в .pr; без вариантов — кнопку «Где дешевле · N»; без расчёта — полосу с data-live.
"""
from __future__ import annotations

import os
import re
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import location, repo, users
from app.models import Product
from app.web.phone import cheaper
from app.web.screens import result

PHONE = "79990000071"
NNBSP = "\u202f"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.open_workspace(PHONE)
    location.save_address("г Москва, ул Арбат, 1")
    yield
    users.deactivate()


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))

    result._CALC.clear()
    from app.web import create_app
    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    result._CALC.clear()
    users.deactivate()


def enter(client):
    from conftest import sign_in
    return sign_in(client, PHONE)


def add_product(name: str, unit: str = "pcs") -> int:
    return repo.upsert_product(Product(id=None, name=name, unit=unit))


def price(product_id: int, store_code: str, value: float) -> None:
    store = repo.get_store(store_code)
    sp_id = repo.upsert_store_product(store.id, f"sku-{store_code}-{product_id}", f"товар {product_id}")
    repo.confirm_mapping(product_id, sp_id, confirmed=True)
    repo.save_price(sp_id, value)


def make_basket() -> tuple[int, int, int]:
    milk = add_product("Молоко 930 мл")
    bread = add_product("Хлеб Бородинский")
    price(milk, "magnit", 40.0)
    price(bread, "magnit", 30.0)
    price(milk, "pyaterochka", 50.0)
    price(bread, "pyaterochka", 25.0)
    basket_id = repo.create_basket("Неделя")
    repo.set_basket_item(basket_id, milk, 2.0)
    repo.set_basket_item(basket_id, bread, 1.0)
    return basket_id, milk, bread


def test_top3_without_bundle_returns_none_and_calls_warm(workspace, monkeypatch):
    """1. top3 без свёртка возвращает None и зовёт warm; со свёртком без phone_places — тоже."""
    basket_id, milk, bread = make_basket()
    warm_calls = []
    monkeypatch.setattr(cheaper, "warm", lambda bid, p: warm_calls.append((bid, p)))

    result._CALC.clear()
    res = cheaper.top3(basket_id)
    assert res is None
    assert len(warm_calls) == 1
    assert warm_calls[0] == (basket_id, PHONE)

    # Со свёртком без phone_places
    key = result._key(basket_id)
    items = repo.basket_items(basket_id)
    stamp = result._stamp(items)
    result._CALC[key] = {
        "variants": [],
        "stamp": stamp,
        "at": time.time(),
    }
    res2 = cheaper.top3(basket_id)
    assert res2 is None
    assert len(warm_calls) == 2
    assert warm_calls[1] == (basket_id, PHONE)


def test_top3_with_different_stamp_returns_none_and_calls_warm(workspace, monkeypatch):
    """2. со свёртком другого состава корзины (stamp не тот) — None и warm."""
    basket_id, milk, bread = make_basket()
    warm_calls = []
    monkeypatch.setattr(cheaper, "warm", lambda bid, p: warm_calls.append((bid, p)))

    key = result._key(basket_id)
    result._CALC[key] = {
        "variants": [],
        "phone_places": [],
        "stamp": (("other", 1.0),),
        "at": time.time(),
    }
    res = cheaper.top3(basket_id)
    assert res is None
    assert len(warm_calls) == 1
    assert warm_calls[0] == (basket_id, PHONE)


def test_top3_with_valid_bundle_returns_places_and_prices(workspace, monkeypatch):
    """3. с годным свёртком — не больше трёх мест, первое рекомендуемое, цены — по строкам рекомендуемого места; warm не зовётся."""
    basket_id, milk, bread = make_basket()
    warm_calls = []
    monkeypatch.setattr(cheaper, "warm", lambda bid, p: warm_calls.append((bid, p)))

    bundle = result._bundle(basket_id, again=True)
    cheaper._places(basket_id, bundle)
    assert "phone_places" in bundle

    res = cheaper.top3(basket_id)
    assert res is not None
    assert "places" in res and "prices" in res
    assert 0 < len(res["places"]) <= 3
    assert res["places"][0]["best"] is True
    assert len(warm_calls) == 0

    board = cheaper._board(basket_id, bundle, None)
    best_variant = board["every"][board["best_n"]]
    expected_prices = {}
    for s in best_variant.stores:
        for ln in s.lines:
            if ln.product_id:
                expected_prices[int(ln.product_id)] = ln.price
    assert res["prices"] == expected_prices


def test_top3_wait_true_without_bundle_calculates_immediately_and_no_warm(workspace, monkeypatch):
    """4. wait=True без свёртка считает сразу и warm не зовёт."""
    basket_id, milk, bread = make_basket()
    warm_calls = []
    monkeypatch.setattr(cheaper, "warm", lambda bid, p: warm_calls.append((bid, p)))

    result._CALC.clear()
    res = cheaper.top3(basket_id, wait=True)
    assert res is not None
    assert "places" in res and "prices" in res
    assert len(warm_calls) == 0
    assert len(res["places"]) <= 3


def test_basket_top_endpoint(web, monkeypatch):
    """5. /basket/top: без расчёта {"ready": false}; с расчётом ready: true, в html есть class="live" и сегмент win, в prices — строка с U+202F перед ₽."""
    enter(web)
    users.open_workspace(PHONE)
    location.save_address("г Москва, ул Арбат, 1")
    basket_id, milk, bread = make_basket()
    users.deactivate()

    result._CALC.clear()
    monkeypatch.setattr(cheaper, "warm", lambda bid, p: None)

    resp = web.get("/phone/basket/top")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {"ready": False}

    users.open_workspace(PHONE)
    bundle = result._bundle(basket_id, again=True)
    cheaper._places(basket_id, bundle)
    users.deactivate()

    resp2 = web.get("/phone/basket/top")
    assert resp2.status_code == 200
    data2 = resp2.get_json()
    assert data2.get("ready") is True
    html = data2.get("html", "")
    assert 'class="live' in html
    assert "win" in html
    prices = data2.get("prices", {})
    assert len(prices) > 0
    for price_str in prices.values():
        assert f"{NNBSP}₽" in price_str


def test_basket_page_live_bar(web, monkeypatch):
    """6. страница корзины с годным расчётом показывает полосу и цену в .pr; без вариантов — кнопку «Где дешевле · N»; без расчёта — полосу с data-live."""
    enter(web)
    users.open_workspace(PHONE)
    location.save_address("г Москва, ул Арбат, 1")
    basket_id, milk, bread = make_basket()
    users.deactivate()

    # 1) без расчёта — полоса с data-live
    result._CALC.clear()
    monkeypatch.setattr(cheaper, "warm", lambda bid, p: None)
    page_no_calc = web.get("/phone/basket").data.decode("utf-8")
    assert "data-live" in page_no_calc
    assert 'class="live wait"' in page_no_calc

    # 2) с годным расчётом — показывает полосу и цену в .pr
    users.open_workspace(PHONE)
    bundle = result._bundle(basket_id, again=True)
    cheaper._places(basket_id, bundle)
    users.deactivate()

    page_with_calc = web.get("/phone/basket").data.decode("utf-8")
    assert 'class="live"' in page_with_calc
    assert "win" in page_with_calc
    assert f"{NNBSP}₽" in page_with_calc
    assert f'id="it-{milk}"' in page_with_calc
    assert re.search(r'id="it-' + str(milk) + r'[\s\S]*?<span class="pr num">[^<]*' + NNBSP + r'₽</span>', page_with_calc)

    # 3) без вариантов — кнопка «Где дешевле · N»
    users.open_workspace(PHONE)
    key = result._key(basket_id)
    items = repo.basket_items(basket_id)
    stamp = result._stamp(items)
    result._CALC[key] = {
        "variants": [],
        "phone_places": [],
        "stamp": stamp,
        "at": time.time(),
        "positions": len(items),
    }
    users.deactivate()

    page_no_variants = web.get("/phone/basket").data.decode("utf-8")
    assert 'data-live=' not in page_no_variants
    assert '<a class="live' not in page_no_variants
    assert "Где дешевле" in page_no_variants
    assert 'class="cta"' in page_no_variants
    assert f"· {len(items)}" in page_no_variants
