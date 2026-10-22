"""Единая база и товары людей (решение владельца 17.10.2026).

Единая база — заранее сопоставленные товары всех сетей с постоянными номерами.
Товары из чеков находят себя в ней один раз, а артикулы сетей берут из неё: так
корзина собирается в любой сети, где база этот товар знает. Здесь сторожа правил:
постоянные номера и переадресации, штрихкод в одном виде, сокращения чека,
мультипак, сильная и слабая связь, выбор человека и его отказ.
"""
from __future__ import annotations

import pytest

from app import cartplan, config, repo, users
from app.catalog import link, match, refresh, store
from app.catalog.model import ChainProduct as P
from app.db import init_db
from app.matcher.normalize import gtin, gtin_trusted
from app.models import Product


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    link._SEEN.clear()
    yield


@pytest.fixture
def user_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "user.db"))
    init_db()


def ean13(head12: str) -> str:
    """Настоящий EAN-13 с верной контрольной цифрой — живые коды её всегда проходят."""
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(head12))
    return head12 + str((10 - total % 10) % 10)


MILK = ean13("460708435002")
OTHER = ean13("460708435003")


def stores() -> dict[str, int]:
    return {s.code: s.id for s in repo.list_stores()}


def product(name: str, **kw) -> int:
    return repo.upsert_product(Product(id=None, name=name, unit=kw.pop("unit", "pcs"), **kw))


# ---------- штрихкод в одном виде ----------
def test_gtin_brings_ean13_gtin14_upca_and_spaces_to_one_code():
    assert gtin(MILK) == gtin("0" + MILK) == "0" + MILK
    assert gtin(" ".join([MILK[:4], MILK[4:]])) == gtin(MILK)
    assert gtin("12345") is None and gtin(None) is None and gtin("00000000") is None
    upc = "036000291452"                    # UPC-A, тот же код в виде EAN-13 — с нулём впереди
    assert gtin(upc) == gtin("0" + upc)


def test_only_real_gtins_are_trusted():
    """Неверная контрольная цифра и внутренние коды магазинов ключом не служат."""
    assert gtin_trusted(gtin(MILK))
    assert not gtin_trusted(gtin(MILK[:-1] + str((int(MILK[-1]) + 1) % 10)))
    assert not gtin_trusted(gtin(ean13("200000000001")))   # весовая этикетка магазина
    assert not gtin_trusted(gtin(ean13("990000000001")))   # купон
    assert not gtin_trusted(None)


def test_receipt_gtin14_meets_catalog_ean13_in_one_item(catalog):
    store.upsert_products("metro", [P("117189", "Сахар песок aro 1 кг", barcode=MILK)])
    store.upsert_products("pyaterochka", [P("55", "Сахар-песок 1кг", barcode="0" + MILK)])
    refresh.match_all()
    rows = store.rows_by_gtin({gtin(MILK)})
    assert {r["chain"] for r in rows} == {"metro", "pyaterochka"}
    assert len({r["item_id"] for r in rows}) == 1


def test_catalog_migration_3_fills_gtin(catalog):
    store.upsert_products("metro", [P("1", "Сахар 1 кг", barcode=MILK)])
    with store.connect() as conn:
        conn.execute("UPDATE chain_products SET gtin=NULL")
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
    store.init()
    with store.connect() as conn:
        assert conn.execute("SELECT gtin FROM chain_products").fetchone()["gtin"] == "0" + MILK
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION


# ---------- постоянные номера единой базы ----------
def test_rebuild_keeps_item_numbers(catalog):
    store.upsert_products("lenta", [P("80424", "Молоко Простоквашино 2,5% 930мл")])
    store.upsert_products("magnit", [P("1", "Молоко Простоквашино 2,5% 930мл")])
    refresh.match_all()
    first = store.rows_by_skus([("lenta", "80424")])[0]["item_id"]
    store.upsert_products("vkusvill", [P("7", "Молоко Простоквашино 2,5% 930мл")])
    summary = refresh.match_all()
    assert store.rows_by_skus([("lenta", "80424")])[0]["item_id"] == first
    assert store.rows_by_skus([("vkusvill", "7")])[0]["item_id"] == first
    assert summary["kept"] >= 1


def test_merged_number_leads_to_the_surviving_item(catalog):
    """Две группы слились в одну — номер второй ведёт на первую, привязки не рвутся."""
    store.upsert_products("magnit", [P("2", "Кефир Домик в деревне 2,5% 900г", barcode=OTHER)])
    store.upsert_products("metro", [P("3", "Кефир Домик в деревне 2,5% 900г", barcode=MILK)])
    refresh.match_all()
    kept = store.rows_by_skus([("magnit", "2")])[0]["item_id"]
    lost = store.rows_by_skus([("metro", "3")])[0]["item_id"]
    assert lost != kept                       # разные штрихкоды — разные товары
    # Сеть исправила штрихкод — METRO склеилась с Магнитом. Смена штрихкода
    # сбрасывает привязку строки, но прежний номер помнится (prev_item_id).
    store.upsert_products("metro", [P("3", "Кефир Домик в деревне 2,5% 900г", barcode=OTHER)])
    store.upsert_products("lenta", [P("1", "Кефир Домик в деревне 2,5% 900г")])
    refresh.match_all()
    alive = store.rows_by_skus([("lenta", "1")])[0]["item_id"]
    assert alive == kept
    assert store.rows_by_skus([("metro", "3")])[0]["item_id"] == alive
    assert store.resolve_alias(lost) == alive
    assert store.resolve_alias(alive) == alive


# ---------- правила сравнения ----------
def test_multipack_is_not_one_piece():
    one = match.prepare({"id": 1, "chain": "a", "name": "Йогурт Чудо клубника 2,5% 125г"})
    four = match.prepare({"id": 2, "chain": "b", "name": "Йогурт Чудо клубника 2,5% 4x125г"})
    water = match.prepare({"id": 3, "chain": "a", "name": "Вода Шишкин лес 6*0,5л"})
    bottle = match.prepare({"id": 4, "chain": "b", "name": "Вода Шишкин лес 0,5л"})
    assert match.same_product(one, four)[0] is False
    assert match.same_product(water, bottle)[0] is False


def test_all_caps_name_gives_no_brand():
    """Строка целиком заглавными — не марка в каждом слове; ГОСТ и БЗМЖ маркой не бывают."""
    caps = match.prepare({"id": 1, "chain": "dixy", "name": "МОЛОКО ПАСТЕРИЗОВАННОЕ 2,5% 930МЛ БЗМЖ"})
    assert caps.brand_words == frozenset()
    lenta = match.prepare({"id": 2, "chain": "lenta", "name": "Колбаса Докторская ГОСТ МИРАТОРГ 400г"})
    assert "гост" not in lenta.brand_words and "мираторг" in lenta.brand_words


# ---------- товар человека находит себя в базе ----------
def test_receipt_barcode_brings_skus_of_three_chains(catalog, user_db):
    store.upsert_products("metro", [P("117189", "Напиток кисломолочный ТОНУС 930г", barcode=MILK,
                                      price=85.0)])
    store.upsert_products("lenta", [P("80424", "Напиток кисломолочный ТОНУС 930г", price=89.99)])
    store.upsert_products("magnit", [P("1000", "Напиток кисломолочный ТОНУС 930г")])
    assert refresh.match_all()["multi"] == 1
    pid = product("НАПИТОК МОЛ.ПАСТ 930", barcode="0" + MILK)

    report = link.link_products()

    assert (report.linked, report.strong) == (1, 1)
    ids = stores()
    assert repo.confirmed_mapping(pid, ids["metro"])["sku"] == "117189"
    assert repo.confirmed_mapping(pid, ids["lenta"])["sku"] == "80424"
    assert repo.confirmed_mapping(pid, ids["magnit"])["sku"] == "1000"
    assert repo.latest_price_for(pid, ids["lenta"])["price"] == 89.99
    assert repo.master_of(pid)["method"] == "barcode"


def test_bridge_from_a_confirmed_sku_reaches_other_chains(catalog, user_db):
    store.upsert_products("magnit", [P("1", "Сыр Ламбер 50% 230г")])
    store.upsert_products("lenta", [P("2", "Сыр Ламбер 50% 230г")])
    store.upsert_products("vkusvill", [P("3", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    pid = product("Что-то совсем другое по названию")
    sp = repo.upsert_store_product(stores()["magnit"], "1", "Сыр Ламбер 50% 230г")
    repo.confirm_mapping(pid, sp)                       # выбор человека в Магните

    link.link_products()

    assert repo.master_of(pid)["method"] == "bridge"
    assert repo.confirmed_mapping(pid, stores()["lenta"])["sku"] == "2"
    assert repo.confirmed_mapping(pid, stores()["vkusvill"])["sku"] == "3"


def test_receipt_abbreviations_find_the_product(catalog, user_db):
    store.upsert_products("lenta", [P("80424", "Молоко пастеризованное ПРОСТОКВАШИНО 2,5% 930мл",
                                      weight_g=930)])
    store.upsert_products("magnit", [P("5", "Коктейль молочный пастеризованный Простоквашино 2,5% 930мл")])
    refresh.match_all()
    pid = product("Паст простоквашино 2 5", weight_g=930)
    repo.add_history_row("2026-10-10", None, pid, "МОЛ.ПАСТ.ПРОСТОКВАШИНО 2,5% 930МЛ", 1, 89.99,
                         89.99, "k1")

    link.link_products()

    assert repo.master_of(pid)["method"] == "abbrev"
    assert repo.confirmed_mapping(pid, stores()["lenta"])["sku"] == "80424"
    assert repo.confirmed_mapping(pid, stores()["magnit"]) is None    # коктейль — не молоко


def test_different_fat_from_the_receipt_line_is_not_linked(catalog, user_db):
    """Эталон потерял процент, но строка чека его помнит: 3,2 % не станет 2,5 %."""
    store.upsert_products("lenta", [P("1", "Молоко пастеризованное ПРОСТОКВАШИНО 3,2% 930мл")])
    refresh.match_all()
    pid = product("Паст простоквашино", weight_g=930)
    repo.add_history_row("2026-10-10", None, pid, "МОЛ.ПАСТ.ПРОСТОКВАШИНО 2,5% 930МЛ", 1, 89.99,
                         89.99, "k1")
    link.link_products()
    assert repo.master_of(pid) is None


def test_weak_link_writes_candidates_that_never_reach_the_cart(catalog, user_db):
    """Вложение слов со вкусом — слабая связь: кандидат, но не корзина."""
    store.upsert_products("lenta", [P("10", "Йогурт АКТИВИА клубника 2,9% 150г")])
    store.upsert_products("magnit", [P("11", "Йогурт АКТИВИА клубника 2,9% 150г")])
    refresh.match_all()
    pid = product("Йогурт АКТИВИА 2,9% 150г")

    report = link.link_products()

    assert (report.linked, report.weak) == (1, 1)
    assert repo.confirmed_mapping(pid, stores()["lenta"]) is None
    plan = cartplan.build("lenta", [type("L", (), {"product_id": pid, "name": "йогурт", "qty": 1})()],
                          force=True)
    assert plan.lines == [] and plan.unknown == ["йогурт"]


def test_two_equal_candidates_stay_doubtful(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Батон нарезной 400г")])
    store.upsert_products("magnit", [P("2", "Батон нарезной 400г")])
    store.upsert_products("dixy", [P("3", "Батон нарезной 400г")])
    with store.connect() as conn:                      # две разные группы одного названия
        conn.execute("INSERT INTO catalog_items (id, name, norm, fnorm, created_at, updated_at)"
                     " VALUES (900, 'Батон нарезной 400г', 'батон нарезной', ' baton nareznoi ', 'x', 'x'),"
                     " (901, 'Батон нарезной 400г', 'батон нарезной', ' baton nareznoi ', 'x', 'x')")
        conn.execute("UPDATE chain_products SET item_id=900 WHERE sku IN ('1','2')")
        conn.execute("UPDATE chain_products SET item_id=901 WHERE sku='3'")
        conn.execute("INSERT INTO match_runs (started_at, finished_at) VALUES ('x', 'x')")
        conn.commit()
    product("Батон нарезной 400г")
    report = link.link_products()
    assert (report.linked, report.doubtful) == (0, 1)


# ---------- что единая база не трогает ----------
def test_link_keeps_a_human_choice(catalog, user_db):
    store.upsert_products("lenta", [P("80424", "Сыр Ламбер 50% 230г")])
    store.upsert_products("magnit", [P("1", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    pid = product("Сыр Ламбер 50% 230г")
    sp = repo.upsert_store_product(stores()["lenta"], "555", "Сыр Ламбер 50% 230г (выбор человека)")
    repo.confirm_mapping(pid, sp)

    link.link_products(force=True)

    assert repo.confirmed_mapping(pid, stores()["lenta"])["sku"] == "555"
    assert repo.confirmed_mapping(pid, stores()["magnit"])["sku"] == "1"


def test_a_dropped_link_does_not_come_back(catalog, user_db):
    store.upsert_products("lenta", [P("80424", "Сыр Ламбер 50% 230г")])
    store.upsert_products("magnit", [P("1", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    pid = product("Сыр Ламбер 50% 230г")
    link.link_products()
    assert repo.confirmed_mapping(pid, stores()["lenta"])["sku"] == "80424"

    repo.drop_mapping(pid, stores()["lenta"])            # «убрать» на «Связях»
    link.link_products(sync=True)

    assert repo.confirmed_mapping(pid, stores()["lenta"]) is None


def test_placeholder_does_not_close_the_chain_and_never_goes_to_the_cart(catalog, user_db):
    store.upsert_products("pyaterochka", [P("3670270", "Сыр Брест-Литовск сливочный 50% 200г")])
    store.upsert_products("lenta", [P("9", "Сыр Брест-Литовск сливочный 50% 200г")])
    refresh.match_all()
    pid = product("Сыр Брест-Литовск сливочный 50% 200г")
    ids = stores()
    sp = repo.upsert_store_product(ids["pyaterochka"], f"hist-{pid}", "СЫР БРЕСТ-ЛИТ.СЛИВ.200Г")
    repo.confirm_mapping(pid, sp)
    line = type("L", (), {"product_id": pid, "name": "сыр", "qty": 1})()
    assert cartplan.build("pyaterochka", [line], force=True).unknown == ["сыр"]

    link.link_products()

    assert repo.confirmed_mapping(pid, ids["pyaterochka"], real_only=True)["sku"] == "3670270"
    assert cartplan.build("pyaterochka", [line], force=True).lines[0].sku == "3670270"


def test_linked_products_are_rechecked_once_per_generation(catalog, user_db, monkeypatch):
    """Привязанный товар сверяется с базой раз на её поколение — и фоном, без кнопки."""
    store.upsert_products("lenta", [P("1", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    pid = product("Сыр Ламбер 50% 230г")
    link.link_products()
    calls = []
    monkeypatch.setattr(link, "find_master", lambda *a, **k: calls.append(a) or None)
    link.link_products()                                 # то же поколение — не трогаем
    assert calls == [] and repo.confirmed_mapping(pid, stores()["magnit"]) is None

    store.upsert_products("magnit", [P("2", "Сыр Ламбер 50% 230г")])
    refresh.match_all()                                  # актуализация базы — новое поколение
    link.link_products()                                 # вход или новый чек
    assert repo.confirmed_mapping(pid, stores()["magnit"])["sku"] == "2"
    assert calls == []                                   # сильная связь не ищется заново


# Когда запускается фоновый проход и что он не теряет — tests/test_unified_catalog_guards.py.

# ---------- отчёт: два знаменателя (замечание владельца 19.10.2026) ----------
PHONE = "79990000051"


@pytest.fixture
def workplaces(tmp_path, monkeypatch):
    """Рабочие места в стороне от живых data/users — для прогона инструмента целиком."""
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    yield tmp_path / "users"
    users.deactivate()


def history(pid: int, raw: str, chain: str | None, key: str) -> None:
    """Строка чека товара: chain=None — магазин не распознан (такси, платёж, маркетплейс)."""
    store_id = stores()[chain] if chain else None
    repo.add_history_row("2026-10-01", store_id, pid, raw, 1, 1.0, 1.0, key)


def test_report_counts_products_of_our_chains_separately(catalog, user_db):
    """«Всего товаров» и «товары наших сетей» — разные знаменатели."""
    store.upsert_products("lenta", [P("1", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    ours = product("Сыр Ламбер 50% 230г")
    taxi = product("Такси Яндекс")
    history(ours, "СЫР ЛАМБЕР 50% 230Г", "lenta", "k1")
    history(taxi, "ТАКСИ ЯНДЕКС", None, "k2")             # сети нет — не наш товар

    report = link.link_products()

    assert (report.tried, report.linked) == (2, 1)
    assert (report.network.tried, report.network.linked) == (1, 1)
    assert report.network.share() == 100.0
    assert report.network.as_dict()["unknown"] == 0


def test_share_of_our_chains_is_counted_over_them(catalog, user_db):
    """Доля привязки — по товарам наших сетей: не-продукты её не размывают."""
    store.upsert_products("lenta", [P("1", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    found = product("Сыр Ламбер 50% 230г")
    missing = product("Кефир Весёлый Молочник 1л")
    alien = product("Аренда квартиры")
    history(found, "СЫР ЛАМБЕР 50% 230Г", "lenta", "k1")
    history(missing, "КЕФИР ВЕСЕЛЫЙ МОЛОЧНИК 1Л", "lenta", "k2")
    history(alien, "АРЕНДА КВАРТИРЫ", None, "k3")

    report = link.link_products()

    assert (report.tried, report.linked) == (3, 1)         # всё, что искали
    assert (report.network.tried, report.network.linked) == (2, 1)
    assert round(report.network.share(), 1) == 50.0
    assert report.network.unknown == 1


def test_cli_prints_both_denominators(catalog, workplaces, capsys):
    """Владельцу печатаем оба числа словами: всего товаров и товары наших сетей."""
    store.upsert_products("lenta", [P("1", "Сыр Ламбер 50% 230г")])
    refresh.match_all()
    users.open_workspace(PHONE)
    try:
        ours = product("Сыр Ламбер 50% 230г")
        taxi = product("Такси Яндекс")
        history(ours, "СЫР ЛАМБЕР 50% 230Г", "lenta", "k1")
        history(taxi, "ТАКСИ ЯНДЕКС", None, "k2")
    finally:
        users.deactivate()

    assert link.main(["--phone", PHONE]) == 0

    out = capsys.readouterr().out
    assert "всего товаров 2" in out
    assert "из чеков наших сетей 1" in out
    assert "привязано 1, 100,0 %" in out
