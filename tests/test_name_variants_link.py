"""Связка словаря альтернативных названий с привязкой товаров (T4, 18.10.2026).

ЗАЧЕМ. Алгоритм сокращений на лету не берёт строку чека, когда чек молчит о вкусе или
сорте, и когда у кандидата нет марки («ЙОГУРТ ДАНИС» против «Йогурт Даниссимо»).
Словарь name_variants (app/catalog/store.py) помнит, как ту же покупку записали сеть и
подтвердивший её человек, а search_variants отдаёт по написанию живой номер единого
товара. Здесь сторожа связки: написание находится другим написанием того же, словарь
отдаёт КАНДИДАТА, а не решение, противоречащее написание связи не даёт вовсе, и «＋»
товара каталога без фасовки кладёт его в сеть.

Плюс сторож ревью: префикс поставщика («ВК/ПОЛ.», «П/Ф.», «СЕТЬ.») — не разновидность,
и строки одного товара им не обнуляются.
"""
from __future__ import annotations

import pytest

from app import cartplan, config, repo, users
from app.catalog import link, refresh, store
from app.catalog.model import ChainProduct as P
from app.db import init_db
from app.importers.ofd_pdf import _ensure_product
from app.models import Product

MILK = "Молоко пастеризованное 930 мл"
# Как написание записано в словаре и как его находит чек — разные написания одного.
WRITTEN = "МОЛ.ПАСТ.ПРОСТОКВАШИНО 2,5% 930МЛ"
LOOKUP = "МОЛ ПАСТ ПРОСТОКВАШИНО 930мл"
# Сокращённое имя строки сети: его алгоритм не берёт (марки нет и «данис» не «даниссимо»).
DANIS = "Йогурт Даниссимо 2,5% 130г"
DANIS_SHORT = "ЙОГУРТ ДАНИС 2,5% 130Г"
DANIS_OTHER = "ЙОГУРТ ДАНИС 130Г 2,5%"
PHONE = "79990000041"


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


@pytest.fixture
def workplaces(tmp_path, monkeypatch):
    """Рабочие места в стороне от живых data/users: словарь читает только то, что есть."""
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    yield tmp_path / "users"
    users.deactivate()


def ids() -> dict[str, int]:
    return {s.code: s.id for s in repo.list_stores()}


def bought(raw: str, qty: float = 1) -> int:
    """Товар, заведённый как при загрузке чека: эталон — display_name строки."""
    pid, _ = _ensure_product(raw, qty)
    repo.add_history_row("2026-10-10", None, pid, raw, qty, 99.0, 99.0, f"k-{pid}-{raw}")
    return pid


def sku(pid: int, chain: str) -> str | None:
    found = repo.confirmed_mapping(pid, ids()[chain])
    return found["sku"] if found else None


def item_of(chain: str, code: str) -> int | None:
    rows = store.rows_by_skus([(chain, code)])
    return rows[0]["item_id"] if rows else None


def plan(chain: str, pid: int) -> list[str]:
    line = type("L", (), {"product_id": pid, "name": "товар", "qty": 1})()
    return [ln.sku for ln in cartplan.build(chain, [line], force=True).lines]


def confirm_pair(phone: str, variant: str, item_id: int, *, chain: str = "dixy",
                 sku: str = "hist-777") -> int:
    """Человек подтвердил пару: строка чека (hist-) — этот единый товар.

    Строкой чека её делает артикул hist-: у настоящего артикула seen_name — написание
    сети, а не чека, и словарь чеков такое написание не берёт (ревью 18.10.2026).
    """
    users.open_workspace(phone)
    try:
        pid = repo.upsert_product(Product(id=None, name="Молоко", unit="pcs"))
        repo.set_master(pid, item_id, "words")
        stores = {s.code: s.id for s in repo.list_stores()}
        sp = repo.upsert_store_product(stores[chain], sku, variant)
        repo.confirm_mapping(pid, sp)
        return pid
    finally:
        users.deactivate()


# ---------- словарь отдаёт кандидата по записанному написанию ----------
def test_written_spelling_finds_the_item_by_another_spelling(catalog, user_db):
    """«МОЛ.ПАСТ.ПРОСТОКВАШИНО 2,5% 930МЛ» находит товар по «МОЛ ПАСТ … 930мл»."""
    store.upsert_products("dixy", [P("1", MILK)])
    refresh.match_all()
    item = item_of("dixy", "1")
    store.remember_variant(item, WRITTEN, "receipt", "dixy")
    pid = bought(LOOKUP)

    report = link.link_products()

    assert repo.master_of(pid)["master_id"] == item
    assert report.weak == 1 and report.strong == 0
    # Связь слабая: артикул ложится КАНДИДАТОМ и в наряд сам не идёт.
    assert repo.master_of(pid)["method"] == "variant?"
    assert sku(pid, "dixy") is None
    assert [c["sku"] for c in repo.base_candidates(pid, ids()["dixy"])] == ["1"]
    assert plan("dixy", pid) == []


def test_without_the_dictionary_nothing_is_found(catalog, user_db):
    """То же самое без записи в словаре — как было: не найдено."""
    store.upsert_products("dixy", [P("1", MILK)])
    refresh.match_all()
    pid = bought(LOOKUP)

    link.link_products()

    assert repo.master_of(pid) is None
    assert sku(pid, "dixy") is None and repo.base_candidates(pid, ids()["dixy"]) == []


def test_human_written_spelling_is_a_strong_argument(catalog, user_db):
    """Написание, подтверждённое человеком, — сильная связь (source=human)."""
    store.upsert_products("dixy", [P("1", MILK)])
    refresh.match_all()
    item = item_of("dixy", "1")
    store.remember_variant(item, WRITTEN, "human")
    pid = bought(LOOKUP)

    with store.connect() as conn:
        found = link._by_words(repo.get_product(pid), repo.raw_names(pid), conn)

    assert isinstance(found, link.Found)
    assert (found.method, found.strong, found.item_id) == ("variant", True, item)


def test_contradicting_written_spelling_does_not_link(catalog, user_db):
    """Контроль: написание «С/ГАЗ» при чеке «Н/ГАЗ» связи не даёт."""
    store.upsert_products("lenta", [P("1", "Вода питьевая AQUA MINERALE газированная 0,5л")])
    refresh.match_all()
    item = item_of("lenta", "1")
    store.remember_variant(item, "ВОДА AQUA MINERALE С/ГАЗ 0,5Л", "receipt", "lenta")
    pid = bought("ВОДА AQUA MINERALE Н/ГАЗ 0,5Л")

    with store.connect() as conn:
        matched = link._matching_variants(
            link._rows_of_person(repo.get_product(pid), repo.raw_names(pid)), conn)
    link.link_products()

    assert matched == {}                      # написание строки не то, что записано
    assert sku(pid, "lenta") is None          # в наряд ничего не ушло
    assert repo.base_candidates(pid, ids()["lenta"]) == []


# ---------- F1: в самом написании записаны процент и штуки — их тоже сверяем ----------
def test_written_spelling_with_another_percent_does_not_link(catalog, user_db):
    """«СМЕТАНА 20 %» в словаре и чек «СМЕТАНА 15 %»: процент спорит — связи нет.

    Adversarial-проверка 18.10.2026: сверялись только наборы слов, и написание
    «СМЕТАНА 20 % 300Г» подтверждало чек «СМЕТАНА 15 % 300Г» — в наряд уходил не тот
    процент. Воспроизведение: Found(method='variant', strong=True) до правки.
    """
    store.upsert_products("dixy", [P("1", "Сметана Большая кружка 300г")])
    refresh.match_all()
    item = item_of("dixy", "1")
    store.remember_variant(item, "СМЕТАНА 20% 300Г", "human", "dixy")
    pid = bought("СМЕТАНА 15% 300Г")
    person = link._rows_of_person(repo.get_product(pid), repo.raw_names(pid))

    with store.connect() as conn:
        assert link._matching_variants(person, conn) == {}
    link.link_products()

    assert repo.master_of(pid) is None
    assert sku(pid, "dixy") is None and plan("dixy", pid) == []


def test_written_spelling_with_the_same_percent_links(catalog, user_db):
    """Контроль к F1: то же написание, но с процентом чека, — связь есть."""
    store.upsert_products("dixy", [P("1", "Сметана Большая кружка 300г")])
    refresh.match_all()
    item = item_of("dixy", "1")
    store.remember_variant(item, "СМЕТАНА 15% 300Г", "human", "dixy")
    pid = bought("СМЕТАНА 15% 300Г")
    person = link._rows_of_person(repo.get_product(pid), repo.raw_names(pid))

    with store.connect() as conn:
        assert link._matching_variants(person, conn) == {item: "human"}


def test_written_spelling_packs_must_agree(catalog, user_db):
    """Число штук в написании — тоже улика: «6 ШТ» против чека одной бутылки."""
    store.upsert_products("dixy", [P("1", "Вода БОРЖОМИ 0,5л 6 шт")])
    refresh.match_all()
    item = item_of("dixy", "1")
    store.remember_variant(item, "ВОДА БОРЖОМИ 0,5Л 6 ШТ", "human", "dixy")
    pid = bought("ВОДА БОРЖОМИ 0,5Л")

    with store.connect() as conn:
        assert link._matching_variants(
            link._rows_of_person(repo.get_product(pid), repo.raw_names(pid)), conn) == {}


# ---------- F2: у номера бывает несколько написаний ----------
def test_second_spelling_of_the_same_item_is_reachable(catalog, user_db):
    """Редкое написание достижимо: раньше search_variants отдавал только частое.

    Adversarial-проверка 18.10.2026: написание схлопывалось до MAX(hits) на
    (item_id, source, chain), и чек ровно со вторым написанием связи не получал.
    """
    store.upsert_products("dixy", [P("1", MILK)])
    refresh.match_all()
    item = item_of("dixy", "1")
    for _ in range(5):
        store.remember_variant(item, WRITTEN, "receipt", "dixy")             # частое
    store.remember_variant(item, "МОЛ ПАСТ БЕЗЛАКТОЗНОЕ 930МЛ", "receipt", "dixy")
    pid = bought("МОЛ ПАСТ БЕЗЛАКТОЗНОЕ 930МЛ")

    link.link_products()

    assert repo.master_of(pid)["master_id"] == item
    assert repo.master_of(pid)["method"] == "variant?"
    assert [c["sku"] for c in repo.base_candidates(pid, ids()["dixy"])] == ["1"]


def test_confirmed_pair_lets_another_receipt_find_the_item(catalog, workplaces):
    """Путь целиком: подтверждённая пара попала в словарь и нашла товар другому чеку."""
    store.upsert_products("dixy", [P("1", DANIS)])
    refresh.match_all()
    item = item_of("dixy", "1")
    confirm_pair(PHONE, DANIS_SHORT, item)
    assert refresh.fill_name_variants()["receipt"] == 1

    users.open_workspace(PHONE)
    pid = bought(DANIS_OTHER)
    link.link_products()

    assert repo.master_of(pid)["master_id"] == item
    assert [c["sku"] for c in repo.base_candidates(pid, ids()["dixy"])] == ["1"]
    assert sku(pid, "dixy") is None       # кандидат, не подтверждение


# ---------- «＋» товара каталога без фасовки ----------
def test_plus_of_a_catalog_item_without_packaging_puts_it_in_the_chain(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Батон нарезной")])
    store.upsert_products("magnit", [P("2", "Батон нарезной")])
    refresh.match_all()
    number = item_of("lenta", "1")

    pid = refresh.adopt(number)               # «＋» на «Корзине»

    assert sku(pid, "lenta") == "1" and sku(pid, "magnit") == "2"
    assert repo.master_of(pid)["method"] == "words"


def test_plus_writes_a_weak_mark_when_nothing_is_confirmed(catalog, user_db):
    """Ни одной живой строки — связи нет, и метка честная: «words?»."""
    store.upsert_products("lenta", [P("1", "Батон нарезной")])
    refresh.match_all()
    number = item_of("lenta", "1")
    with store.connect() as conn:
        conn.execute("DELETE FROM chain_products")
        conn.commit()

    pid = refresh.adopt(number)

    assert repo.master_of(pid)["method"] == "words?"
    assert sku(pid, "lenta") is None


# ---------- сторож ревью: префикс поставщика ----------
def test_supplier_prefix_is_not_a_variety():
    """«ВК/ПОЛ.», «П/Ф.», «СЕТЬ.» — префикс поставщика, а не спор разновидностей."""
    for prefixed in ("ВК/ПОЛ.СЫР СТРАЧАТЕЛЛА 200г", "П/Ф.СЫР СТРАЧАТЕЛЛА 200г",
                     "ТМ.СЫР СТРАЧАТЕЛЛА 200г", "СЕТЬ.СЫР СТРАЧАТЕЛЛА 200г",
                     "ТОРГ.СЫР СТРАЧАТЕЛЛА 200г"):
        assert link._contradicting_variants([prefixed, "СЫР СТРАЧАТЕЛЛА 200г"]) is False, prefixed
    # Противоположные разновидности по-прежнему видны (находка №3).
    assert link._contradicting_variants(
        ["ВОДА AQUA MINERALE Н/ГАЗ 0,5Л", "ВОДА AQUA MINERALE С/ГАЗ 0,5Л"]) is True


def test_prefixed_receipts_keep_their_rows(catalog, user_db):
    """Строки одного товара с префиксом поставщика не обнуляются и находят себя."""
    store.upsert_products("lenta", [P("1", "Сыр Страчателла 200г")])
    refresh.match_all()
    raws = ["СЕТЬ.СЫР СТРАЧАТЕЛЛА 200г", "СЫР СТРАЧАТЕЛЛА 200г"]
    pid, _ = _ensure_product(raws[0], 1)
    assert _ensure_product(raws[1], 1)[0] == pid      # один display_name
    for raw in raws:
        repo.add_history_row("2026-10-01", None, pid, raw, 1, 300.0, 300.0, f"k-{raw}")

    person = link._rows_of_person(repo.get_product(pid), repo.raw_names(pid))
    link.link_products()

    assert person
    assert sku(pid, "lenta") == "1"
