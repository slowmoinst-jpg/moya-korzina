"""Сторожа единой базы по независимому ревью 18.10.2026.

Каждый тест — сценарий, который ревью воспроизвело на временных базах до правки:
не тот товар в корзине (клубничный йогурт вместо обычного, сметана 15 % вместо
20 %, колбаса по чужому внутреннему коду), потерянная связь (шум чека, спящий
номер, выбор человека, снятый кнопкой) и потерянный вызов фонового прохода.
"""
from __future__ import annotations

import threading
import time

import pytest

from app import cartplan, config, repo, users
from app.catalog import link, match, refresh, store
from app.catalog.model import ChainProduct as P
from app.db import init_db
from app.matcher.normalize import gtin
from app.models import Product

REAL_SOON = link.link_soon          # до подмены из tests/conftest.py


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    link._SEEN.clear()
    link._SYNCED.clear()
    yield
    link._SEEN.clear()
    link._SYNCED.clear()


@pytest.fixture
def user_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "user.db"))
    init_db()


def ean13(head12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(head12))
    return head12 + str((10 - total % 10) % 10)


MILK = ean13("460708435002")
OTHER = ean13("460708435003")
SUGAR = ean13("460123456789")


def ids() -> dict[str, int]:
    return {s.code: s.id for s in repo.list_stores()}


def product(name: str, receipt: str | None = None, **kw) -> int:
    pid = repo.upsert_product(Product(id=None, name=name, unit=kw.pop("unit", "pcs"), **kw))
    if receipt:
        repo.add_history_row("2026-10-10", None, pid, receipt, 1, 99.0, 99.0, f"k-{pid}")
    return pid


def sku(pid: int, chain: str) -> str | None:
    found = repo.confirmed_mapping(pid, ids()[chain])
    return found["sku"] if found else None


def plan(chain: str, pid: int) -> list[str]:
    line = type("L", (), {"product_id": pid, "name": "товар", "qty": 1})()
    return [ln.sku for ln in cartplan.build(chain, [line], force=True).lines]


# ---------- блокер: строка подтверждается по товару человека, а не по связи в базе ----------
def test_plain_yogurt_never_gets_the_strawberry_rows(catalog, user_db):
    store.upsert_products("lenta", [P("10", "Йогурт АКТИВИА клубника 2,9% 150г")])
    store.upsert_products("magnit", [P("11", "Йогурт АКТИВИА клубника 2,9% 150г")])
    store.upsert_products("pyaterochka", [P("12", "Йогурт АКТИВИА 2,9% 150г")])
    refresh.match_all()
    pid = product("Йогурт активиа 2 9 150", "ЙОГУРТ АКТИВИА 2,9% 150Г", weight_g=150)

    link.link_products()

    assert sku(pid, "lenta") is None and sku(pid, "magnit") is None
    assert plan("lenta", pid) == [] and plan("magnit", pid) == []


def test_bridge_from_plain_yogurt_does_not_confirm_strawberry(catalog, user_db):
    store.upsert_products("lenta", [P("10", "Йогурт АКТИВИА клубника 2,9% 150г")])
    store.upsert_products("magnit", [P("11", "Йогурт АКТИВИА клубника 2,9% 150г")])
    store.upsert_products("pyaterochka", [P("12", "Йогурт АКТИВИА 2,9% 150г")])
    refresh.match_all()
    pid = product("Йогурт активиа 2 9 150", "ЙОГУРТ АКТИВИА 2,9% 150Г", weight_g=150)
    sp = repo.upsert_store_product(ids()["pyaterochka"], "12", "Йогурт АКТИВИА 2,9% 150г")
    repo.confirm_mapping(pid, sp)                        # человек сам выбрал обычный

    link.link_products()

    assert sku(pid, "pyaterochka") == "12"
    assert sku(pid, "lenta") is None and sku(pid, "magnit") is None


def test_sour_cream_20_never_gets_15(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Сметана ПРОСТОКВАШИНО 15% 300г")])
    store.upsert_products("magnit", [P("2", "Сметана Простоквашино 300г")])
    refresh.match_all()
    pid = product("Сметана простоквашино 20 300", "СМЕТАНА ПРОСТОКВАШИНО 20% 300Г", weight_g=300)

    link.link_products()

    assert sku(pid, "lenta") is None
    assert plan("lenta", pid) == []


def test_bridge_through_a_row_without_percent_does_not_confirm_15(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Сметана ПРОСТОКВАШИНО 15% 300г")])
    store.upsert_products("magnit", [P("2", "Сметана Простоквашино 300г")])
    refresh.match_all()
    pid = product("Сметана простоквашино 20 300", "СМЕТАНА ПРОСТОКВАШИНО 20% 300Г", weight_g=300)
    sp = repo.upsert_store_product(ids()["magnit"], "2", "Сметана Простоквашино 300г")
    repo.confirm_mapping(pid, sp)                        # мост через строку без процента

    link.link_products()

    assert repo.master_of(pid)["method"] == "bridge"
    assert sku(pid, "lenta") is None


def test_internal_store_code_does_not_glue_cheese_and_sausage(catalog, user_db):
    internal = ean13("200000000001")                     # весовая этикетка, контрольная верна
    store.upsert_products("metro", [P("1", "Сыр Ламбер 50% 230г", barcode=internal)])
    store.upsert_products("pyaterochka", [P("2", "Колбаса докторская 230г", barcode=internal)])
    refresh.match_all()
    rows = {r["sku"]: r for r in store.all_products()}
    assert rows["1"]["item_id"] != rows["2"]["item_id"]
    pid = product("Сыр ламбер 50 230", "СЫР ЛАМБЕР 50% 230Г", weight_g=230)

    link.link_products()

    assert sku(pid, "pyaterochka") is None


def test_pick_takes_the_exact_row_not_the_best_scored_neighbour(catalog, user_db):
    store.upsert_products("lenta", [P("A", "Сыр ЛАМБЕР 50% 230г"),
                                    P("B", "Сыр ЛАМБЕР сливочный 50% 230г")])
    store.upsert_products("magnit", [P("M", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    rows = {r["sku"]: r for r in store.all_products()}
    assert rows["A"]["item_id"] == rows["B"]["item_id"] == rows["M"]["item_id"]
    pid = product("Сыр ламбер 50 230", "СЫР ЛАМБЕР 50% 230Г", weight_g=230)

    link.link_products()

    assert sku(pid, "lenta") == "A"


def test_same_ean_brother_is_confirmed_through_the_anchor(catalog, user_db):
    """Контроль: настоящий брат по штрихкоду подтверждается, даже если слова шире."""
    store.upsert_products("metro", [P("1", "Сахар песок 1кг", barcode=SUGAR)])
    store.upsert_products("lenta", [P("2", "Сахар-песок белый 1кг", barcode=SUGAR)])
    refresh.match_all()
    pid = product("Сахар песок 1 кг", "САХАР ПЕСОК 1КГ", weight_g=1000)
    sp = repo.upsert_store_product(ids()["metro"], "1", "Сахар песок 1кг")
    repo.confirm_mapping(pid, sp)                        # мост, у товара человека кода нет

    link.link_products()

    assert sku(pid, "lenta") == "2"


# ---------- строки единого товара не отменяют спор с самим товаром ----------
def test_receipt_20_links_to_the_20_item_not_doubtful(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Сметана ПРОСТОКВАШИНО 15% 300г")])
    store.upsert_products("magnit", [P("2", "Сметана Простоквашино 300г")])
    store.upsert_products("pyaterochka", [P("3", "Сметана ПРОСТОКВАШИНО 20% 300г")])
    refresh.match_all()
    pid = product("Сметана простоквашино 20 300", "СМЕТАНА ПРОСТОКВАШИНО 20% 300Г", weight_g=300)

    report = link.link_products()

    assert report.doubtful == 0
    assert sku(pid, "pyaterochka") == "3"
    assert sku(pid, "lenta") is None


def test_fat_guard_holds_with_a_silent_row_of_another_chain(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Молоко пастеризованное ПРОСТОКВАШИНО 3,2% 930мл")])
    store.upsert_products("magnit", [P("2", "Молоко пастеризованное Простоквашино 930мл",
                                       brand="Простоквашино")])
    refresh.match_all()
    pid = product("Паст простоквашино", "МОЛ.ПАСТ.ПРОСТОКВАШИНО 2,5% 930МЛ", weight_g=930)

    link.link_products()

    assert repo.master_of(pid) is None


# ---------- сокращения чека: «без X» — одно слово, сорт — слабая связь ----------
@pytest.mark.parametrize("receipt, catalog_name", [
    ("ВОДА AQUA MINERALE ГАЗ 0,5Л", "Вода питьевая AQUA MINERALE без газа 0,5л"),
    ("КОФЕ JACOBS МОЛ 250Г", "Кофе молотый JACOBS без кофеина 250г"),
    ("СОК ДОБРЫЙ АПЕЛЬСИН С МЯК 1Л", "Сок ДОБРЫЙ апельсиновый без мякоти 1л"),
    ("ВОДА AQUA MINERALE Н/ГАЗ 0,5Л", "Вода питьевая AQUA MINERALE газированная 0,5л"),
])
def test_a_stated_word_never_finds_its_negation(catalog, user_db, receipt, catalog_name):
    store.upsert_products("lenta", [P("1", catalog_name)])
    store.upsert_products("magnit", [P("2", catalog_name)])
    refresh.match_all()
    pid = product(receipt.title(), receipt)

    link.link_products()

    assert sku(pid, "lenta") is None and sku(pid, "magnit") is None


def test_with_both_waters_the_receipt_gets_the_sparkling_one(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Вода питьевая AQUA MINERALE без газа 0,5л"),
                                    P("2", "Вода питьевая AQUA MINERALE газированная 0,5л")])
    refresh.match_all()
    pid = product("Вода aqua minerale газ 500", "ВОДА AQUA MINERALE ГАЗ 0,5Л", weight_g=500)

    link.link_products()

    assert sku(pid, "lenta") == "2"


def test_explicit_without_still_links(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Вода питьевая AQUA MINERALE без газа 0,5л")])
    refresh.match_all()
    pid = product("Вода aqua minerale без газа 500", "ВОДА AQUA MINERALE БЕЗ ГАЗА 0,5Л",
                  weight_g=500)

    link.link_products()

    assert sku(pid, "lenta") == "1"


def test_abbreviation_silent_about_sort_is_weak(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Йогурт ДАНИССИМО натуральный 2,5% 130г")])
    refresh.match_all()
    pid = product("Йогурт данис 2 5 130", "ЙОГУРТ ДАНИС 2,5% 130Г", weight_g=130)

    link.link_products()

    master = repo.master_of(pid)
    assert master is not None and master["method"].endswith("?")
    assert sku(pid, "lenta") is None


# ---------- спор настоящих штрихкодов ----------
def test_same_name_with_another_real_barcode_stays_a_candidate(catalog, user_db):
    store.upsert_products("metro", [P("117189", "Напиток кисломолочный ТОНУС 930г", barcode=MILK)])
    store.upsert_products("lenta", [P("80424", "Напиток кисломолочный ТОНУС 930г")])
    refresh.match_all()
    pid = product("Напиток кисломолочный ТОНУС 930г", barcode="0" + OTHER, weight_g=930)

    link.link_products()

    assert sku(pid, "metro") is None and sku(pid, "lenta") is None
    assert repo.master_of(pid)["method"].endswith("?")


def test_row_with_another_real_barcode_inside_the_item_is_not_confirmed(catalog, user_db):
    store.upsert_products("metro", [P("1", "Напиток кисломолочный ТОНУС 930г", barcode=MILK)])
    store.upsert_products("lenta", [P("2", "Напиток кисломолочный ТОНУС 930г")])
    store.upsert_products("pyaterochka", [P("3", "Напиток кисломолочный ТОНУС 930г", barcode=OTHER)])
    refresh.match_all()
    pid = product("Напиток тонус 930", barcode=MILK, weight_g=930)

    link.link_products()

    assert sku(pid, "metro") == "1"
    assert sku(pid, "pyaterochka") is None


# ---------- мультипак со строк чека ----------
def test_six_pack_receipt_never_gets_one_bottle(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Вода Шишкин лес 0,5л", brand="Шишкин лес")])
    store.upsert_products("magnit", [P("2", "Вода Шишкин лес 0,5л", brand="Шишкин лес")])
    refresh.match_all()
    pid = product("Вода шишкин лес 6 500 г", "ВОДА ШИШКИН ЛЕС 6*0,5Л", weight_g=500)

    link.link_products()

    assert sku(pid, "lenta") is None and sku(pid, "magnit") is None


def test_six_pack_receipt_gets_the_pack(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Вода Шишкин лес 0,5л", brand="Шишкин лес"),
                                    P("3", "Вода питьевая Шишкин лес 6х0,5л", brand="Шишкин лес")])
    refresh.match_all()
    pid = product("Вода шишкин лес 6 500 г", "ВОДА ШИШКИН ЛЕС 6*0,5Л", weight_g=500)

    link.link_products()

    assert plan("lenta", pid) == ["3"]


@pytest.mark.parametrize("name, packs", [
    ("ЙОГУРТ ЧУДО 4 X 125Г", 4), ("ЙОГУРТ ЧУДО 4×125Г", 4), ("СОК 0,2Л X 3 ШТ", 3),
    ("avokado khass v upakovke 2sht", 2), ("yogurt chudo 4kh125g", 4),
    ("Салфетки бумажные 24x24 см 100шт", 100), ("Сосиски Мираторг 6 шт 300г", 6),
])
def test_pack_count_reads_every_writing(name, packs):
    assert match.pack_count(name) == packs


def test_a_pack_never_equals_one_piece():
    """По тексту не отличить общую фасовку пачки от фасовки одной штуки — не склеиваем."""
    pairs = [("Сосиски Мираторг Баварские 6 шт 300г", "Сосиски Мираторг Баварские 300г"),
             ("Пиво БАЛТИКА 7 0,45л 6шт", "Пиво БАЛТИКА 7 0,45л"),
             ("Вода питьевая Шишкин лес негазированная 6 шт 0,5 л", "Вода питьевая Шишкин лес негазированная 0,5л"),
             ("Сырок глазированный Б.Ю.АЛЕКСАНДРОВ 5 шт. 40 г", "Сырок глазированный Б.Ю.АЛЕКСАНДРОВ 40 г")]
    for many, one in pairs:
        a = match.prepare({"id": 1, "chain": "a", "name": many})
        b = match.prepare({"id": 2, "chain": "b", "name": one})
        assert match.same_product(a, b)[0] is False, many


def test_latin_names_meet_cyrillic_by_pieces(catalog):
    store.upsert_products("magnit", [P("1", "Авокадо Хасс в упаковке 2 шт"),
                                     P("2", "Творожок Чудо клубника 4х100г")])
    store.upsert_products("dixy", [P("3", "avokado khass v upakovke 2sht"),
                                   P("4", "tvorozhok chudo klubnika 4kh100g"),
                                   P("5", "Творожок Чудо клубника 100г")])
    refresh.match_all()
    rows = {r["sku"]: r for r in store.all_products()}
    assert rows["1"]["item_id"] == rows["3"]["item_id"]
    assert rows["2"]["item_id"] == rows["4"]["item_id"]
    assert rows["5"]["item_id"] != rows["2"]["item_id"]


# ---------- выбор человека переживает кнопку ----------
def test_confirmed_candidate_survives_the_link_button(catalog, user_db):
    from app import matcher

    store.upsert_products("lenta", [P("10", "Йогурт АКТИВИА клубника 2,9% 150г")])
    store.upsert_products("magnit", [P("11", "Йогурт АКТИВИА клубника 2,9% 150г")])
    refresh.match_all()
    pid = product("Йогурт АКТИВИА 2,9% 150г")
    link.link_products()
    assert repo.base_candidates(pid, ids()["lenta"])[0]["sku"] == "10"

    matcher.confirm(pid, "lenta", "10")                  # человек на «Связях»
    refresh.link_products()                              # «Связать с каталогом»

    assert sku(pid, "lenta") == "10"
    assert plan("lenta", pid) == ["10"]


def test_live_search_finding_the_same_sku_keeps_the_base_mark(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    pid = product("Сыр Ламбер 50% 230г", weight_g=230)
    link.link_products()
    mapping = repo.confirmed_mapping(pid, ids()["lenta"])

    repo.confirm_mapping(pid, mapping["id"])             # «Узнать цены» нашёл тот же артикул

    assert repo.mapping_method(pid, mapping["id"]) == link.MASTER


# ---------- слабая связь не замерзает ----------
def test_weak_link_becomes_strong_when_the_base_learns_more(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Сыр Ламбер 50%")])
    refresh.match_all()
    pid = product("Сыр Ламбер 50% 230г", weight_g=230)
    link.link_products()
    assert repo.master_of(pid)["method"].endswith("?")

    store.upsert_products("magnit", [P("2", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    link.link_products()                                 # вход в новом поколении базы

    assert repo.master_of(pid)["method"] == "words"
    assert sku(pid, "magnit") == "2"
    assert sku(pid, "lenta") is None                     # без фасовки — только кандидат


def test_weak_link_stays_weak_when_nothing_better(catalog, user_db):
    store.upsert_products("lenta", [P("10", "Йогурт АКТИВИА клубника 2,9% 150г")])
    refresh.match_all()
    pid = product("Йогурт АКТИВИА 2,9% 150г")
    link.link_products()
    before = repo.master_of(pid)

    refresh.match_all()
    link.link_products(sync=True)

    assert repo.master_of(pid)["master_id"] == before["master_id"]
    assert repo.master_of(pid)["method"].endswith("?")


# ---------- тара и надписи с обеих сторон ----------
@pytest.mark.parametrize("receipt, catalog_name", [
    ("СЫР ЛАМБЕР 50% 230Г БЗМЖ", "Сыр ЛАМБЕР 50% 230г"),
    ("ВОДА ШИШКИН ЛЕС 0,5Л ПЭТ", "Вода ШИШКИН ЛЕС 0,5л"),
    ("МОЛОКО ПРОСТОКВАШИНО 2,5% 930МЛ БЗМЖ", "Молоко пастеризованное ПРОСТОКВАШИНО 2,5% 930мл"),
    ("ВОДА СВЯТОЙ ИСТОЧНИК 1,5Л ПЭТ", "Вода Святой источник 1,5л"),
])
def test_receipt_noise_does_not_hide_the_product(catalog, user_db, receipt, catalog_name):
    store.upsert_products("lenta", [P("1", catalog_name)])
    refresh.match_all()
    pid = product(receipt.title(), receipt)

    link.link_products()

    assert sku(pid, "lenta") == "1"


def test_chain_row_with_noise_in_its_name_is_confirmed_too(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Сыр ЛАМБЕР 50% 230г")])
    store.upsert_products("dixy", [P("2", "СЫР ЛАМБЕР 50% 230Г БЗМЖ")])
    refresh.match_all()
    pid = product("Сыр ламбер 50 230", "СЫР ЛАМБЕР 50% 230Г", weight_g=230)

    link.link_products()

    assert sku(pid, "lenta") == "1" and sku(pid, "dixy") == "2"


def test_pasteurised_receipt_does_not_take_the_uht_milk(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Молоко ультрапастеризованное ПРОСТОКВАШИНО 2,5% 950г")])
    refresh.match_all()
    pid = product("Молоко пастеризованное простоквашино 2 5 930",
                  "МОЛОКО ПАСТЕРИЗОВАННОЕ ПРОСТОКВАШИНО 2,5% 930МЛ", weight_g=930)

    link.link_products()

    assert sku(pid, "lenta") is None


# ---------- память, --force, отказ от сети, снятый артикул ----------
def test_link_button_uses_a_bridge_confirmed_after_a_miss(catalog, user_db):
    store.upsert_products("magnit", [P("1", "Сыр Ламбер 50% 230г")])
    store.upsert_products("lenta", [P("2", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    pid = product("Что-то совсем другое по названию")
    assert link.link_products().unknown == 1             # фоновый проход не нашёл
    sp = repo.upsert_store_product(ids()["magnit"], "1", "Сыр Ламбер 50% 230г")
    repo.confirm_mapping(pid, sp)                        # человек нашёл сам

    refresh.link_products()

    assert repo.master_of(pid)["method"] == "bridge"
    assert sku(pid, "lenta") == "2"


def test_force_does_not_bridge_through_its_own_rows(catalog, user_db, monkeypatch):
    store.upsert_products("lenta", [P("1", "Сыр Ламбер 50% 230г")])
    store.upsert_products("magnit", [P("2", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    pid = product("Сыр Ламбер 50% 230г", weight_g=230)
    link.link_products()
    assert sku(pid, "lenta") == "1"
    monkeypatch.setattr(link, "_judge", lambda *a, **k: None)    # правила стали строже

    link.link_products(force=True)

    assert repo.master_of(pid) is None
    assert sku(pid, "lenta") is None and sku(pid, "magnit") is None


def test_dropped_chain_does_not_come_back_with_a_sibling_sku(catalog, user_db):
    store.upsert_products("lenta", [P("80424", "Сыр Ламбер 50% 230г", barcode=SUGAR),
                                    P("80999", "Сыр Ламбер 50% 230г", barcode=SUGAR)])
    store.upsert_products("magnit", [P("1", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    pid = product("Сыр Ламбер 50% 230г", weight_g=230)
    link.link_products()
    assert sku(pid, "lenta") in ("80424", "80999")

    repo.drop_mapping(pid, ids()["lenta"])
    refresh.link_products()

    assert sku(pid, "lenta") is None and plan("lenta", pid) == []
    assert sku(pid, "magnit") == "1"


def test_background_pass_respects_a_dropped_live_search_link(catalog, user_db):
    store.upsert_products("lenta", [P("80424", "Сыр Ламбер 50% 230г")])
    store.upsert_products("magnit", [P("1", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    pid = product("Сыр Ламбер 50% 230г", weight_g=230)
    sp = repo.upsert_store_product(ids()["lenta"], "80424", "Сыр Ламбер 50% 230г")
    repo.confirm_mapping(pid, sp)                        # живой поиск
    repo.drop_mapping(pid, ids()["lenta"])               # человек убрал

    link.link_products()

    assert sku(pid, "lenta") is None
    assert sku(pid, "magnit") == "1"


def test_retired_sku_loses_the_base_confirmation(catalog, user_db):
    store.upsert_products("lenta", [P("80424", "Сыр Ламбер 50% 230г")])
    store.upsert_products("magnit", [P("1", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    pid = product("Сыр Ламбер 50% 230г", weight_g=230)
    link.link_products()
    assert sku(pid, "lenta") == "80424"

    with store.connect() as conn:                        # полный обход Ленты его не увидел
        conn.execute("UPDATE chain_products SET active=0 WHERE chain='lenta'")
        conn.commit()
    refresh.match_all()
    link.link_products()

    assert sku(pid, "lenta") is None and plan("lenta", pid) == []
    assert sku(pid, "magnit") == "1"


# ---------- постоянные номера ----------
def test_renamed_row_does_not_steal_the_kefir_number(catalog):
    store.upsert_products("lenta", [P("A", "Кефир Простоквашино 2,5% 930г")])
    store.upsert_products("magnit", [P("B", "Кефир Простоквашино 2,5% 930г")])
    store.upsert_products("vkusvill", [P("C", "Ряженка Простоквашино 4% 450г")])
    refresh.match_all()
    kefir = store.rows_by_skus([("magnit", "B")])[0]["item_id"]
    ryazhenka = store.rows_by_skus([("vkusvill", "C")])[0]["item_id"]

    store.upsert_products("lenta", [P("A", "Ряженка Простоквашино 4% 450г")])   # артикул отдан другому
    refresh.match_all()

    assert store.rows_by_skus([("magnit", "B")])[0]["item_id"] == kefir
    assert store.rows_by_skus([("vkusvill", "C")])[0]["item_id"] == ryazhenka
    assert store.rows_by_skus([("lenta", "A")])[0]["item_id"] == ryazhenka
    assert store.resolve_alias(kefir) == kefir


def test_purer_group_keeps_the_number_when_rules_regroup(catalog):
    """Мультипак ушёл к мультипаку: одиночная штука сохраняет свой номер."""
    store.upsert_products("magnit", [P("B", "Йогурт Чудо клубника 125г")])
    store.upsert_products("dixy", [P("C", "Йогурт Чудо клубника 4x125г")])
    store.upsert_products("lenta", [P("A", "Йогурт Чудо клубника 4x125г")])
    with store.connect() as conn:                        # прежние правила склеили A с B
        conn.execute("INSERT INTO catalog_items (id, name, norm, created_at, updated_at)"
                     " VALUES (1, 'Йогурт Чудо клубника 125г', 'йогурт', 'x', 'x'),"
                     " (2, 'Йогурт Чудо клубника 4x125г', 'йогурт', 'x', 'x')")
        conn.execute("UPDATE chain_products SET item_id=1 WHERE sku IN ('A', 'B')")
        conn.execute("UPDATE chain_products SET item_id=2 WHERE sku='C'")
        conn.commit()

    refresh.match_all()

    assert store.rows_by_skus([("magnit", "B")])[0]["item_id"] == 1
    assert store.rows_by_skus([("lenta", "A")])[0]["item_id"] == 2


def test_sleeping_number_keeps_the_link_and_comes_back(catalog, user_db):
    store.upsert_products("lenta", [P("230", "Сыр Ламбер 50% 230г"), P("250", "Сыр Ламбер 50% 250г")])
    refresh.match_all()
    number = store.rows_by_skus([("lenta", "230")])[0]["item_id"]
    pid = product("Сыр Ламбер 50% 230г", weight_g=230)
    link.link_products()
    assert repo.master_of(pid)["master_id"] == number

    with store.connect() as conn:                        # ночь вне обхода
        conn.execute("UPDATE chain_products SET active=0 WHERE sku='230'")
        conn.commit()
    refresh.match_all()
    assert store.resolve_alias(number) == number         # номер спит, но жив
    refresh.link_products()                              # кнопка в окно отсутствия
    assert repo.master_of(pid)["master_id"] == number    # не ушёл к 250 г

    store.upsert_products("lenta", [P("230", "Сыр Ламбер 50% 230г")])            # вернулся
    refresh.match_all()
    assert store.rows_by_skus([("lenta", "230")])[0]["item_id"] == number
    link.link_products()
    assert sku(pid, "lenta") == "230"


# ---------- хранилище и правила склейки ----------
def test_gtin_follows_a_changed_barcode(catalog):
    store.upsert_products("metro", [P("1", "Сахар 1 кг", barcode=SUGAR)])
    store.upsert_products("metro", [P("1", "Сахар 1 кг", barcode="2400017")])
    with store.connect() as conn:
        row = conn.execute("SELECT barcode, gtin FROM chain_products").fetchone()
    assert row["barcode"] == "2400017" and row["gtin"] is None
    assert store.rows_by_gtin({gtin(SUGAR)}) == []


def test_lowercase_unit_does_not_turn_caps_into_brand():
    row = match.prepare({"id": 1, "chain": "dixy", "name": "МОЛОКО ПАСТЕРИЗОВАННОЕ 2,5% 930мл БЗМЖ"})
    assert row.brand_words == frozenset()
    wine = match.prepare({"id": 2, "chain": "dixy", "name": "ВИНО КРАСНОЕ СУХОЕ ШАТО ТАМАНЬ 12% 0,75л"})
    plain = match.prepare({"id": 3, "chain": "lenta", "name": "Вино красное сухое 0,75л"})
    assert match.same_product(wine, plain)[0] is False


def test_folded_search_does_not_need_every_receipt_word(catalog):
    store.upsert_products("lenta", [P("1", "Молоко ДОМИК В ДЕРЕВНЕ 2,5% 930мл")])
    refresh.match_all()
    for terms in (["moloko", "paster", "domik", "derevne"], ["paster", "derevne", "moloko"]):
        assert [r["name"] for r in store.search_folded(terms)] == ["Молоко ДОМИК В ДЕРЕВНЕ 2,5% 930мл"]


# ---------- одно правило заглушек ----------
def test_reference_csv_sku_is_a_placeholder_everywhere(user_db):
    pid = product("Огурцы 450г")
    magnit = ids()["magnit"]
    sp = repo.upsert_store_product(magnit, "magnit-ogurcy-450", "Огурцы 450г")
    repo.confirm_mapping(pid, sp)
    basket = repo.create_basket("Неделя")
    repo.set_basket_item(basket, pid, 1)

    assert repo.confirmed_mapping(pid, magnit, real_only=True) is None
    assert plan("magnit", pid) == []
    magnit_row = next(c for c in repo.coverage(basket) if c["code"] == "magnit")
    assert magnit_row["unknown"] == ["Огурцы 450г"]
    assert repo.mapping_matrix(real_only=True).get((pid, magnit)) is False


# ---------- фоновый проход: вызов во время прохода не теряется ----------
def test_receipt_import_starts_linking_for_this_workplace(tmp_path, monkeypatch):
    from app.importers import bundle
    from app.importers.ofd_pdf import Receipt, ReceiptRow

    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    users.open_workspace("79990000017")
    started = []
    monkeypatch.setattr(link, "link_soon", lambda phone: started.append(phone) or True)
    receipt = Receipt(date="2026-10-17", store_name="Магнит", total=89.99,
                      rows=[ReceiptRow(raw_name="МОЛОКО 2,5% 930МЛ", qty=1, unit_price=89.99,
                                       total=89.99)])
    try:
        bundle.store_receipts([{"key": "k-18", "receipt": receipt}], "magnit", "file")
    finally:
        users.deactivate()
    assert started == ["79990000017"]


def test_a_call_during_the_pass_runs_the_pass_again(catalog, tmp_path, monkeypatch):
    phone = "79990000018"
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    store.upsert_products("metro", [P("1", "Сахар песок 1кг", barcode=SUGAR),
                                    P("2", "Молоко Простоквашино 930мл", barcode=MILK)])
    refresh.match_all()
    users.open_workspace(phone)
    first = product("Сахар", barcode=SUGAR)
    users.deactivate()

    hold, entered = threading.Event(), threading.Event()
    real_find = link.find_master

    def slow_find(*args, **kwargs):
        entered.set()
        hold.wait(5)
        return real_find(*args, **kwargs)

    monkeypatch.setattr(link, "find_master", slow_find)
    assert REAL_SOON(phone) is True
    assert entered.wait(5)
    users.open_workspace(phone)
    second = product("Молоко", barcode=MILK)            # пачка чеков пришла во время прохода
    users.deactivate()
    assert REAL_SOON(phone) is False                     # поток уже идёт — вызов запомнен
    hold.set()
    for thread in [t for t in threading.enumerate() if t.name.startswith("catalog-link-")]:
        thread.join(10)
    deadline = time.time() + 10
    while phone in link._RUNNING and time.time() < deadline:
        time.sleep(0.05)

    users.open_workspace(phone)
    try:
        assert repo.master_of(first)["method"] == "barcode"
        assert repo.master_of(second)["method"] == "barcode"
    finally:
        users.deactivate()
