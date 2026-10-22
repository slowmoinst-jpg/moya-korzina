"""Словарь альтернативных названий: схема, наполнение, чтение (решение 18.10.2026).

ЗАЧЕМ. До этой правки сокращение чека считалось только алгоритмом на лету, и он не
берёт строку, когда чек молчит о вкусе или сорте (link._abbrev), и кандидата без марки
(у Дикси 9,7 % строк без марки). Таблица name_variants в общей базе помнит «как пишет
сеть» и «как пишет чек», а поиск (store.search_variants) отдаёт по сокращению живой
номер единого товара. Здесь сторожа: миграция, наполнение из строк сетей и из
подтверждённых пар, обезличенность личных баз, повтор без дублей и сам поиск.

Поведение привязки товаров человека (app/catalog/link.py) эта задача не меняет —
связку со словарём делает T4.
"""
from __future__ import annotations

import os

import pytest

from app import repo, users
from app.catalog import refresh, store
from app.catalog.model import ChainProduct as P
from app.models import Product

PHONE = "79990000031"
OTHER = "79990000032"
MILK = "Молоко пастеризованное 930 мл"
RECEIPT_LINE = "МОЛ.ПАСТ.ПРОСТОКВАШИНО 2,5% 930МЛ"


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    yield


@pytest.fixture
def workplaces(tmp_path, monkeypatch):
    """Рабочие места в стороне от живых data/users: словарь читает только то, что есть."""
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    yield tmp_path / "users"
    users.deactivate()


def confirm(phone: str, variant: str, *, chain: str = "dixy", sku: str = "hist-777",
            item_id: int | None = None, name: str = "Молоко", method: str | None = None,
            master: str = "words", base_written: bool = False) -> int:
    """Личная база: эталон привязан к единому товару, пара подтверждена человеком.

    seen_name при подтверждении repo.confirm_mapping берёт из store_products.raw_name —
    ровно то, как название написал чек. Поэтому по умолчанию артикул — строка ЧЕКА
    (hist-): у настоящего артикула seen_name был бы написанием сети, а не чека, и
    словарь чеков такое написание не берёт (ревью 18.10.2026).

    base_written=True — та же запись, но помеченная как сделанная базой (method='master'
    при готовом снимке названия): так выглядела бы связь, которую база переподтвердила
    сама. Сегодня база снимок не пишет, и состояние ставится прямо — сторож нужен на
    будущее: иначе слабая догадка базы подкрепляла бы себя через словарь чеков.
    """
    users.open_workspace(phone)
    try:
        pid = repo.upsert_product(Product(id=None, name=name, unit="pcs"))
        if item_id is not None:
            repo.set_master(pid, item_id, master)
        stores = {s.code: s.id for s in repo.list_stores()}
        sp = repo.upsert_store_product(stores[chain], sku, variant)
        repo.confirm_mapping(pid, sp, method=method)
        if base_written:
            with repo.get_conn() as c:
                c.execute("UPDATE product_mapping SET method='master' WHERE product_id=?", (pid,))
                c.commit()
        return pid
    finally:
        users.deactivate()


def variants(**where) -> list[dict]:
    sql = "SELECT * FROM name_variants"
    if where:
        sql += " WHERE " + " AND ".join(f"{k}=?" for k in where)
    with store.connect() as conn:
        return [dict(r) for r in conn.execute(sql, tuple(where.values()))]


def single_item() -> int:
    """Один единый товар в каталоге — тот, к которому будут привязаны чеки."""
    store.upsert_products("dixy", [P("1", MILK)])
    refresh.match_all()
    return int(store.all_products()[0]["item_id"])


# ---------- схема и миграция ----------
def test_schema_v5_creates_variant_dictionary(catalog):
    """Миграция на пустой базе: таблица, индекс поиска и версия схемы."""
    assert store.SCHEMA_VERSION >= 5, "словарь появился версией 5 и не исчезает дальше"
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 5
        have = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}
    assert "name_variants" in have
    assert "idx_variants_folded" in have
    # Ключ словаря: у одного товара один и тот же свод с одним источником и сетью — строка одна.
    with store.connect() as conn:
        index = conn.execute("SELECT sql FROM sqlite_master WHERE name='name_variants'").fetchone()
    assert "UNIQUE (item_id, folded, source, chain)" in index["sql"]


def test_migration_from_v4_restores_dictionary(catalog):
    """База прежней версии таблицу не имела: init её заводит и поднимает версию схемы."""
    with store.connect() as conn:
        conn.execute("DROP TABLE name_variants")
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
    store.init()
    with store.connect() as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='name_variants'").fetchone()
        assert conn.execute("PRAGMA user_version").fetchone()[0] >= 5


def test_variant_key_survives_different_writing(catalog, workplaces):
    """Разные написания с одним сводом — одна строка, hits растёт (записи руками не нужны)."""
    item_id = single_item()
    assert store.remember_variant(item_id, "МОЛОКО 930 МЛ", "human")
    assert store.remember_variant(item_id, "молоко 930мл", "human")
    assert not store.remember_variant(item_id, "молоко", "придумка")   # чужой источник не пишем
    rows = variants(source="human")
    assert len(rows) == 1 and rows[0]["hits"] == 2 and rows[0]["chain"] == ""


# ---------- наполнение из строк сетей ----------
def test_fill_from_chain_rows(catalog, workplaces):
    """Как пишет СЕТЬ: название строки ложится в словарь на её единый товар."""
    store.upsert_products("lenta", [P("5", "Молоко ПРОСТОКВАШИНО 930 мл")])
    refresh.match_all()
    rows = variants(source="chain")
    assert len(rows) == 1
    assert rows[0]["variant"] == "Молоко ПРОСТОКВАШИНО 930 мл"
    assert rows[0]["chain"] == "lenta" and rows[0]["hits"] == 1
    assert rows[0]["folded"] == store.folded_variant("Молоко ПРОСТОКВАШИНО 930 мл")
    assert [r["source"] for r in store.variant_stats()] == ["chain"]


def test_match_all_fills_the_dictionary(catalog, workplaces):
    """Наполнение идёт вслед за пересборкой, а не отдельной кнопкой."""
    store.upsert_products("dixy", [P("1", MILK)])
    summary = refresh.match_all()
    assert summary["variants"]["chain"] == 1
    assert variants(item_id=int(store.all_products()[0]["item_id"]), source="chain")


def test_new_sighting_raises_hits_and_moves_last_seen(catalog, workplaces):
    """Повтор наполнения без обхода — не новое написания; с обходом — новое."""
    store.upsert_products("dixy", [P("1", MILK)], seen_at="2026-10-01T10:00:00")
    refresh.match_all()
    first = variants(source="chain")[0]
    refresh.fill_name_variants()                       # ничего не обошли — счёт прежний
    same = variants(source="chain")[0]
    assert (same["hits"], same["last_seen"]) == (first["hits"], first["last_seen"])
    store.upsert_products("dixy", [P("1", MILK)], seen_at="2026-10-02T10:00:00")
    refresh.fill_name_variants()
    grown = variants(source="chain")[0]
    assert grown["hits"] == first["hits"] + 1
    assert grown["last_seen"] == "2026-10-02T10:00:00"


def test_repeat_fill_does_not_duplicate_rows(catalog, workplaces):
    """Повторное наполнение не плодит дублей — ни из сетей, ни из чеков."""
    item_id = single_item()
    confirm(PHONE, RECEIPT_LINE, sku="hist-111", item_id=item_id)
    refresh.fill_name_variants()
    before = len(variants())
    refresh.fill_name_variants()
    refresh.fill_name_variants()
    assert len(variants()) == before
    assert before == 2                                  # строка сети и пара из чека


# ---------- наполнение из подтверждённых пар ----------
def test_fill_from_confirmed_receipt_pair(catalog, workplaces):
    """Как пишет ЧЕК: подтверждённая человеком пара ложится в словарь на его единый товар."""
    item_id = single_item()
    confirm(PHONE, RECEIPT_LINE, sku="hist-111", item_id=item_id)
    refresh.fill_name_variants()
    rows = variants(source="receipt")
    assert len(rows) == 1
    assert (rows[0]["item_id"], rows[0]["variant"], rows[0]["chain"]) == (item_id, RECEIPT_LINE, "dixy")
    assert rows[0]["hits"] == 1
    # Ни неподтверждённая пара, ни товар без привязки к единой базе в словарь не идут.
    users.open_workspace(OTHER)
    try:
        stores = {s.code: s.id for s in repo.list_stores()}
        pid = repo.upsert_product(Product(id=None, name="Сыр", unit="pcs"))
        repo.set_master(pid, item_id, "words")                 # привязка есть…
        sp = repo.upsert_store_product(stores["dixy"], "hist-222", "СЫР 200Г")
        repo.confirm_mapping(pid, sp, confirmed=False)         # …но пара не подтверждена
        other = repo.upsert_product(Product(id=None, name="Кефир", unit="pcs"))
        repo.confirm_mapping(other, repo.upsert_store_product(stores["dixy"], "hist-333", "КЕФИР 1Л"))
    finally:
        users.deactivate()
    refresh.fill_name_variants()
    assert len(variants(source="receipt")) == 1


def test_receipt_variants_are_anonymous(catalog, workplaces):
    """Два человека, одна строка чека — одна запись словаря, и ни следа человека."""
    item_id = single_item()
    confirm(PHONE, RECEIPT_LINE, sku="hist-111", item_id=item_id)
    confirm(OTHER, RECEIPT_LINE, sku="hist-222", item_id=item_id)
    refresh.fill_name_variants()
    rows = variants(source="receipt")
    assert len(rows) == 1 and rows[0]["hits"] == 2      # обезличенно: кто подтвердил — не важно
    with store.connect() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(name_variants)")}
    assert not ({"phone", "product_id", "purchase_id", "date"} & columns)
    assert PHONE not in str(rows[0]) and OTHER not in str(rows[0])


# ---------- что довод для словаря чеков, а что нет (ревью 18.10.2026) ----------
def test_base_written_link_is_not_a_receipt_variant(catalog, workplaces):
    """Связь, записанную самой базой, словарь чеков не берёт: иначе догадка крепит себя."""
    item_id = single_item()
    confirm(PHONE, RECEIPT_LINE, sku="hist-111", item_id=item_id, base_written=True)

    refresh.fill_name_variants()

    assert variants(source="receipt") == []


def test_weak_master_is_not_a_receipt_variant(catalog, workplaces):
    """Слабая связь («words?») — не довод: словарь запомнил бы догадку как написание чека."""
    item_id = single_item()
    confirm(PHONE, RECEIPT_LINE, sku="hist-111", item_id=item_id, master="words?")

    refresh.fill_name_variants()

    assert variants(source="receipt") == []


@pytest.mark.parametrize("sku", ["hist-1", "555", "list-7", "dixy-kefir", "preview-dixy-1"])
def test_only_receipt_rows_become_variants(catalog, workplaces, sku):
    """«Как пишет чек» — только строка чека (hist-). Артикул сети и заглушки — не чек."""
    item_id = single_item()
    confirm(PHONE, RECEIPT_LINE, sku=sku, item_id=item_id)

    refresh.fill_name_variants()

    assert bool(variants(source="receipt")) is sku.startswith("hist-")


def test_receipt_hits_count_people_not_runs(catalog, workplaces):
    """hits — сколько ЛЮДЕЙ подтвердили написание, а не сколько раз шло наполнение."""
    item_id = single_item()
    confirm(PHONE, RECEIPT_LINE, sku="hist-111", item_id=item_id)

    refresh.fill_name_variants()
    refresh.fill_name_variants()
    refresh.fill_name_variants()

    assert [r["hits"] for r in variants(source="receipt")] == [1]
    confirm(OTHER, RECEIPT_LINE, sku="hist-222", item_id=item_id)
    refresh.fill_name_variants()
    assert [r["hits"] for r in variants(source="receipt")] == [2]


def test_relative_workspace_path_still_finds_receipts(catalog, workplaces, monkeypatch):
    """Относительный путь базы человека: as_uri() бросал ValueError, словарь молчал."""
    item_id = single_item()
    confirm(PHONE, RECEIPT_LINE, sku="hist-111", item_id=item_id)
    monkeypatch.chdir(workplaces.parent)          # теперь путь можно задать относительным
    relative = os.path.join(workplaces.name, PHONE, "basket.db")

    rows = refresh._receipt_variant_rows(relative)

    assert [r[1] for r in rows] == [RECEIPT_LINE]


# ---------- наполнение из истории покупок: чек и есть строка истории (18.10.2026) ----------
def history_row(phone: str, raw: str, item_id: int | None, *, chain: str | None = "dixy",
                master: str | None = "words") -> int:
    """Строка чека в личной базе — так её пишет кабинет ФНС (purchase_history.raw_name).

    item_id=None — товар без привязки к единой базе; chain=None — сеть в чеке не названа;
    master=None — master_method пуст (человек или живой поиск — это довод).
    """
    users.open_workspace(phone)
    try:
        pid = repo.upsert_product(Product(id=None, name="Молоко", unit="pcs"))
        if item_id is not None:
            repo.set_master(pid, item_id, master)
        stores = {s.code: s.id for s in repo.list_stores()}
        store_id = stores[chain] if chain else None
        repo.add_history_row("2026-10-01", store_id, pid, raw, 1, 99.0, 99.0, f"k-{pid}-{raw}")
        return pid
    finally:
        users.deactivate()


def test_history_receipt_teaches_the_dictionary(catalog, workplaces):
    """Строка чека из истории попадает в словарь: до правки source='receipt' был пуст."""
    item_id = single_item()
    history_row(PHONE, RECEIPT_LINE, item_id)

    refresh.fill_name_variants()

    rows = variants(source="receipt")
    assert [(r["item_id"], r["variant"], r["chain"]) for r in rows] == [
        (item_id, RECEIPT_LINE, "dixy")]
    assert [r["hits"] for r in rows] == [1]


def test_history_receipt_needs_a_mapped_product(catalog, workplaces):
    """Без привязки к единой базе строка чека товару ничего не говорит."""
    single_item()
    history_row(PHONE, RECEIPT_LINE, None)

    refresh.fill_name_variants()

    assert variants(source="receipt") == []


@pytest.mark.parametrize("master, allowed", [("words", True), (None, True),
                                             ("words?", False), ("variant?", False)])
def test_history_receipt_needs_a_non_weak_link(catalog, workplaces, master, allowed):
    """NULL — человек или живой поиск, это довод; «?» — слабая догадка, не довод."""
    item_id = single_item()
    history_row(PHONE, RECEIPT_LINE, item_id, master=master)

    refresh.fill_name_variants()

    assert bool(variants(source="receipt")) is allowed


def test_history_row_without_a_chain_is_not_taken(catalog, workplaces):
    """Сети в чеке не названо — варианта не выдумываем."""
    item_id = single_item()
    history_row(PHONE, RECEIPT_LINE, item_id, chain=None)

    refresh.fill_name_variants()

    assert variants(source="receipt") == []


def test_history_receipts_do_not_accumulate_hits(catalog, workplaces):
    """hits — число наблюдений написания, а не прогонов: три прогона ничего не накручивают."""
    item_id = single_item()
    history_row(PHONE, RECEIPT_LINE, item_id)
    history_row(PHONE, RECEIPT_LINE, item_id)          # две покупки — два наблюдения

    refresh.fill_name_variants()
    first = [(r["variant"], r["hits"]) for r in variants(source="receipt")]
    refresh.fill_name_variants()
    refresh.fill_name_variants()

    assert first == [(RECEIPT_LINE, 2)]
    assert [(r["variant"], r["hits"]) for r in variants(source="receipt")] == first
    assert len(variants(source="receipt")) == 1        # дублей по ключу нет


def test_history_receipts_carry_no_personal_data(catalog, workplaces):
    """В словаре нет ни даты покупки, ни человека, ни id его товара — только написание."""
    item_id = single_item()
    history_row(PHONE, RECEIPT_LINE, item_id)

    refresh.fill_name_variants()

    rows = variants(source="receipt")
    assert len(rows) == 1
    with store.connect() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(name_variants)")}
    assert not ({"date", "phone", "product_id", "purchase_id", "receipt_key", "qty"} & columns)
    assert PHONE not in str(rows[0]) and "2026-10-01" not in str(rows[0])


def test_variants_follow_numbers_through_merge_and_death(catalog, workplaces):
    """Номер слился — варианты переезжают; номер исчез — записи уходят (дублей нет)."""
    stamp = store.now()
    with store.connect() as conn:
        conn.execute("INSERT INTO catalog_items (id, name, created_at, updated_at)"
                     " VALUES (10, 'Молоко', ?, ?)", (stamp, stamp))
        conn.execute("INSERT INTO catalog_items (id, name, created_at, updated_at)"
                     " VALUES (11, 'Молоко 930 мл', ?, ?)", (stamp, stamp))
        conn.execute("INSERT INTO catalog_items (id, name, created_at, updated_at)"
                     " VALUES (12, 'Кефир 1 л', ?, ?)", (stamp, stamp))
        conn.commit()
    store.remember_variant(10, "молоко 930 мл", "human")     # тот же свод у живого номера
    store.remember_variant(11, "МОЛОКО 930МЛ", "human")
    store.remember_variant(12, "КЕФИР 1Л", "human")
    with store.connect() as conn:                            # 11 слился с 10, 12 исчез вовсе
        conn.execute("DELETE FROM catalog_items WHERE id IN (11, 12)")
        conn.execute("INSERT INTO item_aliases (old_id, new_id, since) VALUES (11, 10, ?)", (stamp,))
        conn.commit()
    refresh.fill_name_variants()
    rows = variants(source="human")
    assert [(r["item_id"], r["hits"]) for r in rows] == [(10, 1)]


def test_missing_or_broken_personal_base_does_not_break_fill(catalog, workplaces):
    """База человека может быть битой, старой или занятой — наполнение из сетей идёт."""
    bad = workplaces / PHONE
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "basket.db").write_bytes(b"this is not a database")
    store.upsert_products("dixy", [P("1", MILK)])
    refresh.match_all()                     # строка сети получила единый товар
    report = refresh.fill_name_variants()
    assert report["chain"] == 1 and report["receipt"] == 0
    assert len(variants(source="chain")) == 1


# ---------- чтение: кандидаты по свёрнутому варианту ----------
def test_search_finds_item_by_variant_absent_from_item_name(catalog, workplaces):
    """Сокращение чека находит единый товар, хотя слова в имени товара нет."""
    item_id = single_item()
    assert "простоквашино" not in MILK.lower()
    confirm(PHONE, RECEIPT_LINE, sku="hist-111", item_id=item_id)
    refresh.fill_name_variants()
    # Алгоритм на лету это слово в имени товара не находит — ровно та дыра, что закрывает словарь.
    assert store.search_folded(["prostokvasino"]) == []
    found = store.search_variants(["prostokvasino"])
    assert [(r["item_id"], r["source"], r["chain"]) for r in found] == [(item_id, "receipt", "dixy")]
    assert found[0]["score"] == 1 and found[0]["hits"] == 1
    # Порядок и отбор: три самые длинные основы, как в search_folded.
    assert store.search_variants([]) == [] and store.search_variants(["ab"]) == []


def test_search_prefers_more_matched_terms(catalog, workplaces):
    """Чем больше основ чека сошлось, тем выше товар; мёртвый номер в поиск не попадает."""
    store.upsert_products("dixy", [P("1", "Молоко пастеризованное 930 мл"),
                                   P("2", "Хлеб Бородинский 400 г")])
    refresh.match_all()
    with store.connect() as conn:
        milk = conn.execute("SELECT item_id FROM chain_products WHERE chain='dixy' AND sku='1'"
                            ).fetchone()["item_id"]
        bread = conn.execute("SELECT item_id FROM chain_products WHERE chain='dixy' AND sku='2'"
                             ).fetchone()["item_id"]
    store.remember_variant(milk, "МОЛОКО ПАСТЕР ДОМИК В ДЕРЕВНЕ 930МЛ", "receipt", "dixy")
    store.remember_variant(bread, "МОЛОКО ХЛЕБ", "receipt", "dixy")
    found = store.search_variants(["moloko", "paster", "derevne"])
    assert found[0]["item_id"] == milk and found[0]["score"] == 3
    # Хлеб по той же основе нашёлся, но слабее: одна основа против трёх.
    assert bread in {r["item_id"] for r in found}
    assert max(r["score"] for r in found if r["item_id"] == bread) == 1
    # Товара нет — его написаний в выдаче тоже нет (наполнение убирает, а JOIN не пускает):
    # и при этом молоко по-прежнему находится, то есть сам поиск жив.
    with store.connect() as conn:
        conn.execute("DELETE FROM catalog_items WHERE id=?", (bread,))
        conn.commit()
    left = store.search_variants(["moloko"])
    assert {r["item_id"] for r in left} == {milk}
