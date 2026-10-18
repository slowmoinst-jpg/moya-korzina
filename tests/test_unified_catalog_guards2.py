"""Сторожа единой базы, второй круг проверки 18.10.2026.

Первый круг закрыл находки ревью; второй — 78 агентов, каждая новая находка
перепроверена скептиками — нашёл то, что внесли сами исправления или что они не
закрыли: эталон из строки чека терял «Н/» и «Б/», «6 шт 0,5 л» склеивалось с одной
бутылкой, «добавить из каталога» и «Узнать цены» обходили правила и отказ, спящий
номер держал товар вечно, замена в одной сети расходилась по другим.
"""
from __future__ import annotations

import pytest

from app import cartplan, config, repo
from app.catalog import link, match, refresh, store
from app.catalog.model import ChainProduct as P
from app.db import init_db
from app.importers.ofd_pdf import _ensure_product
from app.models import Candidate, Product

REAL_SOON = link.link_soon          # до подмены из tests/conftest.py


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    for memo in (link._SEEN, link._SYNCED, link._WEAK, link._GEN):
        memo.clear()
    yield
    for memo in (link._SEEN, link._SYNCED, link._WEAK, link._GEN):
        memo.clear()


@pytest.fixture
def user_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "user.db"))
    init_db()


def ean13(head12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(head12))
    return head12 + str((10 - total % 10) % 10)


MILK = ean13("460708435002")
OTHER = ean13("460708435003")


def ids() -> dict[str, int]:
    return {s.code: s.id for s in repo.list_stores()}


def bought(raw: str, qty: float = 1) -> int:
    """Товар, заведённый как при загрузке чека: эталон — display_name строки."""
    pid, _ = _ensure_product(raw, qty)
    repo.add_history_row("2026-10-10", None, pid, raw, qty, 99.0, 99.0, f"k-{pid}-{raw}")
    return pid


def product(name: str, **kw) -> int:
    return repo.upsert_product(Product(id=None, name=name, unit=kw.pop("unit", "pcs"), **kw))


def sku(pid: int, chain: str) -> str | None:
    found = repo.confirmed_mapping(pid, ids()[chain])
    return found["sku"] if found else None


def plan(chain: str, pid: int) -> list[str]:
    line = type("L", (), {"product_id": pid, "name": "товар", "qty": 1})()
    return [ln.sku for ln in cartplan.build(chain, [line], force=True).lines]


def item_of(chain: str, code: str) -> int | None:
    rows = store.rows_by_skus([(chain, code)])
    return rows[0]["item_id"] if rows else None


# ---------- эталон из строки чека и отрицания «Н/», «Б/» ----------
@pytest.mark.parametrize("receipt, wrong", [
    ("ВОДА AQUA MINERALE Н/ГАЗ 0,5Л", "Вода питьевая AQUA MINERALE газированная 0,5л"),
    ("ВОДА AQUA MINERALE Б/ГАЗ 0,5Л", "Вода питьевая AQUA MINERALE газированная 0,5л"),
    ("ЙОГУРТ ЭКОНИВА Б/САХ 2,5% 300Г", "Йогурт ЭКОНИВА с сахаром 2,5% 300г"),
    ("ПИВО ЖИГУЛИ БАРНОЕ Н/ФИЛЬТР 0,45Л", "Пиво ЖИГУЛИ БАРНОЕ фильтрованное 0,45л"),
    ("ПИВО БАЛТИКА СВЕТЛОЕ Б/А 0,45Л", "Пиво БАЛТИКА светлое 0,45л"),
])
def test_imported_negation_never_finds_the_opposite(catalog, user_db, receipt, wrong):
    store.upsert_products("lenta", [P("1", wrong)])
    store.upsert_products("magnit", [P("2", wrong)])
    refresh.match_all()
    pid = bought(receipt)

    link.link_products()

    assert sku(pid, "lenta") is None and sku(pid, "magnit") is None


def test_imported_still_water_finds_the_still_one(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Вода питьевая AQUA MINERALE негазированная 0,5л"),
                                    P("2", "Вода питьевая AQUA MINERALE газированная 0,5л")])
    refresh.match_all()
    pid = bought("ВОДА AQUA MINERALE Н/ГАЗ 0,5Л")

    link.link_products()

    assert sku(pid, "lenta") == "1"


def test_without_gas_slash_finds_without_gas(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Вода питьевая AQUA MINERALE без газа 0,5л")])
    refresh.match_all()
    pid = bought("ВОДА AQUA MINERALE Б/ГАЗ 0,5Л")

    link.link_products()

    assert sku(pid, "lenta") == "1"


# ---------- тара и надписи — одни правила для базы и людей ----------
def test_noise_glues_the_base_too(catalog, user_db):
    store.upsert_products("magnit", [P("1", "Вода Святой Источник 1,5л")])
    store.upsert_products("dixy", [P("2", "ВОДА СВЯТОЙ ИСТОЧНИК 1,5Л ПЭТ")])
    refresh.match_all()
    assert item_of("magnit", "1") == item_of("dixy", "2")
    pid = bought("ВОДА СВЯТОЙ ИСТОЧНИК 1,5Л")

    report = link.link_products()

    assert report.doubtful == 0
    assert sku(pid, "magnit") == "1" and sku(pid, "dixy") == "2"


@pytest.mark.parametrize("receipt", ["ВОДА БОРЖОМИ МИНЕРАЛЬНАЯ 0,5Л СТ/Б",
                                     "ВОДА БОРЖОМИ МИНЕРАЛЬНАЯ 0,5Л ПЛ/Б",
                                     "ВОДА БОРЖОМИ МИНЕРАЛЬНАЯ 0,5Л ЖБ"])
def test_tare_spelled_with_slashes_is_noise(catalog, user_db, receipt):
    store.upsert_products("lenta", [P("1", "Вода БОРЖОМИ минеральная 0,5л")])
    refresh.match_all()
    pid = bought(receipt)

    link.link_products()

    assert sku(pid, "lenta") == "1"


def test_milk_fat_substitute_is_not_dairy(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Сырок глазированный ПРОСТОКВАШИНО ваниль 40г с змж")])
    refresh.match_all()
    pid = bought("СЫРОК ГЛАЗ.ПРОСТОКВАШИНО ВАНИЛЬ 40Г БЗМЖ")

    link.link_products()

    assert sku(pid, "lenta") is None


def test_caps_name_with_lowercase_tare_gives_no_brand():
    for name in ("ПИВО СВЕТЛОЕ 0,45л ж/б", "МОЛОКО ПАСТЕРИЗОВАННОЕ 2,5% 930мл бзмж",
                 "СОК ЯБЛОЧНЫЙ 1л т/п", "ВОДА ПИТЬЕВАЯ 500 ml"):
        assert match.prepare({"id": 1, "chain": "dixy", "name": name}).brand_words == frozenset(), name
    lenta = match.prepare({"id": 2, "chain": "lenta", "name": "Сыр ЛАМБЕР 50% 230г"})
    assert "ламбер" in lenta.brand_words


# ---------- штуки и упаковки ----------
def test_one_bottle_receipt_never_gets_the_six_pack(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Вода питьевая ШИШКИН ЛЕС негазированная 0,5л")])
    store.upsert_products("magnit", [P("2", "Вода питьевая Шишкин лес негазированная 6 шт 0,5 л")])
    refresh.match_all()
    assert item_of("lenta", "1") != item_of("magnit", "2")
    pid = bought("ВОДА ПИТЬЕВАЯ ШИШКИН ЛЕС НЕГАЗИРОВАННАЯ 0,5Л")

    link.link_products()

    assert sku(pid, "magnit") is None and plan("magnit", pid) == []


@pytest.mark.parametrize("name, packs", [
    ("Чай черный Greenfield Golden Ceylon 25пак*2г", 25), ("Чай черный Greenfield 100х2г", 100),
    ("Чай черный Greenfield 2г x 25шт", 25), ("Чай Greenfield 25 пакетиков х 2 г", 25),
    ("Мешки для мусора 30 шт 50х60", 30), ("Губки для посуды 10x7x3 см 5 шт", 5),
    ("Фольга алюминиевая 29х10м", 1), ("antiperspirant rexona men 48h 150ml", 1),
    ("salfetki bumazhnye zewa 24kh24 sm 100sht", 100), ("pivo zhigulevskoe 4 0 5 st b", 1),
    ("Салфетки влажные 2х10шт", 2), ("Вода 6х0,5", 6),
])
def test_pack_count_second_round(name, packs):
    assert match.pack_count(name) == packs


def test_tea_25_and_100_bags_are_different_items(catalog):
    store.upsert_products("magnit", [P("1", "Чай черный GREENFIELD Golden Ceylon 25пак*2г")])
    store.upsert_products("lenta", [P("2", "Чай черный GREENFIELD Golden Ceylon 100х2г")])
    store.upsert_products("metro", [P("3", "Чай черный GREENFIELD Golden Ceylon 2г x 25шт")])
    refresh.match_all()
    assert item_of("magnit", "1") == item_of("metro", "3")
    assert item_of("lenta", "2") != item_of("magnit", "1")


# ---------- проверка строки: фасовка через якорь, весовое ----------
def test_anchor_does_not_pass_a_far_weight(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Молоко ПРОСТОКВАШИНО 2,5% 1л")])
    store.upsert_products("magnit", [P("2", "Молоко Простоквашино 2,5% 970мл", brand="Простоквашино")])
    store.upsert_products("dixy", [P("3", "Молоко Простоквашино 2,5% 950г", brand="Простоквашино")])
    refresh.match_all()
    pid = bought("МОЛОКО ПРОСТОКВАШИНО 2,5% 1Л")

    link.link_products()

    assert sku(pid, "dixy") is None


def test_weighed_goods_link_without_packaging(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Бананы", unit="kg")])
    store.upsert_products("magnit", [P("2", "Бананы весовые", unit="kg")])
    refresh.match_all()
    assert item_of("lenta", "1") == item_of("magnit", "2")
    pid = bought("БАНАНЫ ВЕС", qty=1.234)

    link.link_products()

    assert sku(pid, "lenta") == "1" and sku(pid, "magnit") == "2"


# ---------- выбор человека: замена не расходится, устаревший мост не ведёт ----------
def test_accepted_substitute_does_not_spread(catalog, user_db):
    from app import matcher

    store.upsert_products("lenta", [P("10", "Йогурт АКТИВИА клубника 2,9% 150г")])
    store.upsert_products("magnit", [P("11", "Йогурт АКТИВИА клубника 2,9% 150г")])
    store.upsert_products("vkusvill", [P("20", "Йогурт АКТИВИА 2,9% 150г")])
    refresh.match_all()
    pid = bought("ЙОГУРТ АКТИВИА 2,9% 150Г")
    link.link_products()
    assert sku(pid, "vkusvill") == "20"

    matcher.confirm(pid, "lenta", "10")              # обычного в Ленте нет — взял клубничный
    refresh.link_products()

    assert sku(pid, "lenta") == "10"                 # своя сеть — его выбор
    assert sku(pid, "magnit") is None                # но клубничный не разошёлся по сетям
    assert sku(pid, "vkusvill") == "20"


def test_sku_given_to_another_product_is_no_bridge(catalog, user_db):
    store.upsert_products("lenta", [P("A", "Кефир Простоквашино 930г")])
    store.upsert_products("magnit", [P("B", "Кефир Простоквашино 930г")])
    refresh.match_all()
    pid = bought("КЕФИР ПРОСТОКВАШИНО 930Г")
    sp = repo.upsert_store_product(ids()["magnit"], "B", "Кефир Простоквашино 930г")
    repo.confirm_mapping(pid, sp)                    # человек выбрал кефир Магнита
    link.link_products()
    assert sku(pid, "lenta") == "A"

    store.upsert_products("magnit", [P("B", "Ряженка Простоквашино 930г")])   # артикул отдан ряженке
    store.upsert_products("vkusvill", [P("V", "Ряженка Простоквашино 930г")])
    refresh.match_all()
    link.link_products()

    assert sku(pid, "vkusvill") is None and plan("vkusvill", pid) == []
    assert sku(pid, "lenta") == "A"


def test_own_barcode_wins_over_a_choice_elsewhere(catalog, user_db):
    store.upsert_products("metro", [P("1", "Молоко Простоквашино 2,5% 930мл", barcode=MILK)])
    store.upsert_products("magnit", [P("3", "Молоко Простоквашино 2,5% 930мл", barcode=OTHER)])
    refresh.match_all()
    assert item_of("metro", "1") != item_of("magnit", "3")
    pid = product("Молоко Простоквашино 2,5% 930мл", barcode=MILK, weight_g=930)
    link.link_products()
    assert sku(pid, "metro") == "1"
    sp = repo.upsert_store_product(ids()["magnit"], "3", "Молоко Простоквашино 2,5% 930мл", ean=OTHER)
    repo.confirm_mapping(pid, sp)                    # «Подобрать всё» или человек

    refresh.link_products()

    assert repo.master_of(pid)["master_id"] == item_of("metro", "1")
    assert sku(pid, "metro") == "1"


def test_bridge_card_out_for_the_night_keeps_the_link(catalog, user_db):
    store.upsert_products("magnit", [P("1", "Сыр Ламбер 50% 230г")])
    store.upsert_products("lenta", [P("2", "Сыр Ламбер 50% 230г")])
    store.upsert_products("vkusvill", [P("3", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    pid = product("Что-то совсем другое по названию")
    sp = repo.upsert_store_product(ids()["magnit"], "1", "Сыр Ламбер 50% 230г")
    repo.confirm_mapping(pid, sp)
    link.link_products()
    assert sku(pid, "lenta") == "2"

    with store.connect() as conn:                    # карточку Магнита обход не увидел
        conn.execute("UPDATE chain_products SET active=0 WHERE chain='magnit'")
        conn.commit()
    refresh.match_all()
    link.link_products()

    assert sku(pid, "lenta") == "2" and sku(pid, "vkusvill") == "3"


def test_background_pass_applies_a_new_choice_in_the_same_generation(catalog, user_db):
    store.upsert_products("magnit", [P("1", "Сыр Ламбер 50% 230г")])
    store.upsert_products("lenta", [P("2", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    pid = product("Что-то совсем другое по названию")
    assert link.link_products().unknown == 1
    sp = repo.upsert_store_product(ids()["magnit"], "1", "Сыр Ламбер 50% 230г")
    repo.confirm_mapping(pid, sp)

    link.link_products()                             # вход, без кнопки

    assert sku(pid, "lenta") == "2"

    repo.drop_mapping(pid, ids()["magnit"])          # человек убрал свой выбор
    link.link_products()

    assert sku(pid, "lenta") is None and repo.master_of(pid) is None


# ---------- отказ и «добавить из каталога» ----------
def test_dropped_chain_is_not_brought_back_by_price_asking(catalog, user_db):
    from app.web.screens.basket import _agrees_with_mapping

    store.upsert_products("lenta", [P("80424", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    pid = product("Сыр Ламбер 50% 230г", weight_g=230)
    link.link_products()
    repo.drop_mapping(pid, ids()["lenta"])
    offer = Candidate(store_code="lenta", sku="80424", name="Сыр Ламбер 50% 230г")

    assert _agrees_with_mapping(pid, offer) is False


def test_catalog_pick_confirms_only_the_picked_variant(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Йогурт АКТИВИА 2,9% 150г"),
                                    P("10", "Йогурт АКТИВИА клубника 2,9% 150г")])
    store.upsert_products("magnit", [P("2", "Йогурт АКТИВИА 2,9% 150г")])
    refresh.match_all()
    number = item_of("lenta", "10")
    assert number == item_of("magnit", "2")

    pid = refresh.adopt(number)                      # «＋» у «…клубника…» на «Корзине»

    assert sku(pid, "lenta") == "10"
    assert sku(pid, "magnit") is None and plan("magnit", pid) == []


def test_catalog_pick_respects_a_dropped_chain(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Сыр Ламбер 50% 230г")])
    store.upsert_products("magnit", [P("2", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    number = item_of("lenta", "1")
    pid = refresh.adopt(number)
    assert sku(pid, "magnit") == "2"
    repo.drop_mapping(pid, ids()["magnit"])

    refresh.adopt(number, product_id=pid)

    assert sku(pid, "magnit") is None


def test_base_never_overwrites_a_fresh_human_decision(user_db):
    pid = product("Сыр Ламбер 50% 230г")
    lenta, magnit = ids()["lenta"], ids()["magnit"]
    sp = repo.upsert_store_product(lenta, "10", "Сыр Ламбер 50% 230г")
    repo.confirm_mapping(pid, sp)                    # человек подтвердил, пока шёл проход
    repo.confirm_mapping(pid, sp, confirmed=False, method=link.MASTER)
    assert sku(pid, "lenta") == "10" and repo.mapping_method(pid, sp) is None

    repo.drop_mapping(pid, magnit)                   # отказ от сети, пока шёл проход
    sp2 = repo.upsert_store_product(magnit, "20", "Сыр Ламбер 50% 230г")
    repo.confirm_mapping(pid, sp2, confirmed=True, method=link.MASTER)
    assert sku(pid, "magnit") is None


# ---------- «Связи»: заглушка отдельно, кандидаты базы на виду ----------
def test_removing_a_receipt_price_is_not_a_refusal(user_db):
    pid = product("Сыр Ламбер 50% 230г")
    dixy = ids()["dixy"]
    stub = repo.upsert_store_product(dixy, f"hist-{pid}", "СЫР ЛАМБЕР 50% 230Г")
    repo.confirm_mapping(pid, stub)
    cand = repo.upsert_store_product(dixy, "555", "Сыр Ламбер 50% 230г")
    repo.confirm_mapping(pid, cand, confirmed=False, method=link.MASTER)

    repo.drop_mapping(pid, dixy)

    assert not repo.chain_rejected(pid, dixy)
    assert [c["sku"] for c in repo.base_candidates(pid, dixy)] == ["555"]


# ---------- спящий номер просыпается ----------
def test_relisted_product_moves_the_link_off_the_sleeping_number(catalog, user_db):
    store.upsert_products("lenta", [P("230", "Сыр ЛАМБЕР 50% 230г")])
    refresh.match_all()
    old = item_of("lenta", "230")
    pid = bought("СЫР ЛАМБЕР 50% 230Г")
    link.link_products()
    assert repo.master_of(pid)["master_id"] == old

    with store.connect() as conn:                    # Лента сняла 230 и выложила тот же сыр под 231
        conn.execute("UPDATE chain_products SET active=0 WHERE sku='230'")
        conn.commit()
    store.upsert_products("lenta", [P("231", "Сыр ЛАМБЕР 50% 230г")])
    store.upsert_products("magnit", [P("7", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    link.link_products()

    assert repo.master_of(pid)["master_id"] == item_of("lenta", "231")
    assert sku(pid, "lenta") == "231" and sku(pid, "magnit") == "7"


# ---------- номера: сброшенный голос — только тому же товару ----------
def test_renamed_last_row_does_not_carry_the_number(catalog):
    store.upsert_products("lenta", [P("A", "Кефир Простоквашино 2,5% 930г")])
    refresh.match_all()
    kefir = item_of("lenta", "A")

    store.upsert_products("lenta", [P("A", "Ряженка Простоквашино 4% 450г")])
    refresh.match_all()

    assert item_of("lenta", "A") != kefir
    assert store.resolve_alias(kefir) is None


def test_kefir_number_waits_for_its_sleeping_row(catalog):
    store.upsert_products("lenta", [P("A", "Кефир Простоквашино 2,5% 930г")])
    store.upsert_products("magnit", [P("B", "Кефир Простоквашино 2,5% 930г")])
    store.upsert_products("vkusvill", [P("C", "Ряженка Простоквашино 4% 450г")])
    refresh.match_all()
    kefir = item_of("magnit", "B")
    with store.connect() as conn:                    # обход Ленты не увидел A
        conn.execute("UPDATE chain_products SET active=0 WHERE sku='A'")
        conn.commit()
    refresh.match_all()
    store.upsert_products("magnit", [P("B", "Ряженка Простоквашино 4% 450г")])   # Магнит отдал B ряженке
    refresh.match_all()
    assert item_of("magnit", "B") != kefir

    store.upsert_products("lenta", [P("A", "Кефир Простоквашино 2,5% 930г")])    # кефир вернулся
    refresh.match_all()

    assert item_of("lenta", "A") == kefir


def test_sleeping_number_leads_to_its_twin(catalog):
    store.upsert_products("lenta", [P("A", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    cheese = item_of("lenta", "A")
    with store.connect() as conn:
        conn.execute("UPDATE chain_products SET active=0 WHERE sku='A'")
        conn.commit()
    refresh.match_all()
    store.upsert_products("magnit", [P("B", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    twin = item_of("magnit", "B")

    store.upsert_products("lenta", [P("A", "Сыр Ламбер 50% 230г")])
    refresh.match_all()

    alive = item_of("lenta", "A")
    assert store.resolve_alias(cheese) == alive and alive in (cheese, twin)


# ---------- заглушки в самом каталоге ----------
def test_reference_rows_in_the_catalog_never_reach_people(catalog, user_db):
    store.upsert_products("pyaterochka", [P("pyaterochka-kefir", "Кефир 3,2% 930г", price=10.0),
                                          P("3670270", "Кефир 3,2% 930г")])
    store.upsert_products("lenta", [P("9", "Кефир 3,2% 930г")])
    refresh.match_all()
    assert all(r["sku"] != "pyaterochka-kefir" for r in store.item_rows(item_of("lenta", "9")))


def test_migration_4_deletes_catalog_placeholders(catalog):
    store.upsert_products("pyaterochka", [P("pyaterochka-kefir", "Кефир 930г"), P("hist-7", "Кефир"),
                                          P("3670270", "Кефир 930г")])
    with store.connect() as conn:
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
    store.init()
    left = sorted(r["sku"] for r in store.all_products(active_only=False))
    assert left == ["3670270"]


# ---------- отчёт и честность силы связи ----------
def test_strong_link_without_any_confirmed_chain_is_written_weak(catalog, user_db):
    store.upsert_products("magnit", [P("101", "Молоко Простоквашино пастеризованное 2,5% 930мл")])
    store.upsert_products("lenta", [P("7", "Кефир ПРОСТОКВАШИНО 1% 930мл")])
    refresh.match_all()
    pid = bought("МОЛОКО ПРОСТОКВАШИНО 2,5% 930МЛ")

    report = link.link_products()

    assert sku(pid, "magnit") is None                # марку строки Магнита проверка не видит
    assert repo.master_of(pid)["method"] == "words?" and (report.strong, report.weak) == (0, 1)
    assert [c["sku"] for c in repo.base_candidates(pid, ids()["magnit"])] == ["101"]


def test_a_failed_write_is_counted_once(catalog, user_db, monkeypatch):
    store.upsert_products("lenta", [P("1", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    product("Сыр Ламбер 50% 230г", weight_g=230)

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(repo, "upsert_store_product", broken)
    report = link.link_products()

    assert (report.linked, report.failed) == (0, 1)
    assert report.tried == report.linked + report.doubtful + report.unknown + report.failed


def test_doubtful_stays_doubtful_on_the_second_pass(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Батон нарезной 400г")])
    store.upsert_products("magnit", [P("2", "Батон нарезной 400г")])
    store.upsert_products("dixy", [P("3", "Батон нарезной 400г")])
    with store.connect() as conn:
        conn.execute("INSERT INTO catalog_items (id, name, norm, fnorm, created_at, updated_at)"
                     " VALUES (900, 'Батон нарезной 400г', 'батон нарезной', ' baton nareznoi ', 'x', 'x'),"
                     " (901, 'Батон нарезной 400г', 'батон нарезной', ' baton nareznoi ', 'x', 'x')")
        conn.execute("UPDATE chain_products SET item_id=900 WHERE sku IN ('1','2')")
        conn.execute("UPDATE chain_products SET item_id=901 WHERE sku='3'")
        conn.execute("INSERT INTO match_runs (started_at, finished_at) VALUES ('x', 'x')")
        conn.commit()
    product("Батон нарезной 400г")

    assert link.link_products().doubtful == 1
    assert link.link_products().doubtful == 1


def test_auto_match_does_not_count_a_receipt_price_again(user_db, monkeypatch):
    from app import matcher

    pid = product("Молоко 1 л")
    five = ids()["pyaterochka"]
    stub = repo.upsert_store_product(five, f"hist-{pid}", "МОЛОКО 1Л")
    repo.confirm_mapping(pid, stub)
    monkeypatch.setattr(matcher, "find_candidates", lambda *a, **k: [
        Candidate(store_code="pyaterochka", sku=f"hist-{pid}", name="МОЛОКО 1Л", score=1.0)])

    report = matcher.auto_match([pid], ["pyaterochka"])

    assert report["auto"] == 0


# ---------- фоновый проход: память и кнопка ----------
def test_the_button_during_a_background_pass_asks_for_one_more_sync():
    phone = "79990000019"
    with link._RUNNING_LOCK:
        link._RUNNING.add(phone)
    try:
        assert link.running(phone)
        assert REAL_SOON(phone, sync=True) is False
        assert link._AGAIN.get(phone) is True
    finally:
        with link._RUNNING_LOCK:
            link._RUNNING.discard(phone)
            link._AGAIN.pop(phone, None)


def test_memory_of_old_generations_is_dropped():
    link._SEEN[("db", 1, 1, ())] = None
    link._SEEN[("db", 1, 2, ())] = None
    link._GEN["db"] = 1
    try:
        link._prune("db", 2)
        assert ("db", 1, 1, ()) not in link._SEEN and ("db", 1, 2, ()) in link._SEEN
    finally:
        link._SEEN.clear()
        link._GEN.clear()
