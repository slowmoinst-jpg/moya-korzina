"""Разбор чека, письма из доставки или скриншота заказа.

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

    ЛЕНТА                                <- бумажный чек, распознанный телефоном:
    КАССОВЫЙ ЧЕК                            заголовки отбрасываются, номера позиций
    1. Молоко ПРОСТОКВАШИНО 930мл           срезаются, «2 x 149.00 =298.00» под
    2 x 149.00 =298.00                      названием закрывает позицию

    Молоко Простоквашино                 <- скриншот приложения: название разорвано
    2,5%, 930 мл                            на две строки, количество и сумма идут
    2 шт                                    ещё двумя
    298 ₽

    КАССОВЫЙ ЧЕК                         <- чек из «Мои чеки онлайн»: три числа
    1. Мультивитамины 2 474,11 1 2 474,11   подряд без «×» и без «₽», а длинное
    Myprotein Alpha men,                    название переносится ВНИЗ, под строку
    комплекс витаминов и                    с числами
    ИНН Поставщика: 246214864320
    НДС не облагается

Если прислали HTML (а письма почти всегда HTML) — теги снимаются, и дальше всё
то же самое.

Про валюту. Распознавание с фото почти никогда не даёт «₽»: выходит «Р», «P» или
не выходит ничего. Поэтому значок необязателен везде, где можно обойтись формой
самого числа, а «Р» и «P» считаются рублями, если за ними не идёт буква.
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
_CUR = r"(?:₽|руб\.?|р\.|[РPр](?![а-яa-z]))"
_TIMES = r"[x×хX*]"

_TAGS = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S | re.I)
_SPACES = re.compile(r"[ \t   ]+")
_DROP = re.compile(
    r"^\s*(итого|итог|всего|сумма|к оплате|доставка|скидка|бонус|кэшбэк|кешбэк|"
    r"промокод|оплачено|заказ|дата|адрес|курьер|ндс|в том числе|чаевые|"
    # хвост кассового чека ФНС: у этих строк есть суммы, и без отсева
    # «Наличные 0,00» и «Предоплата (аванс) 2 604,00» станут товарами
    r"наличные|безналичные|предоплата|аванс|предмет расчета|предмет расчёта|"
    r"цена|кол-во|количество|инн поставщика|инн|общество с ограниченной|"
    r"акцизный товар|признак способа расчета|код товара)\b", re.I)

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
# Кассовый чек ФНС печатает три числа подряд без знака умножения и без значка
# рубля: «1. Мультивитамины 2 474,11 1 2 474,11». Колонки там подписаны
# «ЦЕНА, Р · КОЛ-ВО · СУММА, Р» — в этом порядке и читаем, см. _three_numbers.
_THREE = re.compile(
    rf"^(?P<name>.*?[а-яa-z].*?)\s+(?P<a>{_MONEY})\s+(?P<b>{_QTY})\s+(?P<c>{_MONEY})"
    rf"\s*{_CUR}?\s*$", re.I)
# строка без названия: «2 × 149 ₽» — значит название было выше
_NUMBERS_ONLY = re.compile(
    rf"^\s*(?P<qty>{_QTY})\s*(?:{_UNITS})?\.?\s*(?:{_TIMES}\s*(?P<price>{_MONEY})\s*{_CUR}?)?"
    rf"[\s=·•—–-]*(?P<total>{_MONEY})?\s*{_CUR}?\s*$", re.I)
_HAS_MONEY = re.compile(rf"{_MONEY}\s*{_CUR}", re.I)
# re.M обязателен: без него «^» цепляется только к началу всего текста, и строка
# «ИТОГО 478 ₽» в середине чека не читалась вовсе — итог молча подменялся суммой
# строк, то есть терял доставку, скидки и всё, что не является товаром
_TOTAL_LINE = re.compile(rf"^\s*(?:итого|итог|всего|к оплате)\b\D{{0,20}}({_MONEY})",
                         re.I | re.M)


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


def _three_numbers(name: str, a, b, c) -> ReceiptRow | None:
    """Строка с тремя числами: цена, количество, сумма.

    Порядок берём такой, как он напечатан в чеке: у ФНС колонки подписаны
    «ЦЕНА, Р · КОЛ-ВО · СУММА, Р», и это наш основной источник.

    Угадывать порядок умножением бессмысленно, и это стоит записать, чтобы никто
    не переоткрывал: умножение коммутативно, «3 × 100» и «100 × 3» дают одну и ту
    же сумму, так что проверка сошлась бы при любой расстановке и ничего не
    различала. Единственная настоящая опора — подписи столбцов.

    Умножение всё же считаем, но для другого: если цена на количество не сходится
    с суммой, значит строка разобрана неверно, и тогда честнее взять только сумму
    (последнее число в чеках самое надёжное), чем множить мусор.
    """
    price, qty, total = _money(a), _money(b), _money(c)
    if total is None or total <= 0:
        return None
    if price is not None and qty and qty > 0:
        if abs(price * qty - total) <= max(0.02, total * 0.01):
            return _row(name, qty, price, total)
    return _row(name, qty if qty else 1.0, None, total)


# строка без названия, одни числа: «2 x 149.00 = 298.00», «2 шт», «298 ₽»
_CALC = re.compile(rf"^\s*(?P<qty>{_QTY})\s*(?:{_UNITS})?\.?\s*{_TIMES}\s*(?P<price>{_MONEY})"
                   rf"\s*{_CUR}?[\s=·•—–-]*(?P<total>{_MONEY})?\s*{_CUR}?\s*$", re.I)
_ONLY_QTY = re.compile(rf"^\s*(?P<qty>{_QTY})\s*(?P<unit>{_UNITS})\.?\s*$", re.I)
_ONLY_MONEY = re.compile(rf"^\s*(?P<total>{_MONEY})\s*{_CUR}\s*$", re.I)
_NUMBER_PREFIX = re.compile(r"^\s*\d{1,3}\s*[.)]\s+")
# строки-заголовки чека, которые названием товара быть не могут
_HEADER = re.compile(
    r"^\s*(?:(?:кассовый|товарный)\s+чек|чек|приход|смена|кассир|инн|фн|фд|фпд?|сно|ккт|рн\s+ккт|сайт\s+фнс|www\.|ваш\s+заказ|заказ\s*№|наличными|картой|безналичными|электронными)(?![а-яa-z])",
    re.I)


def parse_lines(text: str) -> tuple[list[ReceiptRow], list[str]]:
    """Строки товаров и то, что разобрать не удалось.

    Настоящий чек редко укладывается в одну строку на позицию. В бумажном название
    стоит отдельно, а «2 x 149.00 = 298.00» — под ним. В скриншоте приложения
    название вообще разорвано на две строки, потом «2 шт», потом «298 ₽». Поэтому
    разбор устроен как небольшой автомат: копим строки, похожие на название, и
    закрываем позицию, когда встречаем числа.

    Непонятые строки возвращаются нарочно: человек должен видеть, чего мы не
    взяли, а не гадать, почему в корзине четырнадцать позиций вместо шестнадцати.
    """
    lines = [_SPACES.sub(" ", ln).strip() for ln in strip_html(text).splitlines()]
    lines = [ln for ln in lines if ln]

    rows: list[ReceiptRow] = []
    skipped: list[str] = []
    name_parts: list[str] = []
    qty_seen: float | None = None
    tail: ReceiptRow | None = None    # позиция, к которой дописывается перенос названия

    def name() -> str:
        return _clean_name(", ".join(name_parts[-3:]))

    def close(qty, price, total) -> bool:
        nonlocal name_parts, qty_seen, tail
        row = _row(name(), qty, price, total)
        name_parts, qty_seen, tail = [], None, None
        if row:
            rows.append(row)
            return True
        return False

    for raw in lines:
        line = _NUMBER_PREFIX.sub("", raw)

        if _DROP.match(line) or _HEADER.match(line):
            name_parts, qty_seen, tail = [], None, None
            continue

        # позиция целиком в одной строке
        done = False
        three = _THREE.match(line)
        if three:
            group = three.groupdict()
            row = _three_numbers(group["name"], group["a"], group["b"], group["c"])
            if row:
                rows.append(row)
                name_parts, qty_seen = [], None
                tail = row              # хвост названия может идти следующими строками
                done = True
        if not done:
            for pattern in (_FULL, _QTY_TOTAL, _JUST_TOTAL):
                match = pattern.match(line)
                if not match:
                    continue
                group = match.groupdict()
                row = _row(group.get("name"), group.get("qty"),
                           group.get("price"), group.get("total"))
                if row:
                    rows.append(row)
                    name_parts, qty_seen = [], None
                    tail = row
                    done = True
                    break
        if done:
            continue

        # «2 x 149.00 = 298.00» под названием
        calc = _CALC.match(line)
        if calc and name_parts:
            group = calc.groupdict()
            if close(group.get("qty"), group.get("price"), group.get("total")):
                continue
            skipped.append(raw)
            continue

        # «2 шт» отдельной строкой — количество запомним, сумма будет ниже
        only_qty = _ONLY_QTY.match(line)
        if only_qty and name_parts:
            qty_seen = _money(only_qty.group("qty"))
            continue

        # «298 ₽» отдельной строкой — это итог по накопленной позиции
        only_money = _ONLY_MONEY.match(line)
        if only_money:
            if name_parts and close(qty_seen, None, only_money.group("total")):
                continue
            value = _money(only_money.group("total"))
            if not (rows and value == rows[-1].total):   # эхо суммы, письма его печатают
                skipped.append(raw)
            continue

        if _HAS_MONEY.search(line):
            skipped.append(raw)
            name_parts, qty_seen, tail = [], None, None
        elif tail is not None and not _NUMBER_PREFIX.match(raw):
            # Хвост названия. В кассовом чеке ФНС длинное название переносится ВНИЗ:
            # «1. Мультивитамины 2 474,11 1 2 474,11», а следом «Myprotein Alpha men,»,
            # «комплекс витаминов и» и так далее. Дописываем их к только что созданной
            # позиции — иначе от товара останется первое слово.
            tail.raw_name = _clean_name(tail.raw_name + " " + line)
        elif re.search(r"[а-яa-z]{3}", line, re.I):
            name_parts.append(line)                       # похоже на начало названия
        elif name_parts and re.search(r"[а-яa-z]", line, re.I):
            # продолжение названия судим мягче: «2,5%, 930 мл» — это фасовка, слова
            # из трёх букв в ней нет вовсе, а терять её нельзя, иначе от «Молока
            # Простоквашино 2,5% 930 мл» останется просто «Молоко Простоквашино»
            name_parts.append(line)

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
