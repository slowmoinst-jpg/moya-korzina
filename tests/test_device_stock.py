"""Наличие из корзины человека: что легло, а чего сеть не дала.

РЕШЕНИЕ ВЛАДЕЛЬЦА 18.10.2026: «наличие и цены будем получать, формируя корзину в
фоне в каждой подключённой сети внутри приложения». Для Пятёрочки и Самоката это
единственный источник наличия: сервер к ним не пробивается ни с серверного адреса, ни с
домашнего, и ни каталога, ни коннектора у них нет. Здесь проверяется, что отчёт
телефона доходит до единой базы и что «нет в наличии» отличается от «не достучались».
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.catalog import refresh, store  # noqa: E402
from app.catalog.model import ChainProduct  # noqa: E402


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    store.upsert_products("samokat", [
        ChainProduct(sku="11", name="Товар 11"),
        ChainProduct(sku="12", name="Товар 12"),
    ])
    store.upsert_products("magnet", [ChainProduct(sku="21", name="Товар 21", in_stock=False)])
    return store


def stock_of(chain: str, sku: str):
    row = next((r for r in store.all_products(active_only=True)
                if r["chain"] == chain and str(r["sku"]) == sku), None)
    return row["in_stock"] if row else None


def test_a_landed_item_is_in_stock(base):
    changed = refresh.note_device_stock("samokat", [{"sku": "11", "ok": True, "why": ""}])

    assert changed == 1
    assert stock_of("samokat", "11") == 1


def test_the_network_said_there_is_none(base):
    changed = refresh.note_device_stock("samokat", [
        {"sku": "12", "ok": False,
         "why": "карточка говорит, что товара нет в наличии"}])

    assert changed == 1
    assert stock_of("samokat", "12") == 0


def test_other_reasons_do_not_lie_about_stock(base):
    """«Сеть отказала» и «не видно, что легло» наличия не говорят — его не трогаем."""
    changed = refresh.note_device_stock("samokat", [
        {"sku": "11", "ok": False, "why": "сеть отказала: кладу касаниями"},
        {"sku": "12", "ok": False, "why": "не видно, что легло: сеть не ответила корзиной"}])

    assert changed == 0
    assert stock_of("samokat", "11") is None
    assert stock_of("samokat", "12") is None


def test_placeholders_and_strangers_are_not_touched(base):
    """Заглушка сеть товаром не считает: по ней наличия не узнать."""
    changed = refresh.note_device_stock("samokat", [
        {"sku": "hist-1", "ok": True},
        {"sku": "samokat-2", "ok": True},
        {"sku": "999", "ok": True},
    ])

    assert changed == 0
    assert stock_of("samokat", "999") is None


def test_the_report_updates_stock_only_in_its_own_chain(base):
    refresh.note_device_stock("samokat", [{"sku": "21", "ok": True, "why": ""}])

    assert stock_of("samokat", "21") is None      # у Самоката такого артикула нет
    assert stock_of("magnet", "21") == 0          # строку Магнита отчёт не трогает


def test_one_report_does_not_flood_the_base(base, monkeypatch):
    monkeypatch.setattr(refresh, "DEVICE_STOCK_LIMIT", 2)
    items = [{"sku": f"9{n}", "ok": True} for n in range(50)]

    assert refresh.note_device_stock("samokat", items) == 0


def test_the_card_said_out_of_stock_though_the_click_failed(base):
    """Явное in_stock=false с карточки важнее причины, по которой позиция не легла.

    Z7h: «Нет в наличии/Больше нет» на карточке — это наличие, даже когда кнопки не
    нашлось и ok:false по другой причине. Прежний вывод из ok/why терял такой товар.
    """
    changed = refresh.note_device_stock("samokat", [
        {"sku": "12", "ok": False, "why": "на карточке не нашлось кнопки «в корзину»",
         "in_stock": False}])

    assert changed == 1
    assert stock_of("samokat", "12") == 0


def test_the_card_said_in_stock_though_the_click_failed(base):
    """Явное in_stock=true тоже важнее ok:false: на карточке товар есть."""
    changed = refresh.note_device_stock("samokat", [
        {"sku": "11", "ok": False, "why": "на карточке не нашлось кнопки «в корзину»",
         "in_stock": True}])

    assert changed == 1
    assert stock_of("samokat", "11") == 1


def test_without_the_field_stock_is_read_from_ok_and_why_as_before(base):
    """Старый отчёт без in_stock: прежняя логика — легло / «нет в наличии», прочее молчит."""
    changed = refresh.note_device_stock("samokat", [
        {"sku": "11", "ok": False, "why": "на карточке не нашлось кнопки «в корзину»"},
        {"sku": "12", "ok": False, "why": "карточка говорит, что товара нет в наличии"}])

    assert changed == 1
    assert stock_of("samokat", "11") is None
    assert stock_of("samokat", "12") == 0


def test_the_report_path_updates_stock_end_to_end(base):
    """Дорога целиком: отчёт телефона → наличие в единой базе."""
    from app import phonecart

    phonecart._stock({"store": "samokat", "items": [
        {"sku": "11", "ok": True, "why": "", "name": "Товар 11"},
        {"sku": "12", "ok": False, "name": "Товар 12",
         "why": "карточка говорит, что товара нет в наличии"},
        {"sku": "13", "ok": False, "name": "Товар 13", "why": "сеть отказала"},
    ]})

    assert stock_of("samokat", "11") == 1
    assert stock_of("samokat", "12") == 0
    assert stock_of("samokat", "13") is None
