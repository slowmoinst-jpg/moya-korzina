"""Корзина собирается в ТОЙ ЖЕ точке, по которой считали, — или не собирается.

ЧТО ЭТИ ПРОВЕРКИ СТОРОЖАТ. Наряд наполнял корзину в магазине, который витрина
выбрала себе сама. Замер 20.09.2026: чистое окно Магнита приходит с уже
поставленной кукой shopCode=%22992301%22, и то же молоко стоит в нём 84,99 ₽,
тогда как расчёт показывал человеку 89,99 ₽ по его точке. Корзина собиралась
настоящая, а сумма в ней — не та, которую человек видел, и заметить это он мог
только на кассе.

Дыра была тихой вдвойне: ни одна проверка не падала, потому что проверять было
нечего — про точку в наряде не было ни строчки.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.models import Location  # noqa: E402
from app.shopbrowser import point, signals  # noqa: E402

# Форматы взяты из живого замера 20.09.2026 (таблица в шапке app/shopbrowser/point.py).
MINI = Location(store_id="628425", shop_type="MM_MINI", delivery=True)
SHOP = Location(store_id="264856", shop_type="MM", delivery=True)


@pytest.fixture(autouse=True)
def no_wandering(monkeypatch):
    """Ни один тест отсюда не ходит в сеть за точкой: точку приносит сам тест."""
    import app.location as client_place

    monkeypatch.setattr(client_place, "for_store", lambda code: None)


# ---------- точка уезжает в окно ----------

def test_the_cart_is_filled_in_the_point_the_calculation_used():
    """Куки точки — те самые три, которыми витрина и выбирает магазин.

    Имена не выдуманы: они замерены 12.09.2026 запросами и записаны в
    app/connectors/magnit.py, а 20.09.2026 подтверждены уже на витрине — одна и
    та же карточка стоит 84,99 ₽ при shopCode 992301 и 89,99 ₽ при 264856.
    """
    got = {c["name"]: c["value"] for c in point.cookies_for("magnit", SHOP)}

    assert got == {"shopCode": '%22264856%22', "x_shop_type": "MM",
                   "nmg_dt": "DELIVERY_TYPE_DELIVERY"}


def test_the_code_goes_in_the_shops_own_form():
    """Код едет в кавычках, закодированных процентами, — как хранит его витрина.

    Сырые кавычки она тоже принимает и переписывает в свою форму, но класть
    сразу её — на одну догадку меньше. Сторож нужен потому, что подмена формы
    ничего не сломает видимо: страница откроется, просто в чужом магазине.
    """
    value = point.cookies_for("magnit", SHOP)[0]["value"]

    assert value == "%22264856%22"
    assert '"' not in value, "сырые кавычки: витрина перепишет их сама, а мы гадаем"


def test_the_point_from_the_calculation_beats_the_one_saved_in_the_window():
    """Точка расчёта побеждает точку из сохранённого входа.

    Человек мог открыть окно магазина неделю назад и с тех пор сменить адрес.
    Решает расчёт, потому что именно его сумму человек видел на экране.
    """
    old = {"cookies": [{"name": "shopCode", "value": "%22999999%22"},
                       {"name": "mg_at", "value": "ключ входа"}]}

    fresh = point.with_point(old, "magnit", SHOP)
    got = {c["name"]: c["value"] for c in fresh["cookies"]}

    assert got["shopCode"] == "%22264856%22"
    assert got["mg_at"] == "ключ входа", "вход потерян вместе с точкой"


def test_the_saved_login_is_not_touched():
    """Чужое состояние не правится на месте: оно живёт в рабочем месте человека."""
    old = {"cookies": [{"name": "shopCode", "value": "%22999999%22"}]}

    point.with_point(old, "magnit", SHOP)

    assert old["cookies"][0]["value"] == "%22999999%22"


def test_a_chain_without_a_measurement_is_left_alone():
    """Сети, чью куку точки никто не мерил, состояние не трогаем вовсе.

    Выдуманное имя куки ничего видимого не сломает — оно просто не сработает, а
    мы будем думать, что точка передана. Поэтому здесь молчание, а не догадка.
    """
    state = {"cookies": [{"name": "что-то", "value": "своё"}]}

    assert point.cookies_for("vkusvill", SHOP) == []
    assert point.with_point(state, "vkusvill", SHOP) is state


# ---------- точка, из которой заказать нельзя ----------

def test_a_shop_without_an_online_storefront_gets_no_cookies():
    """«Магнит у дома мини» работает только на полке — и точку туда не шлём.

    Замер 20.09.2026 на одной карточке: ME, MM, GM и DARKSTORE её открывают, а
    два разных MM_MINI в двух городах отвечают «Не удалось загрузить». Нехватка
    товара выглядит иначе — страница открывается и говорит «Нет в наличии», —
    значит дело в формате, а не в товаре и не в отдельном магазине.
    """
    assert point.served("magnit", SHOP) is True
    assert point.served("magnit", MINI) is False
    assert point.cookies_for("magnit", MINI) == []


def test_the_reason_says_that_waiting_will_not_help():
    """Человеку нужна другая точка, а не совет подождать.

    Это не сбой сети и не перегрузка: у формата нет интернет-витрины вовсе.
    Совет «попробуйте позже» здесь врал бы каждый раз, сколько ни пробуй.
    """
    why = point.trouble("magnit", MINI)

    assert "у дома мини" in why
    assert "не сбой" in why
    assert "позже" not in why.lower()


def test_a_usable_point_has_no_reason_at_all():
    """Где собрать можно — там молчим: лишняя оговорка читается как препятствие."""
    assert point.trouble("magnit", SHOP) == ""
    assert point.trouble("vkusvill", MINI) == ""


def test_an_unknown_point_does_not_stop_anything():
    """Точки нет вовсе — это не препятствие, а прежний порядок вещей.

    До 20.09.2026 наряд работал без точки всегда. Объявить это поломкой значило
    бы погасить передачу там, где она годами шла.
    """
    assert point.served("magnit", Location()) is True
    assert point.trouble("magnit", Location()) == ""


# ---------- «Не удалось загрузить» — не поломка вёрстки ----------

def test_the_page_that_never_opens_is_told_apart_from_a_blocked_one():
    """Два разных отказа, и советы у них противоположные.

    «Не удалось загрузить САЙТ» — сеть не отдала страницу, обновить имеет смысл.
    «Не удалось загрузить. Попробуйте обновить страницу» на карточке Магнита —
    у магазина нет интернет-витрины, и обновлять бесполезно навсегда. Слова
    «сайт» во второй нет, поэтому прежний сторож её не ловил.
    """
    mini = "Не удалось загрузить\nПопробуйте обновить страницу или зайдите позже"
    blocked = "Не удалось загрузить сайт"

    assert signals.no_showcase(mini) is True
    assert signals.no_showcase(blocked) is False
    assert signals.guard_kind(blocked) == "blocked"
    assert not signals.guard_kind(mini), "чужой сторож снова забрал эту страницу"


def test_a_working_card_is_not_mistaken_for_a_dead_one():
    """Обычная карточка сторожем не задевается — иначе передача встанет на ровном."""
    assert signals.no_showcase("Молоко Простоквашино 2.5% 930мл 89.99 ₽ В корзину") is False
