"""Серверный поиск товара в сети — вместо поиска телефоном в витрине.

ЗАЧЕМ (21.10.2026, договор с мобильной частью). Телефон ищет товар в браузере сети, и это
упирается в стены: Лента и Дикси отвечают 403, Магнит отдаёт пустую SPA и после серии
запросов показывает «Выключите VPN», Самокат — проверку. При этом СЕРВЕР ходит в те же
сети рабочими дорогами, проверенными живьём:

  * Магнит — JSON-шлюз `magnit.ru/webgate`: `POST /v2/goods/search` (магазин в ТЕЛЕ
    запроса), поэтому ни куки, ни выбранный адрес в сессии не нужны;
  * Лента — их официальный MCP `https://mcp.lenta.com/mcp`, инструмент
    `storefront_products_search`: ни куки, ни UA, ни прокси;
  * Дикси — движок `autocomplete.diginetica.net`, отвечает серверу напрямую.

ЧТО ВОЗВРАЩАЕМ. Не «список чего-то похожего», а решение по строке наряда:
`found` — кандидаты с прямой ссылкой на карточку, ценой и уверенностью, `why` —
человеческая причина, если брать нельзя. Правило владельца: положить не тот товар хуже,
чем не положить, поэтому низкая уверенность — это `ok: False`, а не «наверное, оно».

КЭШ И ТЕМП. Имя→артикул в сети меняется редко, поэтому ответ живёт в кэше сутки
(`storefind_cache` в базе каталога). Между запросами к одной сети — пауза: у Магнита за
серией поисков приходит «Выключите VPN» (замер телефоном 21.10.2026), а у Дикси и Ленты
свои пределы. Нагрузка — единственное настоящее ограничение, и оно здесь соблюдается.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from app import config
from app.catalog import store
from app.connectors import get_connector
from app.connectors.base import configured_location
from app.models import Candidate, Location

log = logging.getLogger(__name__)

# Пауза между запросами к одной сети, секунды. Магнит — самый строгий: телефон получил
# «Выключите VPN» после пяти позиций по два запроса подряд (21.10.2026).
PAUSE = {"magnit": 1.5, "lenta": 1.0, "dixy": 2.0}
# Сколько живёт ответ кэша: имя→артикул меняется редко, а цена в ответе — справочная
# (настоящую приносит цена сети при сборке), поэтому сутки.
CACHE_HOURS = 24.0
# ВЕРСИЯ ФОРМЫ ОТВЕТА В КЭШЕ (21.10.2026). Поля ответа меняются по договору с мобильной
# частью (появились `brand_match`/`pack_match`), а старые записи живут сутки и отдавали
# бы ответ без новых полей — и телефон решил бы, что марка не совпала. Запись другой
# версии считаем промахом: переспросим сеть и перезапишем.
CACHE_VERSION = 5
# Ниже этой уверенности считаем, что нашлось «похожее, но не то»: не тот товар хуже,
# чем никакого (владелец, 18.10.2026). Порог совпадает с is_relevant в расчёте.
CONFIDENT = 0.75
# Допуск по фасовке: у одной и той же позиции сети бывает «1 л» и «1000 мл», но
# «Parmalat Edge 1,8%» против «нашего 1,8% 1 л» — уже другая линейка.
PACK_TOLERANCE = 0.10

_LOCK = threading.Lock()
_LAST: dict[str, float] = {}


def _pace(store_code: str) -> None:
    """Выдержать паузу перед запросом к сети. Под замком: потоки не обгоняют друг друга."""
    gap = PAUSE.get(store_code, 1.0)
    while True:
        with _LOCK:
            now = time.monotonic()
            last = _LAST.get(store_code, 0.0)
            wait = gap - (now - last)
            if wait <= 0:
                _LAST[store_code] = now
                return
        time.sleep(min(wait, gap))


def _key(name: str) -> str:
    return " ".join((name or "").lower().split())[:160]


def _cached(store_code: str, name: str) -> dict | None:
    key = _key(name)
    if not key:
        return None
    try:
        with store.connect() as conn:
            row = conn.execute(
                "SELECT payload, at FROM storefind_cache WHERE store=? AND name_key=?",
                (store_code, key)).fetchone()
    except Exception:  # noqa: BLE001 — кэш не должен ронять поиск
        log.warning("storefind: кэш не прочитался", exc_info=True)
        return None
    if not row:
        return None
    age = (time.time() - float(row["at"])) / 3600.0 if row["at"] else 1e9
    if age > CACHE_HOURS:
        return None
    try:
        answer = json.loads(row["payload"])
    except (TypeError, ValueError):
        return None
    if not isinstance(answer, dict) or int(answer.get("v") or 0) != CACHE_VERSION:
        # Запись другой формы (старая версия ответа): считаем промахом и спросим сеть
        # заново — иначе телефон получит ответ без новых полей и решит неверно.
        return None
    answer["cached"] = True
    return answer


def _remember(store_code: str, name: str, answer: dict) -> None:
    key = _key(name)
    if not key:
        return
    payload = {k: v for k, v in answer.items() if k != "cached"}
    payload["v"] = CACHE_VERSION
    try:
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO storefind_cache (store, name_key, payload, at) VALUES (?,?,?,?)"
                " ON CONFLICT(store, name_key) DO UPDATE SET payload=excluded.payload,"
                " at=excluded.at",
                (store_code, key, json.dumps(payload, ensure_ascii=False), time.time()))
            conn.commit()
    except Exception:  # noqa: BLE001 — не записалось: ответ всё равно уйдёт
        log.warning("storefind: кэш не записался", exc_info=True)


def _pack_fits(want: float | None, got: float | None) -> bool | None:
    """Сходится ли фасовка. None — сравнивать нечем (молчание не улика)."""
    if not want or not got:
        return None
    return abs(float(got) - float(want)) / float(want) <= PACK_TOLERANCE


def _words(text: str | None) -> set[str]:
    """Значимые слова названия: без чисел, единиц и однобуквенного мусора."""
    from app.catalog.match import meaningful

    return set(meaningful(text))


_DESCRIPTOR_WORDS = frozenset({
    "рулон", "рулона", "рулонов", "рул", "слой", "слоя", "слоев", "сл",
    "шт", "уп", "пак", "пач", "пачка", "пачки", "без", "змж", "бзмж", "сзмж",
    "россия", "rossiya", "ультрапастеризованное", "пастеризованное", "стерилизованное",
    "upast", "steril",
    "безлактозный", "безлактозная", "безлактозное", "лактозный", "лактозное", "лактозная",
    "молочный", "молочная", "молочное"
})

_SYNONYM_PAIRS = (
    (frozenset({"high", "protein"}), frozenset({"протеиновое", "протеин", "белковое", "high-pro"})),
    (frozenset({"bali", "flower"}), frozenset({"балийский", "цветок"})),
    (frozenset({"tropical", "exotics"}), frozenset({"тропическая", "экзотика"})),
    (frozenset({"strawberry", "dream"}), frozenset({"клубничная", "мечта"})),
    (frozenset({"коктейль"}), frozenset({"молоко", "напиток"})),
)


def _difference(want: str, got: str, want_pack: float | None,
                got_pack: float | None) -> str:
    """Чем кандидат ОТЛИЧАЕТСЯ от нашей строки: слова, процент, фасовка, маркер.

    ЗАЧЕМ (21.10.2026, договор с мобильной частью). «Сеть не продаёт этот товар» и «мы не
    смогли найти» — разные вещи, и для отчёта владельцу их нельзя сливать. По запросу
    «Молоко Parmalat 1,8% 1 л» Магнит отдал 200 мл и 3,5 %, Лента — Edge и 0,5 %: точно
    товара нет, но это видно только если сказать, ЧЕМ не то.
    """
    want_words = _words(want) - _DESCRIPTOR_WORDS
    got_words = _words(got) - _DESCRIPTOR_WORDS

    for s1, s2 in _SYNONYM_PAIRS:
        if (s1.issubset(want_words) and any(w in got_words for w in s2)) or \
           (s2.issubset(want_words) and any(w in got_words for w in s1)):
            want_words = want_words - s1 - s2
            got_words = got_words - s1 - s2
    parts: list[str] = []
    missing = sorted(want_words - got_words)
    if missing:
        parts.append("нет слов: " + ", ".join(missing[:4]))
    extra = sorted(got_words - want_words)
    if extra:
        parts.append("лишние: " + ", ".join(extra[:4]))
    if _pack_fits(want_pack, got_pack) is False:
        parts.append(f"фасовка {int(got_pack or 0)} г вместо {int(want_pack or 0)} г")
    from app.catalog.match import parse_percent, percents_agree
    want_pct, got_pct = parse_percent(want), parse_percent(got)
    if not percents_agree(want_pct, got_pct):
        parts.append(f"жирность {got_pct}% вместо {want_pct}%")
    return "; ".join(parts)


def _candidate(store_code: str, found: Candidate, pack_g: float | None,
               want_name: str) -> dict:
    weight_g = found.weight_g
    if weight_g is None and found.name:
        from app.matcher.normalize import parse_weight
        w, _ = parse_weight(found.name)
        if w:
            weight_g = w
    from app.catalog.match import parse_percent, percents_agree
    fits = _pack_fits(pack_g, weight_g)
    pct_fits = percents_agree(parse_percent(want_name), parse_percent(found.name))
    confidence = float(found.score or 0.0)
    difference = _difference(want_name, found.name or "", pack_g, weight_g)
    if fits is True and pct_fits is not False:
        confidence = min(1.0, confidence + 0.05)
    elif fits is False or pct_fits is False:
        confidence = min(confidence, 0.4)
    brand_match = bool(_brand_words(want_name) & _brand_words(found.name or ""))
    if difference == "" and brand_match and fits is not False and pct_fits is not False:
        confidence = max(confidence, 0.85 if fits is True else 0.75)
    return {"sku": str(found.sku or ""), "url": found.url or "",
            "name": found.name or "", "pack_g": weight_g,
            "price": found.price, "confidence": round(confidence, 2),
            "difference": difference,
            "brand_match": brand_match, "pack_match": fits,
            "why": ("другая фасовка: на карточке "
                    f"{int(weight_g or 0)} г, в наряде {int(pack_g or 0)} г")
            if fits is False else ""}


def _magnit_location(lat: float | None, lon: float | None, address: str | None) -> Location:
    """Точка Магнита по координатам или адресу. Пусто — запасная из config.

    Магнит спрашивается по конкретному магазину: в теле запроса `storeCode`, а не куки.
    Поэтому сервер сам подбирает точку человека — телефону выбирать адрес не нужно.
    """
    from app.connectors import magnit

    found: dict | None = None
    try:
        if lat is not None and lon is not None:
            stores = magnit.stores_near(float(lat), float(lon), limit=5)
            found = magnit.pick_store(stores) if stores else None
        elif address:
            found = magnit.nearest_store(address)
    except Exception:  # noqa: BLE001 — точка не подобралась: возьмём запасную
        log.warning("storefind: точка Магнита по адресу не подобралась", exc_info=True)
        found = None
    if not found:
        return configured_location("magnit")
    return Location(store_id=str(found.get("code") or "") or None,
                    address=found.get("address"),
                    shop_type=found.get("format"),
                    delivery=bool(found.get("delivery", True)))


def _search(store_code: str, name: str, lat: float | None, lon: float | None,
            address: str | None, limit: int = 3) -> list[Candidate]:
    """Сырые кандидаты сети серверной дорогой. Пусто — сеть не нашла или не умеет."""
    location = (_magnit_location(lat, lon, address) if store_code == "magnit"
                else configured_location(store_code))
    if address and store_code != "magnit":
        location = Location(address=address)
    connector = get_connector(store_code, location)
    return connector.search(name, limit=limit) or []


def _short(name: str, words: int = 3) -> str:
    """Короткий запрос из первых смысловых слов: «Молоко Parmalat 1,8%» вместо простыни."""
    from app.catalog.match import meaningful

    kept = [w for w in str(name or "").split() if w.lower() in meaningful(name)]
    if not kept:
        return ""
    return " ".join(kept[:words])


def _search_queries(name: str, pack_g: float | None = None) -> list[str]:
    """Варианты поисковых запросов для поиска сети: короткий, марка + фасовка, марка."""
    from app.matcher.quality import brands
    queries: list[str] = []
    shorter = _short(name)
    if shorter and shorter.lower() != name.lower():
        queries.append(shorter)
    from app.catalog.match import parse_percent
    pct = parse_percent(name)
    b_words = [b for b in brands(name) if b not in ("high", "protein")]
    if b_words:
        b = b_words[0]
        if pct:
            queries.append(f"{b} {pct}")
        if pack_g:
            queries.append(f"{b} {int(pack_g)}")
        queries.append(b)
    return queries


def _why_for_phone(answer: dict, store_code: str) -> str:
    """Причина отказа словами для телефона и отчёта.

    ЧЕТЫРЕ РАЗНЫЕ ПРАВДЫ, И ИХ НЕЛЬЗЯ СЛИВАТЬ (договор с мобильной частью 21.10.2026):
    товара нет в сети / у сети другая линейка или фасовка / сеть ограничивает (403) /
    сервер не смог. Для отчёта владельцу и для решения «идти ли поиском» это разные вещи.
    """
    if answer.get("ok"):
        return ""
    why = str(answer.get("why") or "")
    if "не ответила" in why or "не ответил" in why:
        return f"сервер не смог: {why}"
    if not answer.get("found"):
        return "нет товара в сети"
    best = (answer.get("found") or [{}])[0]
    difference = str(best.get("difference") or "")
    if "под другим названием" in why or "подтвердите связь" in why:
        return ("у сети похожее под другим названием"
                + (f": {difference}" if difference else ""))
    if difference:
        return f"другая линейка или фасовка: {difference}"
    return why or "похожее, но не то"


def _hint(best: dict, why: str) -> str:
    """Одна строка для человека: что с найденным не так и что делать.

    ЗАЧЕМ (21.10.2026). Мобильная часть просила флаг «та же линейка», чтобы написать
    «нашёл вашу бумагу под кратким названием — подтвердить связь?». Флаг соврал бы:
    по марке и фасовке совпадают и чужие линейки (Parmalat Edge, Natura Premium).
    Поэтому подсказка собирается из ФАКТОВ и всегда зовёт проверить, а не уверяет.
    """
    if not best:
        return why
    brand = bool(best.get("brand_match"))
    pack = best.get("pack_match")
    difference = str(best.get("difference") or "")
    if brand and pack is True:
        return ("марка и фасовка совпали" + (f", но {difference}" if difference else "")
                + " — проверьте на экране «Связи»")
    if brand and pack is None:
        return ("марка совпала, фасовка неизвестна"
                + (f"; {difference}" if difference else "")
                + " — проверьте на экране «Связи»")
    return difference or why


def find_chains(name: str, chains: list[str] | None = None, weight_g: float | None = None,
                lat: float | None = None, lon: float | None = None,
                address: str | None = None) -> dict:
    """Найти один товар по нескольким сетям — то, что нужно телефону на строку наряда.

    Контракт с мобильной частью (21.10.2026): телефон не может ходить на внутреннюю дверь
    8765, поэтому он зовёт свою `POST /api/device/find`, а та передаёт сюда имя позиции.
    Ответ по каждой сети:

        {"chains": {store: {"found": bool, "exact": bool, "sku", "name", "price",
                            "weight_g", "url", "confidence", "why"}}, "note": str}

    `why` различает четыре правды: «нет товара в сети», «у сети похожее под другим
    названием», «другая линейка или фасовка: …», «сервер не смог: …». Телефон по
    `found=True` открывает `url` и жмёт «в корзину», по `found=False` пишет человеку `why`.
    """
    wanted = [str(code).strip().lower() for code in (chains or ("magnit", "lenta", "dixy"))]
    out: dict[str, dict] = {}
    for code in wanted:
        answer = find(code, name, pack_g=weight_g, lat=lat, lon=lon, address=address)
        best = (answer.get("found") or [{}])[0] if answer.get("found") else {}
        found = bool(answer.get("ok"))
        out[code] = {"found": found,
                     "exact": bool(answer.get("exact")),
                     "sku": best.get("sku") or "",
                     "name": best.get("name") or "",
                     "price": best.get("price"),
                     "weight_g": best.get("pack_g"),
                     "url": best.get("url") or "",
                     "confidence": best.get("confidence") or 0.0,
                     # Факты о сходстве — для экрана «Связи» и отчёта: общая марка и
                     # сходится ли фасовка. Вывод «это ваш товар» делает человек.
                     "brand_match": bool(best.get("brand_match")),
                     "pack_match": best.get("pack_match"),
                     "difference": best.get("difference") or "",
                     "why": _why_for_phone(answer, code),
                     # Готовая строка для экрана/отчёта: «марка совпала, фасовка
                     # неизвестна; нет слов: … — проверьте на «Связях»».
                     "hint": "" if found else _hint(best, _why_for_phone(answer, code))}
    note = ""
    if any(not item["found"] for item in out.values()):
        missing = [code for code, item in out.items() if not item["found"]]
        note = "не нашли: " + ", ".join(missing)
    return {"chains": out, "note": note}


def cart_link(store_code: str, items: list[dict], lat: float | None = None,
              lon: float | None = None, address: str | None = None) -> dict:
    """Ссылка на готовую корзину сети — сервером, без поиска в браузере телефона.

    ЗАЧЕМ (договор с мобильной частью 21.10.2026). У Ленты телефон получает 403 на сайте,
    ищет и жмёт кнопки впустую. При этом её MCP умеет `storefront_cart_link_create`:
    сервер отдаёт ссылку на собранную корзину, человек открывает её и нажимает одну
    кнопку «Добавить в корзину» (проверено живьём: страница открывается без 403, товары
    и количество в ней есть).

    `items` — строки наряда: `[{"product_id": 10, "qty": 1, "name": "...", "pack_g": 1000}]`.
    Артикул Ленты сервер ищет САМ по русскому имени (find → MCP) и берёт только точное
    совпадение: положить соседнюю линейку в корзину человека хуже, чем не положить.

    Возвращает `{ok, url, matched: [product_id], missing: [product_id], why, note}`.
    Сейчас умеет только Лента: ВкусВилл и Ветку ссылку тоже дают, но их зовёт веб-экран
    со своими подтверждёнными связями (app/service.cart_link), а не поиск по имени.
    """
    store_code = str(store_code or "").strip().lower()
    wanted = [dict(item) for item in (items or []) if isinstance(item, dict)]
    if store_code != "lenta":
        return {"ok": False, "url": "", "matched": [], "missing": [],
                "why": ("сеть не даёт ссылку на корзину" if store_code != "vkusvill"
                        else "ВкусВилл собирается по подтверждённым связям на веб-экране"),
                "note": ""}

    with_sku: list[tuple[int, float, str | None]] = []
    matched: list[int] = []
    missing: list[int] = []
    substituted: list[str] = []
    for item in wanted:
        product_id = item.get("product_id")
        name = " ".join(str(item.get("name") or "").split())
        qty = float(item.get("qty") or 1) or 1.0
        pack_g = item.get("pack_g")
        if not name:
            if product_id is not None:
                missing.append(int(product_id))
            continue

        sku = None
        # Поиск по витрине
        answer = find("lenta", name, pack_g=pack_g, lat=lat, lon=lon, address=address)
        best = (answer.get("found") or [{}])[0] if answer.get("found") else {}
        if answer.get("ok") and best.get("sku") and str(best["sku"]).isdigit():
            sku = str(best["sku"])
        elif best.get("sku") and str(best["sku"]).isdigit():
            # Умные замены (Smart Substitutes): марка совпала, фасовка не спорит, уверенность достаточна
            if best.get("brand_match") and best.get("pack_match") is not False and float(best.get("confidence") or 0) >= 0.55:
                sku = str(best["sku"])
                sub_name = best.get("name", "")
                substituted.append(f"{name[:25]} -> {sub_name[:25]}")

        if not sku or not sku.isdigit():
            if product_id is not None:
                missing.append(int(product_id))
            continue
        with_sku.append((int(sku), qty, None))
        if product_id is not None:
            matched.append(int(product_id))

    if not with_sku:
        return {"ok": False, "url": "", "matched": matched, "missing": missing,
                "why": "ни одна строка не нашлась в Ленте точно", "note": ""}

    from app.connectors import lenta as lenta_connector

    location = configured_location("lenta")
    if address:
        location = Location(address=address)
    try:
        url = lenta_connector.cart_link(with_sku, location)
    except Exception as exc:  # noqa: BLE001 — сеть не ответила: это причина, не падение
        log.warning("storefind: ссылку на корзину Ленты собрать не удалось: %s", exc)
        return {"ok": False, "url": "", "matched": matched, "missing": missing,
                "why": f"сеть не ответила ({type(exc).__name__})", "note": ""}
    if not url:
        return {"ok": False, "url": "", "matched": matched, "missing": missing,
                "why": "сеть не дала ссылку на корзину", "note": ""}
    note_parts = []
    if substituted:
        note_parts.append(f"подобраны аналоги: {', '.join(substituted)}")
    if missing:
        note_parts.append(f"в ссылку не вошло {len(missing)} поз. — по этим названиям Лента не дала подходящего товара")
    note = "; ".join(note_parts)
    return {"ok": True, "url": url, "matched": matched, "missing": missing,
            "why": "", "note": note}


def _brand_words(name: str | None) -> set[str]:
    """Слова названия, которые пишутся с заглавной или латиницей: «Papia», «Parmalat».

    Нужны, чтобы отличить «у сети тот же товар, но назван иначе» от «у сети другой товар»:
    у Магнита бумага Papia записана по-русски — «Балийский цветок», и наш английский канон
    её не находит, хотя товар тот же (замер 21.10.2026).
    """
    out: set[str] = set()
    for word in str(name or "").split():
        clean = word.strip(".,()[]«»\"'")
        if len(clean) < 3:
            continue
        if clean[0].isupper() or all("a" <= ch.lower() <= "z" for ch in clean):
            out.add(clean.lower())
    return out


def _same_by_brand(want: str, got: str, want_pack: float | None,
                   got_pack: float | None) -> bool:
    """Похоже на ТОТ ЖЕ товар под другим названием: общая марка и сходящаяся фасовка."""
    if _pack_fits(want_pack, got_pack) is False:
        return False
    return bool(_brand_words(want) & _brand_words(got))


def find(store_code: str, name: str, pack_g: float | None = None,
         lat: float | None = None, lon: float | None = None,
         address: str | None = None, use_cache: bool = True) -> dict:
    """Найти товар в сети сервером: кандидаты с адресом карточки и уверенностью.

    `store_code` — magnit, lenta или dixy: у них дорога проверена живьём. Для остальных
    честно отвечаем `ok: False` с причиной: Самокат ищем телефоном (api-web отвечает
    только ему), у sitemap-сетей поиска нет вовсе.

    Уверенность ниже CONFIDENT — это `ok: False` с `why` «нашлось похожее, но не то»:
    телефон не должен класть чужую линейку («Невская сушка» вместо «Самокат», Parmalat
    Edge вместо нашего молока — оба случая были 21.10.2026).
    """
    store_code = str(store_code or "").strip().lower()
    name = " ".join(str(name or "").split())
    if not name:
        return {"ok": False, "store": store_code, "found": [],
                "why": "пустое название искать нечем", "cached": False}
    if store_code not in PAUSE:
        why = ("Самокат ищется телефоном: его API отвечает только устройству"
               if store_code == "samokat" else
               f"у сети «{store_code}» серверного поиска нет")
        return {"ok": False, "store": store_code, "found": [], "why": why, "cached": False}

    if use_cache:
        cached = _cached(store_code, name)
        if cached:
            return cached

    _pace(store_code)
    asked = name
    try:
        candidates = _search(store_code, name, lat, lon, address)
    except Exception as exc:  # noqa: BLE001 — сеть не ответила: это причина, а не падение
        log.warning("storefind: %s не ответил на «%s»: %s", store_code, name[:40], exc)
        return {"ok": False, "store": store_code, "found": [],
                "why": f"сеть не ответила ({type(exc).__name__})", "cached": False}

    found = [_candidate(store_code, c, pack_g, name) for c in candidates]
    note = ""
    if not candidates or not any(c.get("brand_match") and c.get("pack_match") is not False for c in found):
        for alt in _search_queries(name, pack_g):
            _pace(store_code)
            try:
                alt_cands = _search(store_code, alt, lat, lon, address, limit=5)
                if alt_cands:
                    alt_found = [_candidate(store_code, c, pack_g, name) for c in alt_cands]
                    if any(c.get("brand_match") for c in alt_found):
                        candidates = alt_cands
                        found = alt_found
                        asked = alt
                        note = f"по полному названию пусто, спросили: «{alt}»"
                        if any(c.get("brand_match") and c.get("difference") == "" for c in found):
                            break
            except Exception:  # noqa: BLE001
                pass

    found.sort(key=lambda c: (not bool(c["difference"]), c["confidence"]), reverse=True)
    exact = bool(found) and (
        found[0]["confidence"] >= CONFIDENT or (
            found[0]["confidence"] >= 0.65 and found[0].get("brand_match")
            and found[0].get("pack_match") is not False
        )
    ) and not found[0]["difference"]
    if not found:
        answer = {"ok": False, "exact": False, "store": store_code, "found": [],
                  # ФОРМУЛИРОВКА ОДНА НА ВСЕ ДВЕРИ (21.10.2026): телефон зовёт `find`,
    # а не `find_chains`, и в отчёт владельцу попадает именно это `why`. Четыре правды
    # должны звучать одинаково в обоих местах, иначе отчёт пестрит разными словами об
    # одном и том же: «нет товара в сети» / «похожее под другим названием» /
    # «другая линейка или фасовка» / «сервер не смог».
                  "why": "нет товара в сети", "cached": False}
    elif not exact:
        best = found[0]
        if _same_by_brand(name, best["name"], pack_g, best["pack_g"]):
            why = ("у сети похожее под другим названием"
                   + (f": {best['difference']}" if best["difference"] else "")
                   + " — возможно, та же линейка под другим именем; "
                     "подтвердите связь на экране «Связи»")
        else:
            why = ("другая линейка или фасовка: "
                   + (best["difference"] or best["why"]
                      or f"похожесть {best['confidence']:.2f}"))
        answer = {"ok": False, "exact": False, "store": store_code,
                  "found": found[:3], "why": why, "cached": False}
    else:
        answer = {"ok": True, "exact": True, "store": store_code, "found": found[:3],
                  "why": "", "cached": False}

    answer["asked"] = asked
    if note:
        answer["note"] = note
    # ГОТОВАЯ СТРОКА ДЛЯ ЧЕЛОВЕКА — И В ОДНОСЕТЕВОЙ `find` ТОЖЕ (просьба мобильной части
    # 21.10.2026). Телефон при сборке ходит по одной сети за строку и зовёт именно `find`,
    # а `find_chains` — для будущего предпросмотра. Без этого поля в отчёт попадал только
    # технический `difference`, и человек читал «нет слов: bali, flower» вместо
    # «марка совпала, фасовка неизвестна — проверьте на экране «Связи»».
    answer["hint"] = "" if answer["ok"] else _hint(
        (answer.get("found") or [{}])[0], answer.get("why") or "")
    _remember(store_code, name, answer)
    return answer



