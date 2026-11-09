"""Названия товаров для человека: строка чека Пятёрочки без обрубков, а не ключ поиска.

Владелец 31.10.2026: на «Корзине» и «Где дешевле» стояло «Сараф мор зеф эск ван гл 8 75 г» —
display_name (ключ поиска: без «%» и запятой, со сломанными сокращениями) показывали как название.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, repo  # noqa: E402
from app.db import init_db  # noqa: E402
from app.matcher.normalize import display_name, human_name, is_derived, shown_name  # noqa: E402
from app.models import Product  # noqa: E402


@pytest.mark.parametrize("raw, shown", [
    # четыре товара из заказа 21 августа, с которых началась жалоба
    ("САРАФ.Мор.ЗЕФ.эск.ван.гл.8% 75г", "Сараф. Мороженое зефир эскимо ванильное глазированное 8% 75 г"),
    ("СВИТЛ.Сырок твор.гл.с ван.45г", "Свитл. Сырок творожный глазированный с ванилью 45 г"),
    ("ТЁМА Биойог.бан/зем.об.дет.210г", "Тёма Биойогурт банан/земляника об. детский 210 г"),
    ("САРАФ.Мол.ул/паст.дет.2,5%970мл", "Сараф. Молоко ультрапастеризованное детское 2,5% 970 мл"),
    # род берётся от первого слова: сырок — «глазированный», мороженое — «глазированное»
    ("КЛИН.Сосиски КЛИНСКИЕ вар.405г", "Клин. Сосиски клинские варёные 405 г"),
    ("ТРОЕКУР.Шницель кур.руб.пан350г", "Троекур. Шницель куриный руб. пан 350 г"),
    ("PAPIA Бумага туал.бел.3сл.8рул", "Papia Бумага туалетная белая 3 слоя 8 рулонов"),
    # марка с инициалом, «д/» — родительный: дальше слова не раскрываем
    ("С.ЗЕЛ.Мол.ул/паст.3,2% 950мл", "С.Зел. Молоко ультрапастеризованное 3,2% 950 мл"),
    ("MAGGI Смесь НА ВТОР.д/котлет54г", "Maggi Смесь на втор. для котлет 54 г"),
    # после «с» слово в творительном падеже
    ("ЮБИЛ.Печенье вит.мол.с гл.232г", "Юбил. Печенье вит. мол. с глазурью 232 г"),
])
def test_receipt_abbreviations_are_expanded(raw, shown):
    assert human_name(raw) == shown


@pytest.mark.parametrize("raw, shown", [
    # и так читается: меняется только пробел перед единицей
    ("Сосиска в тесте 110г", "Сосиска в тесте 110 г"),
    ("Молоко Село Зеленое ультрапастеризованное 3.2% 950мл",
     "Молоко Село Зеленое ультрапастеризованное 3.2% 950 мл"),
    ("Пирог Mr.Food киш, мини, со шпинатом, 110 г", "Пирог Mr.Food киш, мини, со шпинатом, 110 г"),
    # служба и адреса — не названия товара
    ("Регистрация домена moikreativ.ru", "Регистрация домена moikreativ.ru"),
    ("MCP серверы для вайб кодинга", "MCP серверы для вайб кодинга"),
    ("02-006 Общий анализ мочи с микроскопией", "02-006 Общий анализ мочи с микроскопией"),
    ("Сидр ДАЛ.ДАЧА №2 ябл.п/сух.0.5л", "Сидр дал. дача №2 ябл. п/сух. 0,5 л"),
    ("", ""),
])
def test_readable_names_stay_as_written(raw, shown):
    assert human_name(raw) == shown


def test_unknown_abbreviations_are_not_guessed():
    # «мол» — молоко, молочный и молотый; «б/к» — без косточек и белокочанная
    assert human_name("МАРК.ПЕР.Орех мускатный мол.18г") == "Марк.Пер. Орех мускатный мол. 18 г"
    assert "б/к" in human_name("GL.VIL.SEL.Маслины б/к 160г")


def test_derived_survives_normalization_drift():
    raw = "ТЁМА Биойог.бан/зем.об.дет.210г"
    old_name = "Тема биойог бан зем об дет 210 г"       # заведён до того, как «об» стало служебным
    assert display_name(raw) != old_name
    assert is_derived(old_name, raw)
    assert not is_derived("Йогурт любимый", raw)
    assert not is_derived("Тема биойог бан зем дет 500 г", raw)       # другая фасовка — другой товар
    assert shown_name("Йогурт любимый", raw) == "Йогурт любимый"      # имя дал человек
    assert shown_name(None, raw) == human_name(raw)
    assert shown_name("Молоко", None) == "Молоко"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "h.db"))
    init_db()


def _bought(raw: str, name: str | None = None) -> int:
    """Товар с одной покупкой по строке чека; name — эталон (по умолчанию display_name строки)."""
    pid = repo.upsert_product(Product(id=0, name=name or display_name(raw), brand=None,
                                      weight_g=None, unit="pcs", category=None, active=True))
    repo.add_history_row("2026-08-21", None, pid, raw, 1, 100.0, 100.0)
    return pid


def test_basket_and_history_show_human_names_but_keep_the_key(db):
    raw = "САРАФ.Мор.ЗЕФ.эск.ван.гл.8% 75г"
    pid = _bought(raw)
    own = repo.upsert_product(Product(id=0, name="Мой любимый кефир", brand=None, weight_g=None,
                                      unit="pcs", category=None, active=True))
    basket = repo.create_basket("Тест")
    repo.set_basket_item(basket, pid, 1)
    repo.set_basket_item(basket, own, 1)

    items = {i["product_id"]: i for i in repo.basket_items(basket)}
    assert items[pid]["title"] == "Сараф. Мороженое зефир эскимо ванильное глазированное 8% 75 г"
    assert items[pid]["name"] == display_name(raw)            # ключ поиска не тронут
    assert items[own]["title"] == "Мой любимый кефир"         # без чеков — как назвал человек

    row = next(r for r in repo.list_history() if r["product_id"] == pid)
    assert row["product_name"] == items[pid]["title"]
    assert row["raw_name"] == raw


def test_history_row_without_product_gets_human_name(db):
    repo.add_history_row("2026-08-21", None, None, "ТЕМА Прод.БИОЛАКТ к/м 3,4% 206г", 1, 90.0, 90.0)
    assert repo.list_history()[0]["product_name"] == "Тема Продукт биолакт кисломолочный 3,4% 206 г"


def test_result_lines_carry_the_title(db):
    from app import service

    pid = _bought("СВИТЛ.Сырок твор.гл.с ван.45г")
    basket = repo.create_basket("Тест")
    repo.set_basket_item(basket, pid, 2)
    line = service.build_basket_lines(basket)[0]
    assert line.title == "Свитл. Сырок творожный глазированный с ванилью 45 г"
    assert line.name == display_name("СВИТЛ.Сырок твор.гл.с ван.45г")   # сопоставление идёт по эталону


def test_cart_plan_names_unknown_goods_by_title(db):
    from app import cartplan
    from app.models import BasketLine

    line = BasketLine(product_id=999, name="Сараф мор зеф", unit="pcs", qty=1, title="Сараф. Мороженое зефир")
    assert cartplan.build("magnit", [line], force=True).unknown == ["Сараф. Мороженое зефир"]


def test_cart_plan_line_reads_in_russian_when_the_chain_names_it_in_translit(db, monkeypatch):
    """Телефон сверяет название наряда с русским заголовком карточки: транслит адреса
    Перекрёстка не совпадал ни с одной карточкой (замер 09.11.2026, 0 из 8 за 82 с)."""
    from app import cartplan
    from app.models import BasketLine

    rows = {"perekrestok": "moloko selo zelenoe ultrapasterizovannoe 3 2 950ml",
            "magnit": "Молоко Село Зелёное ультрапастеризованное 3,2% 950 мл"}
    codes = {s.id: s.code for s in repo.list_stores()}
    monkeypatch.setattr(repo, "confirmed_mapping", lambda pid, store_id, real_only=False:
                        {"sku": "123", "raw_name": rows[codes[store_id]]})
    line = BasketLine(product_id=1, name="С зел мол ул паст 3 2 950 мл", unit="pcs", qty=1,
                      title="С.Зел. Молоко ультрапастеризованное 3,2% 950 мл")
    assert cartplan.build("perekrestok", [line], force=True).lines[0].name == line.title
    assert cartplan.build("magnit", [line], force=True).lines[0].name == rows["magnit"]   # своё русское — как в сети
    line.title = ""
    assert cartplan.build("perekrestok", [line], force=True).lines[0].name == line.name


def test_receipt_connector_still_matches_by_the_key(db, monkeypatch):
    """Цены Пятёрочки берутся из чеков: коннектор сверяет запрос (products.name) с ключом,
    а не со строкой для глаз, иначе «Сараф мор зеф…» перестало бы находить свою же строку чека."""
    from app.connectors import history

    monkeypatch.setattr(history.pricelist, "load", lambda code: [])
    raw = "САРАФ.Мор.ЗЕФ.эск.ван.гл.8% 75г"
    pid = repo.upsert_product(Product(id=0, name=display_name(raw), brand=None, weight_g=None,
                                      unit="pcs", category=None, active=True))
    repo.add_history_row("2026-08-21", repo.get_store("pyaterochka").id, pid, raw, 1, 100.0, 100.0)

    (row,) = repo.list_history()
    assert row["product_name"] == human_name(raw) and row["product_key"] == display_name(raw)

    found = history.PyaterochkaConnector().search(display_name(raw), 3)
    assert [c.sku for c in found] == [f"{history.PREFIX}{pid}"]
    assert found[0].name == display_name(raw) and found[0].score == 1.0
