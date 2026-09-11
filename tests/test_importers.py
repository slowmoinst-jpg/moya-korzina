"""Источники загрузки, кроме бумажного чека: JSON от ОФД и таблицы выгрузок."""
from __future__ import annotations

import csv
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.importers import parse_receipt, parse_receipt_json, parse_receipt_table  # noqa: E402

FNS_JSON = {
    "document": {
        "receipt": {
            "dateTime": "2026-08-16T19:42:00",
            "retailPlace": "Пятёрочка",
            "totalSum": 36904,
            "items": [
                {"name": "Огурцы короткоплодные 450г", "quantity": 1, "price": 9990, "sum": 9990},
                {"name": "Слива", "quantity": 0.9, "price": 23900, "sum": 21510},
                {"name": "Хлебцы ржано-пшеничные 240г", "quantity": 2, "price": 4990, "sum": 9980},
            ],
        }
    }
}

TABLE = [
    ["Дата", "Магазин", "Наименование", "Кол-во", "Цена", "Сумма", "Статус"],
    ["16.08.2026", "ВкусВилл", "Страчателла 200 г", "1", "239,00", "239,00", "доставлен"],
    ["16.08.2026", "ВкусВилл", "Нектарины", "0,5", "149,00", "74,50", "доставлен"],
]


# ---------- JSON ----------
def test_json_kopecks_become_rubles():
    """ФНС отдаёт копейки целыми числами — 9990 это 99,90 ₽, а не 9990 ₽."""
    receipt = parse_receipt_json(json.dumps(FNS_JSON))
    assert len(receipt.rows) == 3
    assert receipt.rows[0].unit_price == pytest.approx(99.90)
    assert receipt.total == pytest.approx(369.04)


def test_json_finds_items_at_any_depth():
    """Структура у разных операторов вложена по-разному."""
    flat = parse_receipt_json(json.dumps(FNS_JSON["document"]["receipt"]))
    deep = parse_receipt_json(json.dumps({"a": {"b": [FNS_JSON]}}))
    assert [r.raw_name for r in flat.rows] == [r.raw_name for r in deep.rows]


def test_json_keeps_weight_and_date():
    receipt = parse_receipt_json(json.dumps(FNS_JSON))
    plum = next(r for r in receipt.rows if r.raw_name == "Слива")
    assert plum.qty == pytest.approx(0.9)
    assert plum.total == pytest.approx(215.10)
    assert receipt.date == "2026-08-16"
    assert receipt.store_name == "Пятёрочка"


def test_json_without_items_is_rejected():
    with pytest.raises(ValueError):
        parse_receipt_json(json.dumps({"dateTime": "2026-08-16", "totalSum": 100}))


# ---------- таблицы ----------
def _write_csv(path, rows, delimiter=";", encoding="utf-8-sig"):
    with open(path, "w", encoding=encoding, newline="") as fh:
        csv.writer(fh, delimiter=delimiter).writerows(rows)
    return str(path)


def test_csv_with_semicolons_and_commas(tmp_path):
    """Выгрузки приходят с точкой с запятой и запятой в числах."""
    receipt = parse_receipt_table(_write_csv(tmp_path / "order.csv", TABLE))
    assert len(receipt.rows) == 2
    assert receipt.store_name == "ВкусВилл"
    assert receipt.date == "2026-08-16"
    assert receipt.total == pytest.approx(313.50)


def test_csv_computes_missing_price_from_sum(tmp_path):
    rows = [["Наименование", "Количество", "Сумма"], ["Нектарины", "0,5", "74,50"]]
    receipt = parse_receipt_table(_write_csv(tmp_path / "no_price.csv", rows))
    assert receipt.rows[0].unit_price == pytest.approx(149.0)


def test_csv_without_name_column_is_rejected(tmp_path):
    rows = [["Количество", "Цена"], ["1", "100"]]
    with pytest.raises(ValueError):
        parse_receipt_table(_write_csv(tmp_path / "no_name.csv", rows))


def test_xlsx_reads_like_csv(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "order.xlsx"
    book = openpyxl.Workbook()
    for row in TABLE:
        book.active.append(row)
    book.save(path)
    receipt = parse_receipt_table(str(path))
    assert [r.raw_name for r in receipt.rows] == ["Страчателла 200 г", "Нектарины"]
    assert receipt.total == pytest.approx(313.50)


# ---------- общий вход ----------
def test_parse_receipt_dispatches_by_extension(tmp_path):
    """Один вход разбирает и чек, и выгрузку — по расширению файла."""
    json_path = tmp_path / "receipt.json"
    json_path.write_text(json.dumps(FNS_JSON), encoding="utf-8")
    csv_path = _write_csv(tmp_path / "order.csv", TABLE)

    assert len(parse_receipt(str(json_path)).rows) == 3
    assert len(parse_receipt(csv_path).rows) == 2
    assert len(parse_receipt(os.path.join(ROOT, "data", "sample_receipt.txt")).rows) == 16
