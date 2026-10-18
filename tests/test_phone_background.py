"""Сборка с телефона в фоне (приложение 1.13): ход, отпечаток корзины и строки на экране.

Пока человек смотрит корзину на телефоне, приложение само собирает её во всех сетях,
где он вошёл, — скрытым окном на сеть. Здесь стережётся серверная половина:

- наряд говорит, какую корзину он описывает (номер и отпечаток состава): по
  отпечатку телефон видит, менялась ли корзина, и без перемен не собирает;
- ход сборки держит строку «идёт» пульсом и замолкший телефон не висит «идёт» вечно;
- две руки в одной корзине не кладут: телефон не идёт туда, где кладёт сервер, а
  сервер — туда, куда эту же корзину уже собрал телефон;
- лежавшее с прошлой сборки («kept») — не отказ, а лишнее названо, но не убрано;
- экран корзины на телефоне показывает строку сети: «собираю 2 из 5», «легло 4 из 5 ·
  сумма», и отдаёт приложению корзину, отпечаток и что сервер знает о входах.
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import cartrun, phonecart, repo, store_accounts, users  # noqa: E402
from app.models import Product  # noqa: E402
from app.shopbrowser import cart  # noqa: E402
from app.shopbrowser import store as shopstore  # noqa: E402

PHONE = "79990000093"
APP_UA = "Mozilla/5.0 (Linux; Android 14; wv) KorzinaApp/1.13"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Рабочее место с корзиной «молоко ×2 + сыр 0,4 кг», знакомой Магниту."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.open_workspace(PHONE)
    milk = repo.upsert_product(Product(id=None, name="Молоко Простоквашино 930 мл", unit="pcs"))
    cheese = repo.upsert_product(Product(id=None, name="Сыр Российский 45%", unit="kg"))
    shop = repo.get_store("magnit")
    milk_sp = repo.upsert_store_product(shop.id, "m-1", "Молоко Простоквашино 930 мл",
                                        url="https://magnit.ru/product/1")
    cheese_sp = repo.upsert_store_product(shop.id, "m-2", "Сыр Российский 45%",
                                          url="https://magnit.ru/product/2")
    repo.confirm_mapping(milk, milk_sp, confirmed=True)
    repo.confirm_mapping(cheese, cheese_sp, confirmed=True)
    repo.save_price(milk_sp, 89.0)
    repo.save_price(cheese_sp, 240.0)
    basket_id = repo.create_basket("Неделя 42")
    repo.set_basket_item(basket_id, milk, 2.0)
    repo.set_basket_item(basket_id, cheese, 0.4)
    from app import matcher
    monkeypatch.setattr(matcher, "auto_match", lambda ids, codes, **kw: {"auto": 0, "need_review": []})
    yield {"basket": basket_id, "milk": milk, "cheese": cheese}
    users.deactivate()


@pytest.fixture
def client(home, monkeypatch):
    users.deactivate()
    from app.web import create_app
    from app.web.screens import result

    result._CALC.clear()
    application = create_app()
    application.config.update(TESTING=True)
    web = application.test_client()
    web.post("/login", data={"phone": PHONE, "next": "/"})
    yield web
    result._CALC.clear()
    users.deactivate()


def workspace() -> None:
    users.open_workspace(PHONE)


def save_login(chain: str = "magnit") -> None:
    shopstore.save(chain, {"cookies": [{"name": "mg_at", "value": "ключ"}], "origins": []})
    store_accounts.mark_connected(chain)


# ---------- наряд говорит, какую корзину он описывает ----------

def test_the_plan_names_its_basket_and_the_stamp_of_its_contents(home):
    plan = phonecart.plan({"store": "magnit"})

    assert plan["basket"] == home["basket"]
    assert plan["stamp"] == cartrun.stamp_of(home["basket"]) and len(plan["stamp"]) == 12

    repo.set_basket_item(home["basket"], home["milk"], 3.0)
    assert phonecart.plan({"store": "magnit"})["stamp"] != plan["stamp"], \
        "другое количество — другая корзина: телефон соберёт её заново"


def test_two_basket_goods_on_one_chain_article_are_one_line(home):
    """Дубль товара на одном артикуле сети — одна позиция с суммой: сеть ставит число, а не прибавляет."""
    twin = repo.upsert_product(Product(id=None, name="Молоко Простоквашино 930мл (из чека)", unit="pcs"))
    shop = repo.get_store("magnit")
    sp = repo.upsert_store_product(shop.id, "m-1", "Молоко Простоквашино 930 мл",
                                   url="https://magnit.ru/product/1")
    repo.confirm_mapping(twin, sp, confirmed=True)
    repo.set_basket_item(home["basket"], twin, 3.0)

    lines = phonecart.plan({"store": "magnit"})["lines"]

    milk = [line for line in lines if line["sku"] == "m-1"]
    assert len(milk) == 1 and milk[0]["clicks"] == 5, "2 + 3 пачки — одна позиция на 5"


def test_the_rules_tell_the_phone_how_to_see_the_whole_cart_and_a_login(home):
    rules = phonecart.plan({"store": "magnit"})["rules"]

    assert rules["auth_cookies"] == ["mg_at"], "по куке входа телефон сам видит, вошёл ли человек"
    assert "/webgate/v1/carts/lite" in rules["full_cart"], "ответ Магнита с корзиной целиком — замерен"
    assert rules["qty_sets"] is True, "запрос Магнита ставит число: прибавка — тот же запрос"
    assert "корзина пуста" in rules["empty_cart"]
    other = phonecart.rules("dixy")
    assert other["qty_sets"] is False and other["full_cart"] == [], \
        "не замерено — прибавка касаниями «+», ответам сети о корзине не верим"


# ---------- ход сборки ----------

def test_phone_progress_keeps_the_row_going_and_tells_the_step(home):
    stamp = cartrun.stamp_of(home["basket"])
    phonecart.progress({"store": "magnit", "basket": home["basket"], "stamp": stamp,
                        "planned": 5, "at": 2, "now": "кладу 3 из 5: Молоко"})

    row = cartrun.get("magnit")
    assert row.state == cartrun.GOING and row.by == cartrun.DEVICE
    assert row.stamp == stamp and row.basket_id == home["basket"]
    assert cartrun.alive(row)
    assert cartrun.live(row) == {"at": 2, "total": 5, "now": "кладу 3 из 5: Молоко"}


def test_a_silent_phone_is_not_going_forever(home, monkeypatch):
    phonecart.progress({"store": "magnit", "basket": home["basket"], "planned": 3, "at": 1})
    later = time.time() + cartrun.PHONE_STALE + 5
    monkeypatch.setattr(cartrun.time, "time", lambda: later)

    row = next(r for r in cartrun.rows() if r.code == "magnit")
    assert row.state == cartrun.FAILED and row.note == cartrun.PHONE_LOST


def test_the_phone_does_not_fill_a_cart_the_server_is_filling(home, monkeypatch):
    monkeypatch.setattr(cart, "running", lambda chain: chain == "magnit")

    with pytest.raises(ValueError, match="кладёт сервер"):
        phonecart.progress({"store": "magnit", "basket": home["basket"], "planned": 2})


def test_the_server_does_not_refill_what_the_phone_already_filled(home, monkeypatch):
    """Сеть кладёт поверх лежащего: эту же корзину сервер второй раз не кладёт."""
    save_login()
    sent = []
    monkeypatch.setattr(cart, "deliver", lambda chain, phone, plan: sent.append(chain))
    monkeypatch.setattr("app.matcher.refresh_prices", lambda ids, stores: None)
    stamp = cartrun.stamp_of(home["basket"])
    phonecart.accept_report({"store": "magnit", "basket": home["basket"], "stamp": stamp,
                             "background": True,
                             "items": [{"name": "Молоко", "sku": "m-1", "ok": True, "how": "request"},
                                       {"name": "Сыр", "sku": "m-2", "ok": True, "how": "kept"}]})

    rows = {row.code: row for row in cartrun.start(PHONE, home["basket"], only="magnit", wait=True)}

    assert sent == [], "сервер не положил корзину, которую уже собрал телефон"
    assert rows["magnit"].by == cartrun.DEVICE and rows["magnit"].state == cartrun.DONE


def test_an_empty_plan_and_a_failure_without_a_report_end_the_row(home):
    phonecart.progress({"store": "dixy", "basket": home["basket"], "state": "empty",
                        "note": "позиции корзины этой сети незнакомы"})
    phonecart.progress({"store": "magnit", "basket": home["basket"], "state": "failed",
                        "note": "Магнит уводит на id.example — просит войти"})

    assert cartrun.get("dixy").state == cartrun.EMPTY
    failed = cartrun.get("magnit")
    assert failed.state == cartrun.FAILED and "просит войти" in failed.note
    assert not cartrun.alive(failed)


# ---------- отчёт фоновой сборки ----------

def test_what_already_lay_in_the_cart_is_landed_and_the_extra_is_named(home):
    stamp = cartrun.stamp_of(home["basket"])
    answer = phonecart.accept_report({
        "store": "magnit", "basket": home["basket"], "stamp": stamp, "background": True,
        "items": [{"name": "Молоко", "sku": "m-1", "ok": True, "how": "kept", "times": 2},
                  {"name": "Сыр", "sku": "m-2", "ok": True, "how": "request", "put": 2}],
        "extra": [{"name": "Кефир 1%", "sku": "m-9", "more": 1}],
        "page_totals": [{"kind": "total", "label": "Итого", "amount": 512.4}]})

    assert answer["landed"] == 2 and answer["planned"] == 2
    report = phonecart.last("magnit")
    assert report["items"][0]["how"] == "kept", "лежавшее с прошлой сборки — не «касание»"
    assert report["background"] is True and report["stamp"] == stamp
    row = cartrun.get("magnit")
    assert row.state == cartrun.DONE and len(row.landed) == 2
    assert row.stamp == stamp and row.basket_id == home["basket"]
    assert row.extra == [{"name": "Кефир 1%", "more": 1}]
    assert "само" in row.note and "не убирает" in row.note
    assert row.total.sum == 512.4


def test_a_stamp_that_is_not_a_stamp_is_dropped(home):
    phonecart.progress({"store": "magnit", "basket": home["basket"], "stamp": "<script>"})
    assert cartrun.get("magnit").stamp == ""


def test_progress_needs_the_owners_login(client):
    stranger = client.application.test_client()
    answer = stranger.post("/api/device/cartprogress", json={"store": "magnit"},
                           headers={"Accept": "application/json"})
    assert answer.status_code in (401, 403)


def test_progress_through_the_door(client, home):
    answer = client.post("/api/device/cartprogress",
                         json={"store": "magnit", "basket": home["basket"], "planned": 2, "at": 1,
                               "now": "кладу 2 из 2"})
    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json()["state"] == cartrun.GOING


def test_the_door_answers_400_when_the_server_fills_this_network(client, home, monkeypatch):
    monkeypatch.setattr(cart, "running", lambda chain: True)
    answer = client.post("/api/device/cartprogress", json={"store": "magnit", "basket": home["basket"]})
    assert answer.status_code == 400 and "кладёт сервер" in answer.get_json()["error"]


# ---------- экран корзины на телефоне ----------

def test_the_basket_gives_the_app_its_stamp_and_what_the_server_knows_of_logins(client, home):
    workspace()
    save_login("magnit")
    users.deactivate()

    page = client.get("/phone/basket", headers={"User-Agent": APP_UA}).data.decode("utf-8")

    assert "Корзина в магазинах" in page
    raw = page.split('data-collect="', 1)[1].split('"', 1)[0]
    import html
    payload = json.loads(html.unescape(raw))
    workspace()
    assert payload["basket"] == home["basket"] and payload["stamp"] == cartrun.stamp_of(home["basket"])
    chains = {c["code"]: c for c in payload["chains"]}
    assert chains["magnit"]["login"] == "" and chains["magnit"]["cookies"] == ["mg_at"]
    assert chains["pyaterochka"]["login"] == "connect"
    assert "соберу, пока открыта корзина" in page, "вошедшую сеть телефон соберёт сам"
    assert "Без входа:" in page


def test_the_rows_say_going_landed_and_the_networks_total(client, home):
    workspace()
    stamp = cartrun.stamp_of(home["basket"])
    phonecart.progress({"store": "magnit", "basket": home["basket"], "stamp": stamp,
                        "planned": 2, "at": 1, "now": "кладу 2 из 2: Сыр"})
    users.deactivate()

    going = client.get("/phone/basket/runs").get_json()
    assert going["going"] is True and going["stamp"] == stamp
    assert "собираю 1 из 2" in going["html"] and "кладу 2 из 2: Сыр" in going["html"]

    workspace()
    phonecart.accept_report({
        "store": "magnit", "basket": home["basket"], "stamp": stamp, "background": True,
        "items": [{"name": "Молоко", "sku": "m-1", "ok": True, "how": "tap"},
                  {"name": "Сыр", "sku": "m-2", "ok": False, "why": "нет в наличии у сети"}],
        "page_totals": [{"kind": "total", "label": "Итого", "amount": 178.0}]})
    users.deactivate()

    done = client.get("/phone/basket/runs").get_json()
    assert done["going"] is False
    assert "легло 1 из 2" in done["html"] and "178" in done["html"]
    assert ">сеть<" in done["html"], "итог прочитан у самой сети — так и подписан"
    assert "нет в наличии: 1" in done["html"]


def test_the_row_names_what_lies_in_the_networks_cart_beyond_the_basket(client, home):
    workspace()
    stamp = cartrun.stamp_of(home["basket"])
    phonecart.accept_report({
        "store": "magnit", "basket": home["basket"], "stamp": stamp, "background": True,
        "items": [{"name": "Молоко", "sku": "m-1", "ok": True, "how": "kept"}],
        "extra": [{"name": "Шоколад Алёнка 15г", "sku": "m-7", "more": 2}],
        "page_totals": [{"kind": "total", "label": "Итого", "amount": 120.0}]})
    users.deactivate()

    html_text = client.get("/phone/basket/runs").get_json()["html"]
    assert "лишнее, не убираю: Шоколад Алёнка 15г +2" in html_text


def test_a_result_for_the_basket_before_the_edit_is_shown_as_old(client, home):
    workspace()
    stamp = cartrun.stamp_of(home["basket"])
    phonecart.accept_report({
        "store": "magnit", "basket": home["basket"], "stamp": stamp, "background": True,
        "items": [{"name": "Молоко", "sku": "m-1", "ok": True}],
        "page_totals": [{"kind": "total", "label": "Итого", "amount": 178.0}]})
    repo.set_basket_item(home["basket"], home["milk"], 5.0)
    users.deactivate()

    html_text = client.get("/phone/basket/runs").get_json()["html"]
    assert "до правки корзины" in html_text, "итог прежней корзины не выдаётся за итог этой"


def test_the_page_asks_the_app_and_polls_only_inside_the_app(client, home):
    page = client.get("/phone/basket", headers={"User-Agent": APP_UA}).data.decode("utf-8")
    assert "KorzinaDevice.collect" in page and "data-poll" in page
    # Мост подключён с defer и встаёт после разбора страницы: без ожидания корзина не
    # уходила в приложение вовсе (эмулятор, 18.10.2026).
    assert "korzina-device.js') }}\" defer" not in page and " defer>" in page
    assert "addEventListener('DOMContentLoaded', kzRuns)" in page

    js = open(os.path.join(ROOT, "app", "web", "static", "korzina-device.js"), encoding="utf-8").read()
    assert "function collect(payload)" in js and "typeof k.collect !== 'function'" in js, \
        "старая сборка приложения без collect — не ошибка страницы, а null"
