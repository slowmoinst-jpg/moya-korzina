r"""Правила названий: тара с точкой, латиница, запятая, «Б.А.» (18.10.2026).

ЗАЧЕМ. Независимое ревью diff нашло четыре дыры в правилах app/catalog/match.py, и
часть подтверждена исполнением. Каждый тест здесь сформулирован на поведение ДО правки
и на ней падал:

1. тара с точкой — `glue("ВОДА СТ.Б.ГАЗ 0,5Л")` читала «Б.ГАЗ» как «без газа», и
   ГАЗИРОВАННАЯ вода становилась негазированной (ошибочный товар, а не потеря связи);
2. латиница — правила упаковки словами (`уп.12`, `пак 6`, `0,5л х 12`) стояли только
   в кириллической ветке `pack_count`, а Перекрёсток, Впрок и Монетка пишут латиницей:
   «Voda up.12 0,5l» и «Pivo pak 6 0,45l» читались одной штукой;
3. запятая-разделитель — `pack_count("Вода, упаковка 12, 0,5 л")` = 1 из-за отсечки
   `(?![\d.,])`, запрещавшей и запятую между числом и следующим словом;
4. «Б.А.»/«Б-А» — безалкогольное пиво: точка и дефис не склеивались, однобуквенные
   «б» и «а» выбрасывались, и светлое пиво сходилось с алкогольным.

Контроли задачи — «Б/А» против алкогольного, «Б/К», «Н-Р», «Суп 250г», тара С/Б и Ж/Б,
«С ЗМЖ» против «БЗМЖ» — проверяются и здесь, и в tests/test_negation_fixes.py.
"""
from __future__ import annotations

import pytest

from app.catalog import link, match, refresh, store
from app.catalog.model import ChainProduct as P
from app.matcher import normalize


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    yield


def prep(name: str, *, ident: int = 1, chain: str = "mine"):
    return match.prepare({"id": ident, "chain": chain, "name": name})


# ---------- 1. тара с точкой — не отрицание ----------
@pytest.mark.parametrize("name", ["ВОДА БОРЖОМИ СТ.Б.ГАЗ 0,5Л",
                                  "ВОДА БОРЖОМИ Ж.Б.ГАЗ 0,5Л",
                                  "ВОДА БОРЖОМИ ПЛ.Б.ГАЗ 0,5Л"])
def test_dotted_tare_is_not_read_as_a_negation(name):
    """«СТ.Б.ГАЗ» — стеклянная бутылка и газ, а не «без газа»."""
    glued = match.glue(name)
    assert "БЕЗ" not in glued.upper()
    assert "ГАЗ" in glued.upper()
    assert "СТ" not in glued.upper().split()


@pytest.mark.parametrize("name", ["ВОДА БОРЖОМИ СТ.Б.ГАЗ 0,5Л",
                                  "ВОДА БОРЖОМИ Ж.Б.ГАЗ 0,5Л",
                                  "ВОДА БОРЖОМИ ПЛ.Б.ГАЗ 0,5Л",
                                  "ВОДА БОРЖОМИ СТ/Б ГАЗ 0,5Л"])
def test_gas_water_does_not_become_still(name):
    """Газированная вода находит газированную и НЕ сходится с негазированной."""
    gas = prep("Вода БОРЖОМИ газ 0,5л", chain="lenta")
    still = prep("Вода БОРЖОМИ без газа 0,5л", chain="lenta")
    row = prep(name, ident=0)

    assert match.same_product(row, gas)[0] is True
    assert match.same_product(row, still)[0] is False


def test_receipt_line_with_dotted_tare_keeps_gas():
    """Строка чека идёт через link._prep: склейка до снятия точек — тара тоже тара."""
    gas = prep("Вода БОРЖОМИ газ 0,5л", chain="lenta")
    for name in ("ВОДА БОРЖОМИ СТ.Б.ГАЗ 0,5Л", "ВОДА БОРЖОМИ Ж.Б.ГАЗ 0,5Л"):
        mine = link._prep({"id": 0, "chain": "mine", "name": name}, receipt=True)
        assert match.same_product(mine, gas)[0] is True


@pytest.mark.parametrize("tare", ["СТ/Б", "Ж/Б", "СТ.Б.", "Ж.Б.", "ПЛ.Б."])
def test_tare_in_any_writing_does_not_change_the_product(tare):
    bare = prep("ВОДА БОРЖОМИ 0,5Л")
    named = prep(f"ВОДА БОРЖОМИ 0,5Л {tare}", ident=5)
    assert named.fwords == bare.fwords


def test_dotted_tare_does_not_split_the_catalog(catalog):
    """Строка сети с тарой «СТ.Б.» и без неё — один единый товар."""
    store.upsert_products("lenta", [P("1", "ВОДА БОРЖОМИ ГАЗ 0,5Л СТ.Б.")])
    store.upsert_products("magnit", [P("2", "Вода БОРЖОМИ газ 0,5л")])
    refresh.match_all()

    rows = {r["sku"]: r["item_id"] for r in store.all_products()}
    assert rows["1"] == rows["2"]


# ---------- 2. латинская упаковка ----------
@pytest.mark.parametrize("name, packs", [
    ("Voda up.12 0,5l", 12),
    ("Voda up 12 0,5l", 12),
    ("Voda upakovka 12", 12),
    ("Voda 12 up 0,5l", 12),
    ("Pivo pak 6 0,45l", 6),
    ("Sok kor. 27", 27),
    ("Voda butylok 6", 6),
    ("Voda 0,5l x 12", 12),
    ("Voda 0,5l kh 12", 12),
])
def test_latin_packaging_words_are_counted(name, packs):
    assert match.pack_count(name) == packs


@pytest.mark.parametrize("name", [
    "pivo zhigulevskoe 4 0 5 st b",           # «st b» — стеклянная бутылка, не штуки
    "antiperspirant rexona men 48h 150ml",    # «h» — часы, не множитель
    "napitok 7up 0,5l",                       # «7up» — марка, а не семь упаковок
    "salfetki 24kh24 sm",                     # размер, не упаковка
    "voda borzhomi 0,5l",                     # упаковка не названа — одна штука
])
def test_latin_packaging_words_do_not_invent_a_pack(name):
    assert match.pack_count(name) == 1


def test_latin_pack_does_not_merge_with_one_bottle(catalog):
    """Упаковка из 12 латиницей не сходится с одной бутылкой."""
    store.upsert_products("perekrestok", [P("1", "voda borzhomi 0,5l up.12")])
    store.upsert_products("lenta", [P("2", "Вода БОРЖОМИ 0,5л")])
    refresh.match_all()

    rows = {r["sku"]: r["item_id"] for r in store.all_products()}
    assert rows["1"] != rows["2"]


def test_latin_and_cyrillic_pack_of_twelve_are_one_item(catalog):
    """«up.12» латиницей и «уп.12» кириллицей — та же упаковка, тот же товар."""
    store.upsert_products("perekrestok", [P("1", "voda borzhomi 0,5l up.12")])
    store.upsert_products("metro", [P("2", "Вода БОРЖОМИ 0,5л уп.12")])
    refresh.match_all()

    rows = {r["sku"]: r["item_id"] for r in store.all_products()}
    assert rows["1"] == rows["2"]


# ---------- 3. запятая-разделитель ----------
@pytest.mark.parametrize("name, packs", [
    ("Вода, упаковка 12, 0,5 л", 12),
    ("Voda, upakovka 12, 0,5 l", 12),
    ("Вода уп.12, 0,5 л", 12),
])
def test_comma_is_a_separator_not_a_fraction(name, packs):
    assert match.pack_count(name) == packs


@pytest.mark.parametrize("name", [
    "Вода упаковка 12,5 л",     # дробь, а не 12
    "Вода уп. 123",              # трёхзначное число — не 12
    "Творог упаковка 300 г",     # вес, а не штуки
    "Печенье КОР. 45%",          # процент, а не штуки
    "Суп 250г",
])
def test_comma_does_not_turn_weight_or_fraction_into_packs(name):
    assert match.pack_count(name) == 1


# ---------- 4. «Б.А.»/«Б-А» — безалкогольное ----------
def test_dotted_light_beer_does_not_meet_alcoholic():
    """Светлое пиво «Б.А.» не подтверждается алкогольным пивом без метки."""
    plain = prep("Пиво БАЛТИКА светлое 0,45л", chain="lenta")
    for name in ("ПИВО БАЛТИКА СВЕТЛОЕ Б.А. 0,45Л", "ПИВО БАЛТИКА СВЕТЛОЕ Б-А 0,45Л"):
        mine = link._prep({"id": 0, "chain": "mine", "name": name}, receipt=True)
        assert "беза" in mine.words
        assert link._judge(mine, plain, frozenset())[0] is False


def test_three_writings_of_nonalcoholic_are_the_same_word():
    """«Б/А», «Б.А.» и «Б-А» — одна метка: записи одного товара читаются одинаково."""
    rows = [prep(f"ПИВО БАЛТИКА СВЕТЛОЕ {form} 0,45Л", ident=i)
            for i, form in enumerate(("Б/А", "Б.А.", "Б-А"), start=1)]
    assert rows[0].words == rows[1].words == rows[2].words
    assert "беза" in rows[0].words


@pytest.mark.parametrize("name", [
    "СЫРОК Б.Ю.АЛЕКСАНДРОВ 40Г",     # инициалы на этикетке — не метка
    "ПИВО СВЕТЛОЕ Б.А.ИВАНОВ 0,5Л",
    "КОНФЕТЫ Н-Р 250Г",               # набор, а не отрицание
    "БАНАН-КЛУБНИКА",
])
def test_initials_and_hyphenated_words_are_not_negations(name):
    assert match.glue(name) == name


# ---------- контроли задачи ----------
def test_controls_stay():
    assert match.glue("КОЛБАСА Б/К 400Г").lower().endswith("безк 400г")
    assert link._starts("безкожи", "безк")
    assert match.pack_count("Суп 250г") == 1
    assert match.glue("Н-Р 250Г") == "Н-Р 250Г"

    light = link._prep({"id": 0, "chain": "mine", "name": "ПИВО БАЛТИКА СВЕТЛОЕ Б/А 0,45Л"},
                       receipt=True)
    plain = prep("Пиво БАЛТИКА светлое 0,45л", ident=2, chain="lenta")
    assert link._judge(light, plain, frozenset())[0] is False

    bare = prep("ВОДА БОРЖОМИ 0,5Л", ident=3)
    for named in ("ВОДА БОРЖОМИ 0,5Л СТ/Б", "ВОДА БОРЖОМИ 0,5Л Ж/Б"):
        assert prep(named, ident=4).fwords == bare.fwords

    with_zmzh = prep("Сырок глазированный 40г с змж", ident=6)
    bzmzh = prep("Сырок глазированный 40Г БЗМЖ", ident=7)
    assert "szmz" in with_zmzh.fwords and with_zmzh.fwords != bzmzh.fwords


# ---------- 5. нормализация: одиночные цифры и служебные слова (19.10.2026) ----------
#
# ЗАЧЕМ. Пункт владельца 3: «матчер должен игнорировать короткие предлоги и одиночные
# цифры». Разведка показала: в СЛОВАХ однобуквенные предлоги и цифры уже не живут
# (ordered_words режет цифры, правило «короче двух букв» — однобуквенные). Дыра была
# в другом месте — в самом нормализованном имени: «Желтое манго 1» давало ключ с
# лишней единицей, а двухбуквенные «со/из/на/по/до/от/за/во/об/при/под/над» оставались
# словами и разводили один товар. Правка — в app/matcher/normalize.py.

def test_real_pairs_from_the_measurement_become_one_name():
    """Пары боевого замера 19.10.2026: расходились ровно на служебных словах и цифре."""
    pairs = [
        ("Онигири семгой соусом спайси", "Онигири с семгой и соусом Спайси"),
        ("Сосиска тесте", "Сосиска в тесте"),
        ("Пиво бочки", "Пиво из бочки"),
        ("Вода льдом", "Вода со льдом"),
    ]
    for receipt, catalog in pairs:
        assert normalize.normalize_name(receipt) == normalize.normalize_name(catalog), receipt

    # «Желтое манго 1» и «Манго желтое»: порядок слов разный, лишняя цифра ушла —
    # набор слов один и тот же (сравнение идёт множествами, а не строкой).
    one = normalize.normalize_name("Желтое манго 1")
    two = normalize.normalize_name("Манго желтое")
    assert one == "желтое манго" and set(one.split()) == set(two.split())
    assert prep("Желтое манго 1", ident=1).words == prep("Манго желтое", ident=2).words


@pytest.mark.parametrize("name, expected", [
    ("Сыр Российский 5% 200 г", "российский 5 200 г"),   # процент остаётся
    ("Сыр Российский 200 г", "российский 200 г"),        # фасовка остаётся
    ("Вода 0,5 л", "вода 500 г"),                        # фасовка остаётся
    ("Вода 4x125 г", "вода 4x 125 г"),                   # множитель упаковки остаётся
    ("Яйца С1 10 шт", "яйца с1 10"),                     # число штук остаётся
    ("Каша 7 злаков", "каша 7 злаков"),                  # счётное слово держит число
    ("Капсулы 3 в 1 стик 20шт", "капсулы 3 1 стик 20шт"),  # «3 в 1» — название
    ("Кабель 2 м", "кабель 2"),                          # длина держит число
    ("Памперсы Pampers 4 46 шт", "памперсы pampers 4 46"),  # размер и количество
    ("Туалетная бумага Zemma 3 слоя 32шт", "туалетная бумага zemma 3 слоя 32шт"),  # слоёв
    ("Яйца 1 категория", "яйца 1 категория"),            # категория держит цифру
    ("Крем №2 50 мл", "крем 2 50 г"),                    # номер по счёту
])
def test_numbers_that_say_something_stay(name, expected):
    assert normalize.normalize_name(name) == expected


def test_a_lone_digit_no_longer_lives_in_the_name():
    assert normalize.normalize_name("Желтое манго 1") == "желтое манго"
    assert normalize.display_name("Желтое манго 1") == "Желтое манго"


def test_multi_digit_numbers_are_not_touched():
    """Режем только ОДНУ цифру: многозначные — коды и размеры, их терять нельзя."""
    assert normalize.normalize_name("Желтое манго 10") == "желтое манго 10"
    pasta = normalize.normalize_name("Макароны Шебекинские спирали №366 450г")
    assert "366" in pasta.split() and "450" in pasta.split()
    assert normalize.normalize_name("Макароны Шебекинские спирали №366 450г") \
        != normalize.normalize_name("Макароны Шебекинские спирали №370 450г")
    assert "9000000000" in normalize.normalize_name("Аванс за услуги связи 9000000000").split()
    # «№1» и «№2» — номер по счёту: цифра при знаке номера остаётся.
    assert normalize.normalize_name("Крем №1 50 мл") != normalize.normalize_name("Крем №2 50 мл")


def test_digit_inside_a_word_is_not_a_lone_digit():
    assert normalize.normalize_name("Лампа 60W") == "лампа 60w"
    assert normalize.normalize_name("Лампа 75W") == "лампа 75w"
    assert normalize.normalize_name("Лампа 60W") != normalize.normalize_name("Лампа 75W")
    assert "с1" in normalize.normalize_name("Яйца С1 10 шт").split()


def test_two_letter_service_words_are_not_words():
    for word in ("со", "из", "на", "по", "до", "от", "за", "во", "об", "при", "под", "над"):
        words = normalize.normalize_name(f"молоко {word} сахар 1 л").split()
        assert word not in words, word
        assert "молоко" in words and "сахар" in words
    # Служебное слово внутри слова не режется.
    assert "напиток" in normalize.normalize_name("Напиток со вкусом груши 0,5 л").split()
    assert normalize.normalize_name("Напиток со вкусом груши 0,5 л") == "напиток вкусом груши 500 г"


def test_negations_stay_meaningful():
    """«без» и «не» — не служебные слова: они отрицания, их трогать нельзя."""
    assert "без" in normalize.normalize_name("Кола без сахара 1 л").split()
    assert "не" in normalize.normalize_name("Кофе не жареный").split()
    assert "безгаза" in prep("ВОДА БОРЖОМИ без газа 0,5Л", ident=1).words
    assert "негаз" in prep("ВОДА БОРЖОМИ Н/ГАЗ 0,5Л", ident=2).words


def test_size_numbers_still_keep_pampers_apart():
    """Контроль: «Pampers 4 46 шт» и «Pampers 5 46 шт» — разные покупки."""
    four = prep("Памперсы Pampers 4 46 шт", ident=1)
    five = prep("Памперсы Pampers 5 46 шт", ident=2)
    assert "4" in normalize.normalize_name(four.name).split()
    assert "5" in normalize.normalize_name(five.name).split()
    assert link._counted(four, five) is False
