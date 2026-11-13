r"""Правила названий по замеру привязки чеков 12.11.2026 и ревью 13.11.2026.

ЗАЧЕМ. Замер на боевой базе: из 479 товаров с распознанной сетью к единой базе привязано
230, а у части непривязанных двойник в базе есть, и строгие правила его не пускали.
Что здесь проверяется (тесты «контроль» проходили и на прежнем коде — они держат то,
что правка менять не должна):

1. диапазон жирности «3,4-4,0 %» и «3.4%-4%» разбирался первым числом с «%» после него,
   и одна и та же линейка расходилась сама с собой (parse_percent); обе границы
   сверяются, когда названы у обеих строк, и нижняя копится по группе (_split_chained);
2. «8 рулонов» не считались штуками, а само слово «рулонов» оставалось в названии, и
   туалетная бумага из чека не встречала «…3 слоя, 8шт» (pack_count, glue); рулоны —
   последнее правило штук, а голое «РУЛ.» чека (рулет) остаётся словом; чек считает штуки
   по склеенной строке, поэтому glue оставляет «8рулонов» одним словом с цифры;
3. «с газом», «С/ГАЗ» и «газированная/-ое/-ые» — одно слово «газированный» (glue), регистр
   сохраняется, а строки чека «С/ГАЗ» и «ГАЗ» не спорят друг с другом;
4. «со вкусом» — связка перед названием вкуса, а не слово товара (NOISE);
5. Перекрёсток пишет «й» одной «j», и «gazirovannyj» не равнялось «газированный» (fold);
   охрана чисел не даёт сойтись «67 %» и «50 5» после этого, но «2,5 %» и «25» (Монетка,
   Дикси пишут процент без разделителя) сходятся; вложение по своду пускает только слова-описания;
6. родовое «напиток» перед названием не делает связь слабой, но только в ветке
   вложения (link._judge, same_product), а не везде (match.GENERIC_HEADS);
7. строки с одним штрихкодом остаются в одной группе, когда цепочка слов делит остальных,
   но «15 %» и «20 %» с одним кодом по-прежнему врозь, в каком бы порядке строки ни стояли;
8. служебные слова («сыр», «уп», «мягкий») русская нормализация роняет, а латиница хранила их
   и мешала вложению (match._SCRIPT_NOISE); страна изготовителя, «цельное» и «фильтрованное» —
   надписи, а не товар (DESCRIPTORS);
9. две латинские строки без «%» сверяются по числам названия, как и пара «кириллица — латиница»;
   молчащий русский эталон группы этой охраны не включает, поэтому _split_chained копит по
   одному представителю каждого набора чисел и сверяет с ними новичка.
"""
from __future__ import annotations

import itertools
import time
from types import SimpleNamespace

import pytest

from app import config, repo
from app.catalog import link, match, refresh, store, x5
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


def prep(name: str, *, ident: int = 1, chain: str = "mine"):
    return match.prepare({"id": ident, "chain": chain, "name": name})


def same(a: str, b: str) -> bool:
    return match.same_product(prep(a), prep(b, ident=2, chain="other"))[0]


def verdict(receipt: str, name: str, brand: str):
    """Вердикт link._judge для строки чека и строки единого товара:
    (сильная ли, уверенность, способ) или None."""
    mine = link._prep({"id": 1, "chain": "mine", "name": receipt}, receipt=True)
    cand = link._prep({"id": 2, "chain": "other", "name": name, "brand": brand})
    return link._judge(mine, cand, mine.brand_words | cand.brand_words)


def bought(raw: str, qty: float = 1) -> int:
    """Товар, заведённый как при загрузке чека: эталон — display_name строки."""
    pid, _ = _ensure_product(raw, qty)
    repo.add_history_row("2026-10-10", None, pid, raw, qty, 99.0, 99.0, f"k-{pid}-{raw}")
    return pid


def sku(pid: int, chain: str) -> str | None:
    ids = {s.code: s.id for s in repo.list_stores()}
    found = repo.confirmed_mapping(pid, ids[chain])
    return found["sku"] if found else None


# ---------- 1. диапазон жирности ----------
@pytest.mark.parametrize("name, expected", [
    ("Молоко Простоквашино отборное 3,4-4,0 %", 4.0),
    ("Молоко Простоквашино отборное 3.4%-4%, 930мл", 4.0),
    ("Молоко Простоквашино отборное 3,4–4,5 %", 4.5),
    ("Молоко Простоквашино отборное 3,4 - 4,0%", 4.0),
    # контроль: не диапазон — одно число, а после тире — вес без «%»
    ("Сметана 15 %", 15.0),
    ("Молоко 2,5%", 2.5),
    ("Сыр 45% - 200г", 45.0),
    ("Масло 82,5%-200г", 82.5),
    ("Молоко 3,2% 1-2 шт", 3.2),
    ("Кефир без процента", None),
])
def test_percent_range_takes_the_upper_bound(name, expected):
    assert match.parse_percent(name) == expected


@pytest.mark.parametrize("name, low", [
    ("Молоко Простоквашино отборное 3,4-4,0 %", 3.4),
    ("Молоко Простоквашино отборное 3.4%-4%, 930мл", 3.4),
    ("Сметана 15 %", None),
    ("Сыр 45% - 200г", None),
])
def test_percent_range_has_a_lower_bound_only_when_it_is_a_range(name, low):
    assert match.parse_percent_low(name) == low


def test_milk_range_in_two_spellings_is_one_product():
    receipt = "Молоко Простоквашино отборное 3,4-4,0 % 930мл"
    assert same(receipt, "Молоко Простоквашино отборное 3.4%-4%, 930мл")


def test_milk_range_with_another_upper_bound_stays_another_product():
    assert not same("Молоко Простоквашино отборное 3,4-4,0 % 930мл",
                    "Молоко Простоквашино отборное 3,4-4,5 % 930мл")


def test_ranges_with_another_lower_bound_stay_two_products():
    assert not same("Молоко Домик в деревне отборное 3,5%-4,5% 930мл",
                    "Молоко Домик в деревне отборное 3,7%-4,5% 930мл")


def test_range_meets_the_single_number_by_its_upper_bound():
    # так «3,5-4,5 %» сходилось с «4,5 %» всегда; правка этого не меняет
    assert same("Молоко Домик в деревне отборное 3,5-4,5 % 930мл",
                "Молоко Домик в деревне отборное 4,5 % 930мл")


def test_percent_range_regex_does_not_backtrack_on_a_long_run_of_spaces():
    started = time.perf_counter()
    assert match.parse_percent("1" + " " * 20000) is None
    assert time.perf_counter() - started < 0.5


def test_percent_range_after_an_abbreviation_dot_is_still_a_range():
    # в чеке перед диапазоном стоит точка сокращения «ОТБ.», и взгляд назад «не точка» его терял
    receipt = "МОЛ.ПАСТ.ПРОСТОКВАШИНО ОТБ.3.4%-4.5% 930МЛ"
    assert match.parse_percent(receipt) == 4.5
    assert match.parse_percent_low(receipt) == 3.4
    mine = link._prep({"id": 1, "chain": "mine", "name": receipt}, receipt=True)
    cand = prep("Молоко отборное пастеризованное Простоквашино 3.4-4.5% 930мл", ident=2, chain="catalog")
    assert match.compatible(mine, cand)


def test_percent_range_does_not_start_in_the_middle_of_a_number():
    # «23.4» и «3.4» — середина числа «123.4», а не начало диапазона
    assert match.parse_percent_low("Артикул 123.4-4.5%") is None
    assert match.parse_percent_low("Масло 13.4-14.5%") == 13.4


# ---------- 2. рулоны — штуки ----------
@pytest.mark.parametrize("name", [
    "Туалетная бумага Zewa 3 слоя 8 рулонов",
    "Туалетная бумага Zewa 3 слоя 8рул",
    "Туалетная бумага Zewa 3 слоя 8шт",
    "Tualetnaya bumaga Zewa 3 sloa 8 rulonov",
])
def test_rolls_count_as_pieces(name):
    assert match.pack_count(name) == 8


@pytest.mark.parametrize("name, packs", [
    # рулон — мера внутри упаковки: штуки называет другое число
    ("Пакеты для мусора 60 л, 1 рулон, 20 шт", 20),
    ("Пакеты для мусора Avikomp 60л 1 рулон 20шт", 20),
    ("Бумага туалетная Zewa Плюс 2 слоя 4 рулона х 2 уп", 2),
    ("Pakety dlya musora 60l 1 rulon 20sht", 20),
    # контроль: рулет — не рулон
    ("Рулет мясной 2 рулета", 1),
])
def test_rolls_never_override_a_pack_named_another_way(name, packs):
    assert match.pack_count(name) == packs


def test_rolls_name_meets_the_pieces_spelling():
    name = "Бумага туалетная Zewa 3 слоя 8 рулонов"
    assert same(name, "Бумага туалетная Zewa 3 слоя 8шт")
    assert not same(name, "Бумага туалетная Zewa 3 слоя 16шт")


def test_roll_count_leaves_the_words_but_the_bare_roll_stays():
    assert "рулонов" not in prep("Бумага туалетная Zewa 3 слоя 8 рулонов").words
    assert "рулон" in prep("Плёнка пищевая рулон 30 м").words


def test_receipt_counts_rolls_after_glue_too():
    # чек считает штуки по склеенной строке: счёт рулонов glue не вырезает, а склеивает в «8рулонов»
    receipt = link._prep({"id": 1, "chain": "mine", "name": "Бумага туалетная Zewa Deluxe 3 слоя 8 рулонов"},
                         receipt=True)
    assert receipt.packs == 8 and "рулонов" not in receipt.words
    one_roll = link._prep({"id": 1, "chain": "mine", "name": "Пакеты для мусора 60 л, 1 рулон, 20 шт"},
                          receipt=True)
    assert one_roll.packs == 20


def test_receipt_of_eight_rolls_finds_the_pack_of_eight_and_not_another_count():
    receipt = "Бумага туалетная Zewa Deluxe 3 слоя 8 рулонов"
    assert verdict(receipt, "Бумага туалетная Zewa Deluxe 3 слоя 8шт", "Zewa")[0] is True
    assert verdict(receipt, "Бумага туалетная Zewa Deluxe 3 слоя 8 рулонов", "Zewa")[0] is True
    # без числа штук и с другим числом — другая покупка (на HEAD «…3 слоя» находилась слабо)
    assert verdict(receipt, "Бумага туалетная Zewa Deluxe 3 слоя", "Zewa") is None
    assert verdict(receipt, "Бумага туалетная Zewa Deluxe 3 слоя 16шт", "Zewa") is None


def test_person_rows_keep_the_roll_count_of_the_receipt():
    product = SimpleNamespace(id=1, name="Бумага туалетная Zewa Deluxe 3 слоя", brand=None,
                              weight_g=None, unit=None)
    rows = link._rows_of_person(product, ["Бумага туалетная Zewa Deluxe 3 слоя 8 рулонов"])
    assert rows and {r.packs for r in rows} == {8}


def test_roulade_abbreviation_from_the_receipt_keeps_its_first_word():
    # «РУЛ.» — начало слова «рулет»; когда его выбрасывали как шум, пара терялась
    roulade = "Рулет бисквитный Яшкино вареная сгущенка 200г"
    assert verdict("РУЛ.БИСК.ЯШКИНО ВАР.СГ 200Г", roulade, "Яшкино")[0] is True
    assert verdict("РУЛ.МАК ЛЕНТА 300Г", "Рулет с маком Лента 300г", "Лента")[0] is True
    # а бисквит без «рулета» — другой товар
    assert verdict("РУЛ.БИСК.ЯШКИНО ВАР.СГ 200Г", "Бисквит Яшкино вареная сгущенка 200г", "Яшкино") is None


# ---------- 3. «с газом» — газированный ----------
@pytest.mark.parametrize("text", [
    "Кола Evervess, с газом, 1,5 л",
    "Вода Святой источник С/ГАЗ 1,5л",
    "Вода питьевая газированная 1,5л",
    "Напиток газированное",
    "Соки газированные",
    "voda pitevaa gazirovannaa 1 5l",
    "napitok gazirovannyj",
])
def test_every_form_of_carbonated_is_one_word(text):
    assert "газированный" in match.glue(text).lower()


@pytest.mark.parametrize("text", [
    "Вода Borjomi без газа 0,5л",
    "Вода негазированная 1,5л",
    "Вода сильногазированная 1,5л",
    "Вода слабогазированная 1,5л",
    "voda negazirovannaa 1 5l",
])
def test_the_opposite_and_the_degrees_are_not_the_plain_carbonated_word(text):
    assert "газированный" not in match.glue(text).lower()


def test_with_gas_name_meets_every_form_of_the_carbonated_name():
    name = "Кола Evervess с газом 1,5 л"
    assert same(name, "Кола Evervess газированный 1,5л")
    assert same(name, "Кола Evervess газированная 1,5л")
    assert same("Вода Святой источник С/ГАЗ 1,5л", "Вода Святой источник газированная 1,5л")


def test_with_gas_receipt_meets_the_carbonated_name_through_link():
    assert verdict("КОЛА EVERVESS С/ГАЗ 1,5Л", "Напиток газированный Evervess Кола 1,5л", "Evervess")[0] is True


def test_glue_keeps_the_case_of_the_carbonated_word():
    # регистр сохраняется: по нему узнаётся марка, и чек заглавными не должен стать «со строчными»
    assert match.glue("С ГАЗОМ ВОДА АРХЫЗ").split()[0] == "ГАЗИРОВАННЫЙ"
    assert match.glue("Вода Архыз с газом").split()[-1] == "газированный"


@pytest.mark.parametrize("receipt", ["ГАЗИРОВАННЫЙ НАПИТОК ДЮШЕС 1,5Л", "С ГАЗОМ ВОДА АРХЫЗ 1,5Л"])
def test_caps_receipt_that_starts_with_gas_gets_no_brand_words(receipt):
    row = link._prep({"id": 1, "chain": "mine", "name": receipt}, receipt=True)
    assert row.brand_words == frozenset()


def test_receipt_lines_with_and_without_the_slash_do_not_contradict_each_other():
    # у строк один display_name: «С/» из него выпадает, и «С/ГАЗ» с «ГАЗ» — один товар
    assert not link._contradicting_variants(["ВОДА АРХЫЗ С/ГАЗ 1,5Л", "ВОДА АРХЫЗ ГАЗ 1,5Л"])
    # а «Н/ГАЗ» — противоположный товар, и такие строки по-прежнему не склеиваются
    assert link._contradicting_variants(["ВОДА АРХЫЗ Н/ГАЗ 1,5Л", "ВОДА АРХЫЗ ГАЗ 1,5Л"])
    assert link._contradicting_variants(["ВОДА АРХЫЗ Н/ГАЗ 1,5Л", "ВОДА АРХЫЗ С/ГАЗ 1,5Л"])


def test_carbonated_stays_the_opposite_of_still():
    assert match.glue("Вода Borjomi без газа 0,5л").lower().count("безгаза") == 1
    assert not same("Вода Borjomi с газом 0,5л", "Вода Borjomi без газа 0,5л")
    assert not same("Вода Святой источник газированная 1,5л", "Вода Святой источник негазированная 1,5л")


# ---------- 4. «со вкусом» ----------
def test_taste_connector_is_not_a_word_of_the_product():
    assert same("Чипсы Lay's со вкусом сметана и лук 140г", "Чипсы Lay's сметана и лук 140г")


def test_the_taste_itself_still_separates_products():
    assert not same("Чипсы Lay's со вкусом сметана и лук 140г", "Чипсы Lay's со вкусом краб 140г")


# ---------- 5. Перекрёсток: «й» одной «j» ----------
@pytest.mark.parametrize("cyrillic, latin", [
    ("газированный", "gazirovannyj"),
    ("большой", "bolsoj"),
    ("майонез", "majonez"),
    ("добрый", "dobryjj"),          # контроль: двойная «jj» — как было
])
def test_fold_reads_single_j_as_the_letter_y_short(cyrillic, latin):
    assert match.fold(cyrillic) == match.fold(latin)


@pytest.mark.parametrize("cyrillic, latin, expected", [
    ("Майонез Махеевъ Провансаль 50,5% 190 г", "majonez maheev provansal 50 5 190ml", True),
    ("Молоко Простоквашино 2,5% 930мл", "moloko prostokvasino 2 5 930ml", True),
    # транслит не пишет «%», и без охраны чисел сходились разные проценты (ревью 13.11.2026)
    ("Майонез Махеевъ Провансаль 67% 190 г", "majonez maheev provansal 50 5 190ml", False),
    ("Майонез Ряба Провансаль 50.5% 575г", "majonez raba provansal 67 630ml", False),
    ("Плавленый продукт Переяславль Дружба 25% 100г", "plavlenyj produkt pereaslavl druzba 55 80g", False),
    # Монетка и Дикси пишут процент без разделителя: «2,5 %» у них «25», «50,5 %» — «505» (ревью 13.11.2026)
    ("Кефир Простоквашино 2.5% 930г", "kefir prostokvashino 25 930ml", True),
    ("Майонез Махеевъ Провансаль 50.5%, 770г", "majjonez maheev provansal 505 770g", True),
    ("СЕЛО ЗЕЛЕНОЕ 3,2% 930мл", "selo zelenoe 32 bez zmzh 930ml", True),
    ("Молоко Простоквашино 2,5% 930мл", "moloko prostokvasino pasterizovannoe 1 5 930ml", False),
])
def test_latin_without_percent_meets_cyrillic_only_when_the_numbers_agree(cyrillic, latin, expected):
    assert same(cyrillic, latin) is expected


def test_the_person_side_numbers_check_reads_the_same_digits():
    mine = link._prep({"id": 1, "chain": "mine", "name": "Кефир Простоквашино 2,5% 930г"}, receipt=True)
    monetka = link._prep({"id": 2, "chain": "monetka", "name": "kefir prostokvashino 25 930ml"})
    other = link._prep({"id": 3, "chain": "monetka", "name": "kefir prostokvashino 1 5 930ml"})
    assert not link._numbers_disagree(mine, monetka)
    assert link._numbers_disagree(mine, other)


# ---------- вложение по своду письма: лишнее слово — только описание ----------
def _cross(a: str, brand_a: str | None, b: str):
    ra = match.prepare({"id": 1, "chain": "metro", "name": a, "brand": brand_a})
    rb = match.prepare({"id": 2, "chain": "perekrestok", "name": b})
    return match.same_product(ra, rb, match.brand_vocabulary([ra, rb]))[0]


@pytest.mark.parametrize("cyrillic, brand, latin", [
    # вкус, начинка, серия — другой товар, хотя марка и фасовка те же (ревью 13.11.2026)
    ("Шоколад молочный Alpen Gold Oreo 85г", "Alpen Gold", "sokolad molocnyj alpen gold 90g"),
    ("Шоколад Аленка молочный с миндалем, 75г", "Аленка", "sokolad alenka molocnyj 75g"),
    ("Шоколад горький Победа вкуса 72% 100г", "Победа вкуса", "sokolad gorkij pobeda vkusa s kusockami apelsina 72 100g"),
    ("Сыр плавленый Viola Классический 35% 380г", "Viola", "syr plavlenyj viola klassiceskij s kuricej gril 35 400g"),
])
def test_cyrillic_does_not_meet_latin_that_adds_a_taste_or_a_filling(cyrillic, brand, latin):
    assert not _cross(cyrillic, brand, latin)


@pytest.mark.parametrize("cyrillic, brand, latin", [
    # лишнее слово — способ обработки или сорт: тот же товар (так было и до замены «j» на «i»)
    ("Молоко пастеризованное Простоквашино 2,5% 930 мл", "Простоквашино", "moloko prostokvasino 2 5 930ml"),
    ("Йогурт питьевой Чудо Клубника-земляника 1.9%, 260г", "Чудо", "jogurt cudo klubnika zemlanika 1 9 bzmz 260g"),
    ("Творог Резной палисад традиционный 9% 400г", "Резной палисад", "tvorog reznoj palisad 9 400g"),
    # контроль: слова равны, вложения нет
    ("Шоколад молочный Alpen Gold 90г", "Alpen Gold", "sokolad molocnyj alpen gold 90g"),
])
def test_cyrillic_meets_latin_that_differs_by_a_descriptor_only(cyrillic, brand, latin):
    assert _cross(cyrillic, brand, latin)


@pytest.mark.parametrize("cyrillic, brand, latin", [
    # normalize_name роняет «сыр», «уп», «мягкий» только по-русски, латиница их хранила (_SCRIPT_NOISE)
    ("Сыр Galbani Моцарелла 45%, 125г", "Galbani", "syr galbani mocarella 45 bzmz 125g"),
    ("Сыр Сыробогатов Маасдам нарезка 45% 125г", "Сыробогатов", "syr syrobogatov maasdam narezka 45 125g"),
    ("Творог мягкий Минская марка 4% 400г", "Минская марка", "tvorog minskaa marka magkij 4 400g"),
    ("Вода БОРЖОМИ 0,5л уп.12", "Боржоми", "voda borzhomi 0,5l up.12"),
    # страна изготовителя — надпись, а не товар (Монетка дописывает «Россия» в конец)
    ("Вода питьевая AQUA MINERALE газированная, 2л", None, "voda pitevaya aqua minerale gazirovannaya 2l rossiya"),
    ("Йогурт САВУШКИН Клубника 2%, без змж, 120г", "Савушкин", "jjogurt savushkin klubnika 2 bez zmzh 120g belarus"),
    # цельное молоко и фильтрованное пиво — обычная разновидность, о которой другая сеть молчит
    ("Молоко Экомилк 3,4-4,5% 900мл отборное пастеризованное", "Экомилк",
     "moloko ekomilk celnoe otbornoe pasterizovannoe 3 4 4 5 900ml"),
    ("Пиво светлое Жигули Барное Экспорт 4,8% 1,25л", "Жигули", "pivo zhiguli barnoe eksport svetloe filtrovannoe 4 8 1250ml"),
    # родовое «напиток» перед названием — не вкус (GENERIC_HEADS держит вложение по своду)
    ("Напиток Святой Источник Лимонад 500мл", "Святой Источник", "limonad svatoj istocnik 500ml"),
])
def test_cyrillic_meets_latin_that_differs_by_a_script_noise_word_or_a_label(cyrillic, brand, latin):
    assert _cross(cyrillic, brand, latin)


@pytest.mark.parametrize("cyrillic, brand, latin", [
    # страна не открывает дорогу вкусу, начинке и чужому изготовителю
    ("Чипсы Lays картофельные 70г", "Lays", "cipsy kartofelnye lays so vkusom kraba 70g rossiya"),
    ("Молоко Простоквашино 3,2% 930мл", "Простоквашино", "moloko prostokvasino ekomilk 3 2 930ml rossiya"),
    # «нефильтрованное» — другое слово, а не «фильтрованное» с отрицанием
    ("Пиво светлое Жигулевское 4% 0,44л", "Жигулевское", "pivo zigulevskoe svetloe nefiltrovannoe 4 440ml"),
])
def test_a_country_or_a_label_does_not_open_the_way_to_another_product(cyrillic, brand, latin):
    assert not _cross(cyrillic, brand, latin)


@pytest.mark.parametrize("a, b", [
    # «jj» Монетки и «j» Перекрёстка стали одним словом (fold), а процента ни у одной строки нет:
    # 1 % и 2,5 % сходились, и допуск веса пропускал 400 г против 500 г (ревью 13.11.2026)
    ("kefir irbitskijj 1 bez zmzh 400g rossiya", "kefir irbitskij 2 5 500g"),
    ("kefir irbitskijj 3 2 400g", "kefir irbitskij 1 500g"),
    ("moloko prostokvasino 3 2 930ml", "moloko prostokvasino 2 5 930ml"),
])
def test_two_latin_names_without_percent_do_not_meet_when_their_numbers_disagree(a, b):
    assert not match.compatible(prep(a), prep(b, ident=2, chain="other"))
    assert not same(a, b)


@pytest.mark.parametrize("a, b", [
    ("kefir prostokvasino 2 5 930ml", "kefir prostokvasino 25 930ml"),   # Монетка пишет без разделителя
    ("kefir prostokvasino 930ml", "kefir prostokvasino 2 5 930ml"),      # молчание не улика
    ("kefir irbitskijj 2 5 bez zmzh 400g", "kefir irbitskij 25 400g"),
])
def test_two_latin_names_meet_when_their_numbers_agree_or_one_row_is_silent(a, b):
    assert same(a, b)


def test_barcode_twin_does_not_drag_a_fat_content_into_a_group_that_holds_another():
    # у 1 и 3 один штрихкод, 2 — «20 %» без кода; как ни стоят строки, «15 %» и «20 %» не сходятся
    products = [
        {"id": 1, "chain": "c1", "name": "Сметана Домик в деревне 300г", "barcode": CODE},
        {"id": 2, "chain": "c2", "name": "Сметана Домик в деревне 20% 300г"},
        {"id": 3, "chain": "c3", "name": "Сметана Домик в деревне 15% 300г", "barcode": CODE},
    ]
    for order in itertools.permutations(products):
        groups = _clustered(*order)
        assert not any({2, 3} <= set(g) for g in groups), (order, groups)


@pytest.mark.parametrize("third", [
    "kefir irbitskij 15 400g",          # латиница с другими числами
    "Кефир Ирбитский 1% 400 г",         # русская строка с другим процентом
])
def test_silent_russian_seed_does_not_hold_names_whose_numbers_disagree_with_each_other(third):
    # эталон — самая длинная строка: «упаковка» из названия выброшена, но длину держит. У молчащей
    # русской строки охрана чисел не работает, и «25» с «15» проходили каждая сама по себе (ревью 13.11.2026)
    products = [
        {"id": 1, "chain": "c1", "name": "Кефир Ирбитский 400 г упаковка"},
        {"id": 2, "chain": "c2", "name": "kefir irbitskij 25 400g"},
        {"id": 3, "chain": "c3", "name": third},
    ]
    for order in itertools.permutations(products):
        groups = _clustered(*order)
        assert not any({2, 3} <= set(g) for g in groups), (order, groups)
        assert any({1, 2} <= set(g) or {1, 3} <= set(g) for g in groups), (order, groups)


def test_script_noise_leaves_the_fold_of_the_row_but_not_its_basket_key():
    row = prep("syr galbani mocarella 45 125g")
    assert "sir" not in row.fwords and "sir" in row.forder
    assert "galbani" in row.fwords


def test_cluster_keeps_the_filled_chocolate_apart_from_the_plain_one():
    groups = _clustered(
        {"id": 1, "chain": "metro", "name": "Шоколад молочный Alpen Gold Oreo 85г", "brand": "Alpen Gold",
         "weight_g": 85},
        {"id": 2, "chain": "perekrestok", "name": "sokolad molocnyj alpen gold 90g", "weight_g": 90},
    )
    assert groups == [[1], [2]]


def test_abbreviated_receipt_without_percent_is_not_judged_by_its_numbers():
    # «45» в чеке — вес без единицы, а у сети «26 %» названо: числа спорят, но это не улика
    mine = prep("Свитл сырок твор гл ван 45")
    cand = prep("Сырок творожный глазированный СВИТЛОГОРЬЕ с ванилью 26%, 50г", ident=2, chain="catalog")
    assert match.compatible(mine, cand)


def test_row_equality_ignores_the_lazy_numbers_cache():
    a, b = prep("Майонез Провансаль 67% 190 г"), prep("Майонез Провансаль 67% 190 г")
    match.compatible(a, prep("majonez provansal 50 5 190ml", ident=3, chain="x"))
    assert a.nums is not None and a == b


def test_perekrestok_names_with_single_j_meet_cyrillic_in_one_item(catalog):
    store.upsert_products("lenta", [P("1", "Напиток газированный Evervess Кола 1,5л", weight_g=1500)])
    store.upsert_products("perekrestok", [P("2", "napitok gazirovannyj evervess kola 1 5l", weight_g=1500)])

    summary = refresh.match_all()

    assert summary["items"] == 1 and summary["multi"] == 1


# ---------- 6. родовое «напиток» не делает связь слабой ----------
def test_receipt_finds_the_drink_whose_name_starts_with_the_generic_noun(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Напиток Evervess Кола 1,5л", brand="Evervess", weight_g=1500),
                                    P("2", "Напиток Evervess Лимон-лайм 1,5л", brand="Evervess", weight_g=1500)])
    refresh.match_all()
    pid = bought("Кола Evervess 1,5 л")

    link.link_products()

    assert sku(pid, "lenta") == "1"
    assert not (repo.master_of(pid) or {}).get("method", "").endswith("?")


def test_receipt_of_another_volume_does_not_find_the_drink(catalog, user_db):
    store.upsert_products("lenta", [P("1", "Напиток Evervess Кола 1,5л", brand="Evervess", weight_g=1500)])
    refresh.match_all()
    pid = bought("Кола Evervess 0,5 л")

    link.link_products()

    assert sku(pid, "lenta") is None


@pytest.mark.parametrize("receipt, name, brand", [
    ("Кофе Жокей 100г", "Напиток кофейный Жокей 100г", "Жокей"),
    ("Сок Добрый яблоко 1л", "Напиток сокосодержащий Добрый яблоко 1л", "Добрый"),
    ("Йогурт Активиа клубника 2,9% 270г", "Напиток йогуртный Активиа клубника 2,9% 270г", "Активиа"),
    ("Квас Очаковский 1,5л", "Напиток квасной Очаковский 1,5л", "Очаковский"),
    ("МОЛ.ПАСТ.ПРОСТОКВАШИНО 2,5% 930МЛ", "Напиток молочный пастеризованный Простоквашино 2,5% 930мл",
     "Простоквашино"),
])
def test_a_kind_of_drink_is_not_the_product_the_receipt_names(receipt, name, brand):
    # «напиток» в DESCRIPTORS выпадал из слов кандидата в _abbrev, и такие пары шли в наряд
    assert verdict(receipt, name, brand) is None


def test_x5_judge_still_rejects_the_drink_kind():
    drink = x5.R("Напиток кофейный Жокей 100г", brand="Жокей", chain="pyaterochka")
    coffee = x5.R("Кофе Жокей 100г", brand="Жокей", chain="perekrestok")
    assert x5.judge(drink, coffee, set(drink.brand)) is not None


# ---------- 7. штрихкод держит строки вместе ----------
CODE = "5449000000996"     # настоящий GTIN: контрольная цифра сходится, префикс не внутренний


def _clustered(*products):
    return [sorted(item[0].id for item in group) for group in match.cluster(list(products))]


def test_barcode_twins_stay_together_when_the_word_chain_splits_the_group():
    # 2 сходится с 1 по словам, 3 — только по штрихкоду с 2; эталон 1 слов 3 не принимает,
    # и без _gather_twins они уходили в разные единые товары
    groups = _clustered(
        {"id": 1, "chain": "c1", "name": "Сок Добрый яблоко осветленный 1л", "brand": "Добрый"},
        {"id": 2, "chain": "c2", "name": "Сок Добрый яблоко 1л", "brand": "Добрый", "barcode": CODE},
        {"id": 3, "chain": "c3", "name": "Нектар яблочный Добрый 1л", "brand": "Добрый", "barcode": CODE},
        {"id": 4, "chain": "c4", "name": "Сок Добрый апельсин 1л", "brand": "Добрый"},
    )
    assert any({2, 3} <= set(g) for g in groups)
    # контроль: чужой сок цепочкой не притянут
    assert [4] in groups


def test_barcode_twins_do_not_merge_two_fat_contents_back():
    # у «15 %» и «20 %» один (грязный) штрихкод: _split_chained развёл их по проценту, и
    # _gather_twins не должен сливать обратно (ревью 13.11.2026)
    groups = _clustered(
        {"id": 1, "chain": "c1", "name": "Сметана Домик в деревне 300г", "barcode": CODE},
        {"id": 2, "chain": "c2", "name": "Сметана Домик в деревне 15% 300г", "barcode": CODE},
        {"id": 3, "chain": "c3", "name": "Сметана Домик в деревне 20% 300г", "barcode": CODE},
    )
    assert not any({2, 3} <= set(g) for g in groups)
    assert any({1, 2} <= set(g) or {1, 3} <= set(g) for g in groups)


def test_barcode_twins_without_a_chain_split_keep_their_group():
    groups = _clustered(
        {"id": 1, "chain": "c1", "name": "Сок Добрый яблоко 1л", "brand": "Добрый", "barcode": CODE},
        {"id": 2, "chain": "c2", "name": "Сок Добрый яблоко 1л", "brand": "Добрый", "barcode": CODE},
    )
    assert groups == [[1, 2]]


def _ean(body: str) -> str:
    """Настоящий EAN-13 из 12 цифр: контрольная цифра сходится, префикс не внутренний."""
    return body + str((10 - sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(body)) % 10) % 10)


def _part(*products):
    return [(match.prepare(p), "barcode", 1.0) for p in products]


def _ids(parts):
    return sorted(sorted(item[0].id for item in part) for part in parts)


CREAM = "Сметана Домик в деревне {}300г"


def test_gather_twins_checks_a_newcomer_against_those_who_moved_in_before_it():
    # «15 %» переехала к двойникам без процента; «20 %» с тем же кодом уже спорит с ней
    parts = [
        _part({"id": 1, "chain": "c1", "name": CREAM.format(""), "barcode": CODE},
              {"id": 2, "chain": "c2", "name": CREAM.format(""), "barcode": CODE}),
        _part({"id": 3, "chain": "c3", "name": CREAM.format("15% "), "barcode": CODE}),
        _part({"id": 4, "chain": "c4", "name": CREAM.format("20% "), "barcode": CODE}),
    ]
    assert _ids(match._gather_twins(parts)) == [[1, 2, 3], [4]]


def test_gather_twins_does_not_check_a_newcomer_against_those_who_moved_out():
    # «15 %» (код CODE) уехала из первой части, и «20 %» (код other) едет к оставшемуся там двойнику
    other = _ean("460000000001")
    assert match.trusted_key(match.prepare({"id": 9, "chain": "c9", "name": "x", "barcode": other}).bkey)
    parts = [
        _part({"id": 1, "chain": "c1", "name": CREAM.format("15% "), "barcode": CODE},
              {"id": 2, "chain": "c2", "name": CREAM.format(""), "barcode": other}),
        _part({"id": 3, "chain": "c3", "name": CREAM.format(""), "barcode": CODE},
              {"id": 4, "chain": "c4", "name": CREAM.format(""), "barcode": CODE}),
        _part({"id": 5, "chain": "c5", "name": CREAM.format("20% "), "barcode": other}),
    ]
    assert _ids(match._gather_twins(parts)) == [[1, 3, 4], [2, 5]]


# ---------- 8. группа копит признаки эталона: _split_chained ----------
def _never_together(products, pair):
    for order in itertools.permutations(products):
        groups = _clustered(*order)
        assert not any(set(pair) <= set(g) for g in groups), (order, groups)


def test_silent_seed_does_not_hold_two_ranges_with_other_lower_bounds():
    # compatible разводит «3,5-4,5 %» и «3,7-4,5 %», но молчащий эталон держал при себе обоих (ревью 13.11.2026)
    _never_together([
        {"id": 1, "chain": "c1", "name": "Молоко Простоквашино отборное 930 мл упаковка пакет", "brand": "Простоквашино"},
        {"id": 2, "chain": "c2", "name": "Молоко Простоквашино отборное 3,5-4,5% 930 мл", "brand": "Простоквашино"},
        {"id": 3, "chain": "c3", "name": "Молоко Простоквашино отборное 3,7-4,5% 930 мл", "brand": "Простоквашино"},
    ], (2, 3))


@pytest.mark.parametrize("products, pair", [
    # русская строка без процента не может стать представителем набора чисел «25»: _numbers_clash
    # с ней молчит, и латинская «25» осталась бы без представителя
    ([{"id": 1, "chain": "c1", "name": "Кефир Ирбитский 25 400 г упаковка"},
      {"id": 2, "chain": "c2", "name": "kefir irbitskij 25 400g"},
      {"id": 3, "chain": "c3", "name": "Кефир Ирбитский 1% 400 г"}], (2, 3)),
    # ключ представителя помнит, назван ли процент: русская «2,5 %» с артикулом спорит с латинской
    # «25» (пара из ключей «25» и «123455»), русская «2,5 %» без артикула с ней не спорит
    ([{"id": 1, "chain": "c1", "name": "Кефир Ирбитский, 400 г, упаковка, пакет"},
      {"id": 2, "chain": "c2", "name": "Кефир Ирбитский 2,5% 400 г"},
      {"id": 3, "chain": "c3", "name": "kefir irbitskij 25 400g"},
      {"id": 4, "chain": "c4", "name": "Кефир Ирбитский 2,5% 400г 3145"}], (3, 4)),
    # эталон сам представитель: латинская «1» с тем же штрихкодом, что у эталона «25», вместе с ним не остаётся
    ([{"id": 1, "chain": "c1", "name": "kefir irbitskij klassiceskij 25 400g", "barcode": CODE},
      {"id": 2, "chain": "c2", "name": "kefir irbitskijj 1 400g", "barcode": CODE},
      {"id": 3, "chain": "c3", "name": "Кефир Ирбитский 400 г"}], (1, 2)),
], ids=["russian-silent-row-is-no-representative", "key-keeps-whether-the-percent-is-named",
        "the-seed-is-a-representative-too"])
def test_the_group_remembers_one_representative_of_every_set_of_numbers(products, pair):
    _never_together(products, pair)
