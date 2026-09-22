"""Подключение магазина и наряд на корзину.

Здесь защищаются две вещи, за которые потом дороже всего платить. Первая: у нас не
должно оказаться ни одного чужого секрета — вход живёт в браузере человека, а мы
храним только факт. Вторая: в корзину человека не должно попасть ничего, в чём мы
не уверены, — артикул берётся из подтверждённого сопоставления и ниоткуда больше.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import cartplan, config, repo, store_accounts  # noqa: E402
from app.db import init_db  # noqa: E402
from app.models import BasketLine  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "accounts.db"))
    init_db()


# ---------- подключение ----------
def test_nothing_is_connected_until_the_collector_says_so(db):
    """Подключение — наблюдаемый факт, а не наше намерение."""
    state = store_accounts.connection("magnit")
    assert state.connected is False and state.gives == ()
    assert not store_accounts.can("magnit", store_accounts.CART)


def test_collector_report_turns_the_connection_on(db):
    state = store_accounts.mark_connected("magnit", account="Карта 1234",
                                          gives=[store_accounts.PRICES, store_accounts.CART])
    assert state.connected is True
    assert state.account == "Карта 1234"
    assert store_accounts.can("magnit", store_accounts.CART)
    assert not store_accounts.can("magnit", store_accounts.HISTORY), \
        "умеет только то, что реально пришло, а не то, что обещано справочником"


def test_connection_keeps_the_first_date_and_moves_the_last_seen(db):
    first = store_accounts.mark_connected("magnit")
    second = store_accounts.mark_connected("magnit")
    assert second.connected_at == first.connected_at
    assert second.last_seen_at >= first.last_seen_at


def test_no_secret_is_ever_stored(db):
    """Ни пароля, ни кода из СМС, ни токена — они и не приходят к нам.

    Сторож грубый нарочно: если кто-то однажды решит «положить сюда токен, так
    удобнее», тест покраснеет раньше, чем это уедет на сервер.
    """
    store_accounts.mark_connected("magnit", account="Карта 1234")
    raw = repo.get_setting(store_accounts.KEY) or ""
    saved = json.loads(raw)
    forbidden = {"token", "access_token", "refresh_token", "password", "code",
                 "jwt", "cookie", "otp", "secret"}
    for record in saved.values():
        assert not (forbidden & set(record)), f"в подключении оказался секрет: {record}"
    assert "token" not in raw.lower()


def test_forget_removes_only_our_record(db):
    store_accounts.mark_connected("magnit")
    store_accounts.forget("magnit")
    assert store_accounts.connection("magnit").connected is False


def test_a_store_where_login_gives_nothing_is_refused(db):
    with pytest.raises(ValueError):
        store_accounts.mark_connected("azbuka")


def test_every_ability_has_a_human_name():
    """Список умений показывается человеку словами; безымянное умение — дыра в экране."""
    for ability in store_accounts.ABILITIES.values():
        for gift in ability.gives:
            assert gift in store_accounts.ABILITY_NAMES
        assert ability.entry.startswith("https://")


def test_connections_follow_the_store_order(db):
    codes = [c.store_code for c in store_accounts.connections()]
    assert set(codes) == set(store_accounts.ABILITIES)
    assert codes[0] == "magnit", "порядок берётся из справочника магазинов"


# ---------- наряд на корзину ----------
def _product_with_mapping(store_code: str, sku: str, name: str) -> int:
    from app.models import Product

    store = repo.get_store(store_code)
    product_id = repo.upsert_product(Product(id=None, name=name, unit="pcs"))
    sp = repo.upsert_store_product(store.id, sku, name, url=f"https://x/{sku}")
    repo.confirm_mapping(product_id, sp)
    return product_id


def test_plan_needs_a_connection(db):
    """Без входа корзину наполнять некуда — и это говорится словами, а не пустотой."""
    plan = cartplan.build("magnit", [BasketLine(product_id=1, name="Молоко", unit="pcs", qty=1)])
    assert not plan.ready
    assert "войдите" in plan.note.lower()


def test_plan_takes_sku_from_a_confirmed_mapping(db):
    store_accounts.mark_connected("magnit", gives=[store_accounts.CART])
    product_id = _product_with_mapping("magnit", "1899800733", "Молоко Простоквашино 930 мл")

    line = BasketLine(product_id=product_id, name="Молоко", unit="pcs", qty=2,
                      prices={"magnit": 179.98, "lenta": 210.0})
    plan = cartplan.build("magnit", [line])

    assert plan.ready
    assert plan.lines[0].sku == "1899800733"
    assert plan.lines[0].qty == 2
    assert plan.total == 179.98, "в наряд идёт цена этого магазина, а не соседнего"
    assert plan.as_dict()["store"] == "magnit"


def test_unmatched_positions_are_named_not_dropped(db):
    """Корзина из 14 позиций вместо 16 — нормально. Молчание о двух — нет."""
    store_accounts.mark_connected("magnit", gives=[store_accounts.CART])
    known = _product_with_mapping("magnit", "1899800733", "Молоко Простоквашино 930 мл")

    plan = cartplan.build("magnit", [
        BasketLine(product_id=known, name="Молоко", unit="pcs", qty=1, prices={"magnit": 90.0}),
        BasketLine(product_id=999, name="Страчателла", unit="pcs", qty=1, prices={"magnit": 280.0}),
    ])

    assert [line.sku for line in plan.lines] == ["1899800733"]
    assert plan.unknown == ["Страчателла"]
    assert "вне корзины" in plan.note


def test_plan_without_any_known_position_says_so(db):
    store_accounts.mark_connected("samokat", gives=[store_accounts.CART])
    plan = cartplan.build("samokat", [BasketLine(product_id=999, name="Творог", unit="pcs", qty=1)])
    assert not plan.ready and plan.unknown == ["Творог"]
    assert "нечего" in plan.note


def test_plan_carries_no_payment_or_order_command(db):
    """Наряд кончается наполненной корзиной. Оформление — дело человека."""
    store_accounts.mark_connected("magnit", gives=[store_accounts.CART])
    product_id = _product_with_mapping("magnit", "1899800733", "Молоко")
    body = json.dumps(cartplan.build(
        "magnit", [BasketLine(product_id=product_id, name="Молоко", unit="pcs", qty=1)]
    ).as_dict(), ensure_ascii=False).lower()

    for word in ("checkout", "order", "pay", "card", "оформ", "оплат"):
        assert word not in body, f"в наряде нашлось «{word}» — он должен кончаться корзиной"


def test_plan_does_not_take_a_price_from_another_store(db):
    """Цена соседнего магазина в наряде — тихая ложь: корзина сойдётся не с той суммой."""
    store_accounts.mark_connected("magnit", gives=[store_accounts.CART])
    product_id = _product_with_mapping("magnit", "1899800733", "Молоко")
    line = BasketLine(product_id=product_id, name="Молоко", unit="pcs", qty=1,
                      prices={"lenta": 210.0})

    plan = cartplan.build("magnit", [line])

    assert plan.lines[0].price is None
    assert plan.total == 0.0


def test_pending_cartplan_save_get_clear(db):
    product_id = _product_with_mapping("magnit", "1899800733", "Молоко")
    line = BasketLine(product_id=product_id, name="Молоко", unit="pcs", qty=1, prices={"magnit": 100.0})
    plan = cartplan.build("magnit", [line], force=True)
    assert plan.ready
    cartplan.save_pending("magnit", plan)

    got = cartplan.get_pending("magnit")
    assert got is not None
    assert got.store_code == "magnit"
    assert got.lines[0].sku == "1899800733"

    cartplan.clear_pending("magnit")
    assert cartplan.get_pending("magnit") is None


def test_only_the_networks_our_browser_reaches_promise_to_fill_a_cart():
    """«Наполнение корзины» обещают ровно там, где наш браузер доходит до витрины.

    ЗАМЕР 19.09.2026 настоящим Chromium с боевого сервера — то, из-за чего список
    и сократился. Открылись Магнит, ВкусВилл и METRO; Лента ответила «403. Доступ
    к сайту lenta.com запрещен» с нашим IP, Дикси — «Не удалось загрузить сайт»,
    Пятёрочка и Самокат закрыты по адресу так же. У METRO витрина открылась, но
    нажатие корзину не наполняет — ей нужен именной токен сети.

    Почему это сторож, а не комментарий: умение живёт в ABILITIES, а работает
    браузером, и разъехаться они могут молча. Тогда экран нарисует кнопку
    «Передать», которая способна только не сработать, — а человек будет ждать
    корзину и не соберёт её сам.

    Сокращать список бесконечно нельзя, и обратное тоже сторожится: Магнит и
    ВкусВилл обязаны умение СОХРАНИТЬ. Их витрины открываются, и тихо потерять
    единственную работающую автоматику было бы не меньшей бедой.
    """
    can_fill = {code for code, ability in store_accounts.ABILITIES.items()
                if store_accounts.CART in ability.gives}

    assert can_fill == {"magnit", "vkusvill"}, (
        "список сетей с наполнением корзины разошёлся с тем, что измерено живьём")

    # У каждой сети БЕЗ этого умения должно быть сказано, почему его нет: молчание
    # читается как «не сделали», а причина у всех названа и проверяема.
    for code, ability in store_accounts.ABILITIES.items():
        if store_accounts.CART in ability.gives:
            continue
        words = ability.note.lower()
        assert "корзин" in words, f"{code}: не сказано ни слова про корзину"
        assert ("адрес" in words or "токен" in words or "ссылк" in words), \
            f"{code}: не названа причина, по которой корзину не наполнить"
