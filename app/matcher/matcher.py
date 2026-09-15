"""Сопоставление эталонов с артикулами магазинов (спецификация, раздел 5.2).

Кандидаты берутся из коннектора магазина, ранжируются по похожести названия
и допуску по граммовке, складываются в store_products как неподтверждённые
сопоставления. Подтверждённые сопоставления — единственный источник цен
для оптимизатора (раздел 4, правило про product_mapping.confirmed).
"""
from __future__ import annotations

from app import config, db, repo
from app.matcher import quality
from app.matcher.normalize import normalize_name, parse_weight, similarity, weight_ok
from app.models import Candidate

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
            if repo.confirmed_mapping(product_id, store.id):
                continue                                # уже подтверждено — пропускаем
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
                confirm(product_id, store_code, best.sku)
                auto += 1
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

def refresh_prices(product_ids: list[int], store_codes: list[str]) -> dict:
    """Тянет цены по ПОДТВЕРЖДЁННЫМ сопоставлениям и пишет снимки в store_prices."""
    updated = 0
    errors: list[str] = []

    for store_code in store_codes:
        store = repo.get_store(store_code)
        if store is None:
            errors.append(f"{store_code}: магазин не найден")
            continue
        connector = _get_connector(store_code)
        if connector is None:
            errors.append(f"{store_code}: коннектор недоступен")
            continue

        by_sku: dict[str, int] = {}
        for product_id in product_ids:
            mapping = repo.confirmed_mapping(product_id, store.id)
            if mapping:
                by_sku[mapping["sku"]] = mapping["id"]
        if not by_sku:
            continue

        try:
            snapshots = connector.get_prices(list(by_sku)) or []
        except Exception as exc:                        # коннектор изолирован (раздел 9)
            errors.append(f"{store_code}: {exc}")
            continue

        for snap in snapshots:
            sp_id = by_sku.get(getattr(snap, "sku", None))
            if sp_id is None or getattr(snap, "price", None) is None:
                continue
            repo.save_price(sp_id, snap.price, getattr(snap, "price_per_kg", None),
                            bool(getattr(snap, "in_stock", True)),
                            getattr(snap, "fetched_at", None))
            updated += 1
    return {"updated": updated, "errors": errors}


__all__ = ["find_candidates", "confirm", "auto_match", "refresh_prices",
           "normalize_name", "parse_weight", "similarity", "weight_ok", "score_candidate"]
