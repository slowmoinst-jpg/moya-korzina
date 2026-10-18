"""Товары человека и единая база: сопоставить один раз, артикулы сетей брать из базы.

ЗАЧЕМ (решение владельца 17.10.2026). Единая база — заранее сопоставленные между собой
товары всех сетей (data/catalog.db, refresh.match_all), и у каждого её товара постоянный
номер. Актуализация базы — отдельная задача. Человеку нужно одно: чтобы товары из его
чеков нашли себя в этой базе. Тогда у товара есть артикулы во всех сетях, где база его
знает, и корзина собирается в любой из них (cartplan.build берёт подтверждённый артикул).

КАК ИЩЕТСЯ товар человека (find_master), по порядку:
  1. собственный доверенный штрихкод товара (normalize.gtin_trusted) — он главнее всего;
  2. штрихкоды и мост — настоящие артикулы, подтверждённые не базой (человек, живой
     поиск), → их единый товар в базе;
  3. слова строк чека и эталона: кандидаты — store.search_folded (общий свод письма,
     начала слов) и прежний store.search_items; решает match.same_product, а сокращения
     чека («МОЛ.ПАСТ.ПРОСТОКВАШИНО») — взаимное покрытие слов при общей марке.
  Ключи дали разные товары или два товара равно хороши — не угадываем: «сомнительно».
  Артикулы, записанные самой базой, доводом не служат: иначе ошибочная связь сама себя
  закрепляла бы. Выбор не базой служит доводом для других сетей, только если он сам
  похож на товар человека и сеть не отдала этот артикул другому товару (_choices).

СИЛЬНАЯ И СЛАБАЯ СВЯЗЬ (критика проекта 17.10.2026). Сильная — штрихкод, мост, равные
слова или полное сокращение, у обеих сторон известна фасовка и расходится не больше чем
на 3 % (у весового товара фасовки нет у обеих сторон — это не помеха). Слабая — прочее
вложение (вкус, сорт), совпадение только по своду письма, фасовка дальше 3 % или
неизвестна, сокращение, где чек промолчал о сорте, спор настоящих штрихкодов. Положить
не тот творог хуже, чем не положить ничего.

КАЖДАЯ СТРОКА СЕТИ ПРОВЕРЯЕТСЯ ОТДЕЛЬНО (_row_ok, ревью 18.10.2026). Единый товар —
группа, склеенная для поиска: молчание процента у неё согласие, фасовка — ±20 %.
Поэтому строка сети подтверждается, только если она сама — товар человека: её
настоящий штрихкод равен коду товара; или она сильно сходится со строкой чека; или
она сильно сходится с якорем — строкой, через которую товар найден, — и ничем не спорит
ни с одной строкой человека, а фасовка в пределах 3 % от его фасовки. Прочие строки
ложатся кандидатами (confirmed=0): в корзину и расчёт они не идут, их подтверждают на
«Связях». Сильная связь, не подтвердившая ни одной сети, записывается слабой.

ЧЕГО ЕДИНАЯ БАЗА НЕ ТРОГАЕТ. Настоящий подтверждённый артикул, записанный не ею
(человек, живой поиск, прежние связи), — это выбор человека. Сеть, связь с которой
человек убрал (link_rejects): отказ — от сети целиком. Заглушки hist-, list- и
«<сеть>-…» сеть не закрывают: на их место база кладёт настоящий артикул.

КОГДА РАБОТАЕТ. После загрузки чеков (новые товары) и при входе человека — фоном
(link_soon): новые товары ищутся, привязанные сверяются с базой один раз на её
поколение (после каждой пересборки) и при каждом новом доводе (выбор человека, новая
строка чека). Один раз для всех людей — инструментом:
    python -m app.catalog.link --all           первичное сопоставление
    python -m app.catalog.link --phone 7999…   одно рабочее место
    python -m app.catalog.link --all --dry-run что нашлось бы, без записи
    python -m app.catalog.link --all --sync    сверить привязанных сейчас, не дожидаясь входа
Показ страниц ничего не ищет и не пишет.
"""
from __future__ import annotations

import argparse
import logging
import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from app import repo
from app.catalog import match as matching
from app.catalog import store
from app.matcher.normalize import display_name, gtin, gtin_trusted

log = logging.getLogger(__name__)

MASTER = "master"            # product_mapping.method у артикулов из единой базы
PACK_SAME = 0.03             # фасовка «та же самая»: 930 мл и 900 мл — уже разные
DOUBTFUL = "doubtful"        # ответ поиска «есть, но угадывать не станем»
ROWS_LIMIT = 40              # строк сетей одного товара, по которым судим
SLEEP_DAYS = 14              # сколько спящий номер держит привязку без перепоиска

# Слова, которыми названия одного товара в разных сетях расходятся, НЕ делая его
# другим: способ обработки молока и обязательные надписи. «Молоко Простоквашино 2,5 %
# 930 мл» и «Молоко пастеризованное ПРОСТОКВАШИНО 2,5 %, без змж, 930 мл» — одна
# покупка. Вкус, сорт и добавки сюда не входят никогда: «Йогурт Активиа 2,9 %» и
# «Йогурт Активиа клубника 2,9 %» — разные (критика проекта 17.10.2026).
# «Ультрапастеризованное» — нет: это другой товар с другим сроком и в другой таре, и
# «Молоко 930 мл» с «ультрапастеризованным 950 г» сильно связываться не должно.
# Тара и надписи (ПЭТ, БЗМЖ) убираются ещё в match.prepare (match.NOISE).
DESCRIPTORS = frozenset(matching.fold(w) for w in (
    "пастеризованное", "пастеризованный", "пастеризованная",
    "питьевое", "питьевой", "питьевая",
    "отборное", "отборный", "отборная", "охлажденное", "охлажденный", "охлажденная"))
# Сорт, о котором чек обычно молчит. Сокращение чека, не покрывшее такое слово, —
# связь, но слабая: полное название с тем же лишним словом тоже даёт только слабую.
SORT_WORDS = frozenset(matching.fold(w) for w in (
    "натуральное", "натуральный", "натуральная", "классическое", "классический",
    "классическая", "традиционное", "традиционный", "традиционная"))
# Точка между буквами в строке чека — сокращение, а не конец предложения.
_ABBR_DOT = re.compile(r"(?<=[A-Za-zА-Яа-яЁё])\.(?=[A-Za-zА-Яа-яЁё])")


@dataclass(frozen=True)
class Found:
    """Единый товар, найденный для товара человека."""
    item_id: int
    method: str        # barcode | bridge | words | abbrev | translit | inclusion
    score: float
    strong: bool

    @property
    def mark(self) -> str:
        """Как записать способ в products.master_method: слабая связь — с «?»."""
        return self.method + ("" if self.strong else "?")


@dataclass
class Report:
    """Что сделал проход: для журнала, «Связей» и инструмента.

    Каждый товар — ровно в одной корзине: tried = linked + doubtful + unknown + failed.
    """
    tried: int = 0
    linked: int = 0          # привязано (и записано, если проход пишет)
    strong: int = 0
    weak: int = 0
    doubtful: int = 0
    unknown: int = 0
    failed: int = 0          # сбой на товаре: проход идёт дальше, товар — в следующий раз
    chains: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def as_dict(self) -> dict:
        return {"tried": self.tried, "linked": self.linked, "strong": self.strong,
                "weak": self.weak, "doubtful": self.doubtful, "unknown": self.unknown,
                "failed": self.failed, "chains": dict(self.chains)}

    def count(self, found: Found) -> None:
        self.linked += 1
        self.strong += int(found.strong)
        self.weak += int(not found.strong)

    def missed(self, result) -> None:
        if result == DOUBTFUL:
            self.doubtful += 1
        else:
            self.unknown += 1


# ---------- правила ----------
def _real(store_code: str, sku: str | None) -> bool:
    """Настоящий артикул сети, а не заглушка чека, прайса или справочника."""
    from app.catalog.model import placeholder_sku

    return bool(str(sku or "")) and not placeholder_sku(sku, store_code)


def _trusted(barcode: str | None) -> str | None:
    """Штрихкод в одном виде, если это настоящий GTIN; внутренний код магазина — None."""
    code = gtin(barcode) if barcode else None
    return code if code and gtin_trusted(code) else None


def _close(a: float | None, b: float | None) -> bool:
    """Обе фасовки известны и расходятся не больше чем на PACK_SAME."""
    if not a or not b:
        return False
    return abs(a - b) / max(a, b) <= PACK_SAME


def _by_weight(a: matching.Row, b: matching.Row) -> bool:
    """Обе стороны продаются на вес: фасовки нет у обеих по определению, и это не помеха.

    Иначе каждый фрукт, овощ и весовой сыр владелец подтверждал бы руками в каждой
    сети (ревью 18.10.2026). Весовым считается только явное «kg» у обеих сторон.
    """
    return a.unit == "kg" and b.unit == "kg" and not a.weight_g and not b.weight_g


def _prep(row: dict, receipt: bool = False) -> matching.Row:
    """Строка сети, единого товара или чека — в вид для сравнения (match.prepare).

    Тару и надписи убирает и отрицания склеивает сам match.prepare. Здесь — только то,
    что нужно строке чека: точка между буквами — сокращение («МОЛ.ПАСТ.»).
    """
    name = row.get("name") or ""
    if receipt:
        name = _ABBR_DOT.sub(" ", name)
    return matching.prepare({**row, "id": int(row.get("id") or 0), "name": name})


def _rows_of_person(product, raw_names: list[str]) -> list[matching.Row]:
    """Товар человека в виде строк для сравнения — по каждой строке чека и эталону.

    Эталон, заведённый импортом из строки чека (его имя — display_name этой строки),
    теряет однобуквенные «Н/» и «Б/»: «ВОДА … Н/ГАЗ» становится «Вода … газ», и такой
    эталон находил газированную воду (ревью 18.10.2026). Поэтому он строится заново из
    склеенной строки: «Н/ГАЗ» остаётся словом «негаз». Эталон, который назвал человек, и
    товар без строк чека — как есть.

    Процент, число штук и метки разновидности берутся по строкам чека: название
    эталона их теряет («МОЛ.ПАСТ.ПРОСТОКВАШИНО 2,5%» → «Паст простоквашино 2 5»,
    «ВОДА ШИШКИН ЛЕС 6*0,5Л» → «Вода шишкин лес 6 500 г»). Строки чека назвали разное
    число штук — не угадываем: строк нет.
    """
    receipts = [n for n in raw_names if n]
    own = (product.name or "").strip().lower()
    derived = bool(receipts) and own in {display_name(n).strip().lower() for n in receipts}
    etalons = [display_name(matching.glue(n)) for n in receipts] if derived else [product.name]
    names = list(dict.fromkeys(receipts + [e for e in etalons if e]))
    pct = next((p for p in (matching.parse_percent(n) for n in names) if p is not None), None)
    said = {matching.pack_count(matching.glue(n)) for n in receipts} - {1}
    if len(said) > 1:
        return []
    packs = next(iter(said), None)
    marks = frozenset().union(*(matching.variant_marks(n) for n in receipts))
    out: list[matching.Row] = []
    for name in names:
        row = _prep({"id": 0, "chain": "mine", "name": name, "brand": product.brand,
                     "weight_g": product.weight_g,
                     "unit": product.unit if product.unit in ("pcs", "kg") else None,
                     "barcode": None}, receipt=True)
        if row.pct is None and pct is not None:
            row = replace(row, pct=pct)
        if packs and row.packs == 1:
            row = replace(row, packs=packs)
        if marks and not row.marks:
            row = replace(row, marks=marks)
        if row.words:
            out.append(row)
    return out


def _negation(word: str) -> str | None:
    """Основа отрицания: «безгаза» и «негазированная» — одно отрицание «газ…»."""
    if word.startswith("bez") and len(word) > 4:
        return word[3:]
    if word.startswith("ne") and len(word) > 3:
        return word[2:]
    return None


def _starts(word: str, head: str) -> bool:
    """Слово начинается с head — с учётом того, что «без…» и «не…» одно отрицание."""
    if word.startswith(head):
        return True
    a, b = _negation(word), _negation(head)
    return bool(a and b and a.startswith(b))


def _abbrev(mine: matching.Row, cand: matching.Row) -> int:
    """Строка чека — сокращение названия кандидата: 0 — нет, 1 — слабое, 2 — полное.

    Каждое слово чека от трёх букв — начало слова кандидата; каждое слово кандидата,
    кроме марки и описаний обработки, покрыто словом чека; и есть общая марка — слово
    чека от четырёх букв начинает слово марки кандидата. Пример: «МОЛ ПАСТ
    ПРОСТОКВАШИНО 930» находит «Молоко пастеризованное ПРОСТОКВАШИНО 930 мл», но не
    «Коктейль молочный пастеризованный Простоквашино» — «коктейль» ничем не покрыт.
    Слово чека, равное слову кандидата, покрывает только его: «КОФЕ» не покрывает ещё
    и «кофеина». Непокрытый сорт («натуральный») — связь слабая. «НЕГАЗ» из чека и
    «без газа» в сети — одно отрицание.
    """
    said = [w for w in mine.forder if len(w) >= 3]
    if len(said) < 2 or not cand.fbrand:
        return 0
    words = set(cand.fwords)
    if not all(any(_starts(c, r) for c in words) for r in said):
        return 0
    exact = {r for r in said if r in words}
    loose = [r for r in said if r not in words]
    rest = words - set(cand.fbrand) - DESCRIPTORS
    uncovered = {c for c in rest if c not in exact
                 and not any(_starts(c, r) or _starts(r, c) for r in loose)}
    if uncovered - SORT_WORDS:
        return 0
    if not any(len(r) >= 4 and any(b.startswith(r) for b in cand.fbrand) for r in said):
        return 0
    return 1 if uncovered else 2


def _judge(mine: matching.Row, cand: matching.Row, vocab: frozenset[str]
           ) -> tuple[bool, float, str] | None:
    """(сильная ли, уверенность, способ) или None — не тот товар."""
    mine, cand = replace(mine, bkey=None), replace(cand, bkey=None)
    ok, method, score = matching.same_product(mine, cand, vocab)
    if ok:
        close = _close(mine.weight_g, cand.weight_g)
        if mine.words == cand.words:
            return close or _by_weight(mine, cand), score, "words"
        # Вложение, где лишние слова — только описание обработки: тот же товар, если
        # фасовка та же. Всё прочее вложение — слабая связь.
        small, big = sorted((mine.fwords, cand.fwords), key=len)
        if close and small < big and (big - small) <= DESCRIPTORS:
            return True, 0.9, "words"
        return False, score, "translit" if mine.fwords == cand.fwords else "inclusion"
    if matching.compatible(mine, cand):
        level = _abbrev(mine, cand)
        if level == 2:
            return _close(mine.weight_g, cand.weight_g), 0.8, "abbrev"
        if level == 1:
            return False, 0.75, "abbrev"
    return None


def _strong(verdict) -> bool:
    return bool(verdict and verdict[0])


def _agrees(person: list[matching.Row], cand: matching.Row) -> bool:
    """Строка ничем не спорит ни с одной строкой человека: единица, фасовка, штуки,
    процент, метки, марка."""
    return all(matching.compatible(mine, cand) for mine in person)


def _weight_ok(person: list[matching.Row], cand: matching.Row) -> bool:
    """Фасовка строки в пределах PACK_SAME от фасовки человека, если обе известны."""
    mine = [m.weight_g for m in person if m.weight_g]
    return not mine or not cand.weight_g or any(_close(g, cand.weight_g) for g in mine)


def _conflict(mine: matching.Row, rows: list[matching.Row]) -> bool:
    """Хоть одна строка единого товара НАЗВАЛА то, что спорит с товаром человека.

    Название единого товара — самое подробное из строк, а не самое точное: молчащая
    о проценте строка Магнита держит в одной группе «Сметану 15 %» Ленты, и товар
    15 % не должен стать сильным для чека «20 %» (ревью 18.10.2026).
    """
    for row in rows:
        if mine.pct is not None and row.pct is not None and not matching.percents_agree(mine.pct, row.pct):
            return True
        if row.marks and row.marks != mine.marks:
            return True
        if row.packs > 1 and not matching.same_pack(mine, row):
            return True
    return False


def _item_verdict(mine: matching.Row, cand: matching.Row, rows: list[matching.Row],
                  vocab: frozenset[str]) -> tuple[bool, float, str] | None:
    """Лучший вердикт строки человека для единого товара: по названию и строкам сетей.

    Название единого товара — самое подробное из сетей. Товар человека может точно
    совпасть с более коротким названием одной из них («Молоко Простоквашино 2,5 %
    930 мл» у Магнита) — тогда связь сильная. Но к строкам обращаемся, только если
    сам товар ничем не спорит с человеком.
    """
    if not matching.compatible(mine, cand) or _conflict(mine, rows):
        return None
    verdict = _judge(mine, cand, vocab)
    if verdict is None or not verdict[0]:
        for row in rows:
            alt = _judge(mine, row, vocab)
            if alt and (verdict is None or alt[:2] > verdict[:2]):
                verdict = alt
    return verdict


# ---------- выбор не базой: человек и живой поиск ----------
def _human(mappings: list[dict]) -> list[dict]:
    """Подтверждённые не базой: выбор человека, живой поиск, прежние связи."""
    return [m for m in mappings if m.get("method") != MASTER]


def _same_name(seen: str, row: dict) -> bool:
    """Строка сети — тот же товар, что был под артикулом в момент подтверждения."""
    before = _prep({"id": 1, "chain": "seen", "name": seen})
    now = _prep(row)
    vocab = before.brand_words | now.brand_words
    return matching.same_product(replace(before, bkey=None), replace(now, bkey=None), vocab)[0]


def _disputed(person: list[matching.Row], cand: matching.Row, strict: bool) -> bool:
    """Строки человека возражают выбору: он не тождество, а замена.

    Человек мог согласиться на «клубничный» там, где обычного нет, или на 250 г вместо
    230 г. Свою сеть такой выбор закрывает, но доводом для других сетей не служит:
    иначе замена расходилась бы по всем корзинам (ревью 18.10.2026). Возражение — спор
    признаков (процент, штуки, метки, марка), фасовка дальше 3 % или лишнее слово
    вкуса и сорта. strict — снимка названия нет: тогда возражает и строка, которая с
    выбором пересекается словами, но ни в чём с ним не сходится.
    """
    for mine in person:
        if not matching.compatible(mine, cand):
            return True
        if mine.weight_g and cand.weight_g and not _close(mine.weight_g, cand.weight_g):
            return True
        if mine.fwords < cand.fwords and (cand.fwords - mine.fwords - cand.fbrand
                                          - DESCRIPTORS - SORT_WORDS):
            return True
    if strict and person:
        own = cand.fwords - cand.fbrand
        if any(mine.fwords & own for mine in person) and all(
                _judge(mine, cand, cand.brand_words) is None for mine in person):
            return True
    return False


def _choices(product, mappings: list[dict], person: list[matching.Row], conn) -> list[dict]:
    """Настоящие артикулы, подтверждённые не базой, с их живой строкой и вердиктом.

    trusted — выбор годится доводом для других сетей: сеть не отдала артикул другому
    товару (название в базе то же, что было при подтверждении) и строки человека
    выбору не возражают (_disputed). Выбор без строки в базе доводом-мостом не бывает,
    но его штрихкод служит.
    """
    picks = [m for m in _human(mappings) if _real(m["store_code"], m["sku"])]
    if not picks:
        return []
    live = {(r["chain"], str(r["sku"])): r for r in
            store.rows_by_skus([(m["store_code"], m["sku"]) for m in picks], conn)}
    out: list[dict] = []
    for m in picks:
        row = live.get((m["store_code"], str(m["sku"])))
        trusted = True
        if row is not None:
            seen = m.get("seen_name")
            if seen and not _same_name(seen, row):
                trusted = False          # сеть отдала артикул другому товару
            elif _disputed(person, _prep(row), strict=not seen):
                trusted = False
        out.append({**m, "row": row, "trusted": trusted})
    return out


def _codes(product, mappings: list[dict], choices: list[dict]) -> set[str]:
    """Доверенные штрихкоды товара: его собственный и у выборов не базой, кроме спорных."""
    spoiled = {m["store_product_id"] for m in choices if not m["trusted"]}
    raw = [product.barcode] + [m.get("ean") for m in _human(mappings)
                               if m["store_product_id"] not in spoiled]
    return {c for c in (_trusted(x) for x in raw) if c}


def _pairs(choices: list[dict]) -> list[tuple[str, str]]:
    """Мост: выборы не базой, годные доводом и живые в базе."""
    return [(m["store_code"], str(m["sku"])) for m in choices if m["trusted"] and m["row"]]


def _own_items(product, conn) -> set[int]:
    """Единые товары с собственным доверенным штрихкодом товара — он главнее моста."""
    code = _trusted(product.barcode)
    return {int(r["item_id"]) for r in store.rows_by_gtin({code}, conn)} if code else set()


def _rows_of_item(item_id: int, conn, cache: dict | None = None) -> list[dict]:
    if cache is not None and item_id in cache:
        return cache[item_id]
    rows = store.item_rows(item_id, conn)[:ROWS_LIMIT]
    if cache is not None:
        cache[item_id] = rows
    return rows


def _exists(item_id: int, conn) -> bool:
    return conn.execute("SELECT 1 FROM catalog_items WHERE id=?", (int(item_id),)).fetchone() is not None


def _retired_items(person: list[matching.Row], conn, pairs=(), codes=()) -> set[int]:
    """Единые товары, на которые указывают якоря, на ночь выпавшие из обхода.

    Якорь (артикул человека, штрихкод) снят с витрины — это не повод рвать связь
    (ревью 18.10.2026). Номер, который снятая строка помнит, берётся, только если он
    жив и хоть одна живая строка товара сильно сходится со снятой: пока строка спала,
    её номер мог уйти к другому товару.
    """
    out: set[int] = set()
    for row in store.retired_rows(pairs, codes, conn):
        alive = store.resolve_alias(row["number"], conn)
        if alive is None or alive in out or not _exists(alive, conn):
            continue
        before = _prep(row)
        if person and _disputed(person, before, strict=False):
            continue
        rows = [_prep(r) for r in _rows_of_item(alive, conn)]
        vocab = matching.brand_vocabulary(rows + [before])
        if any(_strong(_judge(before, r, vocab)) for r in rows):
            out.add(alive)
    return out


def _anchored(product, mappings: list[dict], choices: list[dict], person, conn
              ) -> tuple[set[int], set[int]]:
    """Единые товары, на которые указывают штрихкоды и мост товара человека."""
    codes = _codes(product, mappings, choices)
    by_code = {int(r["item_id"]) for r in store.rows_by_gtin(codes, conn)} if codes else set()
    by_sku = {int(m["row"]["item_id"]) for m in choices if m["trusted"] and m["row"]}
    if codes and not by_code:
        by_code = _retired_items(person, conn, codes=codes)
    if not by_sku:
        dormant = [(m["store_code"], str(m["sku"])) for m in choices if m["trusted"] and not m["row"]]
        if dormant:
            by_sku = _retired_items(person, conn, pairs=dormant)
    return by_code, by_sku


def _weaken_on_codes(found: Found, codes: set[str], conn) -> Found:
    """Спор настоящих штрихкодов делает связь по словам слабой (ревью 18.10.2026).

    У товара человека свой настоящий код, у строк единого товара — свои, и ни один
    не совпал: для same_product это разные товары. Строго отказать нельзя — у
    одного товара бывает несколько GTIN, а сборщик METRO хранит только первый.
    Поэтому артикулы ложатся кандидатами и ждут подтверждения на «Связях».
    """
    if not found.strong or not codes:
        return found
    theirs = {c for c in (_trusted(r.get("barcode")) for r in _rows_of_item(found.item_id, conn)) if c}
    if theirs and not (theirs & codes):
        return replace(found, strong=False)
    return found


def find_master(product, raw_names: list[str], mappings: list[dict], conn,
                person: list[matching.Row] | None = None) -> Found | str | None:
    """Единый товар для товара человека. DOUBTFUL — есть, но не однозначно; None — нет."""
    person = _rows_of_person(product, raw_names) if person is None else person
    own = _own_items(product, conn)
    if len(own) == 1:
        return Found(next(iter(own)), "barcode", 1.0, True)
    if len(own) > 1:
        return DOUBTFUL
    choices = _choices(product, mappings, person, conn)
    by_code, by_sku = _anchored(product, mappings, choices, person, conn)
    if len(by_code) > 1 or len(by_sku) > 1 or (by_code and by_sku and by_code != by_sku):
        return DOUBTFUL
    if by_code:
        return Found(next(iter(by_code)), "barcode", 1.0, True)
    if by_sku:
        return Found(next(iter(by_sku)), "bridge", 0.95, True)
    found = _by_words(product, raw_names, conn, person)
    if isinstance(found, Found):
        found = _weaken_on_codes(found, _codes(product, mappings, choices), conn)
    return found


def _by_words(product, raw_names: list[str], conn,
              person: list[matching.Row] | None = None) -> Found | str | None:
    person = _rows_of_person(product, raw_names) if person is None else person
    best: dict[int, tuple[bool, float, str]] = {}
    cache: dict[int, list[dict]] = {}
    prepared: dict[int, list[matching.Row]] = {}
    searched: dict[tuple, tuple[dict, frozenset]] = {}
    for mine in person:
        terms = list(mine.forder) + sorted(set(mine.fwords) - set(mine.forder))
        query = " ".join(mine.order[:2]) if mine.order else ""
        key = (tuple(store.folded_terms(terms)), query)
        if key not in searched:          # строки чека одного товара ищут одно и то же
            found = {int(c["id"]): c for c in store.search_folded(list(key[0]), 80, conn)}
            if query:
                for c in store.search_items(query, limit=40, conn=conn):
                    found.setdefault(int(c["id"]), c)
            cands = {cid: _prep({**c, "id": cid, "chain": "catalog"}) for cid, c in found.items()}
            searched[key] = (cands, matching.brand_vocabulary(list(cands.values())))
        cands, vocab = searched[key]
        for cid, cand in cands.items():
            if not matching.compatible(mine, cand):
                continue
            if cid not in prepared:
                prepared[cid] = [_prep(r) for r in _rows_of_item(cid, conn, cache)]
            verdict = _item_verdict(mine, cand, prepared[cid], vocab)
            if verdict and (cid not in best or verdict[:2] > best[cid][:2]):
                best[cid] = verdict
    if not best:
        return None
    ranked = sorted(best.items(), key=lambda kv: kv[1][:2], reverse=True)
    top_id, (strong, score, method) = ranked[0]
    if len(ranked) > 1 and ranked[1][1][:2] == (strong, score):
        return DOUBTFUL
    return Found(top_id, method, score, strong)


def _verdict_for_item(product, raw_names: list[str], item_id: int, conn,
                      person: list[matching.Row] | None = None) -> tuple[bool, float, str] | None:
    """Сходится ли товар человека с этим единым товаром — для сверки привязки."""
    row = conn.execute("SELECT * FROM catalog_items WHERE id=?", (int(item_id),)).fetchone()
    if row is None:
        return None
    person = _rows_of_person(product, raw_names) if person is None else person
    cand = _prep({**dict(row), "id": int(item_id), "chain": "catalog"})
    rows = [_prep(r) for r in _rows_of_item(int(item_id), conn)]
    vocab = matching.brand_vocabulary(rows + [cand])
    best = None
    for mine in person:
        verdict = _item_verdict(mine, cand, rows, vocab)
        if verdict and (best is None or verdict[:2] > best[:2]):
            best = verdict
    return best


# ---------- запись в базу человека ----------
@dataclass
class _Evidence:
    """Чем товар человека подтверждает строки сетей единого товара."""
    codes: set[str]                       # его настоящие штрихкоды
    person: list[matching.Row]            # строки чека и эталон
    prepared: dict[int, matching.Row]     # строки единого товара в виде для сравнения
    vocab: frozenset[str]
    strong_ids: set[int]                  # строки, с которыми человек сходится сильно сам
    anchors: list[matching.Row]           # через них товар найден: мост, штрихкод, слова
    anchor_codes: set[str]


def _evidence(product, item_id: int, mappings: list[dict], rows: list[dict],
              all_rows: list[dict], conn) -> _Evidence:
    person = _rows_of_person(product, repo.raw_names(product.id))
    choices = _choices(product, mappings, person, conn)
    codes = _codes(product, mappings, choices)
    prepared = {int(r["id"]): _prep(r) for r in rows}
    # Словарь марок — по всем строкам товара, и тех сетей, которых нет в справочнике
    # магазинов: это строки самого товара (ревью 18.10.2026).
    vocab = matching.brand_vocabulary([prepared.get(int(r["id"])) or _prep(r) for r in all_rows])
    strong_ids: set[int] = set()
    if person:
        for rid, cand in prepared.items():
            if _agrees(person, cand) and any(_strong(_judge(mine, cand, vocab)) for mine in person):
                strong_ids.add(rid)
    bridge = set(_pairs(choices))
    anchors: list[matching.Row] = []
    for r in rows:
        rid = int(r["id"])
        code = _trusted(r.get("barcode"))
        if rid in strong_ids or (r["chain"], str(r["sku"])) in bridge or (code and code in codes):
            anchors.append(prepared[rid])
    # Якорь, на ночь выпавший из обхода, ручается за этот товар, как и раньше.
    dormant = [(m["store_code"], str(m["sku"])) for m in choices if m["trusted"] and not m["row"]]
    for r in store.retired_rows(dormant, codes, conn):
        if store.resolve_alias(r["number"], conn) == int(item_id):
            before = _prep(r)
            if not (person and _disputed(person, before, strict=False)):
                anchors.append(before)
    anchor_codes = {c for c in (_trusted(a.barcode) for a in anchors) if c}
    return _Evidence(codes, person, prepared, vocab, strong_ids, anchors, anchor_codes)


def _row_ok(row: dict, ev: _Evidence) -> bool:
    """Можно ли подтвердить строку сети, пришедшую через единый товар.

    Её настоящий штрихкод решает сам: свой — да, чужой — нет. Без него строка должна
    сама сильно сходиться с товаром человека или с якорем — и при этом ничем не
    спорить ни с одной строкой человека, а фасовка — быть в пределах 3 % от его.
    Связь строки в самой базе (link_method) здесь не довод: это первое удачное
    сравнение с любым соседом по группе.
    """
    code = _trusted(row.get("barcode"))
    if code and ev.codes:
        return code in ev.codes
    if code and ev.anchor_codes and code not in ev.anchor_codes:
        return False                     # у якоря свой настоящий код, у строки — другой
    if not ev.person:
        return False
    rid = int(row["id"])
    if rid in ev.strong_ids:
        return True
    cand = ev.prepared.get(rid)
    if cand is None or not _agrees(ev.person, cand) or not _weight_ok(ev.person, cand):
        return False
    if code and code in ev.anchor_codes:
        return True
    return any(_strong(_judge(anchor, cand, ev.vocab)) for anchor in ev.anchors
               if anchor is not cand)


def _pick(rows: list[dict], ev: _Evidence, oks: dict[int, bool], ref_g: float | None) -> dict:
    """Одна строка сети из нескольких у единого товара: сначала прошедшая проверку,
    и из них — сошедшаяся с человеком сама, а не через якорь."""
    def key(r: dict):
        rid = int(r["id"])
        weight = r.get("weight_g") or matching.parse_weight(r.get("name") or "")[0]
        gap = abs(weight - ref_g) / max(weight, ref_g) if (weight and ref_g) else 9.0
        code = _trusted(r.get("barcode"))
        return (0 if oks.get(rid) else 1, 0 if (rid in ev.strong_ids or (code and code in ev.codes)) else 1,
                gap, -(r.get("link_score") or 0.0), 0 if r.get("in_stock") != 0 else 1,
                0 if r.get("price") is not None else 1, str(r.get("sku")))
    return min(rows, key=key)


def _price(sp_id: int, row: dict) -> None:
    """Цена строки для точки человека — снимком, если такой ещё нет."""
    from app.catalog.refresh import price_for_me

    found = price_for_me(row)
    if found is None:
        return
    price, in_stock, seen = found
    last = repo.latest_price(sp_id)
    if last and last.get("price") == price and last.get("fetched_at") == seen:
        return
    per_kg = price if (row.get("unit") or "").lower() == "kg" else None
    repo.save_price(sp_id, price, price_per_kg=per_kg, in_stock=in_stock, fetched_at=seen)


def materialize(product, item_id: int, strong: bool, conn=None
                ) -> tuple[dict[str, str], dict[str, str]]:
    """Артикулы сетей единого товара — в базу человека.

    Возвращает ({сеть: подтверждённый артикул}, {сеть: артикул-кандидат}). В каждой
    сети — одна строка: подтверждённая, если связь сильная и строка прошла _row_ok,
    иначе кандидатом. Сеть, где у товара больше нет живой строки (снята с витрины,
    ушла в другой товар), теряет подтверждение базы: в наряд мёртвый артикул не
    пойдёт. Прежние кандидаты базы в сети, кроме нынешнего, убираются.
    """
    own = conn is None
    conn = conn or store.connect()
    try:
        stores = {s.code: s for s in repo.list_stores()}
        all_rows = store.item_rows(item_id, conn)
        rows_all = [r for r in all_rows if r["chain"] in stores]
        by_chain: dict[str, list[dict]] = defaultdict(list)
        for row in rows_all:
            by_chain[row["chain"]].append(row)
        mappings = repo.product_mappings(product.id)
        ev = _evidence(product, item_id, mappings, rows_all, all_rows, conn)
        canon = conn.execute("SELECT weight_g FROM catalog_items WHERE id=?", (int(item_id),)).fetchone()
        ref_g = product.weight_g or (canon["weight_g"] if canon else None)
        confirmed: dict[str, str] = {}
        pending: dict[str, str] = {}
        for chain, shop in stores.items():
            here = [m for m in mappings if m["store_code"] == chain]
            if any(_real(chain, m["sku"]) and m.get("method") != MASTER for m in here):
                continue                     # выбор человека, живой поиск или прежняя связь
            if repo.chain_rejected(product.id, shop.id):
                continue                     # человек убрал связь с этой сетью
            ours = [m for m in here if m.get("method") == MASTER]
            rows = by_chain.get(chain) or []
            if not rows:
                for old in ours:
                    repo.unconfirm_mapping(product.id, old["store_product_id"])
                repo.drop_base_candidates(product.id, shop.id)
                continue
            oks = {int(r["id"]): _row_ok(r, ev) for r in rows}
            row = _pick(rows, ev, oks, ref_g)
            confirm = strong and oks[int(row["id"])]
            same = next((m for m in ours if str(m["sku"]) == str(row["sku"])), None)
            if same is not None and confirm:
                sp_id = same["store_product_id"]   # уже лежит и подтверждён: дата связи не двигается
            else:
                sp_id = repo.upsert_store_product(shop.id, str(row["sku"]),
                                                  row.get("name") or product.name,
                                                  weight_g=row.get("weight_g"), unit=row.get("unit"),
                                                  url=row.get("url"), ean=row.get("barcode"),
                                                  keep_known=True)
                repo.confirm_mapping(product.id, sp_id, confirmed=confirm, method=MASTER)
            for old in ours:                  # прежний артикул базы в этой сети уступает место
                if old["store_product_id"] != sp_id:
                    repo.unconfirm_mapping(product.id, old["store_product_id"])
            repo.drop_base_candidates(product.id, shop.id, keep=sp_id)
            _price(sp_id, row)
            (confirmed if confirm else pending)[chain] = str(row["sku"])
        return confirmed, pending
    finally:
        if own:
            conn.close()


def _unlink(product) -> None:
    """Снять привязку и всё, что база записала этому товару: правила больше не сходятся."""
    repo.drop_base_links(product.id)
    repo.set_master(product.id, None, None)


# ---------- проходы ----------
_SEEN: dict[tuple, str | None] = {}  # (база, товар, поколение, доводы) → DOUBTFUL или None
_SYNCED: dict[tuple, bool] = {}      # (база, товар, поколение, доводы) — привязанный сверен
_WEAK: dict[tuple, bool] = {}        # то же — слабую связь в этом поколении уже искали заново
_GEN: dict[str, int] = {}            # поколение, под которое заведены записи памяти базы
_STATE_LOCK = threading.Lock()


def _generation(conn) -> int:
    row = conn.execute("SELECT MAX(id) AS g FROM match_runs WHERE finished_at IS NOT NULL").fetchone()
    return int(row["g"] or 0)


def _prune(db_key: str, generation: int) -> None:
    """Записи памяти прошлых поколений больше не читаются — убрать их."""
    with _STATE_LOCK:
        if _GEN.get(db_key) == generation:
            return
        for memo in (_SEEN, _SYNCED, _WEAK):
            for key in [k for k in memo if k[0] == db_key and k[2] != generation]:
                del memo[key]
        _GEN[db_key] = generation


def _evidence_key(product, raw_names: list[str], mappings: list[dict]) -> tuple:
    """По чему идёт поиск: изменилось хоть что-то — товар ищется снова, не дожидаясь пересборки."""
    picks = sorted((m["store_code"], str(m["sku"])) for m in _human(mappings)
                   if _real(m["store_code"], m["sku"]))
    return (product.name, product.barcode, product.weight_g, product.unit, tuple(raw_names),
            tuple(picks))


def _days_ago(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="microseconds")


def _anchor_agrees(person: list[matching.Row], item_id: int, conn) -> bool:
    """У товара, куда ведут якоря, есть строка, похожая на товар человека."""
    if not person:
        return True
    for row in (_prep(r) for r in _rows_of_item(item_id, conn)):
        if _agrees(person, row) and any(m.fwords <= row.fwords or row.fwords <= m.fwords
                                        for m in person):
            return True
    return False


def _wake(product, alive: int, method: str, weak: bool, raw_names: list[str],
          mappings: list[dict], person: list[matching.Row], conn) -> Found | str | None:
    """Привязка к спящему номеру: держать, пока товар не нашёлся живым.

    Спящий номер бережёт связь на ночь вне обхода. Но если якоря (штрихкод, мост)
    ведут к живому товару или слова сильно находят живой, связь переходит туда: сеть
    могла перевыложить товар под новым артикулом (ревью 18.10.2026). Соседняя фасовка
    сильной не бывает и номер не уводит. Спит дольше SLEEP_DAYS — ищется как обычно.
    """
    since = store.asleep_since(alive, conn)
    stale = bool(since) and since < _days_ago(SLEEP_DAYS)
    choices = _choices(product, mappings, person, conn)
    by_code, by_sku = _anchored(product, mappings, choices, person, conn)
    targets = by_code | by_sku
    if targets:
        if len(targets) == 1 and _anchor_agrees(person, next(iter(targets)), conn):
            target = next(iter(targets))
            return (Found(target, "barcode", 1.0, True) if by_code
                    else Found(target, "bridge", 0.95, True))
    else:
        found = _by_words(product, raw_names, conn, person)
        if isinstance(found, Found):
            found = _weaken_on_codes(found, _codes(product, mappings, choices), conn)
            if found.strong:
                return found
    if stale:
        return find_master(product, raw_names, mappings, conn, person)
    return Found(alive, method or "words", 1.0, not weak)


def _recheck(product, current: dict, raw_names: list[str], mappings: list[dict], conn,
             person: list[matching.Row], search: bool = True) -> Found | str | None:
    """Сверить привязку с нынешней базой. DOUBTFUL или None — привязка больше не держится.

    Собственный штрихкод товара держит связь сам. Номер ведётся по переадресациям
    базы; найденный товар сверяется с товаром человека: штрихкод и мост должны вести к
    нему же, а слова — сходиться (номер мог уйти к группе, которая стала другим
    товаром). Слабая связь ищется заново (search): база растёт, и точная строка могла
    появиться.
    """
    method = (current.get("method") or "").rstrip("?")
    weak = (current.get("method") or "").endswith("?")
    alive = store.resolve_alias(current["master_id"], conn)
    if alive is not None and alive in _own_items(product, conn):
        return Found(alive, "barcode", 1.0, True)
    found: Found | None = None
    if alive is not None:
        if not _exists(alive, conn):
            return _wake(product, alive, method, weak, raw_names, mappings, person, conn)
        choices = _choices(product, mappings, person, conn)
        by_code, by_sku = _anchored(product, mappings, choices, person, conn)
        if (by_code and alive not in by_code) or (by_sku and alive not in by_sku):
            found = None
        elif by_code:
            found = Found(alive, "barcode", 1.0, True)
        elif by_sku:
            found = Found(alive, "bridge", 0.95, True)
        else:
            verdict = _verdict_for_item(product, raw_names, alive, conn, person)
            if verdict is not None:
                strong, score, how = verdict
                found = _weaken_on_codes(Found(alive, how, score, strong),
                                         _codes(product, mappings, choices), conn)
    if found is not None and (found.strong or not search):
        return found
    fresh = find_master(product, raw_names, mappings, conn, person)
    if isinstance(fresh, Found) and (fresh.strong or found is None):
        return fresh
    if found is not None:
        return found
    return fresh


def _link_one(product, conn, generation: int, db_key: str, report: Report, *,
              force: bool, sync: bool, write: bool) -> None:
    current = repo.master_of(product.id)
    raw_names = repo.raw_names(product.id)
    mappings = repo.product_mappings(product.id)
    key = (db_key, product.id, generation, _evidence_key(product, raw_names, mappings))
    if current and not force and not sync:
        with _STATE_LOCK:
            done = _SYNCED.get(key)
        if done:
            report.count(Found(current["master_id"], current.get("method") or "",
                               1.0, not (current.get("method") or "").endswith("?")))
            return
    person = _rows_of_person(product, raw_names)
    if current and not force:
        with _STATE_LOCK:
            search = not _WEAK.get(key)
        found = _recheck(product, current, raw_names, mappings, conn, person, search=search)
        if (current.get("method") or "").endswith("?") and write:
            with _STATE_LOCK:
                _WEAK[key] = True
        if not isinstance(found, Found):
            if write:
                _unlink(product)
            report.missed(found)
            return
    else:
        with _STATE_LOCK:
            seen = key in _SEEN and not force
            result = _SEEN.get(key)
        if seen:
            report.missed(result)
            return
        result = find_master(product, raw_names, mappings, conn, person)
        if not isinstance(result, Found):
            with _STATE_LOCK:
                _SEEN[key] = result
            if write and current:          # --force: правила больше не находят прежний товар
                _unlink(product)
            report.missed(result)
            return
        found = result
    if not write:
        report.count(found)
        return
    confirmed, pending = materialize(product, found.item_id, found.strong, conn=conn)
    # Сильная связь, не подтвердившая ни одной сети, честно записывается слабой: в
    # корзину по ней ничего не уедет, а кандидаты ждут человека на «Связях».
    if found.strong and not confirmed and pending and not any(
            _real(m["store_code"], m["sku"]) for m in _human(mappings)):
        found = replace(found, strong=False)
    # Номер пишется ПОСЛЕ артикулов: сбой посреди записи не оставит товар «привязанным»
    # без единого артикула, который фоновые проходы потом не трогали бы.
    if not current or current["master_id"] != found.item_id or current.get("method") != found.mark:
        repo.set_master(product.id, found.item_id, found.mark)
    with _STATE_LOCK:
        _SYNCED[key] = True
    report.count(found)
    for chain in confirmed:
        report.chains[chain] += 1


def link_products(product_ids: list[int] | None = None, *, force: bool = False,
                  sync: bool = False, write: bool = True) -> Report:
    """Сопоставить товары текущего человека с единой базой и разложить артикулы.

    product_ids=None — все активные товары. Новый товар ищется (find_master).
    Привязанный — сверяется с базой (_recheck) один раз на её поколение и на набор
    доводов: после пересборки номера проверяются по переадресациям, артикулы
    перекладываются, слабые связи ищутся заново. sync=True — сверить привязанных сейчас,
    даже если в этом поколении уже сверяли (кнопка на «Связях», --sync). force=True —
    искать всё заново. write=False — только посчитать, что нашлось бы.
    """
    from app import config

    report = Report()
    store.init()
    wanted = set(product_ids) if product_ids is not None else None
    products = [p for p in repo.list_products() if wanted is None or p.id in wanted]
    db_key = config.db_path()
    with store.connect() as conn:
        generation = _generation(conn)
        _prune(db_key, generation)
        for product in products:
            report.tried += 1
            try:
                _link_one(product, conn, generation, db_key, report,
                          force=force, sync=sync, write=write)
            except Exception:  # noqa: BLE001 — один товар не должен обрывать проход
                report.failed += 1
                log.exception("единая база: товар %s сопоставить не удалось", product.id)
    log.info("единая база: товаров %s, привязано %s (сильно %s, слабо %s), сомнительно %s,"
             " не найдено %s, сбоев %s", report.tried, report.linked, report.strong, report.weak,
             report.doubtful, report.unknown, report.failed)
    return report


_RUNNING: set[str] = set()
_AGAIN: dict[str, bool] = {}         # звали, пока шёл проход: пройти ещё раз (с sync или без)
_RUNNING_LOCK = threading.Lock()


def running(phone: str | None) -> bool:
    """Идёт ли сейчас фоновый проход этого рабочего места."""
    with _RUNNING_LOCK:
        return bool(phone) and phone in _RUNNING


def link_soon(phone: str | None, sync: bool = False) -> bool:
    """Фоном сопоставить товары рабочего места с единой базой.

    Зовут загрузка чеков, вход человека и кнопка «Связать с каталогом», если проход
    уже идёт. Один поток на рабочее место. Вызов, пришедший во время прохода, не
    теряется: проход повторится со свежим списком товаров, а с sync — со сверкой
    привязанных (пачки чеков ФНС приходят каждые несколько секунд, а первый проход идёт
    десятки — ревью 18.10.2026). Возвращает, запущен ли новый поток.
    """
    from app import config

    if not phone or not config.get("catalog.autolink", True):
        return False
    with _RUNNING_LOCK:
        if phone in _RUNNING:
            _AGAIN[phone] = _AGAIN.get(phone, False) or sync
            return False
        _RUNNING.add(phone)

    def work() -> None:
        from app import users

        released = False
        mode = sync
        try:
            users.open_workspace(phone)
            while True:
                link_products(sync=mode)
                with _RUNNING_LOCK:
                    if phone not in _AGAIN:
                        # Метку снимаем в том же захвате замка, где решили выйти: иначе
                        # новый вызов между проверкой и снятием потерялся бы.
                        _RUNNING.discard(phone)
                        released = True
                        return
                    mode = _AGAIN.pop(phone)
        except Exception:  # noqa: BLE001 — фоновая работа не должна ронять сервер
            log.exception("единая база: сопоставление рабочего места не удалось")
        finally:
            try:
                users.deactivate()
            finally:
                if not released:
                    with _RUNNING_LOCK:
                        _RUNNING.discard(phone)
                        _AGAIN.pop(phone, None)

    threading.Thread(target=work, name=f"catalog-link-{phone[-4:]}", daemon=True).start()
    return True


def _masked(phone: str) -> str:
    return phone if len(phone) < 8 else f"{phone[:3]}***{phone[-4:]}"


def main(argv: list[str] | None = None) -> int:
    from app import users

    parser = argparse.ArgumentParser(description="Сопоставить товары людей с единой базой.")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--all", action="store_true", help="все рабочие места")
    who.add_argument("--phone", help="одно рабочее место")
    parser.add_argument("--sync", action="store_true",
                        help="сверить привязанных с базой сейчас, не дожидаясь входа")
    parser.add_argument("--force", action="store_true", help="искать заново и привязанные")
    parser.add_argument("--dry-run", action="store_true", help="ничего не записывать")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    phones = users.list_workspaces() if args.all else [args.phone]
    for phone in phones:
        try:
            users.open_workspace(phone)
            report = link_products(force=args.force, sync=args.sync, write=not args.dry_run)
            chains = ", ".join(f"{k} {v}" for k, v in sorted(report.chains.items())) or "—"
            print(f"{_masked(phone)}: товаров {report.tried}, привязано {report.linked}"
                  f" (сильно {report.strong}, слабо {report.weak}), сомнительно {report.doubtful},"
                  f" не найдено {report.unknown}, сбоев {report.failed};"
                  f" подтверждено артикулов по сетям: {chains}")
        finally:
            users.deactivate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
