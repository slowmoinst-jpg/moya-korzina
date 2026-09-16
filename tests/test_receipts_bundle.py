"""Пакетная загрузка чеков и ответ «сколько загружено, что ещё можно».

Образец пакета — не выдумка: это в точности то, что отдал букмарклет, когда его
прогнали по поддельному кабинету ФНС (250 чеков, три страницы списка, позиции
отдельным запросом по каждому чеку). Формы ответов взяты из собственного кода
кабинета: список отдаёт {receipts, brands, hasMore}, позиции — items[] с
name/quantity/price/sum, суммы целыми в копейках.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import config, receipts, repo  # noqa: E402
from app.db import init_db  # noqa: E402
from app.importers.bundle import fingerprint, import_bundle, parse_bundle  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", lambda: str(tmp_path / "r.db"))
    init_db()


def receipt(key: str, day: str, brand: str, items: list[tuple[str, int, int]]) -> dict:
    """Чек в том виде, в каком его кладёт букмарклет: опись снаружи, позиции внутри."""
    return {
        "key": key,
        "date": f"{day}T19:41:00",
        "store": brand,
        "total": sum(price * qty for _, qty, price in items),
        "fiscalData": {
            "key": key,
            "dateTime": f"{day}T19:41:00",
            "retailPlace": f"Торговая точка {key}",
            "totalSum": sum(price * qty for _, qty, price in items),
            "items": [{"name": name, "quantity": qty, "price": price,
                       "sum": price * qty, "nds": 1} for name, qty, price in items],
        },
    }


TWO = [
    receipt("k1", "2026-08-16", "Пятёрочка",
            [("Молоко 3.2% 900 мл", 2, 10990), ("Хлеб Бородинский 400 г", 1, 5450)]),
    receipt("k2", "2026-08-20", "ВкусВилл",
            [("Сыр Страчателла 200 г", 1, 32900)]),
]


def bundle(taken: list[dict], catalogue: list[dict] | None = None) -> str:
    return json.dumps({
        "source": "lkdr",
        "takenAt": "2026-09-15T20:05:39.742Z",
        "catalogue": catalogue if catalogue is not None
        else [{"key": r["key"], "date": r.get("date"), "store": r.get("store"),
               "total": r.get("total")} for r in taken],
        "receipts": taken,
        "failed": [],
    }, ensure_ascii=False)


# ---------- разбор ----------
def test_bundle_is_split_into_receipts_and_catalogue():
    parsed = parse_bundle(bundle(TWO))

    assert len(parsed["receipts"]) == 2
    assert len(parsed["catalogue"]) == 2
    assert parsed["source"] == "lkdr"
    assert [r["key"] for r in parsed["receipts"]] == ["k1", "k2"]


def test_kopecks_become_rubles():
    """ФНС шлёт суммы целыми в копейках. 10990 — это 109,90 ₽, а не десять тысяч."""
    first = parse_bundle(bundle(TWO))["receipts"][0]["receipt"]

    assert first.rows[0].unit_price == 109.90
    assert first.rows[0].total == 219.80
    assert first.total == 274.30


def test_brand_from_the_list_beats_the_retail_place():
    """В описи лежит сеть, внутри чека — торговая точка. Сопоставить можно только сеть."""
    first = parse_bundle(bundle(TWO))["receipts"][0]["receipt"]

    assert first.store_name == "Пятёрочка"


def test_broken_receipt_does_not_kill_the_rest():
    """Один непонятый чек не должен ронять пакет: остальные обязаны загрузиться."""
    broken = {"key": "k3", "fiscalData": {"dateTime": "2026-08-21T10:00:00", "items": []}}
    parsed = parse_bundle(bundle(TWO + [broken]))

    assert len(parsed["receipts"]) == 2
    assert [f["key"] for f in parsed["failed"]] == ["k3"]


# ---------- загрузка ----------
def test_import_writes_history_and_counts_receipts(db):
    result = import_bundle(bundle(TWO))

    assert result["receipts"] == 2 and result["rows"] == 3
    assert result["total"] == 274.30 + 329.00
    assert result["period"] == ("2026-08-16", "2026-08-20")
    assert len(repo.list_history()) == 3


def test_second_run_adds_nothing(db):
    """Автоматическая загрузка повторяется. Второй заход обязан быть безвредным."""
    import_bundle(bundle(TWO))
    again = import_bundle(bundle(TWO))

    assert again["receipts"] == 0 and again["skipped"] == 2
    assert len(repo.list_history()) == 3, "история удвоилась — дедупликация не сработала"


def test_only_new_receipts_are_added(db):
    import_bundle(bundle(TWO[:1]))
    third = receipt("k3", "2026-09-01", "Магнит", [("Кофе зерновой 1 кг", 1, 89900)])
    result = import_bundle(bundle(TWO + [third]))

    assert result["receipts"] == 2 and result["skipped"] == 1
    assert len(repo.imported_receipt_keys()) == 3


def test_history_rows_know_their_receipt(db):
    """Позиция обязана помнить, из какого чека пришла: иначе чек не убрать назад."""
    import_bundle(bundle(TWO))
    repo.forget_receipt("k1")

    assert len(repo.list_history()) == 1
    assert repo.imported_receipt_keys() == {"k2"}


def test_store_is_matched_to_our_directory(db):
    import_bundle(bundle(TWO))
    rows = {r["raw_name"]: r["store_code"] for r in repo.list_history()}

    assert rows["Молоко 3.2% 900 мл"] == "pyaterochka"
    assert rows["Сыр Страчателла 200 г"] == "vkusvill"


# ---------- «что ещё можно загрузить» ----------
def test_catalogue_leaves_the_rest_as_pending(db):
    """Главный ответ: кабинет показал пять чеков, позиции приехали по двум."""
    wide = [{"key": f"k{i}", "date": f"2026-07-0{i}", "store": "Магнит", "total": 100000}
            for i in range(1, 6)]
    import_bundle(bundle(TWO, catalogue=wide))

    left = receipts.pending()
    assert left["receipts"] == 3, "невзятыми считаются только те, чьих позиций нет"
    assert {r["key"] for r in repo.pending_receipts()} == {"k3", "k4", "k5"}


def test_loaded_counts_what_is_in_history(db):
    import_bundle(bundle(TWO))
    have = receipts.loaded()

    assert have["receipts"] == 2 and have["rows"] == 3
    assert have["first"] == "2026-08-16" and have["last"] == "2026-08-20"


def test_headline_names_both_numbers(db):
    wide = [{"key": f"k{i}", "date": "2026-07-01", "store": "Магнит", "total": 100000}
            for i in range(1, 6)]
    import_bundle(bundle(TWO, catalogue=wide))
    line = receipts.headline()

    assert "2 чека" in line and "3 позиции" in line
    assert "ещё 3 чека" in line, f"не сказано, сколько осталось: {line}"


def test_headline_on_empty_history(db):
    assert "ни одной" in receipts.headline()


def test_next_steps_put_pending_first(db):
    wide = [{"key": f"k{i}", "date": "2026-07-01", "store": "Магнит", "total": 100}
            for i in range(1, 6)]
    import_bundle(bundle(TWO, catalogue=wide))
    steps = receipts.next_steps()

    assert steps[0]["code"] == "lkdr-pending"
    assert "3" in steps[0]["name"]


def test_next_steps_offer_other_sources_when_cabinet_is_empty(db):
    import_bundle(bundle(TWO))
    codes = [s["code"] for s in receipts.next_steps()]

    assert "lkdr" not in codes, "кабинет уже вычерпан — незачем предлагать его снова"
    assert "email" in codes and "ofd" in codes


# ---------- отпечаток для источников без ключа ----------
def test_same_receipt_without_key_is_recognised(db):
    """У файла и у вставки своего ключа нет — отпечаток считаем сами."""
    plain = {"dateTime": "2026-08-16T19:41:00", "retailPlace": "Пятёрочка",
             "totalSum": 21980, "items": [{"name": "Молоко", "quantity": 2, "price": 10990,
                                           "sum": 21980}]}
    first = import_bundle(json.dumps(plain, ensure_ascii=False))
    second = import_bundle(json.dumps(plain, ensure_ascii=False))

    assert first["receipts"] == 1 and second["receipts"] == 0
    assert len(repo.list_history()) == 1


def test_fingerprint_separates_different_receipts():
    a = parse_bundle(bundle(TWO))["receipts"][0]["receipt"]
    b = parse_bundle(bundle(TWO))["receipts"][1]["receipt"]

    assert fingerprint(a) != fingerprint(b)
    assert fingerprint(a) == fingerprint(a)


# ---------- подзаголовок ----------
def test_short_line_does_not_repeat_the_card(db):
    import_bundle(bundle(TWO))

    assert receipts.short() == "2 чека · 3 позиции · последняя покупка 20.08.2026"


def test_short_line_names_what_is_left(db):
    wide = [{"key": f"k{i}", "date": "2026-07-01", "store": "Магнит", "total": 100}
            for i in range(1, 6)]
    import_bundle(bundle(TWO, catalogue=wide))

    assert receipts.short().endswith("не загружено 3")


def test_plural_forms():
    """Числа склоняются: «2 чеков» в отчёте о своей же работе читается как небрежность."""
    forms = ("чек", "чека", "чеков")

    assert receipts.plural(1, *forms) == "1 чек"
    assert receipts.plural(2, *forms) == "2 чека"
    assert receipts.plural(5, *forms) == "5 чеков"
    assert receipts.plural(11, *forms) == "11 чеков"
    assert receipts.plural(21, *forms) == "21 чек"


# ---------- файл ----------
def test_same_file_loaded_twice_does_not_double_history(db, tmp_path):
    """Повторно загруженный файл — обычное дело, и он не должен удваивать историю."""
    from app.importers import import_receipt

    path = tmp_path / "cheque.json"
    path.write_text(json.dumps(TWO[0]["fiscalData"], ensure_ascii=False), encoding="utf-8")

    first = import_receipt(str(path))
    second = import_receipt(str(path))

    assert first["receipts"] == 1 and second["receipts"] == 0
    assert second["skipped"] == 1
    assert len(repo.list_history()) == 2
