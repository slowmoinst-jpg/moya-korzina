"""Проверки, не дающие сопоставить разные товары с похожими названиями.

Все примеры здесь — не выдуманные. Это то, что приложение реально насопоставляло
на демо-корзине, пока этих проверок не было. Отчёт tools/match_report.py показал
22 сомнительных связи из 32.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, repo  # noqa: E402
from app.db import init_db  # noqa: E402
from app.matcher import matcher, quality  # noqa: E402
from app.models import Candidate, Product  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "q.db"))
    init_db()


# ---------- родовое слово ----------
def test_cheese_is_not_ice_cream():
    """Настоящий промах: «Страчателла 200 г» уехала в мороженое Maxiduo."""
    assert quality.type_conflict("Страчателла 200 г", "Мороженое Maxiduo Страчателла брикет 92г")


def test_puree_is_not_juice():
    """И второй: детское пюре сопоставилось с соком Сады Придонья."""
    assert quality.type_conflict("Пюре детское яблоко-груша 200 г",
                                 "Сок Сады Придонья Яблоко-груша 200мл")


def test_same_type_is_not_a_conflict():
    assert not quality.type_conflict("Сыр Страчателла 200 г", 'Сыр "Страчателла", 200 г')
    assert not quality.type_conflict("Молоко 1 л", "Молоко Простоквашино 2,5% 930мл")


def test_type_word_is_found_in_the_raw_name():
    """Нормализация выбрасывает «сыр» как мусор, поэтому тип ищем до неё."""
    from app.matcher.normalize import normalize_name

    assert "сыр" not in normalize_name("Сыр Страчателла 200 г")
    assert quality.product_type("Сыр Страчателла 200 г") == "сыр"


def test_unknown_type_never_blocks():
    """Не разглядели тип — молчим. Лучше пропустить сомнительное, чем забраковать верное."""
    assert not quality.type_conflict("Штуковина обыкновенная", "Штуковина 200 г")
    assert not quality.type_conflict("Сыр Страчателла", "Страчателла 200 г")


def test_synonyms_live_in_one_group():
    """Дезодорант и антиперспирант — одна полка, разбираться там марке."""
    assert not quality.type_conflict("Дезодорант Axe", "Антиперспирант Axe")


# ---------- марка ----------
def test_different_latin_brands_conflict():
    """Ещё один настоящий промах: Axe сопоставился с Rexona."""
    assert quality.brand_conflict("Дезодорант-карандаш Axe Ice Chill 50 мл",
                                  "Антиперспирант Rexona Sukhost Pudry 40 ml")


def test_same_brand_does_not_conflict():
    assert not quality.brand_conflict("Дезодорант Axe Ice Chill", "Axe Ice Chill стик 50 мл")


def test_brand_is_not_checked_when_only_one_side_has_it():
    """У эталонов из чеков латиницы часто нет — это не повод браковать кандидата."""
    assert not quality.brand_conflict("Сыр Страчателла 200 г", 'Сыр Pretto "Страчателла", 200 г')


def test_common_words_are_not_brands():
    assert quality.brands("Молоко Fresh Light 1 л") == set()
    assert "pretto" in quality.brands('Сыр Pretto Фиор Ди Латте')


# ---------- влияние на оценку ----------
def test_type_conflict_zeroes_the_score():
    product = Product(None, "Страчателла 200 г", weight_g=200)
    ice_cream = Candidate("magnit", "1", "Мороженое Maxiduo Страчателла брикет 92г", weight_g=92)

    assert matcher.score_candidate(product, ice_cream) == 0.0


def test_brand_conflict_zeroes_the_score():
    product = Product(None, "Дезодорант-карандаш Axe Ice Chill 50 мл", weight_g=50)
    rexona = Candidate("magnit", "2", "Антиперспирант Rexona Sukhost Pudry 50 ml", weight_g=50)

    assert matcher.score_candidate(product, rexona) == 0.0


def test_right_candidate_still_wins():
    product = Product(None, "Страчателла 200 г", weight_g=200)
    cheese = Candidate("vkusvill", "3", 'Сыр "Страчателла", 200 г', weight_g=200)

    assert matcher.score_candidate(product, cheese) > 0.9


def test_weight_mismatch_is_a_penalty_not_a_veto():
    """Проверено измерением: жёсткий отказ по весу уводит выбор на посторонний товар.

    «Пюре детское банан-яблоко 210 г» при жёстком правиле переехало с «Пюре из яблок
    и банана» на «Котлету рыбную с картофельным пюре» — потому что у котлеты
    граммовки в названии нет вовсе, и придраться не к чему.
    """
    product = Product(None, "Пюре детское банан-яблоко 210 г", weight_g=210)
    close = Candidate("vkusvill", "4", "Пюре из яблок и банана, 90 г", weight_g=90)

    score = matcher.score_candidate(product, close)
    assert 0 < score < 0.75, "не ноль, но и не уверенное совпадение"


# ---------- что видит человек ----------
def test_doubts_name_the_reason():
    product = Product(None, "Страчателла 200 г", weight_g=200)
    flags = quality.doubts(product, "Мороженое Maxiduo Страчателла брикет 92г", 92)

    assert any("тип" in f for f in flags)
    assert "вес" in flags


def test_no_doubts_about_a_good_match():
    product = Product(None, "Огурцы короткоплодные 450 г", weight_g=450)
    assert quality.doubts(product, "Огурцы короткоплодные, 450 г", 450) == []


def test_basket_doubts_report_points_at_the_product(db):
    from app import service

    product_id = repo.upsert_product(Product(None, "Страчателла 200 г", weight_g=200))
    store = repo.get_store("magnit")
    sp = repo.upsert_store_product(store.id, "1", "Мороженое Maxiduo Страчателла брикет 92г",
                                   weight_g=92)
    repo.confirm_mapping(product_id, sp)
    basket_id = repo.create_basket("Проверка")
    repo.set_basket_item(basket_id, product_id, 1)

    summary = service.doubts_summary(basket_id)
    assert summary["products"] == 1 and summary["total"] == 1
    assert any("тип" in f for f in summary["rows"][0]["flags"])


def test_good_basket_raises_no_doubts(db):
    from app import service

    product_id = repo.upsert_product(Product(None, "Огурцы короткоплодные 450 г", weight_g=450))
    store = repo.get_store("magnit")
    sp = repo.upsert_store_product(store.id, "2", "Огурцы короткоплодные тепличные 450г",
                                   weight_g=450)
    repo.confirm_mapping(product_id, sp)
    basket_id = repo.create_basket("Чистая")
    repo.set_basket_item(basket_id, product_id, 1)

    assert service.doubts_summary(basket_id)["products"] == 0


# ---------- кириллица и латиница как одно имя ----------
def test_transliteration_turns_cyrillic_into_latin():
    from app.matcher.normalize import translit

    assert translit("Рексона") == "rexona"
    assert translit("Простоквашино") == "prostokvashino"
    assert translit("Джипопо") == "gipopo"


def test_same_brand_written_two_ways_is_recognised():
    """То, о чём просили прямо: «Рексона» и «Rexona» — один и тот же товар."""
    from app.matcher.normalize import same_word

    assert same_word("Рексона", "Rexona")
    assert same_word("Домик", "Domik")
    assert not same_word("Рексона", "Axe")


def test_similarity_bridges_the_two_alphabets():
    from app.matcher.normalize import similarity

    assert similarity("Дезодорант Рексона Сухость пудры 40 мл",
                      "Antiperspirant Rexona Sukhost Pudry 40 ml") > 0.75
    assert similarity("Молоко Простоквашино 2,5%", "Moloko Prostokvashino 2.5%") == 1.0


def test_transliteration_never_lowers_a_score():
    """Она может только помочь узнать товар: берём лучшее из двух прочтений."""
    from app.matcher.normalize import similarity

    assert similarity("Огурцы короткоплодные 450 г", "Огурцы короткоплодные тепличные 450г") > 0.8


def test_transliteration_does_not_glue_different_brands():
    from app.matcher.normalize import similarity

    assert similarity("Дезодорант Axe Ice Chill 50 мл",
                      "Антиперспирант Рексона Сухость пудры 40 мл") < 0.5
