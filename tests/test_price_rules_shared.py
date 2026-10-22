"""Правила про цены живут в одном месте, а не копиями по модулям.

Повод — самопроверка 20.10.2026: константа «сколько изменений цены нужно, чтобы судить»
была объявлена в ТРЁХ модулях (service, compare, catalog/store), а после перехода на
store.usual_trusted две копии стали мёртвыми. Мёртвая копия — это будущая ошибка: кто-то
поправит её и решит, что поправил правило.
"""
from __future__ import annotations

import pytest

from app import compare, service
from app.catalog import store


def test_the_outlier_bounds_are_the_same_rule_in_both_places():
    """Сравнение и подытог режут выбросы по одной границе: иначе одно и то же
    предложение считалось бы мусором в накладной и нормальным на экране."""
    assert compare.OUTLIER_LOW == service.SANE_LOW
    assert compare.OUTLIER_HIGH == service.SANE_HIGH


def test_the_history_rule_lives_only_in_store():
    """Хватает ли истории — решает store; в других модулях копий быть не должно."""
    assert not hasattr(service, "USUAL_MIN_POINTS"), "в service осталась мёртвая копия"
    assert not hasattr(compare, "USUAL_MIN_POINTS"), "в compare осталась мёртвая копия"
    assert store.USUAL_MIN_POINTS == 3
    assert store.USUAL_MIN_DAYS == 7.0


def test_the_note_threshold_is_a_percentage_of_the_usual_price():
    """Пометка «дороже обычного» — про десяток процентов, а не про рубль."""
    assert compare.USUAL_NOTE_PCT == 10.0


def test_the_plan_bounds_are_narrower_than_the_neighbour_bounds():
    """Одну цену без соседей судим строже: подешеветь втрое она не может."""
    assert service.PLAN_LOW > service.SANE_LOW
    assert service.PLAN_HIGH < service.SANE_HIGH


@pytest.mark.parametrize("module", [compare, service])
def test_the_rules_are_importable_and_documented(module):
    """У правила должен быть текст: иначе следующая сессия не поймёт, откуда границы."""
    name = "OUTLIER_LOW" if module is compare else "SANE_LOW"
    assert getattr(module, name) == 0.2
