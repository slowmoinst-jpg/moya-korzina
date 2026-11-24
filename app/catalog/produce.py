"""Овощи и фрукты без бренда: в каждой сети — самый дешёвый на развес вариант своего вида.

ЗАЧЕМ (решение владельца 15.11.2026). Чек пишет «Морковь весовая 1кг», «Картофель белый грязный 1.5 кг»,
«Огурцы короткоплодные» — это вид товара, а не карточка. Сопоставление по названию клало в сеть первую
найденную карточку: у Магнита и Дикси это была «Морковь мытая 1кг» по 83 и 60 ₽ без наличия, и морковь в
корзине серела, хотя весовая «Морковь» лежала там по 29,4 и 25,99 ₽/кг. Владелец: на развес, самый
дешёвый в пересчёте на килограмм; «нет такого товара» не показывать, а сразу предлагать этот вариант.

ВИД. Корень (морковь, картофель, лук, огурцы…) и признаки, которые делают вид другим. Какие признаки жёсткие,
решает корень (_HARD): у картофеля — цвет, мытый или нет, молодой; у лука — цвет; у яблок, томатов, капусты,
груш, перца — названный сорт, и цвет у них часть сорта («красный перец» — не «грунтовой»); у моркови, огурцов,
свёклы, бананов, лимонов — никакие (мытую и немытую морковь, любые огурцы на развес владелец считает одним
видом). Кто молчит о жёстком признаке, тот обычный — и товар человека, и строка сети: картофель без слов —
белый, немытый, не молодой; лук — жёлтый. «Картофель» не берёт ни красный, ни мытый: это разные товары.
«Помидоры» и «Томаты», «Лимон» и «Лимоны» — один корень (_CANON).

НЕ ВИД. Переработанное (_BLOCK: маринованное, замороженное, по-корейски, пюре, салат, чипсы, очищенное, нарезка)
и товар человека с маркой или с неопознанным словом у корня без сортов («Огурцы Атлет», «Морковь Белая дача
сочная»): это конкретная карточка, её ищет обычное сопоставление (link.materialize).

СТРОКА. Живая строка каталога сети на развес (link._per_kg), в наличии, с ценой ТОЧКИ человека
(refresh.price_for_me); дешевле за килограмм, при равенстве — меньший артикул. Фасованное в расчёт не идёт:
в корзину весового товара его не переведёшь, а мешок в 10 кг за килограмм дешевле, но человек купит 3 кг.
Стоящую связь заменяем, только если она хуже: её нет в каталоге, нет цены или наличия, или она дороже за
килограмм больше чем на SWITCH_MARGIN (на копейках связь не гоняем). Выбор человека, который уже дешевле,
остаётся; выбор, который дороже, перебивается и сразу после действия человека: закрепления нет, а убрать сеть
у товара можно (link_rejects, такая сеть не трогается). Связь пишется с методом PRODUCE: следующий проход
перевыберет её, а когда подходящей строки не стало, снимет и тут же вернёт сети обычное сопоставление.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from app import freshness, repo
from app.catalog import match as matching
from app.catalog import store
from app.catalog.link import PRODUCE, _generation, _per_kg, _prep, _price, materialize

log = logging.getLogger(__name__)

PACK_MIN_G = 1000       # штучный товар легче килограмма («Огурцы 500 г») на вес не переводится: касания целые
FRESH_HOURS = 96        # цена каталога старше четырёх суток — не оценка (ночной обход свежее; дозор цен доведёт остальное)
SWITCH_MARGIN = 0.03    # стоящую связь меняем, если новая дешевле больше чем на 3 %: иначе артикул прыгал бы с каждой
#                         сменой цен, а добор корзины (по артикулам, что уже легли) клал бы второй


def _set(*words: str) -> frozenset[str]:
    return frozenset(matching.fold(w) for w in words)


def _forms(*stems: str) -> frozenset[str]:
    """Основы прилагательных во всех родах и падежах, в своде письма."""
    return _set(*(s + e for s in stems for e in ("ый", "ая", "ое", "ые", "ого", "ой", "ий")))


# Корень (слово по своду письма) → что у вида жёстко. «Картофельные чипсы» — другое слово, корнем не бывает.
_HARD: dict[str, frozenset[str]] = {
    **dict.fromkeys(_set("морковь", "свекла", "огурцы", "огурец", "бананы", "банан", "лимоны", "лимон", "апельсины",
                         "апельсин", "мандарины", "мандарин"), frozenset()),
    **dict.fromkeys(_set("картофель", "картошка"), frozenset({"color", "wash", "age"})),
    **dict.fromkeys(_set("лук"), frozenset({"color"})),
    **dict.fromkeys(_set("капуста", "помидоры", "помидор", "томаты", "томат", "яблоки", "яблоко", "груши", "груша",
                         "перец"), frozenset({"kind"})),
}
_KIND_ROOTS = frozenset(k for k, v in _HARD.items() if "kind" in v)   # сорт — отдельный вид
# Одна полка под разными словами (в каталоге весовые лимоны чаще «Лимон», чем «Лимоны»): слово чека → корень вида.
_CANON = {matching.fold(a): matching.fold(b) for a, b in (
    ("огурец", "огурцы"), ("банан", "бананы"), ("лимон", "лимоны"), ("апельсин", "апельсины"),
    ("мандарин", "мандарины"), ("яблоко", "яблоки"), ("груша", "груши"), ("помидор", "томаты"),
    ("помидоры", "томаты"), ("томат", "томаты"), ("картошка", "картофель"))}
_PLAIN = _set("лук")                                      # «Лук-порей», «Лук-шалот» — другой товар: лишних слов нет
_DEFAULT_COLOR = {**dict.fromkeys(_set("картофель", "картошка"), "бел"), **dict.fromkeys(_set("лук"), "жёлт")}
_DEFAULT_KIND = {matching.fold("капуста"): matching.fold("белокочанная")}   # «Капуста» без слов — белокочанная

_COLOR = {matching.fold(stem + end): canon
          for canon, stems in (("бел", ("бел",)), ("красн", ("красн",)), ("розов", ("розов",)),
                               ("жёлт", ("жёлт", "желт")), ("зелён", ("зелён", "зелен")),
                               ("чёрн", ("чёрн", "черн")), ("фиолетов", ("фиолетов",)), ("оранжев", ("оранжев",)))
          for stem in stems for end in ("ый", "ая", "ое", "ые", "ого", "ой", "ий")}
_WASHED = _forms("мыт")
_UNWASHED = _forms("немыт", "грязн", "необработан")
_YOUNG = _forms("молод", "ранн", "нов")
# Слова, которые вид не меняют: фасовка, свежесть, размер, страна.
_SAFE = _set("фасованный", "фасованная", "фасованное", "упаковке", "упаковка", "сетке", "сетка", "пакете", "пакет",
             "свежий", "свежая", "свежие", "отборный", "отборная", "отборные", "крупный", "крупная", "крупные",
             "мелкий", "мелкая", "мелкие", "средний", "средняя", "репчатый", "репчатая", "столовая", "столовый",
             "урожай", "урожая", "калибр", "россия", "российский", "отечественный", "белорусский", "беларусь",
             "египет", "египетский", "турция", "турецкий", "китай", "китайский", "азербайджан", "узбекистан", "иран",
             "марокко", "эквадор", "аргентина", "чили", "юар", "испания", "италия", "греция", "польша", "сербия",
             "армения", "грузия", "молдавия", "импортный", "короткоплодные", "короткоплодный", "среднеплодные",
             "длинноплодные", "пупырчатые", "гладкие", "тепличные", "тепличный", "грунтовые", "грунтовый")
# Переработанное и прочее, что на кухню овощем не бывает (по началу слова в своде письма).
_BLOCK = tuple(matching.fold(w) for w in (
    "маринован", "солен", "квашен", "консерв", "заморож", "зам", "отвар", "варен", "жарен", "тушен", "запечен",
    "сушен", "сушён", "вялен", "копчен", "пюре", "сок", "нектар", "соус", "паст", "салат", "чипс", "фри", "снек",
    "соломк", "кубик", "корейск", "ферментир", "крем", "рагу", "смесь", "набор", "семен", "рассад", "фарш",
    "пельмен", "вареник", "суп", "борщ", "очищен", "нарезк", "нарезан", "терт", "протерт", "вакуум", "гриль", "пирог",
    "оладь", "драник", "икра", "лимонад", "напиток", "конфет", "мармелад", "джем", "варенье", "чай", "кофе",
    "корнишон", "чили", "халапен", "остр"))      # острый перец — не болгарский: цена и вкус другие


@dataclass(frozen=True)
class Spec:
    """Вид товара: корень и названные признаки."""
    root: str
    color: str | None
    wash: str | None
    young: bool
    kind: frozenset[str]          # слова сверх признаков и «безопасных»: сорт, подвид


@lru_cache(maxsize=50_000)
def _parse(name: str) -> Spec | None:
    """Вид по названию товара человека или строки сети. None — это не овощ и не фрукт целиком."""
    order = _prep({"id": 0, "chain": "mine", "name": name}, receipt=True).forder
    roots = [w for w in order if w in _HARD]
    # Корень — первое слово: «Биотворог груша», «Йогурт с яблоком» овощем не бывают.
    if len(roots) != 1 or order[0] != roots[0] or any(w.startswith(_BLOCK) for w in order):
        return None
    root = _CANON.get(roots[0], roots[0])
    default = _DEFAULT_KIND.get(root)
    color = wash = None
    young = False
    kind: set[str] = set()
    for w in order:
        if w in _COLOR:
            stem = matching.fold(_COLOR[w])
            if root not in _KIND_ROOTS:
                color = _COLOR[w]
            elif not (default and default.startswith(stem)):     # «белая» капуста — та же белокочанная
                kind.add(stem)                  # у сортовых корней цвет — часть сорта: «красный перец» не «грунтовой»
        elif w in _UNWASHED:
            wash = "немытый"
        elif w in _WASHED:
            wash = "мытый"
        elif w in _YOUNG:
            young = True
        elif w != roots[0] and w not in _SAFE:
            kind.add(w)
    return Spec(root, color, wash, young, frozenset(kind))


def spec_of(product) -> Spec | None:
    """Вид товара человека. С маркой, а у корня без сортов и с лишним словом — это карточка, а не вид.

    Товар должен переводиться в килограммы: весовой, либо штучный с известным весом от килограмма
    («Морковь 1 кг» × 3 — три килограмма; cartplan.build считает так же). Без веса строка на развес
    не получила бы цены (service.line_price: «штука неизвестного веса»).
    """
    if getattr(product, "brand", None) or not (
            product.unit == "kg" or float(product.weight_g or 0) >= PACK_MIN_G):
        return None
    spec = _parse(product.name or "")
    return None if spec is None or (spec.kind and spec.root not in _KIND_ROOTS) else spec


def _same(a: str, b: str) -> bool:
    return a == b or (len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)))


def _color(spec: Spec) -> str | None:
    """Цвет вида. Молчание — обычный цвет корня, но не у строки с лишними словами («Картофель Гала»: цвет неизвестен)."""
    return spec.color or (None if spec.kind else _DEFAULT_COLOR.get(spec.root))


def _fits(spec: Spec, cand: Spec | None) -> bool:
    """Строка сети — тот же вид: корень, жёсткие признаки, переработанного нет. Молчащий о признаке — обычный."""
    if cand is None or cand.root != spec.root or (cand.kind and spec.root in _PLAIN):
        return False
    hard = _HARD[spec.root]
    if "color" in hard and _color(spec) != _color(cand):
        return False
    if "wash" in hard and (spec.wash or "немытый") != (cand.wash or "немытый"):
        return False
    if "age" in hard and cand.young != spec.young:
        return False
    if spec.kind:
        return all(any(_same(k, c) for c in cand.kind) for k in spec.kind)
    default = _DEFAULT_KIND.get(spec.root)
    return not default or not cand.kind or any(_same(default, c) for c in cand.kind)


_IDS: dict[tuple, list[int]] = {}


def _item_ids(root: str, conn) -> list[int]:
    """Единые товары, в чьём названии есть корень (или его синоним, _CANON). Просмотр всей таблицы — десятые доли
    секунды на корень, а таблица меняется только пересборкой (link._generation): ответ помним до следующей."""
    key = (store.path(), _generation(conn), root)
    ids = _IDS.get(key)
    if ids is None:
        forms = [root, *(k for k, v in _CANON.items() if v == root)]
        ids = [int(r[0]) for r in conn.execute(
            "SELECT id FROM catalog_items WHERE " + " OR ".join("fnorm LIKE ?" for _ in forms),
            [f"% {w} %" for w in forms])]
        if len(_IDS) > 200:
            _IDS.clear()
        _IDS[key] = ids              # ответ держим в переменной: чужой поток мог очистить словарь между «есть» и «взять»
    return ids


def _candidates(root: str, conn) -> list[dict]:
    """Живые строки сетей у этих единых товаров. Цены и наличие читаются заново: обход меняет их и без пересборки."""
    ids = _item_ids(root, conn)
    rows: list[dict] = []
    for start in range(0, len(ids), 500):        # SQLite берёт не больше 999 переменных в запросе
        part = ids[start:start + 500]
        rows += [dict(r) for r in conn.execute(
            f"SELECT p.* FROM chain_products p WHERE p.active = 1 AND p.item_id IN ({','.join('?' * len(part))})"
            f"{store._NOT_PLACEHOLDER}", part)]
    return rows


def best_rows(spec: Spec, conn) -> dict[str, tuple[dict, tuple]]:
    """Самая дешёвая за килограмм подходящая весовая строка по каждой сети: {сеть: (строка, (цена, наличие, когда))}."""
    from app.catalog.refresh import price_for_me

    best: dict[str, tuple[dict, tuple]] = {}
    for row in _candidates(spec.root, conn):
        if not _fits(spec, _parse(row["name"] or "")) or not _per_kg(_prep(row)):
            continue
        found = price_for_me(row)
        if found is None or not found[1] or found[0] <= 0 or not freshness.is_fresh(found[2], hours=FRESH_HOURS):
            continue
        have = best.get(row["chain"])
        if have is None or (found[0], str(row["sku"])) < (have[1][0], str(have[0]["sku"])):
            best[row["chain"]] = (row, found)
    return best


def _worse(cur: dict, price: float, chain: str, conn) -> bool:
    """Стоящая связь хуже выбранной строки: её нет в каталоге, нет цены или наличия, или она дороже за килограмм
    больше чем на SWITCH_MARGIN."""
    from app.catalog.refresh import price_for_me

    rows = store.rows_by_skus([(chain, str(cur["sku"]))], conn)
    found = price_for_me(rows[0]) if rows else None
    if found is None or not found[1] or found[0] <= 0:
        return True
    weight = rows[0].get("weight_g")
    mine = found[0] if _per_kg(_prep(rows[0])) else (found[0] / weight * 1000.0 if weight else None)
    return mine is None or mine > price * (1 + SWITCH_MARGIN) + 0.005


def refresh(product, conn) -> dict[str, str]:
    """Выбрать товару человека самую дешёвую строку вида в каждой сети. Возвращает {сеть: артикул} выбранных."""
    spec = spec_of(product)
    if spec is None:
        return {}
    picks = best_rows(spec, conn)
    chosen: dict[str, str] = {}
    freed = False
    for shop in repo.list_stores():
        if repo.chain_rejected(product.id, shop.id):
            continue
        cur = repo.confirmed_mapping(product.id, shop.id, real_only=True)
        got = picks.get(shop.code)
        if got is None:
            if cur and repo.mapping_method(product.id, cur["id"]) == PRODUCE:
                repo.unconfirm_mapping(product.id, cur["id"])      # подходящей строки не стало
                freed = True
            continue
        row, (price, _, _) = got
        if cur and (str(cur["sku"]) == str(row["sku"]) or not _worse(cur, price, shop.code, conn)):
            continue
        chosen[shop.code] = str(row["sku"])
        _write(product, shop, row, cur)
    if freed:
        _back_to_master(product, conn)
    if chosen:
        log.info("овощи: товар %s — самая дешёвая строка вида в сетях: %s", product.id, ", ".join(sorted(chosen)))
    return chosen


def _back_to_master(product, conn) -> None:
    """Снятой связи на смену — обычная, сразу. Память прохода (link._SEEN, _SYNCED) её не вернула бы: доводы товара
    не изменились, и сеть осталась бы без связи до следующей пересборки базы — то самое «нет такого товара»."""
    master = repo.master_of(product.id)
    if master:
        materialize(product, master["master_id"], not (master.get("method") or "").endswith("?"), conn=conn)


def _write(product, shop, row: dict, cur: dict | None) -> None:
    sp_id = repo.upsert_store_product(shop.id, str(row["sku"]), row.get("name") or product.name,
                                      weight_g=row.get("weight_g"), unit=row.get("unit"), url=row.get("url"),
                                      ean=row.get("barcode"), keep_known=True)
    repo.confirm_mapping(product.id, sp_id, confirmed=True, method=PRODUCE)
    now = repo.confirmed_mapping(product.id, shop.id, real_only=True)
    if not now or now["id"] != sp_id:
        return                                  # человек отказался от сети, пока шёл проход, или записал своё
    if cur and cur["id"] != sp_id:
        repo.unconfirm_mapping(product.id, cur["id"])
    repo.drop_base_candidates(product.id, shop.id, keep=sp_id)
    _price(sp_id, row)
