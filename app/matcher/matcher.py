"""Сопоставление эталонов с артикулами магазинов (спецификация, раздел 5.2).

Кандидаты берутся из коннектора магазина, ранжируются по похожести названия
и допуску по граммовке, складываются в store_products как неподтверждённые
сопоставления. Подтверждённые сопоставления — единственный источник цен
для оптимизатора (раздел 4, правило про product_mapping.confirmed).
"""
from __future__ import annotations

import contextvars
import logging
import threading
import time

from app import config, db, repo
from app.matcher import quality
from app.matcher.normalize import normalize_name, parse_weight, similarity, weight_ok
from app.models import Candidate

log = logging.getLogger(__name__)

WEIGHT_PENALTY = 0.5      # во столько раз режем score кандидата с негодной граммовкой


def digits(value) -> str:
    """Только цифры: штрихкод записывают и с пробелами, и с дефисами, и числом."""
    return "".join(ch for ch in str(value or "") if ch.isdigit())


# --- доступ к коннекторам (модуль пишется параллельно, может отсутствовать) ---

def _get_connector(store_code: str, location=None):
    """Коннектор магазина, настроенный на место клиента.

    Место не передано — спрашиваем адрес клиента сами. Это важно: сопоставление и
    цены обязаны сниматься там же, где человек будет покупать, иначе «Сравнение»
    и «Результат» разойдутся между собой и оба будут выглядеть правдоподобно.
    """
    try:
        from app.connectors import get_connector       # noqa: PLC0415
    except ImportError:
        return None
    if location is None:
        try:
            from app import location as client_place   # noqa: PLC0415
            location = client_place.for_store(store_code)
        except Exception:                              # noqa: BLE001 — без адреса работаем как раньше
            location = None
    try:
        return get_connector(store_code, location)
    except Exception:
        return None


def _tolerance() -> float:
    return float(config.get("weight_tolerance_pct", 20) or 20)


def _search(connector, query: str, limit: int) -> list[Candidate]:
    try:
        try:
            result = connector.search(query, limit)
        except TypeError:
            result = connector.search(query)
        return list(result or [])
    except Exception:
        return []


def _store_product_id(store_id: int, sku: str) -> int | None:
    rows = db.query("SELECT id FROM store_products WHERE store_id=? AND sku=?", (store_id, sku))
    return rows[0]["id"] if rows else None


def _mapped_rows(product_id: int, store_id: int) -> list[dict]:
    return [dict(r) for r in db.query(
        "SELECT m.store_product_id, m.confirmed, sp.sku FROM product_mapping m"
        " JOIN store_products sp ON sp.id = m.store_product_id"
        " WHERE m.product_id=? AND sp.store_id=?", (product_id, store_id))]


# --- поиск кандидатов ------------------------------------------------------

def score_candidate(product, candidate: Candidate, tolerance_pct: float | None = None) -> float:
    """Похожесть кандидата на эталон с учётом штрихкода и допуска по граммовке.

    Штрихкод бьёт всё остальное. Совпал — сомнений нет, это тот самый товар.
    Разошёлся — это точно не он, сколько бы ни совпадали названия: именно так
    «Страчателла» оказывалась мороженым, а детское пюре — соком.
    """
    tol = _tolerance() if tolerance_pct is None else tolerance_pct
    ours, theirs = digits(getattr(product, "barcode", None)), digits(candidate.ean)
    if ours and theirs:
        return 1.0 if ours == theirs else 0.0

    # Родовое слово и марка — не оттенок похожести, а признак «это другой товар».
    # Похожесть тут бессильна: «Сыр Страчателла 200 г» и «Мороженое Страчателла 92 г»
    # отличаются ровно теми словами, которые нормализация выбрасывает как мусор.
    name = product.name or ""
    if quality.type_conflict(name, candidate.name or ""):
        return 0.0
    if quality.brand_conflict(name, candidate.name or ""):
        return 0.0

    score = similarity(product.name, candidate.name or "")
    cand_weight = candidate.weight_g
    if cand_weight is None:
        cand_weight = parse_weight(candidate.name or "")[0]
    if not weight_ok(product.weight_g, cand_weight, tol):
        # Штраф, а не отказ — и это проверено измерением. Жёсткий отказ пробовали:
        # он обнуляет правильный по смыслу товар с чуть другой фасовкой, и наверх
        # всплывает совсем посторонний. «Пюре детское банан-яблоко» так уехало с
        # «Пюре из яблок и банана» на «Котлету рыбную с картофельным пюре».
        score *= WEIGHT_PENALTY
    return round(score, 4)


def find_candidates(product_id: int, store_code: str, limit: int = 3) -> list[Candidate]:
    """Топ-N кандидатов эталона в магазине. Сохраняет их как неподтверждённые сопоставления."""
    product = repo.get_product(product_id)
    store = repo.get_store(store_code)
    if product is None or store is None:
        return []
    connector = _get_connector(store_code)
    if connector is None:
        return []

    found = _search(connector, product.name, max(limit * 3, 10))
    exact = connector.search_barcode(product.barcode) if getattr(product, "barcode", None) else None
    if exact:
        # найденный по штрихкоду идёт первым и вытесняет одноимённого из выдачи по названию
        found = [exact] + [c for c in found if c.sku != exact.sku]
    scored: list[Candidate] = []
    for cand in found:
        if not getattr(cand, "sku", None):
            continue                                   # без sku кандидат бесполезен
        if not cand.store_code:
            cand.store_code = store_code
        if cand.weight_g is None:
            cand.weight_g = parse_weight(cand.name or "")[0]
        cand.score = score_candidate(product, cand)
        scored.append(cand)

    scored.sort(key=lambda c: (-c.score, c.name or ""))
    top = scored[:limit]

    confirmed = repo.confirmed_mapping(product_id, store.id)
    confirmed_sku = confirmed["sku"] if confirmed else None
    for cand in top:
        sp_id = repo.upsert_store_product(store.id, cand.sku, cand.name, cand.weight_g,
                                          cand.unit, cand.url, cand.ean)
        if cand.sku != confirmed_sku:                  # подтверждённое не понижаем
            repo.confirm_mapping(product_id, sp_id, confirmed=False)
    return top


def confirm(product_id: int, store_code: str, sku: str) -> None:
    """Подтверждает выбранный артикул и снимает подтверждение с остальных кандидатов."""
    store = repo.get_store(store_code)
    if store is None:
        raise ValueError(f"Магазин не найден: {store_code}")
    sp_id = _store_product_id(store.id, sku)
    if sp_id is None:
        raise ValueError(f"Артикул {sku} не найден в каталоге магазина {store_code}")
    for row in _mapped_rows(product_id, store.id):
        if row["store_product_id"] != sp_id and row["confirmed"]:
            repo.confirm_mapping(product_id, row["store_product_id"], confirmed=False)
    repo.confirm_mapping(product_id, sp_id, confirmed=True)


def auto_match(product_ids: list[int], store_codes: list[str], threshold: float = 0.75) -> dict:
    """Автоподтверждение лучшего кандидата. Подтверждённые сопоставления не трогает
    (критерий приёмки 7: повторный запуск не спрашивает подтверждённые позиции)."""
    auto = 0
    need_review: list[dict] = []
    tol = _tolerance()

    for product_id in product_ids:
        product = repo.get_product(product_id)
        if product is None:
            continue
        for store_code in store_codes:
            store = repo.get_store(store_code)
            if store is None:
                need_review.append({"product_id": product_id, "store_code": store_code,
                                    "reason": "магазин не найден", "candidates": []})
                continue
            # Уже подтверждено настоящим артикулом — пропускаем. Заглушка строки чека
            # (hist-) сеть не закрывает: по ней в корзину ничего не положить, и
            # настоящий артикул искать надо (тем же правилом, что cartplan.build).
            if repo.confirmed_mapping(product_id, store.id, real_only=True):
                continue
            # Связь с этой сетью человек убирал: автоматически её не возвращаем, до
            # нового подтверждения руками (обещание экрана «Связи»).
            if repo.chain_rejected(product_id, store.id):
                continue
            held = repo.confirmed_mapping(product_id, store.id)      # заглушка или ничего
            candidates = find_candidates(product_id, store_code, limit=3)
            if not candidates:
                need_review.append({"product_id": product_id, "product_name": product.name,
                                    "store_code": store_code, "reason": "нет кандидатов",
                                    "candidates": []})
                continue
            best = candidates[0]
            # совпавший штрихкод снимает вопрос о граммовке: это тот же товар,
            # даже если в названии магазина фасовка написана иначе
            by_barcode = bool(digits(getattr(product, "barcode", None))
                              and digits(best.ean) == digits(getattr(product, "barcode", None)))
            fits = by_barcode or weight_ok(product.weight_g, best.weight_g, tol)
            if best.score >= threshold and fits:
                from app.catalog.model import placeholder_sku

                stub = placeholder_sku(best.sku, store_code)
                if stub and held:
                    # Та же или другая заглушка вместо уже подтверждённой: пустое
                    # действие или подмена цены чужим чеком. Настоящего артикула поиск
                    # не нашёл — считать нечего (ревью 18.10.2026).
                    continue
                confirm(product_id, store_code, best.sku)
                auto += int(not stub)        # «подтверждено» — только настоящий артикул
            else:
                need_review.append({
                    "product_id": product_id, "product_name": product.name,
                    "store_code": store_code,
                    "reason": "низкий score" if not fits or best.score < threshold else "",
                    "best_score": best.score,
                    "candidates": [{"sku": c.sku, "name": c.name, "score": c.score} for c in candidates],
                })
    return {"auto": auto, "need_review": need_review}


# --- цены ------------------------------------------------------------------

# Кулдаун фонового обновления и пауза сети, бросившей исключение. Константы модуля, а
# не настройки установки: это поведение кода — сколько ждать сеть, — и меняется оно
# вместе с кодом, а не правкой config.yaml (границы задачи Z3).
COOLDOWN_SECONDS = 15 * 60          # сеть, которую спрашивали только что, не дёргаем
BLOCK_PAUSE_SECONDS = 24 * 3600     # ЯВНАЯ блокировка сети — пауза «до завтра»
FAIL_BACKOFF_SECONDS = 5 * 60       # обычный сбой — короткая передышка, а не сутки
MAX_WORKERS = 6                     # по потоку на сеть, но не больше шести сразу

# Ключ отметок — (путь базы человека, код сети), а не один код сети. Базы у людей
# разные (app/users.py: data/users/<номер>/basket.db), и общий кулдаун на всех
# значил бы, что обновление у одного человека молча отменяет обновление у другого.
_LAST_RUN: dict[tuple[str, str], float] = {}       # (база, сеть) -> когда спрашивали
_PAUSED_UNTIL: dict[tuple[str, str], float] = {}   # (база, сеть) -> до какого времени
_STATE_LOCK = threading.Lock()


def _state_key(db_path: str, store_code: str) -> tuple[str, str]:
    """Ключ отметок: база человека и сеть. База захвачена вызывающим потоком.

    Путь передаётся сюда, а не читается из config.db_path() внутри рабочего потока:
    contextvars в пул потоков сами не переезжают, и путь, прочитанный там, оказался
    бы общим (см. refresh_prices_parallel).
    """
    return str(db_path), str(store_code)


def _asked_now(db_path: str, store_code: str) -> None:
    """Отметить попытку спросить сеть: пока кулдаун не вышел, её не переспрашивают.

    Отмечается ПОПЫТКА, а не удача: сеть, которая молчит и висит, иначе дёргалась бы
    заново каждым открытием «Где дешевле», а не раз в пятнадцать минут.
    """
    with _STATE_LOCK:
        _LAST_RUN[_state_key(db_path, store_code)] = time.time()


def _is_blocked(exc: BaseException) -> bool:
    """Явная блокировка сети — единственная причина паузы «до завтра».

    CrawlBlocked (app/catalog/model.py) — это не «сеть отвалилась», а «витрина не
    пускает»: повтор через пять минут только продлевает проверку. Аналог из
    коннекторов узнаётся по имени класса: app/connectors пока своего исключения не
    завёл, но блокировкой он тоже не должен считаться обычным сбоем.
    """
    try:
        from app.catalog.model import CrawlBlocked     # noqa: PLC0415 — каталог не всегда нужен
    except Exception:                                  # noqa: BLE001 — без каталога блокировок нет
        CrawlBlocked = ()                              # type: ignore[assignment]
    if isinstance(exc, CrawlBlocked):
        return True
    return any(cls.__name__.endswith("Blocked") for cls in type(exc).__mro__)


def _pause(db_path: str, store_code: str, seconds: float, why: str) -> None:
    """Не спрашивать сеть этой базы `seconds`. Постановка паузы — всегда в лог.

    Сутки — только за явную блокировку (`_is_blocked`). Обычный сбой (таймаут,
    обрыв, ошибка разбора) — короткий backoff: сеть, у которой моргнула связь,
    иначе выпадала бы до завтра вместе со всеми своими ценами.
    """
    until = time.time() + float(seconds)
    with _STATE_LOCK:
        _PAUSED_UNTIL[_state_key(db_path, store_code)] = until
    log.warning("%s: %s — не спрашиваю %d мин", store_code, why,
                max(1, round(float(seconds) / 60)))


def _pause_until_tomorrow(db_path: str, store_code: str, why: str) -> None:
    """Сеть заблокировала — не трогаем её до завтра."""
    _pause(db_path, store_code, BLOCK_PAUSE_SECONDS, why)


def _is_cooled(db_path: str, store_code: str) -> tuple[bool, bool]:
    """(была недавно, на паузе). Обе отметки читаются под одним замком."""
    now = time.time()
    key = _state_key(db_path, store_code)
    with _STATE_LOCK:
        paused = _PAUSED_UNTIL.get(key, 0.0) > now
        last = _LAST_RUN.get(key)
    return (last is not None and now - last < COOLDOWN_SECONDS), paused


def _place_for(store_code: str):
    """Место человека для сети — то же, что подставляет коннектор (_get_connector).

    Спрашиваем сами, чтобы рядом с ценой записать точку и вид цены. Ошибки места не
    повод бросать цены: без места коннектор берёт запасное из config.yaml, как раньше.
    """
    try:
        from app import location as client_place     # noqa: PLC0415
        return client_place.for_store(store_code)
    except Exception:                                # noqa: BLE001 — без места работаем как раньше
        return None


def _place_mark(place) -> tuple[str | None, str | None]:
    """Снимок помнит, где и как снят: (point_key, mode), либо (None, None).

    point_key — координаты точки, которую человек поставил на карте: формат
    «широта,долгота» с пятью знаками (≈1 м, для магазина достаточно). Точки нет —
    оба поля пустые: снимок общий, и подставлять его как цену конкретного магазина
    нельзя (app/freshness.price_for). mode — «delivery» или «pickup»: у сети это
    разные цены, и смешивать их в одной истории нельзя.
    """
    if place is None:
        return None, None
    try:
        from app import location as client_place     # noqa: PLC0415
        key = client_place.point_key()
    except Exception:                                # noqa: BLE001 — точки нет, снимок общий
        return None, None
    if not key:
        return None, None
    # Location.delivery — цены доставки или самовывоза: у сетей они разные.
    return key, ("delivery" if getattr(place, "delivery", True) else "pickup")


def _refresh_one_store(store_code: str, product_ids: list[int],
                       fresh_within: float | None = None,
                       db_path: str | None = None) -> tuple[int, str | None]:
    """Обновить цены одной сети. Возвращает (сколько записано, ошибка или None).

    Общая часть последовательного refresh_prices и параллельного
    refresh_prices_parallel: внутри только сеть и короткие записи снимков, никакой
    длинной транзакции. Исключение коннектора НЕ глотается: вызывающий сам решает,
    записать его в errors (последовательный) или ещё и поставить сеть на паузу
    (параллельный).

    db_path — база человека, захваченная ДО ухода в поток. Внутри потока
    config.db_path() тоже вернёт её (refresh_prices_parallel передаёт контекст), но
    путь нужен ключом кулдауна и паузы, поэтому приходит аргументом: так отметка не
    зависит от того, успел ли контекст переехать.

    fresh_within (секунды) — для дозора цен (app/pricewatch.py), кнопка его не передаёт.
    Ответ сети берётся не старше этого: кэш коннекторов старее не отдаёт
    (cache.max_age), иначе снимок обновился бы одной видимостью. Пишутся только снимки,
    снятые за это время: коннектор, не дозвонившись до сети, отдаёт цену из чека, прайса
    или справочника, и дозор, записывая её каждые два часа, плодил бы копии вчерашних
    цен. Место человека (магазин Магнита к адресу) ищется со своим обычным сроком кэша:
    за два часа оно не меняется, и гонять справочник магазинов незачем.
    """
    from contextlib import nullcontext
    from datetime import datetime, timedelta

    from app import freshness

    db_path = db_path or config.db_path()
    since = datetime.now() - timedelta(seconds=fresh_within) if fresh_within else None

    def held_to_fresh():
        """Кэш не старше fresh_within — только для дозора; кнопке — обычный срок."""
        if not fresh_within:
            return nullcontext()
        from app.connectors import cache       # noqa: PLC0415 — коннекторов может не быть
        return cache.max_age(fresh_within)

    def taken_in_time(snap) -> bool:
        if since is None:
            return True
        if getattr(snap, "source", None) == freshness.REFERENCE:
            return False
        taken = freshness.moment(getattr(snap, "fetched_at", None))
        return taken is not None and taken >= since

    store = repo.get_store(store_code)
    if store is None:
        return 0, f"{store_code}: магазин не найден"

    place = _place_for(store_code)
    connector = _get_connector(store_code, place)
    if connector is None:
        return 0, f"{store_code}: коннектор недоступен"
    point_key, mode = _place_mark(place)

    by_sku: dict[str, int] = {}
    for product_id in product_ids:
        mapping = repo.confirmed_mapping(product_id, store.id)
        if mapping:
            by_sku[mapping["sku"]] = mapping["id"]
    if not by_sku:
        return 0, None

    _asked_now(db_path, store_code)
    with held_to_fresh():
        snapshots = connector.get_prices(list(by_sku)) or []

    updated = 0
    for snap in snapshots:
        sp_id = by_sku.get(getattr(snap, "sku", None))
        if sp_id is None or getattr(snap, "price", None) is None or not taken_in_time(snap):
            continue
        repo.save_price(sp_id, snap.price, getattr(snap, "price_per_kg", None),
                        bool(getattr(snap, "in_stock", True)),
                        getattr(snap, "fetched_at", None) or None,
                        source=getattr(snap, "source", None),
                        point_key=point_key, mode=mode)
        updated += 1
    return updated, None


def refresh_prices(product_ids: list[int], store_codes: list[str], progress=None,
                   fresh_within: float | None = None) -> dict:
    """Тянет цены по ПОДТВЕРЖДЁННЫМ сопоставлениям и пишет снимки в store_prices.

    Последовательный путь — кнопка «Обновить» и дозор цен: сети спрашиваются по
    одной, ход виден по сетям. Параллельный путь для экрана — refresh_prices_parallel.

    progress(сделано, всего, сеть) — ход по сетям: обновление идёт минутами, и без
    хода экран «Корзина» стоял бы на «0 из N», неотличимо от зависания.

    fresh_within — см. _refresh_one_store: срок ответа сети для дозора.
    """
    updated = 0
    errors: list[str] = []
    # База человека захватывается здесь, в том потоке, где живёт контекст входа:
    # кулдаун и пауза считаются по паре (база, сеть), а не по одной сети.
    db_path = config.db_path()

    for number, store_code in enumerate(store_codes):
        if progress is not None:
            try:
                progress(number, len(store_codes), store_code)
            except Exception:  # noqa: BLE001 — отметка хода не повод бросать цены
                pass
        try:
            store_updated, error = _refresh_one_store(store_code, product_ids, fresh_within,
                                                      db_path)
        except Exception as exc:                        # коннектор изолирован (раздел 9)
            errors.append(f"{store_code}: {exc}")
            continue
        updated += store_updated
        if error:
            errors.append(error)
    return {"updated": updated, "errors": errors}


def refresh_prices_parallel(product_ids: list[int], store_codes: list[str], progress=None,
                            fresh_within: float | None = None, per_store_deadline: float = 25.0,
                            total_deadline: float = 60.0, force: bool = False) -> dict:
    """Обновить цены всех сетей разом, уложившись в общий срок.

    ЗАЧЕМ. «Где дешевле» рисуется сразу, а цены в момент — фоном, и последовательный
    обход восьми сетей по 25 с превращал ответ в четыре минуты, которых у экрана нет.
    Здесь по потоку на сеть: сеть, которая молчит, ждёт свой срок, а остальные уже
    записали цены. В потоке только сеть и короткие записи repo.save_price — каждое
    соединение своё (app/db.py:get_conn открывает новое на вызов), поэтому потоки не
    делят один sqlite-коннект и длинной транзакции никто не держит.

    БАЗА ЧЕЛОВЕКА В ПОТОКЕ. Потоки ThreadPoolExecutor не наследуют contextvars, и
    config.db_path() в рабочем потоке вернул бы ОБЩУЮ базу data/basket.db — цены
    человека легли бы не туда, а «нет таблиц» поставило бы паузу всем сетям. Поэтому
    каждая задача уходит в пул своим снимком контекста: ctx = copy_context(),
    pool.submit(ctx.run, ...). Один снимок на все задачи нельзя: Context не
    потокобезопасен и не может быть занят двумя потоками сразу. Путь базы при этом
    захватывается на ГЛАВНОМ потоке до отправки и передаётся в задачу ключом
    кулдауна: отметка не зависит от того, успел ли переехать контекст.

    ВОЗВРАТ: {"updated": записано снимков, "errors": [«сеть: что случилось»],
    "timed_out": [сети, не ответившие за свой срок], "skipped": [сети, которые не
    спрашивали — кулдаун или пауза], "seconds": сколько заняло}.

    КУЛДАУН. Сеть, которую спрашивали меньше COOLDOWN_SECONDS назад, пропускается:
    фон не должен ходить в сеть каждым открытием страницы. Отметка — по паре
    (база человека, сеть): обновление у одного не блокирует другого. force=True
    обходит кулдаун (ручное «обновить сейчас»), но не паузу.

    ПАУЗА. «До завтра» (BLOCK_PAUSE_SECONDS) — ТОЛЬКО явная блокировка сети
    (`CrawlBlocked` и аналоги): повтор туда продлевает проверку. Обычное исключение
    (таймаут, обрыв, разбор) — короткий FAIL_BACKOFF_SECONDS и log.warning с
    причиной: сеть, у которой моргнула связь, не выпадает до завтра. Постановка
    паузы тоже пишется в лог (_pause).

    СРОКИ. per_store_deadline — сколько ждать одну сеть, total_deadline — все вместе.
    Не дождались — сеть попадает в timed_out; её поток доделывает в стороне. Если
    сеть всё-таки ответит, снимок ляжет в базу (цена настоящая, вреда нет), но этот
    вызов её уже не ждёт; пока не вышел кулдаун, второй раз её не спросят.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    started = time.monotonic()
    result: dict = {"updated": 0, "errors": [], "timed_out": [], "skipped": [], "seconds": 0.0}
    codes = list(dict.fromkeys(store_codes or []))
    # Захват базы — здесь, в потоке прогона интерфейса, где contextvar ещё жив.
    db_path = config.db_path()

    def tick(done_number: int, code: str) -> None:
        if progress is not None:
            try:
                progress(done_number, len(codes), code)
            except Exception:  # noqa: BLE001 — отметка хода не повод бросать цены
                pass

    if not codes:
        return result

    ready: list[str] = []
    done = 0
    for code in codes:
        cooled, paused = _is_cooled(db_path, code)
        if paused or (cooled and not force):
            result["skipped"].append(code)
            done += 1
            tick(done, code)
        else:
            ready.append(code)

    if not ready:
        result["seconds"] = round(time.monotonic() - started, 3)
        return result

    pool = ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(ready)),
                              thread_name_prefix="prices")
    futures: dict = {}
    try:
        # Попытку отмечаем до запуска: сеть, которая уже висит, не должна дёргаться
        # заново следующим открытием экрана, пока не выйдет кулдаун.
        for code in ready:
            _asked_now(db_path, code)
            # Свой снимок контекста на КАЖДУЮ задачу: Context нельзя входить из двух
            # потоков сразу, а общий на всех именно это и сделал бы.
            ctx = contextvars.copy_context()
            futures[pool.submit(ctx.run, _refresh_one_store, code, product_ids,
                                fresh_within, db_path)] = code

        for future, code in futures.items():
            left = total_deadline - (time.monotonic() - started)
            wait = min(per_store_deadline, left)
            if wait <= 0:
                future.cancel()
                result["timed_out"].append(code)
            else:
                try:
                    updated, error = future.result(timeout=wait)
                except FuturesTimeout:
                    future.cancel()
                    result["timed_out"].append(code)
                except Exception as exc:                # сеть ответила исключением
                    if _is_blocked(exc):
                        _pause_until_tomorrow(db_path, code, f"сеть заблокировала ({exc})")
                    else:
                        _pause(db_path, code, FAIL_BACKOFF_SECONDS, f"сбой ({exc})")
                    result["errors"].append(f"{code}: {exc}")
                else:
                    result["updated"] += updated
                    if error:
                        result["errors"].append(error)
            done += 1
            tick(done, code)
    finally:
        # Ждать зависшие потоки нельзя: этим и обнулился бы весь срок экрана.
        # Не начавшиеся задачи отменяем, начавшиеся доделывают в стороне.
        pool.shutdown(wait=False, cancel_futures=True)

    result["seconds"] = round(time.monotonic() - started, 3)
    return result


__all__ = ["find_candidates", "confirm", "auto_match", "refresh_prices",
           "refresh_prices_parallel", "normalize_name", "parse_weight", "similarity",
           "weight_ok", "score_candidate"]
