"""Перенесённые исправления единого каталога: отрицания, сокращения, штуки (18.10.2026).

Каждый тест здесь сформулирован на поведение, описанное в
docs/design/edinyi-katalog-dorabotki-2026-10-18.md как «что не так», и на коде ДО
правки падал. Пункты документа: №1 «НЕ ГАЗ»/«НЕ ФИЛЬТР»/«СЛ/ГАЗ»; №2 «Н.ГАЗ»,
«Н-ГАЗ», «Н\\ГАЗ», «Б.САХ»; №3 импорт чеков сводит противоположные разновидности;
№5 упаковка словами; №6 «3в1 стик 20шт»; №7 яйца «10 шт» без массы; №8 «＋» товара
каталога без фасовки; №9 весовое без названной единицы; №14 выбор человека и
выведенный эталон. Пункт №16 («_same_item» без словаря марок) не реализован намеренно.
Пункты №4, №10, №11, №12 — не эта задача.
"""
from __future__ import annotations

import pytest

from app import config, repo
from app.catalog import link, match, refresh, store
from app.catalog.model import ChainProduct as P
from app.db import init_db
from app.importers.ofd_pdf import _ensure_product
from app.models import Product


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


# ---------- №1: отрицание словом и двухбуквенное уточнение ----------
def test_ne_word_is_a_negation(catalog, user_db):
    """«НЕ ГАЗ» сильно подтверждал газированную воду: «не» из двух букв выбрасывалось."""
    store.upsert_products("lenta", [P("1", "Вода питьевая AQUA MINERALE газированная 0,5л")])
    refresh.match_all()
    pid = bought("ВОДА AQUA MINERALE НЕ ГАЗ 0,5Л")

    link.link_products()

    assert "негаз" in match.glue("ВОДА AQUA MINERALE НЕ ГАЗ 0,5Л").lower()
    assert sku(pid, "lenta") is None
    # Положительный контроль: без «не» та же строка товар находит — помешало отрицание.
    good = bought("ВОДА AQUA MINERALE ГАЗ 0,5Л")
    link.link_products()
    assert sku(good, "lenta") == "1"


def test_two_letter_word_of_the_receipt_never_confirms(catalog, user_db):
    """«СЛ/ГАЗ» и «СР/ГАЗ»: «сл» — не слово кандидата, связь не сильнее слабой."""
    store.upsert_products("lenta", [P("1", "Вода питьевая AQUA MINERALE газированная 0,5л")])
    refresh.match_all()
    pid = bought("ВОДА AQUA MINERALE СЛ/ГАЗ 0,5Л")

    link.link_products()

    assert sku(pid, "lenta") is None
    # Положительный контроль: товар НАЙДЕН и лежит кандидатом, а связь слабая —
    # значит, сработал сторож двухбуквенного слова, а не «поиск ничего не нашёл».
    assert repo.master_of(pid)["method"] == "abbrev?"
    assert [c["sku"] for c in repo.base_candidates(pid, ids()["lenta"])] == ["1"]


# ---------- №2: точка, дефис и обратная черта как отрицание ----------
@pytest.mark.parametrize("receipt", ["ВОДА AQUA MINERALE Н.ГАЗ 0,5Л",
                                     "ВОДА AQUA MINERALE Н-ГАЗ 0,5Л",
                                     "ВОДА AQUA MINERALE Н\\ГАЗ 0,5Л"])
def test_dot_dash_backslash_negation_does_not_confirm(catalog, user_db, receipt):
    store.upsert_products("lenta", [P("1", "Вода питьевая AQUA MINERALE газированная 0,5л")])
    refresh.match_all()
    pid = bought(receipt)

    link.link_products()

    assert "негаз" in match.glue(receipt).lower()
    assert sku(pid, "lenta") is None


def test_dot_negation_of_sugar_does_not_confirm_sugar(catalog, user_db):
    """«Б.САХ» — «без сахара»: эталон терял одну букву и находил «с сахаром»."""
    store.upsert_products("lenta", [P("1", "Йогурт ЭКОНИВА с сахаром 2,5% 300г")])
    refresh.match_all()
    pid = bought("ЙОГУРТ ЭКОНИВА Б.САХ 2,5% 300Г")

    link.link_products()

    assert sku(pid, "lenta") is None
    # Положительный контроль: без отрицания та же покупка находится — помешало «Б.САХ».
    good = bought("ЙОГУРТ ЭКОНИВА С САХАРОМ 2,5% 300Г")
    link.link_products()
    assert sku(good, "lenta") == "1"


# ---------- №3: импорт не сводит противоположные разновидности ----------
def test_opposite_variants_of_one_display_name_are_not_guessed(catalog, user_db):
    """«Н/ГАЗ» и «С/ГАЗ» — один display_name, разные слова: строк нет, не угадываем."""
    store.upsert_products("lenta", [P("1", "Вода питьевая AQUA MINERALE газированная 0,5л")])
    refresh.match_all()
    first = "ВОДА AQUA MINERALE Н/ГАЗ 0,5Л"
    second = "ВОДА AQUA MINERALE С/ГАЗ 0,5Л"
    pid, _ = _ensure_product(first, 1)
    repo.add_history_row("2026-10-01", None, pid, first, 1, 30.0, 30.0, "k1")
    pid2, _ = _ensure_product(second, 1)
    assert pid2 == pid                     # импорт свёл строки по display_name
    repo.add_history_row("2026-10-02", None, pid, second, 1, 31.0, 31.0, "k2")

    assert link._rows_of_person(repo.get_product(pid), repo.raw_names(pid)) == []
    link.link_products()

    assert repo.master_of(pid) is None
    assert sku(pid, "lenta") is None


def test_one_negation_in_two_spellings_still_links(catalog, user_db):
    """Контроль: «Н/ГАЗ» и «Б/ГАЗ» — одно отрицание, связь обязана остаться."""
    store.upsert_products("lenta", [P("1", "Вода питьевая AQUA MINERALE негазированная 0,5л")])
    refresh.match_all()
    pid, _ = _ensure_product("ВОДА AQUA MINERALE Н/ГАЗ 0,5Л", 1)
    repo.add_history_row("2026-10-01", None, pid, "ВОДА AQUA MINERALE Н/ГАЗ 0,5Л",
                         1, 30.0, 30.0, "k1")
    repo.add_history_row("2026-10-02", None, pid, "ВОДА AQUA MINERALE Б/ГАЗ 0,5Л",
                         1, 31.0, 31.0, "k2")

    link.link_products()

    assert sku(pid, "lenta") == "1"


# ---------- №5: упаковка словами ----------
@pytest.mark.parametrize("name, packs", [
    ("Вода БОРЖОМИ 0,5л уп.12", 12),
    ("Сок ДОБРЫЙ кор. 27", 27),
    ("Молоко ДОМИК В ДЕРЕВНЕ упаковка 12 бут", 12),
    ("Зубная паста пак 6", 6),
    ("Вода БОРЖОМИ 0,5л х 12", 12),
])
def test_packaging_words_are_counted(name, packs):
    assert match.pack_count(name) == packs


@pytest.mark.parametrize("name", ["Суп 250г", "Творог упаковка 300 г", "Печенье КОР. 45%"])
def test_packaging_words_do_not_swallow_weight_or_percent(name):
    assert match.pack_count(name) == 1


def test_pack_words_do_not_confirm_a_single_bottle(catalog, user_db):
    store.upsert_products("metro", [P("1", "Вода БОРЖОМИ 0,5л уп.12")])
    store.upsert_products("lenta", [P("2", "Вода БОРЖОМИ 0,5л")])
    refresh.match_all()
    pid = bought("ВОДА БОРЖОМИ 0,5Л")

    link.link_products()

    assert sku(pid, "lenta") == "2"
    assert sku(pid, "metro") is None


# ---------- №6: «3в1 стик 20шт» ----------
def test_three_in_one_does_not_become_one_piece():
    assert match.pack_count("Капсулы 3в1 стик 20г 20шт") == 20
    assert match.pack_count("Таблетки 3 в 1 стик 20шт") == 20
    assert match.pack_count("Капсулы All in 1 таблетки 30шт") == 30


def test_pieces_glued_to_a_letter_still_count():
    """Обратная сторона правила: «Smile15шт» и «FINISH60ШТ» — не одна штука."""
    assert match.pack_count("Пастилки Smile15шт") == 15
    assert match.pack_count("Таблетки FINISH60ШТ") == 60


def test_three_in_one_stick_is_not_glued_with_a_pack(catalog):
    store.upsert_products("lenta", [P("1", "Капсулы 3в1 стик 20г")])
    store.upsert_products("magnit", [P("2", "Капсулы 3в1 стик 20г 20шт")])
    refresh.match_all()

    assert item_of("lenta", "1") != item_of("magnit", "2")


# ---------- №7: яйца «10 шт» без массы ----------
def test_counted_eggs_link_strongly(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Яйца куриные отборные 10 шт")])
    store.upsert_products("magnit", [P("2", "Яйца куриные отборные 10 шт")])
    refresh.match_all()
    pid = bought("ЯЙЦА КУРИНЫЕ ОТБОРНЫЕ 10 ШТ")

    report = link.link_products()

    assert (report.strong, report.weak) == (1, 0)
    assert sku(pid, "lenta") == "1" and sku(pid, "magnit") == "2"


def test_counted_does_not_mix_different_numbers():
    four = match.prepare({"id": 1, "chain": "a", "name": "Подгузники Pampers 4 46 шт"})
    five = match.prepare({"id": 2, "chain": "b", "name": "Подгузники Pampers 5 46 шт"})
    twin = match.prepare({"id": 3, "chain": "c", "name": "Подгузники Pampers 4 46 шт"})

    assert link._counted(four, five) is False        # 4 против 5: числа названий спорят
    assert four is not twin
    assert link._counted(four, twin) is True         # те же числа у РАЗНЫХ строк — один товар


# ---------- №8: «＋» товара каталога без фасовки ----------
def test_plus_of_a_catalog_item_without_packaging_is_a_human_choice(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Батон нарезной")])
    store.upsert_products("magnit", [P("2", "Батон нарезной")])
    refresh.match_all()
    number = item_of("lenta", "1")

    pid = refresh.adopt(number)                 # «＋» на «Корзине» — явный выбор человека
    assert sku(pid, "lenta") == "1" and sku(pid, "magnit") == "2"

    # Правила №8 автоматика не получает: без «＋» та же строка остаётся кандидатом.
    other = repo.upsert_product(Product(id=None, name="Батон нарезной", unit="pcs"))
    with store.connect() as conn:
        confirmed, pending = link.materialize(repo.get_product(other), number, True, conn=conn)
    assert confirmed == {} and "lenta" in pending


# ---------- №9: весовое без названной единицы ----------
def test_weighed_word_gives_the_unit_the_chain_is_silent_about():
    row = match.prepare({"id": 1, "chain": "metro", "name": "Сыр Российский, весовые"})
    assert row.unit == "kg"


def test_weighed_and_packed_are_different_items(catalog):
    store.upsert_products("metro", [P("1", "Сыр Российский, весовые")])
    store.upsert_products("lenta", [P("2", "Сыр Российский 250г")])
    refresh.match_all()

    assert item_of("metro", "1") != item_of("lenta", "2")


# ---------- №14: выбор человека, якорь, выведенный эталон ----------
def test_shortened_receipt_disputes_a_reassigned_article():
    """«КЕФ.ПРОСТОКВ.» и выбор «Ряженка Простоквашино»: строгий суд обязан возразить."""
    person = [link._prep({"id": 0, "chain": "mine", "name": "КЕФ.ПРОСТОКВ."}, receipt=True)]
    choice = link._prep({"id": 1, "chain": "vkusvill", "name": "Ряженка Простоквашино 930г"})

    assert link._disputed(person, choice, strict=True) is True


def test_derived_etalon_keeps_its_head(catalog, user_db):
    """Эталон из чека «КЕФ.ПРОСТОКВАШИНО» терял «КЕФ.»: normalize._PREFIX_RE съедала голову."""
    pid = bought("КЕФ.ПРОСТОКВАШИНО")

    person = link._rows_of_person(repo.get_product(pid), repo.raw_names(pid))

    assert len(person) == 2
    assert all("kef" in r.fwords for r in person)


def test_slash_mark_finds_the_marked_product(catalog, user_db):
    """«Б/САХ» — метка «без сахара»: раньше метки не сходились и товар не находился."""
    store.upsert_products("lenta", [P("1", "Йогурт ЭКОНИВА без сахара 2,5% 300г")])
    refresh.match_all()
    pid = bought("ЙОГУРТ ЭКОНИВА Б/САХ 2,5% 300Г")

    link.link_products()

    assert sku(pid, "lenta") == "1"


# ---------- контроли задачи: они обязаны уцелеть ----------
def test_controls_of_the_task_survive():
    light = match.prepare({"id": 1, "chain": "mine", "name": "ПИВО БАЛТИКА СВЕТЛОЕ Б/А 0,45Л"})
    plain = match.prepare({"id": 2, "chain": "lenta", "name": "Пиво БАЛТИКА светлое 0,45л"})
    assert link._judge(light, plain, frozenset())[0] is False        # «Б/А» — своё слово

    assert match.glue("КОЛБАСА Б/К 400Г").lower().endswith("безк 400г")
    assert link._starts("безкожи", "безк")                          # «Б/К» не теряет «без кожи»

    assert match.glue("КОНФЕТЫ Н-Р 250Г") == "КОНФЕТЫ Н-Р 250Г"     # «Н-Р» — набор, не отрицание
    assert match.glue("БАНАН-КЛУБНИКА") == "БАНАН-КЛУБНИКА"
    assert match.glue("Б.Ю.АЛЕКСАНДРОВ") == "Б.Ю.АЛЕКСАНДРОВ"

    assert match.pack_count("Суп 250г") == 1
    assert match.prepare({"id": 3, "chain": "mine", "name": "Суп 250г"}).weight_g == 250

    bare = match.prepare({"id": 4, "chain": "mine", "name": "ВОДА БОРЖОМИ 0,5Л"})
    for named in ("ВОДА БОРЖОМИ 0,5Л СТ/Б", "ВОДА БОРЖОМИ 0,5Л Ж/Б"):
        assert match.prepare({"id": 5, "chain": "mine", "name": named}).fwords == bare.fwords

    with_zmzh = match.prepare({"id": 6, "chain": "mine", "name": "Сырок глазированный 40г с змж"})
    bzmzh = match.prepare({"id": 7, "chain": "mine", "name": "Сырок глазированный 40Г БЗМЖ"})
    assert "szmz" in with_zmzh.fwords and with_zmzh.fwords != bzmzh.fwords
