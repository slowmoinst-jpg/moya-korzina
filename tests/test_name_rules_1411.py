r"""Названия из чека Пятёрочки: что правила берут, а что нет (замер семи названий 14.11.2026).

ЗАЧЕМ. Корзина, заведённая повтором старого заказа, носит названия из чека, и у части из них в
других сетях артикулов нет, хотя единая база товар знает. Разбор семи названий на боевой базе
(«Свитл сырок твор гл ван 45 г», «Пятерочка 65х40см», «Батон домашний нарез 230 г», «Предп мука
2 кг», «Отруби хруст овсян 230 г», «Морковь 1 кг», «Ясно солн хлопья овсян 3 350 г») нашёл две
дыры, которые правила закрывают без потери точности, и пять границ, которые правила обходить
не должны. Что здесь проверяется:

1. «Морковь весовая 1кг»: чек сам называет товар весовым, а «1кг» — цена за килограмм. Импорт
   ставил такому товару «pcs» и 1000 г (ofd_pdf: `and not grams`), и он не встречал весовую
   «Морковь» сетей: спор единиц рвал пару, а выбор человека по весовой строке считался
   возражением. Строки человека берут единицу и фасовку у чека (link._rows_of_person);
2. «Батон ДОМАШНИЙ НАРЕЗ.230г» (так пишет Пятёрочка): match.prepare принимал заглавный хвост за
   марку, и она спорила с маркой «Хлебный дом» у «Батон Хлебный Дом Домашний нарезанный 230 г».
   Мост человека вёл к этому товару, а строки Дикси, Метро и ВкусВилла не подтверждались.
   Спор снимается, только когда каждое заглавное слово есть в названии кандидата (link._fit);
   чужая марка («ПРОСТОКВАШИНО» против «Данон») спорит как раньше;
3. границы, которые остаются (проверяются «контролем»: они держались и до правки):
   фасовка 45 г против 50 г — слабая связь, а не сильная (3 %); строка сети без марки — только
   слабая связь;
4. «ПРЕДП.Мука в/с 2кг» (разбор пяти позиций 14.11.2026, вечер): «в/с» — «высший сорт», а
   match.prepare роняет его как однобуквенный мусор, и «Мука Предпортовая пшеничная высший сорт»
   оставалась непокрытой словами. Теперь «в/с» чека покрывает слова «высший сорт» кандидата, а у
   муки — ещё «пшеничная» (у сорта «в/с» вид один). Кандидат обязан сам назвать высший сорт или
   тоже написать «в/с»: «1 сорт», «кукурузная», «ржаная», лишнее слово («хлебопекарная»), чужая
   марка, другая фасовка и чек без «в/с» связи не дают (link._graded).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import config, repo
from app.catalog import link, refresh, store
from app.catalog.model import ChainProduct as P
from app.db import init_db
from app.importers.ofd_pdf import _ensure_product


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


def bought(raw: str, qty: float = 1) -> int:
    """Товар, заведённый как при загрузке чека: эталон — display_name строки."""
    pid, _ = _ensure_product(raw, qty)
    repo.add_history_row("2026-10-10", None, pid, raw, qty, 99.0, 99.0, f"k-{pid}-{raw}")
    return pid


def chains() -> dict[str, int]:
    return {s.code: s.id for s in repo.list_stores()}


def sku(pid: int, chain: str) -> str | None:
    found = repo.confirmed_mapping(pid, chains()[chain], real_only=True)
    return found["sku"] if found else None


def pick(pid: int, chain: str, number: str, name: str, **kw) -> None:
    """Выбор человека: он сам подтвердил артикул в сети (мост)."""
    repo.confirm_mapping(pid, repo.upsert_store_product(chains()[chain], number, name, **kw))


def verdict(receipt: str, name: str, brand: str | None, weight_g: float | None = None):
    """Вердикт link._judge для строки чека и строки единого товара."""
    mine = link._prep({"id": 1, "chain": "mine", "name": receipt}, receipt=True)
    cand = link._prep({"id": 2, "chain": "other", "name": name, "brand": brand, "weight_g": weight_g})
    return link._judge(mine, cand, mine.brand_words | cand.brand_words)


def person(name: str, raw: list[str], *, weight_g=None, unit="pcs"):
    product = SimpleNamespace(id=0, name=name, brand=None, weight_g=weight_g, unit=unit, barcode=None)
    return link._rows_of_person(product, raw)


# ---------- 1. «весовая» в чеке делает строки человека весовыми ----------
def test_receipt_that_calls_the_product_weighed_makes_the_person_rows_weighed():
    rows = person("Морковь 1 кг", ["Морковь весовая             1кг"], weight_g=1000.0)

    assert rows and all(r.unit == "kg" and r.weight_g is None for r in rows)


def test_receipt_without_the_weighed_word_keeps_the_pack():
    rows = person("Морковь мытая 1 кг", ["Морковь мытая 1кг"], weight_g=1000.0)

    assert rows and all(r.unit == "pcs" and r.weight_g == 1000.0 for r in rows)


def test_one_receipt_line_that_is_silent_about_the_weight_keeps_the_rows_as_they_were():
    rows = person("Морковь 1 кг", ["Морковь весовая 1кг", "Морковь 1кг"], weight_g=1000.0)

    assert rows and all(r.unit == "pcs" and r.weight_g == 1000.0 for r in rows)


def test_weighed_receipt_finds_the_weighed_product_and_not_the_packed_one(catalog, user_db):
    # Дикси пишет у весового «1000 г» (298 из 307 весовых строк на 14.11.2026), Магнит — без фасовки
    store.upsert_products("dixy", [P("1", "Морковь", unit="kg", weight_g=1000)])
    store.upsert_products("magnit", [P("2", "Морковь", unit="kg")])
    store.upsert_products("lenta", [P("3", "Морковь мытая 1кг", weight_g=1000, unit="pcs")])
    refresh.match_all()
    pid = bought("Морковь весовая             1кг")

    link.link_products()

    assert sku(pid, "dixy") == "1" and sku(pid, "magnit") == "2"
    assert sku(pid, "lenta") is None            # мытая морковь по килограмму в пакете — другой товар


def test_the_kilogram_that_dixy_writes_for_a_weighed_row_is_not_a_pack():
    mine = person("Морковь 1 кг", ["Морковь весовая 1кг"], weight_g=1000.0)[0]

    def judged(weight_g, unit="kg"):
        cand = link._prep({"id": 2, "chain": "dixy", "name": "Морковь", "weight_g": weight_g, "unit": unit})
        return link._judge(mine, cand, frozenset())

    assert judged(1000.0)[0] and judged(None)[0]
    assert judged(500.0)[0] is False            # весовая со своей фасовкой — это уже не килограмм цены
    assert judged(1000.0, unit="pcs") is None   # пакет в килограмм — не весовой товар


def test_weighed_receipt_keeps_the_bridge_of_the_human_choice(catalog, user_db):
    store.upsert_products("magnit", [P("1", "Морковь", unit="kg")])
    store.upsert_products("dixy", [P("2", "Морковь", unit="kg")])
    store.upsert_products("perekrestok", [P("3", "Морковь", unit="kg")])
    refresh.match_all()
    pid = bought("Морковь весовая             1кг")
    pick(pid, "magnit", "1", "Морковь", unit="kg")      # выбор человека по весовой строке

    link.link_products()

    assert repo.master_of(pid)["method"] == "bridge"
    assert sku(pid, "dixy") == "2" and sku(pid, "perekrestok") == "3"


# ---------- 2. заглавный хвост чека не спорит с маркой кандидата ----------
BATON = "Батон Хлебный Дом Домашний нарезанный 230 г"


def agrees(receipt: str, name: str, brand: str, weight_g: float | None = None) -> bool:
    mine = link._prep({"id": 1, "chain": "mine", "name": receipt}, receipt=True)
    cand = link._prep({"id": 2, "chain": "other", "name": name, "brand": brand, "weight_g": weight_g})
    return link._agrees([mine], cand)


def test_caps_tail_of_the_receipt_is_not_a_foreign_brand():
    assert agrees("Батон ДОМАШНИЙ НАРЕЗ.230г", BATON, "Хлебный дом", 230.0)


@pytest.mark.parametrize("receipt, name, brand", [
    # заглавное слово — настоящая марка, и кандидат её не называет
    ("Йогурт ПРОСТОКВАШИНО клубника 230г", "Йогурт Данон клубника 230 г", "Данон"),
    ("Батон ДОМАШНИЙ НАРЕЗ.230г", "Батон Колибри нарезной 230 г", "Колибри"),
    # одно слово кандидатом покрыто, другое нет: спор остаётся
    ("Батон ДОМАШНИЙ КОЛИБРИ 230г", BATON, "Хлебный дом"),
])
def test_a_caps_word_that_the_candidate_does_not_name_still_disputes_its_brand(receipt, name, brand):
    assert not agrees(receipt, name, brand)


def test_the_bridge_of_a_receipt_with_a_caps_tail_confirms_the_other_chains(catalog, user_db):
    for chain, number in (("magnit", "1"), ("dixy", "2"), ("metro", "3"), ("vkusvill", "4")):
        store.upsert_products(chain, [P(number, BATON, brand="Хлебный дом", weight_g=230)])
    refresh.match_all()
    pid = bought("Батон ДОМАШНИЙ НАРЕЗ.230г")
    pick(pid, "magnit", "1", BATON, weight_g=230)       # выбор человека в Магните

    link.link_products()

    assert repo.master_of(pid)["method"] == "bridge"
    assert [sku(pid, c) for c in ("dixy", "metro", "vkusvill")] == ["2", "3", "4"]


# ---------- 3. границы, которые остаются ----------
SYROK = "Сырок творожный глазированный СВИТЛОГОРЬЕ с ванилином 26%, без змж, {}г"


def test_a_pack_of_another_size_is_a_weak_link_and_the_same_size_is_a_strong_one():
    receipt = "СВИТЛ.Сырок твор.гл.с ван.45г"

    assert verdict(receipt, SYROK.format(50), "СВИТЛОГОРЬЕ", 50.0)[0] is False      # 45 и 50 г — 10 %
    assert verdict(receipt, SYROK.format(45), "СВИТЛОГОРЬЕ", 45.0)[0] is True


def test_a_row_without_a_brand_is_only_a_weak_link():
    verdict_ = verdict("ЯСНО СОЛН.Хлопья ОВСЯН.№3  350г", "hlopya ovsyanye yasno solnyshko 3 350g rossiya",
                       None, 350.0)

    assert verdict_ is not None and verdict_[0] is False


# ---------- 4. «в/с» чека — высший сорт ----------
FLOUR = "ПРЕДП.Мука в/с       2кг"
DIXY_FLOUR = "Мука Предпортовая пшеничная высший сорт 2кг"


def test_the_grade_abbreviation_of_the_receipt_takes_the_flour_that_names_the_top_grade():
    assert verdict(FLOUR, DIXY_FLOUR, "Предпортовая", 2000.0) == (True, 0.8, "abbrev")
    # кандидат пишет то же сокращение: «пшеничная» у муки сорта «в/с» — единственный вид
    assert verdict(FLOUR, "Мука пшеничная Предпортовая в/с 2 кг", "Предпортовая", 2000.0)[0] is True


@pytest.mark.parametrize("name, brand, weight_g", [
    ("Мука Предпортовая пшеничная 1 сорт 2кг", "Предпортовая", 2000.0),                      # другой сорт
    ("Мука Предпортовая кукурузная высший сорт 2кг", "Предпортовая", 2000.0),                # другой вид
    ("Мука Предпортовая ржаная обдирная 2кг", "Предпортовая", 2000.0),
    ("Мука Предпортовая пшеничная хлебопекарная высший сорт 2кг", "Предпортовая", 2000.0),   # лишнее слово
    ("Мука Предпортовая Пшеничная 2кг", None, 2000.0),                                       # сорт не назван
    ("Мука пшеничная Макфа высший сорт 2кг", "Макфа", 2000.0),                               # чужая марка
    ("Мука Предпортовая пшеничная высший сорт ГОСТ, 2кг", "ПРЕДПОРТОВАЯ", 2000.0),           # ГОСТ — не описание
    ("Мука пшеничная Предпортовая в/с 2 кг", "Петербургский мельничный комбинат", 2000.0),   # марка поля чужая
    ("Мука Предпортовая пшеничная высший сорт 1кг", "Предпортовая", 1000.0),                 # другая фасовка
])
def test_the_grade_abbreviation_takes_neither_another_grade_nor_kind_nor_brand_nor_pack(name, brand, weight_g):
    assert verdict(FLOUR, name, brand, weight_g) is None


def test_a_receipt_that_does_not_say_the_grade_takes_no_flour_of_the_top_grade():
    assert verdict("ПРЕДП.Мука 2кг", DIXY_FLOUR, "Предпортовая", 2000.0) is None


def test_the_flour_without_a_brand_field_is_only_a_weak_link():
    verdict_ = verdict(FLOUR, DIXY_FLOUR, None, 2000.0)

    assert verdict_ is not None and verdict_[0] is False


def test_the_flour_of_the_grade_abbreviation_lands_where_the_chain_names_the_grade(catalog, user_db):
    store.upsert_products("dixy", [P("1", DIXY_FLOUR, brand="Предпортовая", weight_g=2000)])
    store.upsert_products("magnit", [P("2", "Мука Предпортовая Пшеничная 2кг", weight_g=2000)])
    store.upsert_products("lenta", [P("3", "Мука Предпортовая кукурузная высший сорт 2кг",
                                      brand="Предпортовая", weight_g=2000)])
    refresh.match_all()
    pid = bought(FLOUR)

    link.link_products()

    assert sku(pid, "dixy") == "1"
    assert sku(pid, "magnit") is None and sku(pid, "lenta") is None      # сорт не назван; другой вид


# ---------- 5. «нарез.» чека и батон без слова «нарезанный» у Дикси и METRO (замер 15.11.2026) ----------
DIXY_BATON = "Батон Хлебный Дом Домашний 230г"
METRO_BATON = "Батон Хлебный дом Домашний, 230г"


def judged(anchor: str, name: str, brand: str | None, weight_g: float | None = 230.0):
    """Вердикт строки сети человека (якоря) для строки другой сети."""
    mine = link._prep({"id": 1, "chain": "magnit", "name": anchor, "brand": "Хлебный дом", "weight_g": 230.0})
    cand = link._prep({"id": 2, "chain": "dixy", "name": name, "brand": brand, "weight_g": weight_g})
    return link._judge(mine, cand, mine.brand_words | cand.brand_words)


def test_the_cut_of_the_receipt_is_a_form_and_not_a_brand_that_the_candidate_must_name():
    assert agrees("Батон ДОМАШНИЙ НАРЕЗ.230г", DIXY_BATON, "Хлебный Дом", 230.0)
    assert agrees("Батон ДОМАШНИЙ НАРЕЗ.230г", METRO_BATON, "ХЛЕБНЫЙ ДОМ", 230.0)


@pytest.mark.parametrize("receipt, name, brand", [
    ("Батон ДОМАШНИЙ НАРЕЗ.230г", "Батон Хлебный Дом Утренний 230г", "Хлебный Дом"),   # «домашний» не назван
    ("Батон ДОМАШНИЙ НАРЕЗ.230г", "Батон Колибри нарезной 230 г", "Колибри"),
    ("Батон ДОМАШНИЙ КОЛИБРИ НАРЕЗ.230г", DIXY_BATON, "Хлебный Дом"),                  # настоящая марка в хвосте
])
def test_the_cut_does_not_hide_the_other_caps_words_of_the_receipt(receipt, name, brand):
    assert not agrees(receipt, name, brand)


def test_a_row_that_does_not_say_sliced_is_the_same_loaf_when_brand_and_pack_agree():
    assert judged(BATON, DIXY_BATON, "Хлебный Дом")[0] is True
    assert judged(BATON, METRO_BATON, "ХЛЕБНЫЙ ДОМ")[0] is True


def test_what_the_cut_does_not_take_is_another_loaf_or_another_pack():
    stuffed = judged(BATON, "Батон Хлебный Дом Домашний с изюмом 230г", "Хлебный Дом")
    assert stuffed is None or stuffed[0] is False        # лишнее слово — начинка, а не нарезка
    assert judged(BATON, "Батон Хлебный Дом Домашний 350г", "Хлебный Дом", 350.0) is None
    assert judged(BATON, "Батон Колибри Домашний 230г", "Колибри") is None


def test_the_bridge_of_a_receipt_with_a_cut_tail_confirms_the_chains_that_do_not_say_sliced(catalog, user_db):
    store.upsert_products("magnit", [P("1", BATON, brand="Хлебный дом", weight_g=230)])
    store.upsert_products("dixy", [P("2", DIXY_BATON, brand="Хлебный Дом", weight_g=230)])
    store.upsert_products("metro", [P("3", METRO_BATON, brand="ХЛЕБНЫЙ ДОМ", weight_g=230)])
    store.upsert_products("lenta", [P("4", "Батон Хлебный Дом Утренний 230г", brand="Хлебный Дом", weight_g=230)])
    refresh.match_all()
    pid = bought("Батон ДОМАШНИЙ НАРЕЗ.230г")
    pick(pid, "magnit", "1", BATON, weight_g=230)       # выбор человека в Магните

    link.link_products()

    assert repo.master_of(pid)["method"] == "bridge"
    assert [sku(pid, c) for c in ("dixy", "metro")] == ["2", "3"]
    assert sku(pid, "lenta") is None                    # «Утренний» — другой батон
