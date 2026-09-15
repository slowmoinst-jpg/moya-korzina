"""Разбор письма или страницы заказа из службы доставки.

Чек ОФД — формат строгий и предсказуемый. Письмо из доставки — наоборот: у Ленты
одна вёрстка, у Самоката другая, у Купера третья, и меняются они, когда захотят.
Но устроены все одинаково: строка товара несёт название, количество и деньги,
а порядок и разделители пляшут.

Поэтому здесь не один шаблон, а несколько, и разбор нарочно снисходительный:
лучше понять девять строк из десяти и честно сказать про десятую, чем не понять
ничего и потребовать «правильный» файл, которого у человека нет.

Что понимает:

    Молоко Простоквашино 930 мл      2 шт × 149,00 ₽ = 298,00 ₽
    Огурцы короткоплодные            0,4 кг × 215 ₽    86 ₽
    Хлеб бородинский                 1 шт              94 ₽
    Сыр Страчателла 200 г            219 ₽

    Молоко Простоквашино 930 мл          <- название и цифры на разных строках,
    2 × 149 ₽                               как часто бывает в письмах
    298 ₽

Если прислали HTML (а письма почти всегда HTML) — теги снимаются, и дальше всё
то же самое.
"""
from __future__ import annotations

import html as html_mod
import re

from app.importers.ofd_pdf import Receipt, ReceiptRow, _extract_date

# деньги: «1 234,56», «1234.56», «149», с неразрывными пробелами внутри
_MONEY = r"\d[\d\s ]*(?:[.,]\d{1,2})?"
_QTY = r"\d+(?:[.,]\d{1,3})?"
_UNITS = r"шт|штук[аи]?|кг|г|гр|мл|л|уп|упак|пак"
# «200 г» в строке «Сыр Страчателла 200 г — 219 ₽» — это фасовка, а не сколько взяли:
# граммами и миллилитрами заказ не считают, ими подписывают упаковку
_COUNT_UNITS = r"шт|штук[аи]?|кг|л|уп|упак|пак"
_CUR = r"(?:₽|руб\.?|р\.)"
_TIMES = r"[x×хX*]"

_TAGS = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S | re.I)
_SPACES = re.compile(r"[ \t   ]+")
_DROP = re.compile(
    r"^\s*(итого|итог|всего|сумма|к оплате|доставка|скидка|бонус|кэшбэк|кешбэк|"
    r"промокод|оплачено|заказ|дата|адрес|курьер|ндс|в том числе|чаевые)\b", re.I)

# «Название 2 шт × 149,00 ₽ = 298,00 ₽» и всё, что от этого остаётся
_FULL = re.compile(
    rf"^(?P<name>.*?\S)[\s.·•—–-]*?(?P<qty>{_QTY})\s*(?:{_UNITS})?\.?\s*{_TIMES}\s*"
    rf"(?P<price>{_MONEY})\s*{_CUR}?[\s=·•—–-]*(?P<total>{_MONEY})?\s*{_CUR}?\s*$",
    re.I)
# «Название 2 шт 298,00 ₽» — без знака умножения
_QTY_TOTAL = re.compile(
    rf"^(?P<name>.*?\S)[\s.·•—–-]+(?P<qty>{_QTY})\s*(?:{_COUNT_UNITS})\.?[\s.·•—–-]+"
    rf"(?P<total>{_MONEY})\s*{_CUR}\s*$", re.I)
# «Название 219 ₽» — одна штука
_JUST_TOTAL = re.compile(
    rf"^(?P<name>.*?\S)[\s.·•—–-]{{2,}}(?P<total>{_MONEY})\s*{_CUR}\s*$", re.I)
# строка без названия: «2 × 149 ₽» — значит название было выше
_NUMBERS_ONLY = re.compile(
    rf"^\s*(?P<qty>{_QTY})\s*(?:{_UNITS})?\.?\s*(?:{_TIMES}\s*(?P<price>{_MONEY})\s*{_CUR}?)?"
    rf"[\s=·•—–-]*(?P<total>{_MONEY})?\s*{_CUR}?\s*$", re.I)
_HAS_MONEY = re.compile(rf"{_MONEY}\s*{_CUR}", re.I)
_TOTAL_LINE = re.compile(rf"^\s*(?:итого|итог|всего|к оплате)\b\D{{0,20}}({_MONEY})", re.I)


def _money(raw) -> float | None:
    cleaned = _SPACES.sub("", str(raw or "")).replace(",", ".")
    try:
        return round(float(cleaned), 2)
    except (TypeError, ValueError):
        return None


def strip_html(text: str) -> str:
    """Письмо почти всегда HTML. Снимаем теги, сохраняя разбиение на строки."""
    if "<" not in text or ">" not in text:
        return text
    body = re.sub(r"</(tr|div|p|li|table|h\d)>", "\n", text, flags=re.I)
    body = re.sub(r"</t[dh]>", "  ", body, flags=re.I)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    return html_mod.unescape(_TAGS.sub(" ", body))


def _clean_name(raw: str) -> str:
    name = _SPACES.sub(" ", raw or "").strip(" .·•—–-\t")
    return name if re.search(r"[а-яa-z]{2}", name, re.I) else ""


def _row(name: str, qty, price, total) -> ReceiptRow | None:
    name = _clean_name(name)
    if not name:
        return None
    count = _money(qty) or 1.0
    unit_price, amount = _money(price), _money(total)
    if amount is None and unit_price is not None:
        amount = round(unit_price * count, 2)
    if unit_price is None and amount is not None:
        unit_price = round(amount / count, 2) if count else amount
    if unit_price is None or amount is None or amount <= 0:
        return None
    return ReceiptRow(raw_name=name, qty=count, unit_price=unit_price, total=amount)


def parse_lines(text: str) -> tuple[list[ReceiptRow], list[str]]:
    """Строки товаров и то, что разобрать не удалось.

    Непонятые строки возвращаются нарочно: человек должен видеть, чего мы не
    поняли, а не гадать, почему в корзине четырнадцать позиций вместо шестнадцати.
    """
    lines = [_SPACES.sub(" ", ln).strip() for ln in strip_html(text).splitlines()]
    lines = [ln for ln in lines if ln]

    rows: list[ReceiptRow] = []
    skipped: list[str] = []
    pending: str | None = None          # название, у которого цифры на следующей строке

    for line in lines:
        if _DROP.match(line):
            pending = None
            continue

        for pattern in (_FULL, _QTY_TOTAL, _JUST_TOTAL):
            match = pattern.match(line)
            if not match:
                continue
            group = match.groupdict()
            row = _row(group.get("name"), group.get("qty"), group.get("price"), group.get("total"))
            if row:
                rows.append(row)
                pending = None
                break
        else:
            numbers = _NUMBERS_ONLY.match(line)
            if numbers and pending and _HAS_MONEY.search(line):
                group = numbers.groupdict()
                row = _row(pending, group.get("qty"), group.get("price"), group.get("total"))
                if row:
                    rows.append(row)
                    pending = None
                    continue
            if not _HAS_MONEY.search(line) and re.search(r"[а-яa-z]{3}", line, re.I):
                pending = line          # похоже на название, цифры ждём ниже
            elif _HAS_MONEY.search(line):
                # хвост вида «298 ₽» сразу после разобранной строки — это её же итог,
                # письма часто печатают его отдельной строкой. Молчим про него.
                seen = numbers.groupdict() if numbers else {}
                value = _money(seen.get("total")) or _money(seen.get("qty"))
                echo = bool(numbers and rows and value == rows[-1].total)
                if not echo:
                    skipped.append(line)
    return rows, skipped


def parse_order(text: str, store_name: str = "") -> tuple[Receipt, list[str]]:
    """Письмо или страница заказа -> чек и список непонятых строк."""
    rows, skipped = parse_lines(text)
    plain = strip_html(text)
    declared = _TOTAL_LINE.search(plain)
    total = _money(declared.group(1)) if declared else None
    if total is None:
        total = round(sum(r.total for r in rows), 2)
    receipt = Receipt(date=_extract_date(plain), store_name=store_name or "—",
                      rows=rows, total=total)
    return receipt, skipped
