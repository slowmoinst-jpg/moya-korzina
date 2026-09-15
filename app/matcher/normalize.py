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


def _num(value: float) -> str:
    """200.0 -> '200', 0.5 -> '0.5'"""
    return str(int(value)) if float(value).is_integer() else str(round(float(value), 3))


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


def normalize_name(raw: str) -> str:
    """Сырое название -> нормализованная строка для сравнения.

    'ВК/ПОЛ.Сыр СТРАЧАТЕЛЛА мяг.200г' -> 'страчателла 200 г'
    """
    if not raw:
        return ""
    s = raw.lower().replace("ё", "е").strip()
    s = _PREFIX_RE.sub(" ", s)

    grams, unit = parse_weight(s)
    # Убираем все токены граммовки из тела названия — вернём один в конец.
    s = _WEIGHT_RE.sub(" ", s)

    s = _PUNCT_RE.sub(" ", s)
    s = _JUNK_RE.sub(" ", s)

    tokens: list[str] = []
    for tok in s.split():
        if tok in NOISE_WORDS:
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
