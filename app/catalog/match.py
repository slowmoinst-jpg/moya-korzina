"""Сопоставление товаров разных сетей между собой: из строк каталогов — единые товары.

На чём стоит. На тех же правилах, что уже спасли живой подбор цен (app/compare.py,
решения 15.09.2026): ФАСОВКА — жёсткое условие («Страчателла 200 г» и «страчателла
92 г» — разные товары, как бы ни совпадали слова), и НАПРАВЛЕНИЕ ВХОЖДЕНИЯ — кандидат
шире запроса это другой товар («Персики» ≠ «Персики консервированные»). Штрихкод —
самый сильный ключ, но его отдают не все сети (Лента и Магнит не отдают), поэтому он
идёт первым там, где есть, и не мешает остальным.

Как решается пара товаров a и b (same_product):
  1. оба со штрихкодом — решает штрихкод;
  2. единицы спорят (штучный против весового), фасовки спорят, марки спорят — разные;
  3. смысловые слова названий равны (марка из поля добавлена к словам, потому что одна
     сеть пишет её в названии, другая — отдельным полем) — один товар;
  4. слова одной стороны ВХОДЯТ в слова другой («Молоко Простоквашино 930 мл» внутри
     «Молоко пастеризованное ПРОСТОКВАШИНО без змж 930 мл») — один товар, только если
     обе фасовки известны и сходятся И среди общих слов есть марка. Без марки короткое
     название — это более широкий товар, а не тот же самый: «Персики» против «Персики
     консервированные 500 г» спотыкается и об это, и об единицу (весовой против штучного).
  Марка узнаётся по полю brand, по словарю марок всего каталога (поля brand других
  сетей) и по СЛОВАМ ЗАГЛАВНЫМИ в названии — так пишет марки Лента.

Кандидаты ищутся не полным перебором: товары группируются по первым двум смысловым
словам названия, сравниваются только внутри групп.

Чего здесь нет намеренно: похожести по буквам (SequenceMatcher). На каталогах в
десятки тысяч строк она склеивает «молоко 2,5 %» с «молоко 3,2 %» — цифры для неё
шум, а для покупателя это разные товары.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

from app import config
from app.matcher.normalize import normalize_name, parse_weight

UNITS = {"г", "гр", "кг", "мл", "л", "шт", "мг", "уп", "пач", "бут"}
_NUMERIC = re.compile(r"^\d+([.,]\d+)?$")
_CAPS = re.compile(r"\b[А-ЯЁA-Z]{3,}\b")

# --- ПРОЦЕНТ И РАЗНОВИДНОСТЬ: чем товары отличаются, когда слова одинаковы -----
#
# ЗАЧЕМ. Замер 20.09.2026 на живом каталоге: 188 групп из 7 941 (2,4 %) смешали
# РАЗНЫЕ проценты — «Сметана Большая кружка 15 %» и она же 20 %, майонез 67 % и
# 55 %, пудинг 6 % и 5,2 %. Причина в том, что meaningful() выбрасывает числа как
# шум, и у обеих сметан остаётся один и тот же набор слов. Для покупателя это
# разные товары и разные деньги, а расчёт складывал их в один.
#
# Процент ведёт себя как фасовка: обе стороны молчат или молчит одна — не улика
# (weights_agree уже написан по этому правилу), обе назвали и назвали разное —
# разные товары.
_PCT = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s*%")

# Слова, которые ДЕЛАЮТ товар другим товаром, даже когда всё остальное совпало.
# «Спагетти Barilla 450 г» и «Спагетти Barilla без глютена 400 г» — разные покупки,
# и человек, купивший не то, узнает об этом дома. Поэтому такая метка на одной
# стороне и её отсутствие на другой — веский довод против, а не мелочь.
#
# Список нарочно короткий и из наблюдений: сюда идёт только то, что встретилось
# живым каталогом (замер 20.09.2026 — Curaprox Zero, спагетти без глютена).
# Добавлять сюда «вкусовые» слова нельзя: «малиновый» и «клубничный» различаются
# сами по себе, а вот отсутствие слова метки — это утверждение.
VARIANT_MARKS = {
    "безглютена", "глютенфри", "glutenfree", "безлактозы", "безлактозный",
    "безсахара", "zero", "зеро", "light", "лайт", "легкий",
    "обезжиренный", "обезжиренное", "цельнозерновой", "цельнозерновые",
    "органический", "organic",
}
# ЧЕГО ЗДЕСЬ НЕТ НАРОЧНО, И ЭТО ВАЖНЕЕ ТОГО, ЧТО ЕСТЬ.
#
# «без ЗМЖ» — метка на половине молочного отдела: живой каталог 20.09.2026 полон
# строк вида «jjogurt pitevojj irbitskijj pechenaya grusha 25 bez zmzh 500g». Это
# не разновидность товара, а обязательная надпись, и одна сеть её пишет, другая
# нет. Взяв её меткой, мы развели бы по разным товарам один и тот же йогурт.
#
# «детский» — по той же причине, но с другой стороны: сети пишут его вразнобой,
# «Творожок детский» против «tvorog dlya detejj». Метка на одной стороне и её
# отсутствие на другой означали бы «разные товары» там, где различается только
# оборот речи.
# «без глютена» приходит двумя словами — склеиваем пары «без X» в одно слово,
# иначе «без» потеряется как короткое, а «глютена» останется само по себе и
# встретится с обычным товаром, где его просто нет. Латиница Перекрёстка пишет то
# же самое как «bez sahara», поэтому оба написания склеиваются одинаково.
_WITHOUT = re.compile(r"\b(?:без|bez)\s+(\w+)", re.I)


def parse_percent(name: str | None) -> float | None:
    """Процент из названия: жирность, крепость. «Сметана 15 %» -> 15.0."""
    found = _PCT.search(name or "")
    if not found:
        return None
    try:
        return float(found.group(1).replace(",", "."))
    except ValueError:
        return None


def percents_agree(a: float | None, b: float | None) -> bool:
    """Проценты не спорят: обе неизвестны или одна неизвестна — молчание не улика."""
    if a is None or b is None:
        return True
    return abs(a - b) < 0.05


@lru_cache(maxsize=1)
def _marks_folded() -> frozenset[str]:
    """Метки в общем своде письма — считаются один раз на весь обход."""
    return frozenset(fold(w) for w in VARIANT_MARKS)


def variant_marks(name: str | None) -> frozenset[str]:
    """Метки разновидности: «без глютена», «zero», «обезжиренный», «детская».

    Считаются ПО СВОДУ ПИСЬМА, иначе метка сама бы и ломала межписьменные пары:
    «Каша детская Gipopo» имела бы метку, а kasa detskaa gipopo — нет, и вместо
    защиты от переклейки вышла бы новая дыра в сопоставлении.
    """
    text = (name or "").lower()
    glued = _WITHOUT.sub(lambda m: "без" + m.group(1), text)
    known = _marks_folded()
    out = {fold(w.strip(",.;:()«»\"'")) for w in glued.split()}
    return frozenset(w for w in out if w in known)

# --- ОБЩИЙ СВОД ПИСЬМА: кириллица и чужая латиница как одно слово ------------
#
# ЗАЧЕМ. Перекрёсток и Впрок отдают названия ЛАТИНИЦЕЙ — они берутся прямо из
# адреса карточки: /p/maslo-slivocnoe-icalki-krestanskoe-72-5-500g. Остальные сети
# пишут кириллицей. Для сравнения множеств слов это разные слова без единой общей
# буквы, и замер 20.09.2026 показал цену этого: пару в другой сети нашли 0,2 %
# строк Перекрёстка и 0,6 % Впрока против 6–21 % у сетей с кириллицей. 186 тысяч
# строк, две трети каталога, лежали отдельным островом.
#
# ПОЧЕМУ НЕ ГОТОВАЯ translit ИЗ app/matcher/normalize.py. Она переводит «ч» в «ch»,
# «ш» в «sh», «ж» в «zh» — так пишут в паспортах. У Перекрёстка схема ДРУГАЯ, без
# второй буквы: «сливочное» у него slivocnoe, «Ичалки» — icalki, «крестьянское» —
# krestanskoe. Наша дала бы slivochnoe, ichalki, krestyanskoe — и не совпало бы
# ничего.
#
# ЧТО ДЕЛАЕМ. Сводим ОБЕ стороны к общей краткой форме: и кириллицу, и уже готовую
# латиницу. Диграфы схлопываются в одну букву (ch -> c, sh -> s, zh -> z), парные
# гласные — в одну (yu -> u, ya -> a), «y» приравнивается к «i». Тогда «сливочное»
# и slivocnoe сходятся в slivocnoe, «масло» и maslo — в maslo.
#
# НАПРАВЛЕНИЕ ВЫБРАНО ОДНО: кириллица -> латиница. Обратное породило бы догадки
# («c» — это «с» или «ц»?), а это направление однозначно.
#
# СВОД ОГРУБЛЯЕТ, И ЭТО ОСОЗНАННО: «ч» и «ц» сходятся в «c», «ш» и «щ» — в «s».
# Поэтому он НЕ заменяет строгое сравнение, а идёт вторым заходом, когда строгое
# не нашло совпадения, и под теми же охранами — единица, фасовка, марка.
_FOLD_LETTERS = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "z", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "c", "ш": "s", "щ": "s", "ъ": "",
    "ы": "i", "ь": "", "э": "e", "ю": "u", "я": "a",
}
# Порядок важен: «sch» должно схлопнуться до того, как за него возьмётся «ch».
_FOLD_DIGRAPHS = (("shch", "s"), ("sch", "s"), ("ch", "c"), ("sh", "s"),
                  ("zh", "z"), ("kh", "h"), ("ts", "c"), ("yu", "u"),
                  ("ya", "a"), ("ye", "e"), ("yo", "e"), ("x", "ks"))


def fold(text: str | None) -> str:
    """Общая краткая форма слова: «сливочное» и «slivocnoe» -> «slivocnoe»."""
    s = (text or "").lower()
    s = "".join(_FOLD_LETTERS.get(ch, ch) for ch in s)
    for pair, one in _FOLD_DIGRAPHS:
        s = s.replace(pair, one)
    # «y» осталась только там, где пришла латиницей: приравниваем её к «i», иначе
    # «krestyanskoe» и «krestanskoe» разойдутся на одну букву.
    return s.replace("y", "i")


def fold_words(words) -> frozenset[str]:
    """Свод набора слов. Пустые после свода отбрасываем: они ничего не различают."""
    return frozenset(w for w in (fold(x) for x in words) if len(w) >= 2)


@lru_cache(maxsize=8)
def _folded_vocab(vocab: frozenset[str]) -> frozenset[str]:
    """Свод словаря марок — один раз на обход, а не на каждой паре.

    Словарь марок всего каталога — это тысячи слов, а сравнений сотни миллионов.
    Сводить его внутри same_product значило бы проделать одну и ту же работу
    заново на каждой паре и растянуть сопоставление с двух минут на часы.
    """
    return fold_words(vocab)


@dataclass
class Row:
    """Товар сети, подготовленный к сравнению."""
    id: int
    chain: str
    name: str
    words: frozenset[str]        # смысловые слова названия плюс марка
    order: tuple[str, ...]       # те же слова в порядке названия — для группировки
    brand_words: frozenset[str]  # что мы считаем маркой у этого товара
    brand: str | None            # марка полем сети, как она написана
    weight_g: float | None
    unit: str | None
    barcode: str | None
    category: str | None
    # Те же три набора в общем своде письма (fold): по ним кириллица одной сети
    # встречается с латиницей другой. Считаются один раз здесь, а не при каждом
    # сравнении: свод зовётся на каждой паре, а пар — сотни миллионов.
    fwords: frozenset[str] = frozenset()
    forder: tuple[str, ...] = ()
    fbrand: frozenset[str] = frozenset()
    pct: float | None = None       # жирность, крепость: «Сметана 15 %» -> 15.0
    marks: frozenset[str] = frozenset()   # «без глютена», «zero», «детская»


def meaningful(text: str | None) -> frozenset[str]:
    """Смысловые слова: без чисел, единиц измерения и однобуквенного мусора."""
    return frozenset(ordered_words(text))


def ordered_words(text: str | None) -> list[str]:
    out: list[str] = []
    for w in normalize_name(text or "").split():
        if _NUMERIC.match(w) or w in UNITS or w[:1].isdigit() or len(w) < 2 or w in out:
            continue
        out.append(w)
    return out


def weights_agree(a: float | None, b: float | None) -> bool:
    """Фасовки не спорят: обе неизвестны или одна неизвестна — молчание не улика."""
    if not a or not b:
        return True
    tolerance = float(config.get("weight_tolerance_pct", 20) or 20) / 100.0
    return abs(a - b) / max(a, b) <= tolerance


def prepare(product: dict) -> Row:
    name = product.get("name") or ""
    weight = product.get("weight_g")
    if not weight:
        weight, _ = parse_weight(name)
    brand = product.get("brand") or None
    caps = {w for w in meaningful(" ".join(_CAPS.findall(name)))}
    brand_words = meaningful(brand) | caps
    order = tuple(ordered_words(name))
    words = frozenset(order) | meaningful(brand)
    return Row(id=int(product["id"]), chain=product["chain"], name=name,
               words=words, order=order, brand_words=brand_words,
               fwords=fold_words(words),
               forder=tuple(dict.fromkeys(w for w in (fold(x) for x in order) if len(w) >= 2)),
               fbrand=fold_words(brand_words),
               pct=parse_percent(name), marks=variant_marks(name),
               brand=brand, weight_g=weight or None, unit=product.get("unit") or None,
               barcode=(str(product["barcode"]).strip() or None) if product.get("barcode") else None,
               category=product.get("category"))


def same_product(a: Row, b: Row, brand_vocab: frozenset[str] = frozenset()
                 ) -> tuple[bool, str, float]:
    """Один ли это товар. Возвращает (да/нет, способ, уверенность)."""
    if a.barcode and b.barcode:
        return (a.barcode == b.barcode), "barcode", 1.0
    if not a.words or not b.words:
        return False, "", 0.0
    if a.unit and b.unit and a.unit != b.unit:
        return False, "", 0.0
    if not weights_agree(a.weight_g, b.weight_g):
        return False, "", 0.0
    # Процент — такая же жёсткая улика, как фасовка. Замер 20.09.2026: без него
    # «Сметана Большая кружка 15 %» и она же 20 % склеивались в один товар, потому
    # что числа выброшены как шум и наборы слов у них одинаковые.
    if not percents_agree(a.pct, b.pct):
        return False, "", 0.0
    # Метка разновидности на одной стороне и её отсутствие на другой — довод
    # против. «Спагетти Barilla 450 г» и «Спагетти Barilla без глютена 400 г» —
    # разные покупки, и человек узнаёт об этом уже дома.
    if a.marks != b.marks:
        return False, "", 0.0
    # Спор марок проверяется ПО СВОДУ, иначе он сам же и рубит межписьменные пары:
    # «Простоквашино» и prostokvasino общих слов не имеют, и до сравнения названий
    # дело бы не дошло — пара отсеялась бы как «марки спорят».
    if a.fbrand and b.fbrand and not (a.fbrand & b.fbrand):
        return False, "", 0.0
    if a.words == b.words:
        return True, "words", 0.95 if (a.weight_g and b.weight_g) else 0.8
    small, big = (a.words, b.words) if len(a.words) <= len(b.words) else (b.words, a.words)
    if small < big and a.weight_g and b.weight_g:
        marks = brand_vocab | a.brand_words | b.brand_words
        if small & marks:
            return True, "words", 0.85

    # Второй заход — по своду письма, и только если строгое сравнение не нашло
    # совпадения. Свод огрубляет («ч» и «ц» в нём одна буква), поэтому уверенность
    # ниже, а охраны — те же самые, они уже отработали выше.
    if a.fwords and b.fwords:
        if a.fwords == b.fwords:
            return True, "translit", 0.9 if (a.weight_g and b.weight_g) else 0.75
        fsmall, fbig = ((a.fwords, b.fwords) if len(a.fwords) <= len(b.fwords)
                        else (b.fwords, a.fwords))
        if fsmall < fbig and a.weight_g and b.weight_g:
            fmarks = _folded_vocab(brand_vocab) | a.fbrand | b.fbrand
            if fsmall & fmarks:
                return True, "translit", 0.8
    return False, "", 0.0


class _Union:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def brand_vocabulary(rows: list[Row]) -> frozenset[str]:
    """Марки, известные хоть одной сети полем brand или заглавными в названии."""
    out: set[str] = set()
    for r in rows:
        out |= r.brand_words
    return frozenset(out)


def cluster(products: list[dict]) -> list[list[tuple[Row, str, float]]]:
    """Разбить строки каталогов на группы «один товар».

    Кандидаты — товары с общим словом среди первых двух смысловых слов названия
    (обычно это существительное: «молоко», «сыр», «персики»); штрихкоды — отдельный
    индекс. Внутри группы перебор попарный, но группы на порядки меньше каталога.
    """
    rows = [prepare(p) for p in products]
    vocab = brand_vocabulary(rows)
    union = _Union()
    how: dict[int, tuple[str, float]] = {}

    by_barcode: dict[str, list[Row]] = defaultdict(list)
    by_head: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if r.barcode:
            by_barcode[r.barcode].append(r)
        # Корзины — ПО СВОДУ ПИСЬМА, а не по самому слову. Иначе «молоко» и moloko
        # попадают в разные корзины и не встречаются никогда: перебор идёт только
        # внутри корзины, и второй заход по своду в same_product до них не дошёл бы.
        for head in r.forder[:2]:
            by_head[head].append(r)

    for group in by_barcode.values():
        for other in group[1:]:
            union.union(group[0].id, other.id)
            how[other.id] = ("barcode", 1.0)
            how.setdefault(group[0].id, ("barcode", 1.0))

    # ЗДЕСЬ БЫЛО МНОЖЕСТВО ВСЕХ СРАВНЁННЫХ ПАР, И ОНО УБИВАЛО СОПОСТАВЛЕНИЕ.
    #
    # Замер 20.09.2026, когда каталог дорос до 262 475 строк: головных слов 30 785,
    # а пар к сравнению 195 342 771 — на одно только множество пар нужно 10,9 ГБ при
    # 7,9 ГБ на сервере. Процесс убивали сигналом, в журнале оставалось одно слово
    # «Killed», и сопоставление не отработало ТРИ ОБХОДА ПОДРЯД: Впрок, Монетка и
    # Fix Price лежали в каталогах сетей, но в единые товары не попадали, а
    # catalog_items стоял на цифрах от 19.09. Снаружи это выглядело как «всё ok».
    #
    # Множество берегло время: пара, попавшая в две корзины сразу, сравнивалась бы
    # дважды. Замер показал, чего это стоило: одно сравнение — 1,2 мкс, все 195 млн
    # пар это ЧЕТЫРЕ МИНУТЫ. То есть память тратилась гигабайтами ради минут.
    #
    # Вместо него — проверка по самому объединению: если строки уже в одной группе,
    # сравнивать их незачем. Она не только бесплатна по памяти, но и отсекает больше
    # работы, чем отсекало множество: пара пропускается и тогда, когда её склеили
    # через третью строку. Потолок на размер корзины не понадобился — он резал бы
    # совпадения у «вино», «сыр», «напиток», где строк по шесть тысяч.
    for group in by_head.values():
        if len(group) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a.chain == b.chain and not (a.barcode and b.barcode):
                    continue   # два артикула одной сети с одним названием — не склеиваем
                if union.find(a.id) == union.find(b.id):
                    continue   # уже одна группа: ни сравнение, ни склейка ничего не изменят
                ok, method, score = same_product(a, b, vocab)
                if ok:
                    union.union(a.id, b.id)
                    how.setdefault(a.id, (method, score))
                    how.setdefault(b.id, (method, score))

    groups: dict[int, list[tuple[Row, str, float]]] = defaultdict(list)
    for r in rows:
        method, score = how.get(r.id, ("self", 1.0))
        groups[union.find(r.id)].append((r, method, score))

    out: list[list[tuple[Row, str, float]]] = []
    for group in groups.values():
        out.extend(_split_chained(group, vocab) if len(group) > 2 else [group])
    return out


def _split_chained(group: list[tuple[Row, str, float]], vocab: frozenset[str]
                   ) -> list[list[tuple[Row, str, float]]]:
    """Разбить группу так, чтобы каждый её член совпадал с эталоном НАПРЯМУЮ.

    ЗАЧЕМ. Объединение групп транзитивно, а «тот же товар» — НЕТ. Пара a-b похожа,
    пара b-c похожа, и обе склеиваются через b, хотя a и c — разные вещи. Замер
    20.09.2026 на живом каталоге поймал это в чистом виде: в одной группе оказались
    «Бульон куриный Роллтон Домашний 90 г», «Бульон Роллтон говяжий Домашний 90 г»
    и «Пюре Дары Кубани Яблоко банан клубника 90 г». Ни одна пара из этих трёх не
    прошла бы сравнение сама по себе — их свёл общий сосед.

    Поэтому после объединения группа пересобирается: берём самую подробную строку
    за эталон, оставляем при ней тех, кто совпадает СО ВСЕМИ уже оставленными, а
    остальные уходят собирать свою группу тем же правилом.

    СВЕРЯТЬ ТОЛЬКО С ЭТАЛОНОМ — МАЛО, и это выяснилось замером после первой же
    правки. Молчание уликой не считается: у строки без процента он неизвестен, и
    такая строка пропускает к себе и 15 %, и 20 %. В живом каталоге 20.09.2026
    так и вышло — «Сметана Домик в деревне 300 г» без процента держала при себе
    обе жирности, и 57 групп остались спорными. Проверка со всеми оставленными
    закрывает эту дыру: 15 % и 20 % не проходят друг мимо друга, кто бы ни был
    эталоном.

    Группы из двух строк сюда не попадают: там цепочки быть не может по определению.
    """
    rest = list(group)
    out: list[list[tuple[Row, str, float]]] = []
    while rest:
        seed = max(rest, key=lambda item: (len(item[0].words), len(item[0].name)))
        kept, dropped = [seed], []
        # Признаки группы копятся по мере набора: первая строка, которая НАЗВАЛА
        # процент, задаёт его всей группе. Так закрывается дыра молчащего эталона,
        # и при этом новичок сверяется с одним числом, а не со всеми, кто уже
        # внутри, — иначе честная группа из восьмидесяти одинаковых строк стоила
        # бы трёх тысяч сравнений вместо восьмидесяти.
        pct = seed[0].pct
        marks = seed[0].marks
        for item in rest:
            row = item[0]
            if item is seed:
                continue
            if pct is not None and row.pct is not None and not percents_agree(pct, row.pct):
                dropped.append(item)
                continue
            if marks and row.marks and marks != row.marks:
                dropped.append(item)
                continue
            if not same_product(seed[0], row, vocab)[0]:
                dropped.append(item)
                continue
            kept.append(item)
            if pct is None:
                pct = row.pct
            if not marks:
                marks = row.marks
        out.append(kept)
        rest = dropped
    return out


def canonical(group: list[tuple[Row, str, float]]) -> dict:
    """Имя и признаки единого товара: самое подробное название, фасовка, марка, штрихкод."""
    rows = [g[0] for g in group]
    best = max(rows, key=lambda r: (len(r.words), len(r.name)))
    weight = next((r.weight_g for r in rows if r.weight_g), None)
    # марка — как её пишет сеть полем; заглавные из названия — только если поля нет ни у кого
    brand = next((r.brand for r in rows if r.brand), None)
    if not brand:
        caps = next((r.brand_words for r in rows if r.brand_words), None)
        brand = " ".join(sorted(caps)).capitalize() if caps else None
    barcode = next((r.barcode for r in rows if r.barcode), None)
    unit = next((r.unit for r in rows if r.unit), None)
    category = next((r.category for r in rows if r.category), None)
    # norm — слова НАЗВАНИЯ в его порядке (первое слово — обычно сам товар), затем
    # остальные слова группы и марка: поиск сначала смотрит на начало названия
    tail = set().union(*(r.words for r in rows)) - set(best.order)
    return {"name": best.name, "weight_g": weight, "brand": brand,
            "barcode": barcode, "unit": unit, "category": category,
            "norm": " ".join(best.order + tuple(sorted(tail)))}
