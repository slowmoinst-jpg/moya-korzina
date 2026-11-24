"""Сверка артикулов X5: Пятёрочка и Перекрёсток делят общий PLU.

ЗАЧЕМ. Пятёрочка и Перекрёсток входят в одну группу X5 и используют общий
внутренний код товара (PLU). Перекрёсток отдаёт полный каталог при ночном сборе,
а витрина Пятёрочки закрыта защитой. Зная общий артикул из каталога Перекрёстка,
мы можем подставить его Пятёрочке для сборки корзины на телефоне владельца.
Замер 08.11.2026: 675 из 693 известных артикулов Пятёрочки есть у Перекрёстка.

Пятёрочке артикул пишется только с доводом, что товар у неё есть (артикул уже
встречался в её каталоге или товар там покупали): несуществующий артикул сбивает
телефону кладку запросом сети на всю сборку. Спорные пары не подтверждаются.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass
import logging
import re
import sqlite3

from app import config, repo
from app.catalog import link as L
from app.catalog import match as M
from app.catalog import nonfood, store
from app.catalog.model import placeholder_sku
from app.matcher.normalize import parse_weight
from app.models import Product

log = logging.getLogger(__name__)

METHOD = "x5"
X5 = ("pyaterochka", "perekrestok")

AGE = re.compile(
    r"(?i)(?<![а-яёa-z])(?:с|s)\s*\d+(?:[.,]\d+)?\s*(?:мес\w*|mes\w*|лет|let|год\w*|god\w*)\.?"
    r"|(?<![а-яёa-z])(?:с|s)\s+(?:рождения|rozdenia|rozhdeniya)"
    r"|(?<![а-яёa-z])(?:детск|detsk)\w*|(?<![а-яёa-z])(?:для|dla)\s+(?:детей|detej)"
)
JUNK_HEAD = re.compile(
    r"^(?:\s*(?:-?\d+\s*%|%\s*от\s*\d+\s*шт\s*[\d,.]+|нет оценок|комбо\s*[\d,.]+))+\s*",
    re.I,
)
JUNK_TAIL = re.compile(r"\s+\d[\d\s]*\s\d{2}\s*₽.*$")
# Хвост «1l»/«1kg» после ненулевой цифры (WEIGHT, LAT_DEC) — целый литр или килограмм, а цифра
# перед ним — процент («kefir 1 1kg» = 1 %, 1 кг); «0 1l» остаётся 0,1 л.
WEIGHT = re.compile(
    r"(?i)\d+(?:[.,]\d+)?\s*(?:кг|г|гр|мл|л|kg|gr|g|ml|l)(?![а-яёa-z])|(?:0\s|\d+\s(?!1(?:l|kg)(?![a-z])))\d{1,3}(?:l|kg)(?![a-z])"
)
PACK = re.compile(r"(?i)\d+\s*(?:шт|sht|st|пак\w*|pak\w*)(?![а-яёa-z])|\d+\s*[xх*]\s*\d+")
LAT_DEC = re.compile(r"(?<![\d])(0|\d(?!\s1(?:l|kg)(?![a-z])))\s(\d{1,3})(l|kg)(?![a-z])")
# Дробь без разделителя в слаге Дикси и Монетки: «…negazirovannaya 05l» — это 0,5 л. Каталог хранит
# у таких строк 5 л (Монетка) или ничего (Дикси), и вода 5 л сходилась с бутылкой 0,5 л.
LAT_ZERO = re.compile(r"(?<![\da-z])0(\d{1,3})(l|kg)(?![a-z])")
GAS_NO = re.compile(r"(?i)без\s+газа|не\s*газированн\w*|bez\s+gaza|ne\s*gazirovann\w*")
GAS_YES = re.compile(r"(?i)(?:с\s+)?газом|газированн\w*|(?:s\s+)?gazom|gazirovann\w*")
SLUG = re.compile(r"/product/([^/?#]*?)--\d+")


def J(w: str) -> str:
    w = w.replace("j", "i")
    return "kola" if w == "cola" else w


NEUTRAL = frozenset(J(w) for w in L._NEUTRAL) | frozenset(
    J(M.fold(w)) for w in ("вкусом", "вкуса", "вкус", "ассортименте", "ассортимент", "какао")
)
SOFT = tuple(
    J(M.fold(w))
    for w in (
        "картофельн",
        "натуральн",
        "газированн",
        "замороженн",
        "охлажденн",
        "обогащенн",
        "бифидобактер",
        "витаминизированн",
        "сухая",
        "сухой",
        "питьев",
        "безалкогольн",
        "упаковк",
        "высшего",
        "сорта",
        "сорт",
    )
)
BABY_BRANDS = {
    J(M.fold(w))
    for w in ("тема", "агуша", "фрутоняня", "спеленок", "gerber", "hipp", "малютка")
}


def clean(name: str | None) -> str:
    name = re.sub(r"(?<=\w)['’`](?=\w)", "", name or "")
    name = JUNK_TAIL.sub("", JUNK_HEAD.sub("", name))
    return re.sub(r"\s+", " ", AGE.sub(" ", name)).strip()


def weight_of(name: str | None, stored: float | None) -> float | None:
    m = LAT_DEC.search(name or "")
    if m:
        value = float(f"{m.group(1)}.{m.group(2)}")
        return value * 1000
    m = LAT_ZERO.search(name or "")
    if m:
        return float(f"0.{m.group(1)}") * 1000
    return stored or parse_weight(name or "")[0]


def numbers(name: str | None) -> list[str]:
    text = PACK.sub(" ", WEIGHT.sub(" ", M.glue(clean(name)).lower()))
    return sorted(re.findall(r"\d+", text))


def stem_eq(a: str, b: str) -> bool:
    if a == b:
        return True
    n = min(len(a), len(b))
    if n < 4:
        return False
    k = 0
    while k < n and a[k] == b[k]:
        k += 1
    return k >= (n - 1 if n == 4 else max(4, n - 2))


def brand_eq(a: str, b: str) -> bool:
    if a == b:
        return True
    n = min(len(a), len(b))
    k = 0
    while k < n and a[k] == b[k]:
        k += 1
    return n >= 5 and k >= n - 1


def covered(w: str, words: set[str] | list[str]) -> bool:
    return any(stem_eq(w, x) for x in words)


class R:
    """Нормализованный вид позиции для сверки: марка, граммовка, газ, детское."""

    def __init__(
        self,
        name: str | None,
        weight: float | None = None,
        unit: str | None = None,
        brand: str | None = None,
        chain: str = "x",
        sku: str = "",
    ):
        self.raw = name
        self.baby = bool(AGE.search(name or ""))
        self.gas = (
            "no"
            if GAS_NO.search(name or "")
            else ("yes" if GAS_YES.search(name or "") else None)
        )
        name = GAS_YES.sub(" ", GAS_NO.sub(" ", name or ""))
        self.chain, self.sku = chain, sku
        row = M.prepare(
            {
                "id": 0,
                "chain": chain,
                "name": clean(name),
                "brand": brand,
                "weight_g": None,
                "unit": unit,
            }
        )
        self.words = {J(w) for w in row.fwords if not w.isdigit()}
        self.order = [J(w) for w in row.forder if not w.isdigit()]
        self.brand = {J(w) for w in row.fbrand}
        self.weight = weight_of(name, weight)
        self.unit = unit
        self.packs = row.packs
        self.nums = numbers(name)


SAMOKAT_TRANS = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})


def samokat_slug(name: str | None) -> str:
    """Артикул Самоката в каталоге — транслитерированный слаг названия карточки.

    В чеке Самоката стоит то же название, что и в каталоге сети, но без артикула.
    Транслитерируя строку чека по правилам сети, мы восстанавливаем артикул-слаг
    для быстрой сборки корзины на телефоне владельца.
    """
    text = (name or "").lower().translate(SAMOKAT_TRANS)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def x5name(r: dict | sqlite3.Row) -> str | None:
    """Каноническое название строки X5.

    У Пятёрочки берём слаг из адреса карточки: имена из каталога часто испорчены
    старыми нарядами (наше название позиции вместо имени сети). Если слага нет,
    строку Пятёрочки пропускаем.
    """
    m = SLUG.search(r["url"] or "") if r["chain"] == "pyaterochka" else None
    return (
        m.group(1).replace("-", " ")
        if m and m.group(1)
        else (None if r["chain"] == "pyaterochka" else r["name"])
    )


def judge(e: R, c: R, pbrand: set[str], soft: bool = False) -> str | None:
    """Судейство пары позиций. None — подходит, иначе причина отказа."""
    if e.gas and c.gas and e.gas != c.gas:
        return "газ"
    if e.weight and c.weight:
        if abs(e.weight - c.weight) / max(e.weight, c.weight) > 0.03:
            return f"вес {e.weight:g}≠{c.weight:g}"
    elif not (e.unit == "kg" and c.unit == "kg") and not (e.packs == c.packs > 1):
        return "фасовка неизвестна"
    if e.packs != c.packs:
        return f"штук {e.packs}≠{c.packs}"
    if e.nums != c.nums:
        return f"числа {e.nums}≠{c.nums}"
    if not any(any(brand_eq(b, x) for x in c.words) for b in pbrand):
        return "марка"
    head = next((w for w in c.order if w not in pbrand), None)
    if not head or not covered(head, e.words):
        return f"тип {head}"
    sig_e = {w for w in e.words if len(w) >= 3 and w not in NEUTRAL and not covered(w, pbrand)}
    sig_c = {w for w in c.words if len(w) >= 3 and w not in NEUTRAL and not covered(w, pbrand)}
    miss = [w for w in sig_e if not covered(w, c.words)]
    extra = [w for w in sig_c if not covered(w, e.words)]
    if soft:
        extra = [w for w in extra if not w.startswith(SOFT)]
    if miss or extra:
        return f"слова -{miss} +{extra}"
    return None


@dataclass
class Index:
    """Поисковый индекс каталога X5."""

    rows: list[R]
    index: dict[str, list[int]]
    brand_vocab: set[str]
    rows_by: dict[tuple[str, str], dict]
    five: set[str]


def build_index(conn: sqlite3.Connection) -> Index:
    """Построить поисковый индекс каталогов Пятёрочки и Перекрёстка."""
    raw_rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM chain_products WHERE chain IN ('pyaterochka', 'perekrestok')"
        ).fetchall()
    ]
    rows_by = {(r["chain"], str(r["sku"])): r for r in raw_rows}
    five = {str(r["sku"]) for r in raw_rows if r["chain"] == "pyaterochka"}
    rows = [
        R(
            x5name(r),
            r.get("weight_g") if r["chain"] != "pyaterochka" else None,
            r.get("unit"),
            r.get("brand"),
            r["chain"],
            str(r["sku"]),
        )
        for r in raw_rows
        if x5name(r)
    ]
    first_freq = collections.Counter(r.order[0] for r in rows if r.order)
    word_freq = collections.Counter(w for r in rows for w in r.words)
    brand_vocab = set()
    for (b,) in conn.execute(
        "SELECT DISTINCT brand FROM catalog_items WHERE brand IS NOT NULL AND brand != ''"
    ):
        for w in M.fold_words(re.findall(r"[a-zа-яё0-9]+", re.sub(r"['’`]", "", b.lower()))):
            w = J(w)
            if len(w) >= 3 and not w.isdigit():
                brand_vocab.add(w)
    generic = {w for w, _ in word_freq.most_common(400)} | {
        w for w, _ in first_freq.most_common(300)
    }
    brand_vocab -= generic
    index = collections.defaultdict(list)
    for i, r in enumerate(rows):
        for w in r.words:
            index[w].append(i)
    return Index(
        rows=rows,
        index=index,
        brand_vocab=brand_vocab,
        rows_by=rows_by,
        five=five,
    )


def find_sku(product: Product, index: Index) -> tuple[str, bool] | None:
    """Найти артикул X5 для товара.

    Возвращает (sku, False) для строгого совпадения, (sku, True) для мягкого,
    или None при отсутствии совпадений либо неоднозначности.
    """
    p = product
    maps = repo.product_mappings(p.id)
    own_names = _own_names(p)
    names = list(
        dict.fromkeys(
            own_names
            + [
                m["raw_name"]
                for m in maps
                if m["store_code"] not in X5
                and m.get("raw_name")
                and not placeholder_sku(m["sku"], m["store_code"])
            ]
        )
    )
    ev = [R(n, p.weight_g if n == p.name else None, p.unit) for n in names]
    own = ev[: len(own_names)]
    for e in ev:
        if not e.weight and p.weight_g:
            e.weight = p.weight_g
    pbrand = set()
    for e in ev:
        pbrand |= e.brand | {w for w in e.words if w in index.brand_vocab}
    if p.brand:
        pbrand |= {
            J(w)
            for w in M.fold_words(
                re.findall(r"[a-zа-яё0-9]+", re.sub(r"['’`]", "", p.brand.lower()))
            )
        }
    pbrand = {b for b in pbrand if len(b) >= 3}
    if not pbrand:
        return None
    baby = any(e.baby for e in ev) or bool(pbrand & BABY_BRANDS)
    cand_ids = set()
    for b in pbrand:
        for w in [b] + [
            x for x in index.index if len(b) >= 5 and x != b and brand_eq(b, x)
        ][:5]:
            cand_ids.update(index.index.get(w, ()))
    ok = collections.defaultdict(list)
    soft = collections.defaultdict(list)
    for i in cand_ids:
        c = index.rows[i]
        if baby != (c.baby or bool(pbrand & BABY_BRANDS)):
            continue
        if any(
            (o.weight and c.weight and abs(o.weight - c.weight) / max(o.weight, c.weight) > 0.03)
            or (o.gas and c.gas and o.gas != c.gas)
            or (o.nums and c.nums and o.nums != c.nums)
            for o in own
        ):
            continue
        losts = [
            [
                w
                for w in o.words
                if len(w) >= 3
                and w not in NEUTRAL
                and not covered(w, pbrand)
                and not any(x.startswith(w) or stem_eq(w, x) for x in c.words)
            ]
            for o in own
        ]
        lost = min(losts, key=len)
        if lost:
            continue
        verdicts = [judge(e, c, pbrand) for e in ev]
        if any(v is None for v in verdicts):
            ok[c.sku].append(c)
        else:
            if any(judge(e, c, pbrand, soft=True) is None for e in ev):
                soft[c.sku].append(c)
    plus = sorted(ok)
    if len(plus) == 1:
        return (plus[0], False)
    if len(plus) > 1:
        return None
    if len(soft) == 1:
        return (next(iter(soft)), True)
    return None


def _own_names(p: Product) -> list[str]:
    """Свои названия товара: строки чеков, наше название и человеческое."""
    return list(dict.fromkeys(repo.raw_names(p.id) + [p.name]
                              + [repo.human_titles({p.id: p.name}).get(p.id) or p.name]))


def _contradicts(p: Product, row: dict) -> bool:
    """Строка сети расходится со своими названиями товара по весу, числам или газу.

    Так ловится чужая строка единой базы. Замер 08.11.2026: «Молоко Простоквашино 2,5 %»
    связано с Перекрёстком через «…pasterizovannoe 1 5 930ml», «Село Зелёное 2,5 %» —
    через «…6 950ml»: в транслите Перекрёстка нет знака процента, и жирность не сверилась.
    """
    c = R(x5name(row), row.get("weight_g") if row["chain"] != "pyaterochka" else None,
          row.get("unit"))
    for n in _own_names(p):
        o = R(n, p.weight_g if n == p.name else None, p.unit)
        if ((o.weight and c.weight and abs(o.weight - c.weight) / max(o.weight, c.weight) > 0.03)
                or (o.gas and c.gas and o.gas != c.gas) or (o.nums and c.nums and o.nums != c.nums)):
            return True
    return False


HEAD = re.compile(r"^\s*([A-Za-zА-Яа-яЁё/ ]{2,24})\.(?=\S)")     # голова строки чека: «ВК/ПОЛ.Отруби»


def _marks(p: Product) -> set[str]:
    """Слова марки, которые разбор названия теряет: голова строки чека до первой точки и марка товара.

    «ВК/ПОЛ.Отруби хруст.овсян.230г» разбирается в «отруби, хруст, овсян»: без головы карточка
    «Отруби хрустящие овсяные Лито 230 г» сошлась бы со своим названием слово в слово.
    """
    heads = [m.group(1) for m in map(HEAD.match, repo.raw_names(p.id)) if m]
    return {J(M.fold(w)) for text in [*heads, p.brand or ""]
            for w in re.findall(r"[a-zа-яё]+", text.lower()) if len(w) >= 3}


def _near(w: str, x: str) -> bool:
    """Наше слово — начало слова карточки («хруст» → «хрустящие») или та же основа."""
    return x.startswith(w) or stem_eq(w, x)


def found_by_phone(p: Product, card: str | None) -> bool:
    """Карточка, которую телефон нашёл поиском сети и положил в корзину, — тот же товар?

    Довода «марка совпала» (find_sku) здесь нет: его заменяет находка — карточку выдал поиск самой
    сети по нашему названию, и товар лёг. Проверяется то, чего поиск сети не проверяет. Замер
    13.11.2026: «Ясно солн хлопья овсян №3 350 г» телефон положил карточкой «…solnyshko-2-500g».
    Поэтому у каждого своего названия фасовка обязана совпасть, а не «не противоречить», свои числа
    карточка повторяет, число штук и пометка «детское» те же, а про газ обе стороны говорят одно
    или обе молчат: чек без слова про газ сошёлся бы и с газированной водой, и с негазированной.
    И хотя бы одно название сходится с карточкой словами в обе стороны: значимых слов вместе
    с маркой (_marks) не меньше двух, все они есть на карточке, и у карточки нет слова, которого
    нет у нас.

    Лишнее слово карточки отсекает и другой тип товара («Сэндвич с сыром Ламбер»), и производный
    товар той же марки и фасовки: «Кефир» → «Кефирный напиток», «Творог» → «Творожная масса»,
    «…безлактозное» — связь записала бы товару цену соседнего. Лишними не считаются только
    описательные слова (NEUTRAL). Мягкий список judge (SOFT) здесь не прощается: «безалкогольное»,
    «сухой», «замороженное» меняют товар, а связь пишется как подтверждённая. Короткое слово
    карточки (марка «Fa») — лишнее, если такого слова нет в нашем названии. Сокращения чека
    («гл», «ван») раскрывает человеческое название товара (_own_names).
    shortcut: слова сверяются по началу и основе, «Творог» и «Творожок» не различаются, а карточка
    многословнее чека («мука … пшеничная») не свяжется; смягчать — когда таких наберётся в отчётах.
    """
    c = R(card)
    if not c.weight:
        return False
    marks, fits = _marks(p), False
    for n in _own_names(p):
        o = R(n, p.weight_g if n == p.name else None, p.unit)
        weight = o.weight or p.weight_g
        if (not weight or abs(weight - c.weight) / max(weight, c.weight) > 0.03 or o.baby != c.baby
                or o.gas != c.gas or (o.nums and o.nums != c.nums) or o.packs != c.packs):
            return False
        ours = {w for w in o.words | marks if len(w) >= 3 and w not in NEUTRAL}
        extra = any(x not in NEUTRAL and x not in o.words and not any(_near(w, x) for w in ours)
                    for x in c.words)
        fits = fits or (len(ours) >= 2 and not extra
                        and all(any(_near(w, x) for x in c.words) for w in ours))
    return fits


def _wrong_base_link(p: Product, cur: dict, chain: str, index: Index) -> bool:
    """Связь записала единая база (не человек и не живой поиск), и её строка расходится с чеком."""
    row = index.rows_by.get((chain, str(cur["sku"])))
    return bool(row) and repo.mapping_method(p.id, cur["id"]) == L.MASTER and _contradicts(p, row)


ASSORTED = J(M.fold("ассортимент"))     # карточка «в ассортименте»: вкус выбирает сборщик, это не тот же товар


def _evidence(p: Product, vocab: set[str]) -> tuple[list[R], set[str], bool]:
    """Свои названия товара в виде для сверки, слова его марки и пометка «детское»."""
    own = [R(n, p.weight_g if n == p.name else None, p.unit) for n in _own_names(p)]
    pbrand: set[str] = set()
    for e in own:
        if not e.weight and p.weight_g:
            e.weight = p.weight_g
        pbrand |= e.brand | {w for w in e.words if w in vocab}
    if p.brand:
        pbrand |= {J(w) for w in M.fold_words(
            re.findall(r"[a-zа-яё0-9]+", re.sub(r"['’`]", "", p.brand.lower())))}
    pbrand = {b for b in pbrand if len(b) >= 3}
    return own, pbrand, any(e.baby for e in own) or bool(pbrand & BABY_BRANDS)


def _brand_items(conn: sqlite3.Connection, brands: set[str]) -> dict[str, list[int]]:
    """Единые товары, в названии которых есть слово марки: {слово марки: [номера]}.

    Один проход по своду названий (fnorm) на весь список марок: запрос LIKE на каждую марку
    читал бы всю таблицу заново. Слово свода подходит марке так же широко, как слово карточки в
    сверке (_near, brand_eq): «prof» находит и «professional».
    shortcut: строка, чей единый товар пишет марку иначе («Lay's» в своде — «lay» и «s»), сюда не
    попадает, и сверка её не видит; расширять, когда такие пары наберутся в списке «не нашлось».
    """
    heads: dict[str, list[str]] = collections.defaultdict(list)
    for b in brands:
        heads[b[:3]].append(b)
    out: dict[str, list[int]] = collections.defaultdict(list)
    for item_id, fnorm in conn.execute("SELECT id, fnorm FROM catalog_items WHERE fnorm IS NOT NULL"):
        for w in {J(w) for w in fnorm.split()}:
            for b in heads.get(w[:3], ()):
                if brand_eq(b, w) or _near(b, w):
                    out[b].append(int(item_id))
    return out


def _same(o: R, c: R, pbrand: set[str]) -> bool:
    """Своё название не спорит со строкой сети весом, газом и числами, и все его слова марки на ней есть.

    judge хватает одного слова марки, а остальные он из сверки слов убирает. Словарь марок широк
    («пряник», «свежая», «плюс» — тоже чьи-то марки), и «Тульский пряник Ясная Поляна» сошёлся бы
    с чужим «Пряником Тульским», а «Голубика Artfruit свежая» — с голубикой без марки (прогон на
    копии 16.11.2026). Сокращение чека считается началом слова карточки.

    Отрицание своего названия тоже обязано быть на карточке. Наше название товара импорт выводит из
    строки чека и однобуквенные «Н/», «Б/» в нём теряет: «ПИВО … Н/ФИЛЬТР» становится «Пиво …
    фильтр», «Б/А» пропадает совсем. judge хватает одного сошедшегося названия, и такое название
    сошлось бы с фильтрованным и с обычным пивом, хотя строка чека говорит обратное.
    """
    if ((o.weight and c.weight and abs(o.weight - c.weight) / max(o.weight, c.weight) > 0.03)
            or (o.gas and c.gas and o.gas != c.gas) or (o.nums and c.nums and o.nums != c.nums)
            # «беза» (Б/А) короче, чем отрицания у link._negation, поэтому мерка здесь своя. «Без
            # косточек» у нас и «без косточки» на карточке — одно слово с разным окончанием (stem_eq).
            or any(len(w) >= 4 and w.startswith(("bez", "ne"))
                   and not any(L._covers(w, x) or (min(len(w), len(x)) > 4 and stem_eq(w, x))
                               for x in c.words) for w in o.words)):
        return False
    return all(any(_near(w, x) for x in c.words)
               for w in o.words if len(w) >= 3 and w not in NEUTRAL and covered(w, pbrand))


def fill_chains(products: list[Product], vocab: set[str], conn: sqlite3.Connection,
                write: bool = True) -> dict:
    """Строгая сверка названий по каталогам остальных сетей (не X5): {сеть: записано}, picks, gone.

    ЗАЧЕМ (замер 15.11.2026, живые привычки владельца). Единая база сводит строки сетей в один
    товар попарным сравнением названий, и тот же товар часто остаётся в соседнем едином товаре:
    «Lay's» против «Lays», «Шоколадный батончик» против «Шоколадные батончики», первые два слова
    названия в другом порядке. До такой строки товар человека через единый товар не дотягивается.
    Здесь товар сверяется со строками сети напрямую тем же судьёй, что у X5 (judge): марка, вид,
    все значимые слова в обе стороны, фасовка в пределах 3 %, числа, штуки, газ.

    Пара пишется, только если в сети ровно одна такая живая строка, свои названия товара с ней не
    спорят (_same), не спорят и охраны единой базы (link._agrees), а настоящий штрихкод строки не
    чужой. Товар, чьи строки чека спорят между собой, не сверяется.
    Доводом служат только свои названия товара (чек, наше, человеческое): названия уже
    связанных строк сетей не берутся, иначе одна неверная связь расходилась бы по остальным сетям.
    Сеть, где связь уже есть (человек, единая база, живой поиск), и сеть, которую человек убрал,
    не трогаются. Своя прежняя связь снимается, когда её строка ушла с витрины или перестала
    сходиться: мёртвый артикул в наряд не идёт.
    """
    shops = {s.code: s for s in repo.list_stores() if s.code not in X5}
    out: dict = {"chains": collections.Counter(), "picks": [], "gone": 0}
    evs = {p.id: _evidence(p, vocab) for p in products}
    brands = set().union(*(pbrand for _, pbrand, _ in evs.values())) if evs else set()
    if not shops or not brands:
        return out
    items_of = _brand_items(conn, brands)
    chains = list(shops)
    cache: dict[int, R] = {}
    for p in products:
        own, pbrand, baby = evs[p.id]
        real = [m for m in repo.product_mappings(p.id)
                if not placeholder_sku(m["sku"], m["store_code"])]
        mine = {m["store_code"]: m for m in real if m.get("method") == L.STRICT}
        free = [c for c in chains if c in mine or not any(m["store_code"] == c for m in real)]
        # Слово марки из своего названия обязано быть на карточке (_same): кандидатов хватает взять
        # по самому редкому такому слову, а не по всем («свежая» и «пряник» — тысячи единых товаров).
        must = [b for b in pbrand if b not in NEUTRAL and any(b in o.words for o in own)]
        items = (sorted(min((items_of.get(b, ()) for b in must), key=len)) if must
                 else sorted({i for b in pbrand for i in items_of.get(b, ())}))
        if not free or not (items or mine):
            continue
        weights = [o.weight for o in own if o.weight]
        rows: list[dict] = []
        for i in range(0, len(items), 500):
            chunk = items[i:i + 500]
            # Плюсы держат запрос на индексе единого товара: без них SQLite шёл по индексу сети
            # и перебирал все её живые строки, 0,27 с на запрос против 0,01 (замер 16.11.2026).
            sql = (f"SELECT * FROM chain_products WHERE item_id IN ({','.join('?' * len(chunk))})"
                   f" AND +active=1 AND +chain IN ({','.join('?' * len(free))})")
            args: list = [*chunk, *free]
            if weights:        # грубый отсев по фасовке; точную сверку ведёт judge
                sql += " AND (weight_g IS NULL OR weight_g BETWEEN ? AND ?)"
                args += [min(weights) * 0.96, max(weights) * 1.04]
            rows += [dict(r) for r in conn.execute(sql, args)]
        person = codes = None
        fit: dict[str, dict[str, dict]] = collections.defaultdict(dict)
        for row in rows:
            if placeholder_sku(row["sku"], row["chain"]):
                continue
            c = cache.get(row["id"])
            if c is None:
                c = cache[row["id"]] = R(row["name"], row.get("weight_g"), row.get("unit"),
                                          row.get("brand"), row["chain"], str(row["sku"]))
            if (baby != (c.baby or bool(pbrand & BABY_BRANDS)) or any(w.startswith(ASSORTED) for w in c.words)
                    or not all(_same(o, c, pbrand) for o in own)
                    or not any(judge(e, c, pbrand) is None for e in own)):
                continue
            if person is None:
                maps = repo.product_mappings(p.id)
                person = L._rows_of_person(p, repo.raw_names(p.id))
                codes = L._codes(p, maps, L._choices(p, maps, person, conn))
            # Строк человека нет — строки чека спорят между собой («С/ГАЗ» и «Н/ГАЗ» в одном товаре,
            # разное число штук): какую разновидность искать, неизвестно, и пара не пишется.
            code = L._trusted(row.get("barcode"))
            if (not person or (code and codes and code not in codes)
                    or not L._agrees(person, L._prep(row))):
                continue
            # Чужой штрихкод у соседней строки того же единого товара паре не мешает, хотя база такую
            # строку держит кандидатом (link._weaken_on_codes). Прогон на копии 16.11.2026: запрет снял
            # бы 34 пары владельца с тем же названием, числами и фасовкой — у товара сменился код
            # («Сметана Простоквашино 15 % 300 г» у нового изготовителя) или METRO хранит код коробки.
            fit[row["chain"]][str(row["sku"])] = row
        for chain in free:
            shop, old = shops[chain], mine.get(chain)
            if old is not None:
                if str(old["sku"]) in fit[chain]:
                    continue                 # своя связь по-прежнему сходится
                out["gone"] += 1
                if write:
                    repo.unconfirm_mapping(p.id, old["store_product_id"])
            if len(fit[chain]) != 1 or repo.chain_rejected(p.id, shop.id):
                continue
            row = next(iter(fit[chain].values()))
            out["chains"][chain] += 1
            out["picks"].append((p.id, p.name, chain, str(row["sku"]), row["name"]))
            if write:
                sp = repo.upsert_store_product(shop.id, str(row["sku"]), row["name"],
                                               weight_g=row.get("weight_g"), unit=row.get("unit"),
                                               url=row.get("url"), ean=row.get("barcode"),
                                               keep_known=True)
                repo.confirm_mapping(p.id, sp, confirmed=True, method=L.STRICT)
                repo.drop_base_candidates(p.id, shop.id, keep=sp)
                L._price(sp, row)
    return out


_DONE: dict[str, tuple[int, set[int]]] = {}


def fill(
    products: list[Product] | None = None,
    *,
    write: bool = True,
    conn: sqlite3.Connection | None = None,
    ignore_done: bool = False,
) -> dict:
    """Сверить товары человека с каталогами сетей X5 и разложить артикулы.

    Проверяет только товары из покупной истории и корзин. Индекс строится
    один раз на поколение каталога: повторные запуски без новых товаров
    пропускаются без затрат CPU и памяти. Остальные сети сверяются строго
    (fill_chains) по тем же товарам.
    """
    if conn is None:
        with store.connect() as c:
            return fill(products=products, write=write, conn=c, ignore_done=ignore_done)

    db_key = config.db_path()
    gen = L._generation(conn)
    if not ignore_done:
        done_gen, done_ids = _DONE.get(db_key, (-1, set()))
        if done_gen != gen:
            done_ids = set()
    else:
        done_ids = set()

    with repo.get_conn() as c:
        food = {
            r[0]
            for r in c.execute(
                "SELECT DISTINCT product_id FROM purchase_history WHERE store_id IS NOT NULL"
            )
        }
        food |= {r[0] for r in c.execute("SELECT DISTINCT product_id FROM basket_items")}

    if products is None:
        products = repo.list_products()

    candidates = [p for p in products if p.id in food and p.id not in done_ids]
    if not candidates:
        return {
            "tried": 0,
            "picked": 0,
            "soft": 0,
            "pyaterochka": 0,
            "perekrestok": 0,
            "samokat": 0,
            "already": 0,
            "differs": 0,
            "replaced": 0,
            "no_evidence": 0,
            "picks": [],
            "chains": {},
            "chain_picks": [],
            "gone": 0,
        }

    index = build_index(conn)

    store_perekrestok = repo.get_store("perekrestok")
    store_pyaterochka = repo.get_store("pyaterochka")
    store_samokat = repo.get_store("samokat")

    five_store_id = store_pyaterochka.id if store_pyaterochka else None
    five_purchases: set[int] = set()
    if five_store_id is not None:
        with repo.get_conn() as c:
            five_purchases = {
                r[0]
                for r in c.execute(
                    "SELECT DISTINCT product_id FROM purchase_history WHERE store_id=?",
                    (five_store_id,),
                )
            }

    samokat_store_id = store_samokat.id if store_samokat else None
    samokat_purchases: set[int] = set()
    if samokat_store_id is not None:
        with repo.get_conn() as c:
            samokat_purchases = {
                r[0]
                for r in c.execute(
                    "SELECT DISTINCT product_id FROM purchase_history WHERE store_id=?",
                    (samokat_store_id,),
                )
            }

    tried = 0
    picked = 0
    soft = 0
    perekrestok_cnt = 0
    pyaterochka_cnt = 0
    samokat_cnt = 0
    already = 0
    differs = 0
    replaced = 0
    no_evidence = 0
    picks = []

    for p in candidates:
        tried += 1
        found = find_sku(p, index)
        if not found:
            continue
        sku, is_soft = found
        picked += 1
        if is_soft:
            soft += 1
        picks.append((p.id, p.name, sku, is_soft))

        if store_perekrestok:
            cur_p = repo.confirmed_mapping(p.id, store_perekrestok.id, real_only=True)
            if (cur_p is not None and str(cur_p["sku"]) != str(sku)
                    and _wrong_base_link(p, cur_p, "perekrestok", index)):
                replaced += 1
                if write:
                    repo.unconfirm_mapping(p.id, cur_p["id"])
                cur_p = None
            if cur_p is not None:
                if str(cur_p["sku"]) == str(sku):
                    already += 1
                else:
                    differs += 1
            else:
                row_p = index.rows_by.get(("perekrestok", sku))
                if row_p and row_p.get("active", 1) == 1:
                    if not repo.chain_rejected(p.id, store_perekrestok.id):
                        perekrestok_cnt += 1
                        if write:
                            sp = repo.upsert_store_product(
                                store_perekrestok.id,
                                sku,
                                row_p["name"],
                                weight_g=row_p.get("weight_g"),
                                unit=row_p.get("unit"),
                                url=row_p.get("url"),
                                keep_known=True,
                            )
                            repo.confirm_mapping(p.id, sp, confirmed=True, method=METHOD)
                            # Снятая чужая строка базы иначе висела бы кандидатом на «Связях».
                            repo.drop_base_candidates(p.id, store_perekrestok.id, keep=sp)
                            L._price(sp, row_p)

        if store_pyaterochka:
            cur_5 = repo.confirmed_mapping(p.id, store_pyaterochka.id, real_only=True)
            if (cur_5 is not None and str(cur_5["sku"]) != str(sku)
                    and _wrong_base_link(p, cur_5, "pyaterochka", index)):
                replaced += 1
                if write:
                    repo.unconfirm_mapping(p.id, cur_5["id"])
                cur_5 = None
            if cur_5 is not None:
                if str(cur_5["sku"]) == str(sku):
                    already += 1
                else:
                    differs += 1
            else:
                if not repo.chain_rejected(p.id, store_pyaterochka.id):
                    has_evidence = (sku in index.five) or (p.id in five_purchases)
                    if not has_evidence:
                        no_evidence += 1
                    else:
                        row_5 = index.rows_by.get(("pyaterochka", sku))
                        row_p = index.rows_by.get(("perekrestok", sku))
                        url = (
                            row_5.get("url")
                            if row_5 and row_5.get("url")
                            else f"https://5ka.ru/product/--{sku}/"
                        )
                        src = row_5 or row_p or {}
                        name = src.get("name") or p.name
                        weight_g = src.get("weight_g")
                        unit = src.get("unit")
                        pyaterochka_cnt += 1
                        if write:
                            sp = repo.upsert_store_product(
                                store_pyaterochka.id,
                                sku,
                                name,
                                weight_g=weight_g,
                                unit=unit,
                                url=url,
                                keep_known=True,
                            )
                            repo.confirm_mapping(p.id, sp, confirmed=True, method=METHOD)
                            repo.drop_base_candidates(p.id, store_pyaterochka.id, keep=sp)
                            if row_5:
                                L._price(sp, row_5)

    others = fill_chains(candidates, index.brand_vocab, conn, write=write)

    # Шаг Самоката: артикул в его витрине — это транслитерированный слаг названия.
    # Если позицию покупали в Самокате и её слаг однозначно есть в каталоге сети,
    # связываем их: телефону это даёт артикул для быстрой сборки корзины.
    # Каталог Самоката у нас неполон (15.11.2026: 1625 строк, а из 367 купленных там товаров связаны были 60),
    # поэтому товар, которого в каталоге нет, связываем по слагу названия из чека самого Самоката: у 48 из 60
    # уже связанных артикул ему равен. Есть ли товар на витрине адреса, скажет сеть при сборке
    # (cartapi.js, samokatCart: 404 → «нет в наличии у сети»).
    if store_samokat:
        samokat_items: list[tuple[Product, list[str]]] = []
        all_samokat_slugs: set[str] = set()
        for p in candidates:
            if p.id not in samokat_purchases:
                continue
            if repo.confirmed_mapping(p.id, store_samokat.id, real_only=True) is not None:
                continue
            if repo.chain_rejected(p.id, store_samokat.id):
                continue
            slugs = list(
                dict.fromkeys(
                    samokat_slug(n)
                    for n in repo.raw_names(p.id, limit=5)
                    if samokat_slug(n)
                )
            )
            if not slugs:
                continue
            samokat_items.append((p, slugs))
            all_samokat_slugs.update(slugs)

        if samokat_items and all_samokat_slugs:
            slug_list = list(all_samokat_slugs)
            rows_by_sku: dict[str, dict] = {}
            for i in range(0, len(slug_list), 900):
                batch = slug_list[i : i + 900]
                placeholders = ",".join("?" for _ in batch)
                for r in conn.execute(
                    f"SELECT * FROM chain_products WHERE chain = 'samokat' AND sku IN ({placeholders})",
                    batch,
                ).fetchall():
                    row = dict(r)
                    rows_by_sku[str(row["sku"])] = row

            for p, slugs in samokat_items:
                matching = [rows_by_sku[s] for s in slugs if s in rows_by_sku]
                if len(matching) == 1:
                    row_s = matching[0]
                    sku = str(row_s["sku"])
                    samokat_cnt += 1
                    if write:
                        sp = repo.upsert_store_product(
                            store_samokat.id,
                            sku,
                            row_s["name"],
                            weight_g=row_s.get("weight_g"),
                            unit=row_s.get("unit"),
                            url=row_s.get("url"),
                            keep_known=True,
                        )
                        repo.confirm_mapping(p.id, sp, confirmed=True, method=METHOD)
                        L._price(sp, row_s)
                elif not matching:
                    own = repo.raw_names(p.id, limit=1, store_id=store_samokat.id)
                    sku = samokat_slug(own[0]) if own else ""
                    # «Доставка» и «Сервисный сбор» в чеке — не товары витрины.
                    if not sku or nonfood.is_service_name(own[0]):
                        continue
                    samokat_cnt += 1
                    if write:
                        sp = repo.upsert_store_product(store_samokat.id, sku, own[0], keep_known=True)
                        repo.confirm_mapping(p.id, sp, confirmed=True, method=METHOD)

    if write and not ignore_done:
        done_gen, done_ids = _DONE.get(db_key, (-1, set()))
        if done_gen != gen:
            done_ids = set()
        _DONE[db_key] = (gen, done_ids | {p.id for p in candidates})

    log.info("сверка X5: проверено %d, найдено %d (мягко %d), записано Пятёрочке %d,"
             " Перекрёстку %d, Самокату %d; уже было %d, расходится %d, заменено чужих строк базы %d,"
             " без довода Пятёрочки %d; остальным сетям строгой сверкой %s, снято своих %d",
             tried, picked, soft, pyaterochka_cnt, perekrestok_cnt,
             samokat_cnt, already, differs, replaced, no_evidence,
             dict(others["chains"]), others["gone"])
    return {
        "tried": tried,
        "picked": picked,
        "soft": soft,
        "pyaterochka": pyaterochka_cnt,
        "perekrestok": perekrestok_cnt,
        "samokat": samokat_cnt,
        "already": already,
        "differs": differs,
        "replaced": replaced,
        "no_evidence": no_evidence,
        "picks": picks,
        "chains": dict(others["chains"]),
        "chain_picks": others["picks"],
        "gone": others["gone"],
    }


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI для ручной сверки X5."""
    import argparse
    from app import users

    parser = argparse.ArgumentParser(description="Сверка артикулов X5 (Пятёрочка и Перекрёсток).")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--all", action="store_true", help="все рабочие места")
    who.add_argument("--phone", help="одно рабочее место")
    parser.add_argument("--dry-run", action="store_true", help="ничего не записывать")
    parser.add_argument("--verbose", action="store_true", help="подробный вывод")
    args = parser.parse_args(argv)

    phones = users.list_workspaces() if args.all else [args.phone]
    for phone in phones:
        if args.all and phone == users.DEMO:
            continue
        try:
            users.open_workspace(phone)
            res = fill(write=not args.dry_run, ignore_done=True)
            print(
                f"{phone}: проверено {res['tried']}, найдено {res['picked']} (мягко {res['soft']}); "
                f"записано 5ка {res['pyaterochka']}, перекрёсток {res['perekrestok']}, самокат {res['samokat']}; "
                f"уже было {res['already']}, отличается {res['differs']}, заменено {res['replaced']}, нет довода 5ки {res['no_evidence']}; "
                f"остальным сетям {res['chains']}, снято своих {res['gone']}"
            )
            if args.verbose:
                for pid, name, sku, is_soft in res["picks"]:
                    soft_mark = " (мягко)" if is_soft else ""
                    print(f"  #{pid} {name} -> {sku}{soft_mark}")
                for pid, name, chain, sku, card in res["chain_picks"]:
                    print(f"  #{pid} {name} -> {chain} {sku} «{card}»")
        finally:
            users.deactivate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
