"""Тесты сверки артикулов X5 (Пятёрочка и Перекрёсток)."""
from __future__ import annotations

import pytest

from app import cartplan, config, repo
from app.catalog import fromurl, link, store, x5
from app.catalog.model import ChainProduct
from app.db import init_db
from app.models import Product


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    link._SEEN.clear()
    link._SYNCED.clear()
    x5._DONE.clear()
    yield
    link._SEEN.clear()
    link._SYNCED.clear()
    x5._DONE.clear()


@pytest.fixture
def user_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "user.db"))
    init_db()
    x5._DONE.clear()
    yield
    x5._DONE.clear()


def ids() -> dict[str, int]:
    return {s.code: s.id for s in repo.list_stores()}


def plan(chain: str, pid: int) -> list[str]:
    line = type("L", (), {"product_id": pid, "name": "товар", "qty": 1})()
    return [ln.sku for ln in cartplan.build(chain, [line], force=True).lines]


def test_strict_pair_links_both_and_builds_cartplan(catalog, user_db):
    """Строгая пара: артикул подтверждён в обеих сетях, у 5ки адрес со слагом."""
    store.upsert_products("perekrestok", [
        ChainProduct(
            "4212867",
            "biolakt tema klubnika banan s 8 mesacev 3 206g",
            weight_g=206.0,
            unit="pcs",
            url="https://www.perekrestok.ru/cat/58/p/biolakt-4212867",
        )
    ])
    store.upsert_products("pyaterochka", [
        ChainProduct(
            "4212867",
            "Биолакт Тёма клубника банан 206г",
            weight_g=206.0,
            unit="pcs",
            url="https://5ka.ru/product/biolakt-tema-klubnika-banan-s-8-mes-3-bzmzh-206g--4212867/",
        )
    ])
    pid = repo.upsert_product(
        Product(
            id=None,
            name="Биолакт детский клубника банан тема 3 206 г",
            brand="Тема",
            weight_g=206.0,
            unit="pcs",
        )
    )
    repo.add_history_row("2026-10-10", ids()["perekrestok"], pid, "Биолакт детский", 1, 99.0, 99.0, "k-1")

    res = x5.fill()
    assert res["picked"] == 1
    assert res["soft"] == 0
    assert res["perekrestok"] == 1
    assert res["pyaterochka"] == 1

    maps = {m["store_code"]: m for m in repo.product_mappings(pid)}
    assert maps["perekrestok"]["sku"] == "4212867"
    assert maps["perekrestok"]["method"] == "x5"
    assert maps["pyaterochka"]["sku"] == "4212867"
    assert maps["pyaterochka"]["method"] == "x5"

    sp_5 = repo.confirmed_mapping(pid, ids()["pyaterochka"], real_only=True)
    assert sp_5["url"] == "https://5ka.ru/product/biolakt-tema-klubnika-banan-s-8-mes-3-bzmzh-206g--4212867/"
    assert plan("pyaterochka", pid) == ["4212867"]


def test_soft_pair_finds_sku(catalog, user_db):
    """Мягкая пара: лишние мягкие слова позволяют сопоставить товар."""
    store.upsert_products("perekrestok", [
        ChainProduct(
            "4441269",
            "biojogurt tema s malinoj obogasennyj bifidobakteriami s 8 mesacev 2 5 210g",
            weight_g=210.0,
            unit="pcs",
        )
    ])
    p = Product(
        id=1,
        name="Биойогурт детский со вкусом малины тема 2 5 210 г",
        brand="Тема",
        weight_g=210.0,
        unit="pcs",
    )
    with store.connect() as conn:
        idx = x5.build_index(conn)
        res = x5.find_sku(p, idx)
    assert res == ("4441269", True)


def test_different_weight_rejected_matching_accepted(catalog, user_db):
    """Другой вес отсеивается, правильный вес сопоставляется."""
    store.upsert_products("perekrestok", [
        ChainProduct("5001", "kefir prostokvasino 3 2 bzmz 500g", weight_g=500.0)
    ])
    p = Product(
        id=1,
        name="Кефир ПРОСТОКВАШИНО 3,2%, без змж, 930г",
        brand="Простоквашино",
        weight_g=930.0,
    )
    with store.connect() as conn:
        idx = x5.build_index(conn)
        assert x5.find_sku(p, idx) is None

    store.upsert_products("perekrestok", [
        ChainProduct("97452", "kefir prostokvasino 3 2 bzmz 930g", weight_g=930.0)
    ])
    with store.connect() as conn:
        idx2 = x5.build_index(conn)
        assert x5.find_sku(p, idx2) == ("97452", False)


def test_duplicate_rows_with_different_sku_are_ambiguous(catalog, user_db):
    """Две одинаковые строки Перекрёстка с разными sku дают неоднозначность."""
    store.upsert_products("perekrestok", [
        ChainProduct("111", "kefir prostokvasino 3 2 bzmz 930g", weight_g=930.0),
        ChainProduct("222", "kefir prostokvasino 3 2 bzmz 930g", weight_g=930.0),
    ])
    p = Product(
        id=1,
        name="Кефир ПРОСТОКВАШИНО 3,2%, без змж, 930г",
        brand="Простоквашино",
        weight_g=930.0,
    )
    with store.connect() as conn:
        idx = x5.build_index(conn)
        assert x5.find_sku(p, idx) is None


def test_baby_and_adult_distinction(catalog, user_db):
    """Детский товар не путается со взрослым при одинаковой марке и граммовке."""
    store.upsert_products("perekrestok", [
        ChainProduct("3373718", "voda pitevaa svatoj istocnik negazirovannaa 5l", weight_g=5000.0),
        ChainProduct("3622220", "voda detskaa svatoj istocnik svetlacok negazirovannaa 5l", weight_g=5000.0),
    ])
    p_adult = Product(
        id=1,
        name="Вода святой источник без газа 5 кг",
        brand="Святой источник",
        weight_g=5000.0,
    )
    p_baby = Product(
        id=2,
        name="Вода святой источник светлячок для детей без газа с рождения 5 кг",
        brand="Святой источник",
        weight_g=5000.0,
    )
    with store.connect() as conn:
        idx = x5.build_index(conn)
        assert x5.find_sku(p_adult, idx) == ("3373718", False)
        assert x5.find_sku(p_baby, idx) == ("3622220", False)


def test_gas_conflict_is_rejected(catalog, user_db):
    """Негазированная вода не сопоставляется с газированной."""
    store.upsert_products("perekrestok", [
        ChainProduct("3373718", "voda pitevaa svatoj istocnik gazirovannaa 5l", weight_g=5000.0)
    ])
    p = Product(
        id=1,
        name="Вода святой источник без газа 5 кг",
        brand="Святой источник",
        weight_g=5000.0,
    )
    with store.connect() as conn:
        idx = x5.build_index(conn)
        assert x5.find_sku(p, idx) is None


def test_pyaterochka_name_in_catalog_is_not_evidence(catalog, user_db):
    """Имя строки Пятёрочки берётся строго из слага адреса, а не из поля name каталога."""
    store.upsert_products("pyaterochka", [
        ChainProduct(
            "4210938",
            "Чипсы Lay's рифлёные сметана и лук 140 г",
            weight_g=140.0,
            url="https://5ka.ru/product/chipsy-lays-s-solyu-150g--4210938/",
        )
    ])
    p = Product(
        id=1,
        name="Чипсы Lay's рифлёные сметана и лук 140 г",
        brand="Lay's",
        weight_g=140.0,
    )
    with store.connect() as conn:
        idx = x5.build_index(conn)
        assert x5.find_sku(p, idx) is None


def test_pyaterochka_evidence_requirement(catalog, user_db):
    """Пятёрочка пишется только при наличии довода продажи (наличие в 5ке или чек 5ки)."""
    store.upsert_products("perekrestok", [
        ChainProduct("998877", "smetana prostokvasino 20 300g", weight_g=300.0)
    ])
    pid = repo.upsert_product(
        Product(
            id=None,
            name="Сметана Простоквашино 20% 300 г",
            brand="Простоквашино",
            weight_g=300.0,
        )
    )
    # Покупка только в Перекрёстке
    repo.add_history_row("2026-10-10", ids()["perekrestok"], pid, "Сметана", 1, 99.0, 99.0, "k-p")

    res = x5.fill()
    assert res["perekrestok"] == 1
    assert res["pyaterochka"] == 0
    assert res["no_evidence"] == 1
    assert repo.confirmed_mapping(pid, ids()["perekrestok"], real_only=True) is not None
    assert repo.confirmed_mapping(pid, ids()["pyaterochka"], real_only=True) is None

    # Добавляем чек из Пятёрочки
    repo.add_history_row("2026-10-11", ids()["pyaterochka"], pid, "Сметана", 1, 99.0, 99.0, "k-5")

    res2 = x5.fill(ignore_done=True)
    assert res2["already"] == 1
    assert res2["pyaterochka"] == 1
    assert res2["no_evidence"] == 0

    sp_5 = repo.confirmed_mapping(pid, ids()["pyaterochka"], real_only=True)
    assert sp_5 is not None
    assert sp_5["sku"] == "998877"
    assert sp_5["url"] == "https://5ka.ru/product/--998877/"


def test_human_mapping_integrity_and_reject(catalog, user_db):
    """Человеческое решение не затирается, а после отказа сеть пропускается."""
    store.upsert_products("perekrestok", [
        ChainProduct("4212867", "biolakt tema klubnika banan s 8 mesacev 3 206g", weight_g=206.0, unit="pcs")
    ])
    store.upsert_products("pyaterochka", [
        ChainProduct("4212867", "biolakt tema klubnika banan", weight_g=206.0, unit="pcs",
                     url="https://5ka.ru/product/biolakt-tema-klubnika-banan-s-8-mes-3-bzmzh-206g--4212867/")
    ])
    pid = repo.upsert_product(
        Product(
            id=None,
            name="Биолакт детский клубника банан тема 3 206 г",
            brand="Тема",
            weight_g=206.0,
            unit="pcs",
        )
    )
    repo.add_history_row("2026-10-10", ids()["perekrestok"], pid, "Биолакт", 1, 99.0, 99.0, "k-p")

    # Человек подтвердил другой артикул в Пятёрочке без method
    sp_other = repo.upsert_store_product(
        ids()["pyaterochka"],
        "55555",
        "Другой биолакт",
        url="https://5ka.ru/product/--55555/",
    )
    repo.confirm_mapping(pid, sp_other, confirmed=True, method=None)

    res = x5.fill(ignore_done=True)
    assert res["differs"] == 1
    cur = repo.confirmed_mapping(pid, ids()["pyaterochka"], real_only=True)
    assert cur["sku"] == "55555"

    # Человек убрал связь с сетью (отказ)
    repo.drop_mapping(pid, ids()["pyaterochka"])

    res2 = x5.fill(ignore_done=True)
    assert repo.confirmed_mapping(pid, ids()["pyaterochka"], real_only=True) is None


def test_write_false_does_not_modify_database(catalog, user_db):
    """write=False возвращает счёты, но ничего не пишет в базу."""
    store.upsert_products("perekrestok", [
        ChainProduct("4212867", "biolakt tema klubnika banan s 8 mesacev 3 206g", weight_g=206.0, unit="pcs")
    ])
    store.upsert_products("pyaterochka", [
        ChainProduct("4212867", "biolakt tema klubnika banan", weight_g=206.0, unit="pcs",
                     url="https://5ka.ru/product/biolakt-tema-klubnika-banan-s-8-mes-3-bzmzh-206g--4212867/")
    ])
    pid = repo.upsert_product(
        Product(
            id=None,
            name="Биолакт детский клубника банан тема 3 206 г",
            brand="Тема",
            weight_g=206.0,
            unit="pcs",
        )
    )
    repo.add_history_row("2026-10-10", ids()["perekrestok"], pid, "Биолакт", 1, 99.0, 99.0, "k-p")

    res = x5.fill(write=False)
    assert res["perekrestok"] == 1
    assert res["pyaterochka"] == 1
    assert repo.confirmed_mapping(pid, ids()["perekrestok"], real_only=True) is None
    assert repo.confirmed_mapping(pid, ids()["pyaterochka"], real_only=True) is None


def test_link_products_calls_x5_fill_only_for_full_run(catalog, user_db, monkeypatch):
    """link.link_products() зовёт x5.fill, а при одиночном товаре — нет."""
    calls = []
    monkeypatch.setattr(x5, "fill", lambda *a, **kw: calls.append(kw))

    link.link_products()
    assert len(calls) == 1

    calls.clear()
    link.link_products([1])
    assert len(calls) == 0


def test_second_fill_same_generation_skips_index_build(catalog, user_db, monkeypatch):
    """Повторный fill в том же поколении не строит индекс."""
    pid = repo.upsert_product(
        Product(
            id=None,
            name="Биолакт детский клубника банан тема 3 206 г",
            brand="Тема",
            weight_g=206.0,
            unit="pcs",
        )
    )
    repo.add_history_row("2026-10-10", ids()["perekrestok"], pid, "Биолакт", 1, 99.0, 99.0, "k-p")

    cnt = [0]
    real_build = x5.build_index

    def mock_build(conn):
        cnt[0] += 1
        return real_build(conn)

    monkeypatch.setattr(x5, "build_index", mock_build)

    x5.fill()
    assert cnt[0] == 1

    x5.fill()
    assert cnt[0] == 1


def test_fromurl_name_of_examples():
    """Проверка пяти примеров извлечения названия из адреса."""
    assert (
        fromurl.name_of(
            "https://5ka.ru/product/biolakt-tema-klubnika-banan-s-8-mes-3-bzmzh-206g--4212867/",
            4212867,
        )
        == "biolakt tema klubnika banan s 8 mes 3 bzmzh 206g"
    )
    assert (
        fromurl.name_of(
            "https://www.perekrestok.ru/cat/58/p/bliny-s-masom-zamorozennye-morozko-370g-3342904",
            3342904,
        )
        == "bliny s masom zamorozennye morozko 370g"
    )
    assert (
        fromurl.name_of("https://magnit.ru/product/2000642072-moloko-3-2-930-ml", 2000642072)
        == "moloko 3 2 930 ml"
    )
    assert fromurl.name_of("https://samokat.ru/product/hleb-77", "hleb-77") == "hleb 77"
    assert (
        fromurl.name_of(
            "https://dixy.ru/catalog/molochnye-produkty-yaytsa/moloko/2000328870/",
            2000328870,
        )
        is None
    )


def test_a_wrong_base_link_is_replaced_but_a_human_one_is_kept(catalog, user_db):
    """Строка единой базы с другой жирностью уступает находке X5; выбор человека — нет.

    Замер 08.11.2026: «Молоко Простоквашино 2,5 %» было связано с Перекрёстком через
    «…pasterizovannoe 1 5 930ml»: в транслите Перекрёстка нет знака процента.
    """
    store.upsert_products("perekrestok", [
        ChainProduct("2093081", "moloko prostokvasino pasterizovannoe 2 5 bzmz 930ml", weight_g=930.0),
        ChainProduct("3432833", "moloko prostokvasino pitevoe pasterizovannoe 1 5 930ml", weight_g=930.0),
    ])
    shop = ids()["perekrestok"]

    def milk(name: str, method: str | None) -> int:
        pid = repo.upsert_product(Product(id=None, name=name, brand="Простоквашино",
                                          weight_g=930.0, unit="pcs"))
        repo.add_history_row("2026-10-10", shop, pid, name, 1, 99.0, 99.0, f"k-{pid}")
        sp = repo.upsert_store_product(shop, "3432833", "moloko prostokvasino pitevoe pasterizovannoe 1 5 930ml")
        repo.confirm_mapping(pid, sp, confirmed=True, method=method)
        return pid

    base = milk("Молоко простоквашино 2 5 пастеризованное 930 г", "master")
    human = milk("Молоко простоквашино пастеризованное 2 5 930 г", None)

    res = x5.fill()

    assert res["replaced"] == 1 and res["differs"] == 1
    assert repo.confirmed_mapping(base, shop, real_only=True)["sku"] == "2093081"
    assert repo.base_candidates(base, shop) == [], "снятая строка базы осталась кандидатом"
    assert repo.confirmed_mapping(human, shop, real_only=True)["sku"] == "3432833"


def test_samokat_slug_five_examples():
    """Пять примеров транслитерации названия в слаг-артикул Самоката."""
    assert (
        x5.samokat_slug("Вода Шишкин лес, без газа, 5 л")
        == "voda-shishkin-les-bez-gaza-5-l"
    )
    assert (
        x5.samokat_slug("Вишнёвый сок Vifresh, 100%, 1 л")
        == "vishnyovyy-sok-vifresh-100-1-l"
    )
    assert (
        x5.samokat_slug("Творожный сыр Hochland, с зеленью, 60%, 140 г")
        == "tvorozhnyy-syr-hochland-s-zelenyu-60-140-g"
    )
    assert (
        x5.samokat_slug("Яблочно-виноградный сок Сады Придонья, без сахара, 1 л")
        == "yablochno-vinogradnyy-sok-sady-pridonya-bez-sakhara-1-l"
    )
    assert x5.samokat_slug("Щи") == "shchi"


def test_samokat_fill_confirms_sku_from_receipt(catalog, user_db):
    """Товар куплен в Самокате с чеком, строка есть в каталоге — подтверждён этот sku с method x5."""
    store.upsert_products("samokat", [
        ChainProduct(
            "voda-shishkin-les-bez-gaza-5-l",
            "Вода Шишкин лес, без газа, 5 л",
            weight_g=5000.0,
            unit="pcs",
            price=149.0,
        )
    ])
    shop = ids()["samokat"]
    pid = repo.upsert_product(Product(id=None, name="Вода Шишкин лес, без газа, 5 л", weight_g=5000.0, unit="pcs"))
    repo.add_history_row("2026-10-10", shop, pid, "Вода Шишкин лес, без газа, 5 л", 1, 149.0, 149.0, "s-1")

    res = x5.fill()
    assert res["samokat"] == 1

    mapping = repo.confirmed_mapping(pid, shop, real_only=True)
    assert mapping is not None
    assert mapping["sku"] == "voda-shishkin-les-bez-gaza-5-l"
    maps = {m["store_code"]: m for m in repo.product_mappings(pid)}
    assert maps["samokat"]["sku"] == "voda-shishkin-les-bez-gaza-5-l"
    assert maps["samokat"]["method"] == "x5"


def test_samokat_fill_ignores_when_not_in_catalog(catalog, user_db):
    """Строки в каталоге Самоката нет — артикул не подтверждается (несуществующий ломает сборку)."""
    shop = ids()["samokat"]
    pid = repo.upsert_product(Product(id=None, name="Вода Шишкин лес, без газа, 5 л", weight_g=5000.0, unit="pcs"))
    repo.add_history_row("2026-10-10", shop, pid, "Вода Шишкин лес, без газа, 5 л", 1, 149.0, 149.0, "s-2")

    res = x5.fill()
    assert res["samokat"] == 0
    assert repo.confirmed_mapping(pid, shop, real_only=True) is None


def test_samokat_fill_ignores_when_not_bought_in_samokat(catalog, user_db):
    """Товар не покупали в Самокате — шаг Самоката его не связывает."""
    store.upsert_products("samokat", [
        ChainProduct(
            "voda-shishkin-les-bez-gaza-5-l",
            "Вода Шишкин лес, без газа, 5 л",
            weight_g=5000.0,
            unit="pcs",
        )
    ])
    shop_p = ids()["perekrestok"]
    shop_s = ids()["samokat"]
    pid = repo.upsert_product(Product(id=None, name="Вода Шишкин лес, без газа, 5 л", weight_g=5000.0, unit="pcs"))
    # Купили в Перекрёстке, а не в Самокате
    repo.add_history_row("2026-10-10", shop_p, pid, "Вода Шишкин лес, без газа, 5 л", 1, 149.0, 149.0, "p-3")

    res = x5.fill()
    assert res["samokat"] == 0
    assert repo.confirmed_mapping(pid, shop_s, real_only=True) is None


def test_samokat_fill_preserves_human_confirmed_mapping(catalog, user_db):
    """У товара подтверждённый человеком другой артикул Самоката — автоматика его не трогает."""
    store.upsert_products("samokat", [
        ChainProduct(
            "voda-shishkin-les-bez-gaza-5-l",
            "Вода Шишкин лес, без газа, 5 л",
            weight_g=5000.0,
            unit="pcs",
        )
    ])
    shop = ids()["samokat"]
    pid = repo.upsert_product(Product(id=None, name="Вода Шишкин лес, без газа, 5 л", weight_g=5000.0, unit="pcs"))
    repo.add_history_row("2026-10-10", shop, pid, "Вода Шишкин лес, без газа, 5 л", 1, 149.0, 149.0, "s-4")

    # Человек сам выбрал другой артикул
    sp_human = repo.upsert_store_product(shop, "human-sku-888", "Вода Шишкин лес ручная")
    repo.confirm_mapping(pid, sp_human, confirmed=True, method=None)

    res = x5.fill()
    assert res["samokat"] == 0
    cur = repo.confirmed_mapping(pid, shop, real_only=True)
    assert cur is not None
    assert cur["sku"] == "human-sku-888"

