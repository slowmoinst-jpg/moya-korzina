"""Нормализация названий товаров и разбор граммовки (спецификация, раздел 5.1).

Задача: привести сырое название из чека или каталога магазина к сопоставимому виду.
Пример из спецификации: 'ВК/ПОЛ.Сыр СТРАЧАТЕЛЛА мяг.200г' -> 'страчателла 200 г'.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# --- словари ---------------------------------------------------------------

# Префикс поставщика/категории в начале строки чека: 'ВК/ПОЛ.', 'П/Ф.', 'ТМ.'
_PREFIX_RE = re.compile(r"^[а-яa-z]{1,4}(?:[/\\][а-яa-z]{1,4})*\.")

# Мусорные токены: сокращения ОФД и родовые слова, не несущие различающего смысла.
NOISE_WORDS: set[str] = {
    "вк", "пол", "тм", "ип", "ооо", "зао", "оао", "ао",
    "шт", "уп", "упак", "упаковка", "пак", "пакет", "ед", "кор", "бут",
    "вес", "весовой", "весовая", "нов", "новинка", "акция",
    "сыр", "мяг", "мягк", "мягкий", "мягкая",
}

# ЗАЧЕМ (19.10.2026, пункт владельца 3). Служебные слова сеть и чек пишут вразнобой:
# «Онигири с семгой и соусом» против «Онигири семгой соусом», «Сосиска в тесте» против
# «Сосиска тесте». Однобуквенные (с, и, в, к, у, о) уже отбрасывает правило «короче
# двух букв» ниже, а двух- и трёхбуквенные оставались словами и разводили один товар.
# Здесь только самостоятельные токены — внутри слова («напиток», «изюм») ничего не
# режется. «без» и «не» сюда НАРОЧНО не входят: это отрицания, они смысловые
# (match.glue склеивает их в «безгаза»/«негаз», и товар обязан остаться другим).
SERVICE_WORDS: set[str] = {
    "со", "из", "на", "по", "до", "от", "за", "во", "об",
    "при", "под", "над",
}

# Единицы измерения, которые остаются в нормализованной строке.
_KEEP_TOKENS: set[str] = {"г", "кг", "мл", "л", "шт"}

_UNIT_TO_G = {"г": 1.0, "гр": 1.0, "g": 1.0, "мл": 1.0, "ml": 1.0,
              "кг": 1000.0, "kg": 1000.0, "л": 1000.0, "l": 1000.0}
_UNIT_CANON = {"г": "г", "гр": "г", "g": "г", "мл": "мл", "ml": "мл",
               "кг": "кг", "kg": "кг", "л": "л", "l": "л"}

# Порядок альтернатив важен: 'кг' и 'гр' должны проверяться раньше 'г'.
_WEIGHT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(кг|kg|гр|мл|ml|г|g|л|l)(?![а-яa-z])")

_PUNCT_RE = re.compile(r"[./\\,;:()\[\]{}\"'«»+*®™_-]")
_JUNK_RE = re.compile(r"[^0-9a-zа-я ]+")

# ОДИНОЧНАЯ ЦИФРА в названии. ЗАЧЕМ (19.10.2026, пункт владельца 3). «Желтое манго 1» и
# «Манго желтое», «4 Хинкали с говядиной и свининой» и «Хинкали…» — один товар, но цифра
# оставалась в нормализованном имени, а значит и в ключе написания/поиска: «почти тот же
# товар, а не сошёлся».
#
# РЕЖЕМ ТОЛЬКО ОДНУ ЦИФРУ (1–9) и только самостоятельную. Многозначные числа остаются:
# это коды и размеры — «Макароны … спирали №366» против «№370», «Аванс … 9000000000»,
# «OZON FRESH 35+18х61»; вырезав их, мы бы склеили разные товары. Одиночная цифра
# остаётся, когда она ПРИ ДЕЛЕ: процент («5 %»), счётное слово или единица («3 слоя»,
# «7 злаков», «2 м», «10 шт»), номер с «№» («Крем №1») и «N в 1». Обрезаем ДО снятия
# пунктуации — иначе «%», «x» и «№» уже потеряны, и отличить процент от мусора нечем.
_BARE_NUMBER = re.compile(r"(?<![\w.,])(\d)(?![\w.,])")
# Что стоит сразу после числа, если число при деле: процент, единица, счётное слово.
_NUMBER_WITH = re.compile(
    r"\s*(?:%|шт|штук|уп|упак|пач|пакет|пакетик|капсул|саше|стик|таблет|злак|месяц"
    r"|мес\b|дн\b|порци|кусоч|ломтик|сло|лист|рулон|секци|предмет|доз|категор|сорт"
    r"|см|мм|км|м\b|вт|гц|ма[хч])")
# Число рядом с числом («Pampers 4 46 шт»): размер и количество — оба нужны.
_NUMBER_NEAR = re.compile(r"\s*[\d.,]")
# «3 в 1»: цифра перед формулой «в 1» — часть названия, а не мусор.
_IN_ONE_AFTER = re.compile(r"\s*(?:в|in|v)\s*-?\s*\d")
# Номер по счёту: «Крем №1», «Сорт N 2» — цифра при знаке номера.
_NUMBER_BEFORE = re.compile(r"(?:№|no|n)\s*\.?\s*$")


def _drop_bare_numbers(text: str) -> str:
    """Убрать из нормализованного имени одиночные цифры, которые ничего не уточняют.

    Оставляет проценты, цифры при единицах и счётных словах, номера с «№» и «N в 1»;
    вырезает одиночную цифру вроде «Желтое манго 1». Режется только самостоятельная
    цифра: «60W», «с1», «4x», «366» — одно слово с буквой или многозначное число,
    их не трогаем.
    """
    def decide(match: re.Match) -> str:
        tail, head = text[match.end():], text[:match.start()]
        if (_NUMBER_WITH.match(tail) or _NUMBER_NEAR.match(tail)
                or _IN_ONE_AFTER.match(tail) or _NUMBER_BEFORE.search(head)):
            return match.group(0)
        return " "

    return _BARE_NUMBER.sub(decide, text)


def _num(value: float) -> str:
    """200.0 -> '200', 0.5 -> '0.5'"""
    return str(int(value)) if float(value).is_integer() else str(round(float(value), 3))


# Длины штрихкодов GS1: EAN-8, UPC-A, EAN-13, GTIN-14. Все приводятся к 14 цифрам.
_GTIN_LENGTHS = (8, 12, 13, 14)


def gtin(raw: str | int | None) -> str | None:
    """Один ключ на один штрихкод, в каком бы виде его ни написали.

    ЗАЧЕМ (17.10.2026). Чек ФНС отдаёт код товара как GTIN-14 с ведущим нулём
    («04607084350020»), METRO и Лента — как EAN-13 («4607084350020»), а кто-то
    пишет его с пробелами. Строки сравнивались буквально, и один и тот же товар
    расходился: штрихкод, самый сильный ключ единой базы, молчал.

    Оставляем только цифры и дополняем нулями слева до 14 — так поступает сам
    стандарт GS1. Контрольная цифра НЕ проверяется: коды в тестах и часть живых
    кодов сетей её не проходят, а отбросить настоящий код дороже, чем принять
    странный. Длина не из GS1 — не штрихкод, None.
    """
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) not in _GTIN_LENGTHS or not digits.strip("0"):
        return None
    return digits.zfill(14)


def gtin_trusted(code: str | None) -> bool:
    """Годится ли код в ключ, которому верят без слов: «это тот же товар».

    Критика проекта 17.10.2026: в чеках и каталогах встречаются обрывки кодов
    маркировки и внутренние коды магазинов (весовые этикетки, своя выпечка). Такой
    код у каждой сети свой, и совпадение в двух сетях ничего не значит. Настоящий
    GTIN контрольную цифру проходит всегда, а внутренние коды узнаются по префиксу
    GS1: 02–05 и 2 у EAN-13 (то же у UPC в виде EAN-13), 98–99 (купоны, возвраты),
    0 и 2 у EAN-8. Код, не прошедший проверку, остаётся ключом сравнения строк как
    раньше, но в единой базе ключом поиска не служит.
    """
    if not code or len(code) != 14 or not code.isdigit():
        return False
    body = [int(c) for c in code[:13]]
    total = sum(d * (3 if (12 - i) % 2 == 0 else 1) for i, d in enumerate(body))
    if (10 - total % 10) % 10 != int(code[13]):
        return False
    if code.startswith("000000"):              # EAN-8
        return code[6] not in "02"
    if code[0] == "0":                          # EAN-13 и UPC-A в виде EAN-13
        ean = code[1:]
        return not (ean[0] == "2" or ean[:2] in ("02", "04", "05", "98", "99"))
    return True                                 # GTIN-14 с индикатором упаковки


def parse_weight(raw: str) -> tuple[float | None, str]:
    """Граммовка и единица товара.

    '200г' -> (200.0, 'pcs'); 'Молоко 1л' -> (1000.0, 'pcs');
    'Слива' (нет граммовки в названии) -> (None, 'kg') — весовой товар.
    """
    if not raw:
        return None, "kg"
    matches = _WEIGHT_RE.findall(raw.lower().replace("ё", "е"))
    if not matches:
        return None, "kg"
    value, unit = matches[-1]
    grams = float(value.replace(",", ".")) * _UNIT_TO_G.get(unit, 1.0)
    return round(grams, 3), "pcs"


# Слова, которыми сеть или чек помечают товар на развес. «Кг» без числа перед ним
# («Бананы, кг») — тоже метка; «1 кг» — это фасовка, а не развес, и она убирается
# из строки до поиска меток.
_WEIGHED_RE = re.compile(r"\bвесов\w*|\bразвес\w*|\bна\s+вес\b|\bвес\b|(?<![а-яa-z])кг(?![а-яa-z])")


def sold_by_weight(raw: str) -> bool:
    """Сказано ли в названии, что товар продаётся на вес.

    Не путать с parse_weight(...)[1]: тот отвечает «kg» на ЛЮБОЕ название без
    граммовки, и «Хлеб Бородинский нарезка» или «Яйцо С1 10шт» становились
    весовыми — расчёт брал им цену за килограмм, а наряд клал три буханки вместо
    одной (ревизия 26.09.2026). Здесь нужен признак, а не отсутствие граммовки.
    """
    s = (raw or "").lower().replace("ё", "е")
    s = _WEIGHT_RE.sub(" ", s)
    return bool(_WEIGHED_RE.search(s))


def unit_from_name(raw: str) -> str | None:
    """Единица товара по названию: «kg» — сказано «на вес», «pcs» — названа фасовка,
    None — название ничего не говорит (и выдумывать не надо)."""
    if sold_by_weight(raw):
        return "kg"
    grams, _ = parse_weight(raw)
    return "pcs" if grams else None


def normalize_name(raw: str) -> str:
    """Сырое название -> нормализованная строка для сравнения.

    'ВК/ПОЛ.Сыр СТРАЧАТЕЛЛА мяг.200г' -> 'страчателла 200 г'

    Служебные слова и одиночные цифры выбрасываются (см. SERVICE_WORDS и
    _drop_bare_numbers): «Пиво из бочки» и «Пиво бочки», «Желтое манго 1» и
    «Манго желтое» дают один и тот же ключ, а проценты, единицы и фасовка остаются.
    """
    if not raw:
        return ""
    s = raw.lower().replace("ё", "е").strip()
    s = _PREFIX_RE.sub(" ", s)

    grams, unit = parse_weight(s)
    # Убираем все токены граммовки из тела названия — вернём один в конец.
    s = _WEIGHT_RE.sub(" ", s)
    # Одиночные цифры — до снятия пунктуации: «%» и «x» ещё нужны, чтобы понять,
    # при деле ли число.
    s = _drop_bare_numbers(s)

    s = _PUNCT_RE.sub(" ", s)
    s = _JUNK_RE.sub(" ", s)

    tokens: list[str] = []
    for tok in s.split():
        if tok in NOISE_WORDS or tok in SERVICE_WORDS:
            continue
        if len(tok) < 2 and tok not in _KEEP_TOKENS and not tok.isdigit():
            continue
        tokens.append(tok)

    if grams is not None and unit == "pcs":
        # Показываем компактную форму: до 1000 г — в граммах, дальше — в килограммах.
        if grams >= 1000:
            tokens += [_num(grams / 1000), "кг"]
        else:
            tokens += [_num(grams), "г"]
    return " ".join(tokens).strip()


def display_name(raw: str) -> str:
    """Нормализованное название с заглавной буквы — для карточки эталона."""
    n = normalize_name(raw)
    return n[:1].upper() + n[1:] if n else raw.strip()


# --- название для человека ---------------------------------------------------
# display_name — ключ поиска: он теряет «%» и запятую («2,5%» -> «2 5»), слова «сыр» и
# «пакет», а строка чека Пятёрочки к тому же сокращена («САРАФ.Мор.ЗЕФ.эск.ван.гл.8% 75г»).
# Человеку это не показывают (31.10.2026, владелец: «нужно человеческое название»).
# human_name раскрывает самые частые ОДНОЗНАЧНЫЕ сокращения; остальное остаётся как в чеке:
# «мол» — молоко, молочный и молотый, «б/к» — без косточек и белокочанная, и гадать нельзя.
# Марку не раскрываем никогда: чья она, чек не говорит.
_LETTERS = "A-Za-zА-Яа-яЁё"
_ABBR_DOT = re.compile(rf"[{_LETTERS}]\.[{_LETTERS}/]")
_NOT_A_NAME = re.compile(r"\.(?:ru|ру|com|net|org)\b|www\.|https?:|№\s*\d{5,}", re.I)
_ODD_CHAR = re.compile(rf"[^{_LETTERS}0-9 .,%/\-№]")
_GLUED_UNIT = re.compile(r"(?<=\d)(кг|гр|мл|шт|уп|см|мм|г|л)(?![а-яa-z])")
# «С1» (категория яиц) и «№187» — одно слово, а не буква и число врозь.
_PIECE = re.compile(rf"(№?\d+(?:[.,]\d+)?|[{_LETTERS}]\d(?![\d.,]))"
                    rf"|([{_LETTERS}]+(?:[/\-][{_LETTERS}]+)*)(\.?)|(%)")
_RECEIPT_WIDTH = 40      # строка чека Пятёрочки обрезана по 40 знаков; длиннее — название сети
_UNITS = {"г", "гр", "кг", "мл", "л", "шт", "уп", "см", "мм"}
# Первое слово названия — существительное, и от его рода зависят прилагательные дальше.
_HEAD = {"мол": ("молоко", "n"), "мор": ("мороженое", "n"), "кеф": ("кефир", "m"),
         "йог": ("йогурт", "m"), "биойог": ("биойогурт", "m"), "печ": ("печенье", "n"),
         "твор": ("творог", "m"), "колб": ("колбаса", "f"), "изд": ("изделия", "p"),
         "прод": ("продукт", "m"), "нап": ("напиток", "m"), "з/п": ("зубная паста", "f")}
# Существительные и вкусы: рода не требуют.
_NOUN = {"эск": "эскимо", "зеф": "зефир", "бан": "банан", "зем": "земляника",
         "клуб": "клубника", "арб": "арбуз", "дын": "дыня", "кок": "кокос"}
# Прилагательные: основа; окончание подбирается по роду первого слова.
_ADJ = {"гл": "глазированн", "ван": "ванильн", "твор": "творожн", "паст": "пастеризованн",
        "ул": "ультрапастеризованн", "ул/паст": "ультрапастеризованн", "дет": "детск",
        "вар": "варён", "жар": "жарен", "сол": "солён", "охл": "охлаждённ", "зам": "замороженн",
        "бел": "бел", "туал": "туалетн", "кур": "курин", "к/м": "кисломолочн",
        "н/г": "негазированн", "мак": "макаронн", "струч": "стручков", "класс": "классическ",
        "клас": "классическ"}
_ENDING = {"m": "ый", "f": "ая", "n": "ое", "p": "ые"}
_ENDING_SOFT = {"m": "ий", "f": "ая", "n": "ое", "p": "ие"}     # детский, классический
# После «с» слово стоит в творительном падеже, прилагательное из словаря тут не годится.
_WITH = {"гл": "глазурью", "ван": "ванилью", "зелен": "зеленью"}
_AFTER_NUMBER = {"сл": "слоя", "рул": "рулонов"}
_PREPOSITIONS = {"с", "со", "из", "без", "для", "на", "в", "по"}


def _gender(word: str) -> str:
    """Род слова по окончанию: колбаса — f, молоко — n, сосиски — p, сыр — m."""
    last = word[-1:]
    if word.endswith("ель"):                                     # шницель, кисель
        return "m"
    return "f" if last in "аяь" else "n" if last in "ое" else "p" if last in "ыи" else "m"


def _expand(low: str, gender: str | None) -> str | None:
    """Раскрытие одного сокращения или None — слово остаётся как в чеке."""
    if low in _NOUN:
        return _NOUN[low]
    if low in _ADJ:
        stem = _ADJ[low]
        return stem + (_ENDING_SOFT if stem.endswith("ск") else _ENDING)[gender or "m"]
    parts = low.split("/")
    if len(parts) > 1 and all(p in _NOUN for p in parts):        # «бан/зем»
        return "/".join(_NOUN[p] for p in parts)
    return None


def _brand(word: str) -> str:
    """Марка прописными в начале строки: «САРАФ» -> «Сараф», инициалы («ЧЧ», «М») как есть."""
    return word if len(word) <= 2 else word.title()


def human_name(raw: str | None) -> str:
    """Строка чека — в виде, который читает человек.

    'САРАФ.Мор.ЗЕФ.эск.ван.гл.8% 75г' -> 'Сараф. Мороженое зефир эскимо ванильное
    глазированное 8% 75 г'. Названия, которые и так читаются («Молоко Домик в деревне
    2,5% 930 мл»), возвращаются как есть, меняется только то, что чек сократил.
    """
    s = " ".join((raw or "").split())
    if not s or _NOT_A_NAME.search(s):
        return s
    s = _GLUED_UNIT.sub(r" \1", s)
    if s.isupper():
        return s.capitalize()
    # Марка прописными в начале: «ТЁМА Биойог…», «С.ЗЕЛ.Мол…», «Д.В Д.Мол…».
    caps_start = re.match(r"[A-ZА-ЯЁ]{2,}[. \-]|[A-ZА-ЯЁ]\.[A-ZА-ЯЁ]", s)
    dots = len(_ABBR_DOT.findall(s))
    if (_ODD_CHAR.search(s) or len(s) > _RECEIPT_WIDTH
            or not (dots >= 2 or caps_start and (dots or re.search(r"\d", s)))):
        return s
    words: list[str] = []
    brand, glue = bool(caps_start), False     # марка: прописные слова в начале; glue — «С.ЗЕЛ.»
    gender = start = None                     # род и место первого слова после марки
    skip = frozen = with_ = False             # после предлога слово не трогаем: падеж неизвестен
    for number, word, dot, percent in _PIECE.findall(s):
        if percent:
            words[-1:] = [words[-1] + "%"] if words else []
            continue
        if number:
            words.append(number.replace(".", ","))
            brand = False
            continue
        low = word.lower()
        if brand and word.isupper():
            text = _brand(word) + dot            # точка сокращения остаётся: марку не раскрываем
            if glue:
                words[-1] += text
            else:
                words.append(text)
            glue = bool(dot)
            continue
        brand = glue = False
        if start is None:
            start = len(words)
        if words and words[-1][-1:].isdigit() and low in _AFTER_NUMBER:
            text = _AFTER_NUMBER[low]
        elif low in _UNITS:
            text = low
        elif low.startswith("д/") and len(low) > 2:
            text, frozen = "для " + low[2:], True    # дальше «для чего»: родительный падеж
        elif low in _HEAD and gender is None:
            text, gender = _HEAD[low]
        else:
            text = _WITH.get(low) if with_ else None
            if text is None and not (skip or frozen):
                text = _expand(low, gender)
            if text is None:                     # неизвестное слово не угадываем
                text = word.title() if word.isascii() and len(word) > 3 else (
                    word if word.isascii() else word.lower())
                text += "." if dot and len(word) <= 5 and low not in _PREPOSITIONS else ""
            if gender is None and low not in _PREPOSITIONS:
                gender = _gender(text.rstrip("."))
        skip = low in _PREPOSITIONS
        with_ = low in ("с", "со")
        words.append(text)
    if start is not None and start < len(words):
        words[start] = words[start][:1].upper() + words[start][1:]
    return " ".join(words)


def is_derived(name: str | None, raw: str | None) -> bool:
    """Имя товара выведено из этой строки чека, а не дано человеком.

    Сравниваются слова без чисел: эталон потерял «%» и запятую, и заново нормализованный
    он теряет ещё и число («8 75 г» -> «75 г»), а правила нормализации с тех пор менялись
    (слово «об» эталона 19.10 уже не слово). Другая фасовка — другой товар.
    """
    if not raw:
        return False
    words = lambda text: [t for t in normalize_name(text or "").split() if not t.isdigit()]
    weights = (parse_weight(name or "")[0], parse_weight(raw)[0])
    return bool(words(raw)) and words(name) == words(raw) and (
        None in weights or weights[0] == weights[1])


def shown_name(name: str | None, raw: str | None) -> str:
    """Название товара для экрана: строка чека по-человечески, если имя выведено из неё.

    Эталон, который завёл импорт из строки чека, — ключ поиска, а не название. Имя,
    которое дал человек или принёс каталог, и товар без строк чека остаются как есть.
    """
    return human_name(raw) if raw and (not name or is_derived(name, raw)) else name or ""


# --- кириллица и латиница как одно и то же имя -----------------------------

# Одна и та же марка в каталогах пишется по-разному: «Rexona» на ценнике и
# «Рексона» в чеке, «Домик в деревне» и «Domik v derevne». Для сравнения строк
# это разные слова без единой общей буквы, и похожесть честно даёт ноль.
# Приводим кириллицу к латинице — направление выбрано потому, что оно
# однозначное: обратное («x» это «кс» или «икс»?) породило бы догадки.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y",
    "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
# Сочетания, которые латиница пишет одной буквой: «кс» -> «x» сближает
# «рексона» с «rexona», «джи» -> «g» — «джипопо» с «gipopo».
_DIGRAPHS = (("кс", "x"), ("дж", "g"))

BRAND_MATCH = 0.72       # с какой похожести считаем, что это одна и та же марка


def translit(text: str) -> str:
    """«Рексона» -> «rexona», «Простоквашино» -> «prostokvashino»."""
    s = (text or "").lower().replace("ё", "е")
    for pair, latin in _DIGRAPHS:
        s = s.replace(pair, latin)
    return "".join(_TRANSLIT.get(ch, ch) for ch in s)


def same_word(a: str, b: str, threshold: float = BRAND_MATCH) -> bool:
    """Одно ли это слово с точностью до написания: «рексона» и «rexona» — да.

    Точного равенства мало: транслитерация никогда не совпадёт с фирменным
    написанием буква в букву («reksona» против «rexona»), да и таблиц перевода
    у каждого каталога своя.
    """
    x, y = translit(a), translit(b)
    if not x or not y:
        return False
    if x == y or x in y or y in x:
        return True
    return SequenceMatcher(None, x, y).ratio() >= threshold


def _pair_score(na: str, nb: str) -> float:
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    return max(ratio, 0.5 * ratio + 0.5 * jaccard)


def similarity(a: str, b: str) -> float:
    """Похожесть двух названий, 0..1.

    Считаем дважды: как есть и в транслитерации. Второй проход нужен потому, что
    одна и та же марка в каталогах пишется то кириллицей, то латиницей — «Рексона»
    в чеке и «Rexona» на ценнике. Без него у этих слов нет ни одной общей буквы и
    похожесть честно равна нулю, хотя товар один и тот же.

    Берём лучшее из двух: транслитерация может только помочь узнать товар, но
    никогда не должна мешать — иначе она начнёт сближать случайные слова.
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    score = _pair_score(na, nb)
    ta, tb = translit(na), translit(nb)
    if (ta, tb) != (na, nb):
        score = max(score, _pair_score(ta, tb))
    return round(score, 4)


def weight_ok(product_weight_g, candidate_weight_g, tolerance_pct: float = 20) -> bool:
    """Проходит ли граммовка кандидата допуск ±tolerance_pct (раздел 4 спецификации)."""
    if not product_weight_g or not candidate_weight_g:
        return True                      # весовой товар или граммовка неизвестна
    base = float(product_weight_g)
    if base <= 0:
        return True
    return abs(float(candidate_weight_g) - base) / base <= float(tolerance_pct) / 100.0
