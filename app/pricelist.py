"""Прайс-лист, заведённый руками, — для магазинов без доступного каталога.

У Пятёрочки и Дикси спросить цены не у кого: 5ka.ru отвечает заглушкой и 403 на
всё, dixy.ru показывает капчу, MCP у них нет. Чеки закрывают только то, что человек
уже покупал, — а корзину он собирает и из нового.

Поэтому второй источник: файл с ценами, который человек приносит сам. Хоть выписал
с ценников в зале, хоть выгрузил из приложения магазина, хоть скопировал из
семейной таблицы. Формат нарочно снисходительный — лишь бы нашлись название и
цена:

    Название;Цена;Единица;Артикул
    Молоко 1 л;79,90;шт;
    Яблоки;149;кг;

Понимает и заголовки по-русски, и по-английски (name/price/unit/sku), и запятую
как десятичный разделитель, и точку с запятой как разделитель колонок.

Файл лежит в data/prices_<код магазина>.csv и целиком принадлежит человеку: мы
его только читаем и перезаписываем при повторной загрузке. Дата файла — это дата
цен, и коннектор доносит её до интерфейса, чтобы было видно, насколько прайс
несвежий.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import os
import re

from app import config

log = logging.getLogger(__name__)

DIR = os.path.join(config.ROOT, "data")
PREFIX = "prices_"
SKU_PREFIX = "list-"

# как могут называться колонки — по-русски и по-английски
_NAME = ("название", "наименование", "товар", "name", "product", "title")
_PRICE = ("цена", "price", "стоимость", "цена за единицу")
_UNIT = ("единица", "ед", "unit", "мера")
_SKU = ("артикул", "код", "sku", "id")
_NUM = re.compile(r"[-+]?\d[\d\s  ]*[.,]?\d*")


def path_for(store_code: str) -> str:
    return os.path.join(DIR, f"{PREFIX}{store_code}.csv")


def _to_float(raw) -> float | None:
    match = _NUM.search(str(raw or "").replace(" ", " "))
    if not match:
        return None
    try:
        return round(float(match.group(0).replace(" ", "").replace(",", ".")), 2)
    except ValueError:
        return None


def _column(headers: list[str], names: tuple[str, ...]) -> int | None:
    for i, head in enumerate(headers):
        clean = (head or "").strip().lower().replace("ё", "е")
        if clean in names or any(clean.startswith(n) for n in names):
            return i
    return None


def _unit(raw: str | None) -> str:
    text = (raw or "").strip().lower()
    return "kg" if text.startswith("кг") or text in ("kg", "килограмм") else "pcs"


def parse(text: str) -> list[dict]:
    """Строки прайса из текста CSV. Непонятные строки молча пропускаем — это не ошибка."""
    if not text.strip():
        return []
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if text.count(";") > text.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        return []

    headers = [str(c) for c in rows[0]]
    i_name, i_price = _column(headers, _NAME), _column(headers, _PRICE)
    body = rows[1:]
    if i_name is None or i_price is None:          # заголовка нет — считаем, что это «название; цена»
        i_name, i_price, body = 0, 1, rows
    i_unit, i_sku = _column(headers, _UNIT), _column(headers, _SKU)

    out: list[dict] = []
    for number, row in enumerate(body, start=1):
        if len(row) <= max(i_name, i_price):
            continue
        name = (row[i_name] or "").strip()
        price = _to_float(row[i_price])
        if not name or price is None or price <= 0:
            continue
        sku = (row[i_sku].strip() if i_sku is not None and len(row) > i_sku else "") or f"{SKU_PREFIX}{number}"
        out.append({
            "sku": sku,
            "name": name,
            "price": price,
            "unit": _unit(row[i_unit] if i_unit is not None and len(row) > i_unit else None),
        })
    return out


def load(store_code: str) -> list[dict]:
    """Прайс магазина. Пустой список, если файла нет, — это нормальный ответ."""
    path = path_for(store_code)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return parse(fh.read())
    except OSError as exc:
        log.warning("не смог прочитать прайс %s: %s", path, exc)
        return []


def updated_at(store_code: str) -> str:
    """Дата файла — она же дата цен. Пустая строка, если прайса нет."""
    path = path_for(store_code)
    if not os.path.exists(path):
        return ""
    return dt.date.fromtimestamp(os.path.getmtime(path)).isoformat()


def save(store_code: str, text: str) -> int:
    """Кладёт присланный файл на место прайса магазина. Возвращает число понятых строк."""
    rows = parse(text)
    if not rows:
        return 0
    os.makedirs(DIR, exist_ok=True)
    with open(path_for(store_code), "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["Название", "Цена", "Единица", "Артикул"])
        for row in rows:
            writer.writerow([row["name"], f"{row['price']:.2f}".replace(".", ","),
                             "кг" if row["unit"] == "kg" else "шт", row["sku"]])
    return len(rows)


def remove(store_code: str) -> bool:
    path = path_for(store_code)
    if not os.path.exists(path):
        return False
    os.remove(path)
    return True
