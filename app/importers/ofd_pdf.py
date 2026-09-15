"""Импорт чека ОФД: PDF (pdfplumber) или текстовая выгрузка (спецификация, раздел 5.1).

Разбирает строки вида:
    1. Огурцы короткоплодные 450г 1 x 109.99 = 109.99      (штучный товар)
    6. Слива 0.970 x 249.99 = 242.49                       (весовой товар)
и шапку с магазином, датой и итогом.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from app import repo
from app.db import init_db
from app.matcher.normalize import display_name, normalize_name, parse_weight


@dataclass
class ReceiptRow:
    raw_name: str
    qty: float
    unit_price: float
    total: float
    barcode: str | None = None      # если ОФД его отдал — это лучший ключ сопоставления


@dataclass
class Receipt:
    date: str                      # 'YYYY-MM-DD'
    store_name: str
    rows: list[ReceiptRow] = field(default_factory=list)
    total: float = 0.0


# --- регулярные выражения --------------------------------------------------

# необязательный номер позиции, название, кол-во x цена = сумма
_ROW_RE = re.compile(
    r"^\s*(?:\d{1,3}[.)]\s*)?"
    r"(?P<name>\S.*?)\s+"
    r"(?P<qty>\d+(?:[.,]\d+)?)\s*[xх*×]\s*"
    r"(?P<price>\d[\d\s]*(?:[.,]\d+)?)\s*=\s*"
    r"(?P<total>\d[\d\s]*(?:[.,]\d+)?)\s*(?:руб|₽|р\.)?\s*$",
    re.IGNORECASE,
)
_TOTAL_RE = re.compile(r"^\s*(?:ИТОГ|ИТОГО|ВСЕГО|СУММА\s+ЧЕКА)\b\D{0,12}"
                       r"(\d[\d\s]*(?:[.,]\d{1,2})?)", re.IGNORECASE)
_DATE_DMY_RE = re.compile(r"(\d{2})[.\-/](\d{2})[.\-/](\d{4})")
_DATE_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

KNOWN_STORES = {
    "пятерочка": "Пятёрочка",
    "магнит": "Магнит",
    "вкусвилл": "ВкусВилл",
    "перекресток": "Перекрёсток",
    "лента": "Лента",
    "дикси": "Дикси",
    "ашан": "Ашан",
    "окей": "О'Кей",
}


def _money(text: str) -> float:
    return round(float(text.replace(" ", "").replace(" ", "").replace(",", ".")), 2)


def _extract_date(text: str) -> str:
    m = _DATE_ISO_RE.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _DATE_DMY_RE.search(text)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return ""


def _extract_store(text: str) -> str:
    flat = text.lower().replace("ё", "е")
    for key, title in KNOWN_STORES.items():
        if key in flat:
            return title
    for line in text.splitlines()[:8]:
        if "магазин" in line.lower():
            return line.strip()
    return "Неизвестный магазин"


# --- разбор ----------------------------------------------------------------

def parse_receipt_text(text: str) -> Receipt:
    """Разбирает текст чека ОФД в структуру Receipt."""
    rows: list[ReceiptRow] = []
    declared_total: float | None = None

    for line in text.splitlines():
        line = line.replace(" ", " ").rstrip()
        if not line.strip():
            continue
        if declared_total is None:
            mt = _TOTAL_RE.match(line)
            if mt:
                declared_total = _money(mt.group(1))
                continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        name = m.group("name").strip(" .\t-")
        if not name or not re.search(r"[а-яА-Яa-zA-Z]", name):
            continue
        rows.append(ReceiptRow(
            raw_name=name,
            qty=float(m.group("qty").replace(",", ".")),
            unit_price=_money(m.group("price")),
            total=_money(m.group("total")),
        ))

    calc = round(sum(r.total for r in rows), 2)
    total = declared_total if declared_total is not None else calc
    return Receipt(date=_extract_date(text), store_name=_extract_store(text), rows=rows, total=total)


def _read_pdf(path: str) -> str:
    import pdfplumber

    chunks: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks)


def _read_txt(path: str) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            with open(path, encoding=enc) as fh:
                return fh.read()
        except UnicodeDecodeError:
            continue
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


SUPPORTED = (".pdf", ".txt", ".json", ".csv", ".xlsx", ".xlsm", ".html", ".htm", ".eml")


def parse_receipt(path: str) -> Receipt:
    """Разбирает покупку из файла, выбирая разбор по расширению.

    .pdf  — бумажный чек ОФД через pdfplumber
    .txt  — тот же чек текстом
    .json — выгрузка чека из «Мои чеки онлайн» ФНС или от оператора данных
    .csv / .xlsx — таблица: выгрузка заказа из личного кабинета или свой список
    .html / .eml — письмо из доставки или сохранённая страница заказа
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    ext = os.path.splitext(path)[1].lower()

    if ext in (".json", ".csv", ".xlsx", ".xlsm"):
        # поздний импорт: sources берёт Receipt отсюда, встречный импорт был бы кольцом
        from app.importers import sources

        if ext == ".json":
            return sources.parse_receipt_json(_read_txt(path))
        return sources.parse_receipt_table(path)

    text = _read_pdf(path) if ext == ".pdf" else _read_txt(path)
    if ext in (".html", ".htm", ".eml"):
        from app.importers.orders import parse_order

        return parse_order(text)[0]
    receipt = parse_receipt_text(text)
    if not receipt.rows:
        # строгий формат ОФД не подошёл — похоже, это письмо из доставки
        from app.importers.orders import parse_order

        loose = parse_order(text)[0]
        if loose.rows:
            return loose
    return receipt


# --- загрузка в базу -------------------------------------------------------

def _resolve_store_id(receipt: Receipt, store_code: str | None) -> tuple[int | None, str]:
    if store_code:
        store = repo.get_store(store_code)
        if store:
            return store.id, store.code
        return None, store_code
    wanted = receipt.store_name.lower().replace("ё", "е")
    for store in repo.list_stores():
        if store.name.lower().replace("ё", "е") in wanted:
            return store.id, store.code
    return None, receipt.store_name


def _ensure_product(raw_name: str) -> tuple[int, bool]:
    """Находит эталон по нормализованному названию или заводит новый. -> (id, создан ли)"""
    from app.models import Product

    name = display_name(raw_name)
    existing = repo.find_product_by_name(name)
    if existing and existing.id:
        return existing.id, False
    grams, unit = parse_weight(raw_name)
    pid = repo.upsert_product(Product(id=None, name=name, weight_g=grams, unit=unit))
    return pid, True


def _remember_barcode(product_id: int, barcode: str) -> None:
    """Запоминает штрихкод у эталона, если его там ещё нет.

    Уже записанный не трогаем: в чеке может оказаться код другой фасовки, а тот,
    что человек подтвердил руками, надёжнее.
    """
    product = repo.get_product(product_id)
    if product is None or getattr(product, "barcode", None):
        return
    product.barcode = barcode
    repo.upsert_product(product)


def import_receipt(path: str, store_code: str | None = None) -> dict:
    """Разбирает чек и пишет его в purchase_history, заводя недостающие эталоны.

    Повторный импорт того же чека не плодит дубли эталонов (поиск через
    repo.find_product_by_name по нормализованному названию).
    """
    init_db()
    receipt = parse_receipt(path)
    store_id, store_label = _resolve_store_id(receipt, store_code)

    created = 0
    for row in receipt.rows:
        product_id, is_new = _ensure_product(row.raw_name)
        created += int(is_new)
        if row.barcode:
            _remember_barcode(product_id, row.barcode)
        repo.add_history_row(
            date=receipt.date, store_id=store_id, product_id=product_id,
            raw_name=row.raw_name, qty=row.qty, unit_price=row.unit_price, total=row.total,
        )
    return {
        "rows": len(receipt.rows),
        "products_created": created,
        "total": receipt.total,
        "date": receipt.date,
        "store": store_label,
    }


__all__ = ["Receipt", "ReceiptRow", "parse_receipt", "parse_receipt_text",
           "import_receipt", "normalize_name"]
