"""Интеграционный слой: собирает корзину, цены, акции и зовёт оптимизатор.

Единственная точка входа для UI: calculate(basket_id).
"""
from __future__ import annotations

import logging

from app import config, repo
from app.models import BasketLine, Offer, Store, Variant

log = logging.getLogger(__name__)


def _price_for_line(product_id: int, store: Store, qty: float, unit: str) -> tuple[float | None, bool]:
    """Стоимость позиции целиком в магазине: (цена * qty, в наличии).

    Для весовых берём price_per_kg, для штучных — цену упаковки.
    """
    snap = repo.latest_price_for(product_id, store.id)
    if not snap:
        return None, False
    if unit == "kg":
        base = snap.get("price_per_kg") or snap.get("price")
    else:
        base = snap.get("price")
    if base is None:
        return None, False
    return round(float(base) * qty, 2), bool(snap.get("in_stock", 1))


def build_basket_lines(basket_id: int) -> list[BasketLine]:
    """Строки корзины с ценами по всем магазинам, где есть подтверждённое сопоставление."""
    stores = repo.list_stores()
    lines: list[BasketLine] = []
    for item in repo.basket_items(basket_id):
        line = BasketLine(
            product_id=item["product_id"],
            name=item["name"],
            unit=item["unit"] or "pcs",
            qty=float(item["qty"]),
        )
        for store in stores:
            price, in_stock = _price_for_line(line.product_id, store, line.qty, line.unit)
            if price is not None:
                line.prices[store.code] = price
                line.in_stock[store.code] = in_stock
        lines.append(line)
    return lines


def _history_price(product_id: int) -> float | None:
    """Последняя цена за единицу из истории покупок."""
    with repo.get_conn() as c:
        r = c.execute(
            "SELECT unit_price FROM purchase_history WHERE product_id=? ORDER BY date DESC, id DESC LIMIT 1",
            (product_id,),
        ).fetchone()
    return float(r["unit_price"]) if r else None


def baseline_total(basket_id: int) -> float:
    """Baseline: стоимость всей корзины в одном базовом магазине без акций (раздел 2 спецификации).

    Порядок источников цены: цена базового магазина -> последняя цена из истории покупок ->
    минимальная известная цена среди остальных магазинов.
    """
    base_store = repo.get_store(config.get("baseline_store", "pyaterochka"))
    total = 0.0
    for item in repo.basket_items(basket_id):
        qty, unit, pid = float(item["qty"]), item["unit"] or "pcs", item["product_id"]
        price = None
        if base_store:
            price, _ = _price_for_line(pid, base_store, qty, unit)
        if price is None:
            hp = _history_price(pid)
            price = round(hp * qty, 2) if hp is not None else None
        if price is None:
            others = []
            for store in repo.list_stores():
                p, _ = _price_for_line(pid, store, qty, unit)
                if p is not None:
                    others.append(p)
            price = min(others) if others else 0.0
        total += price
    return round(total, 2)


def offers_map(day: str | None = None) -> dict[int, list[Offer]]:
    """store_id -> действующие акции (срок, активация, остаток лимита учтены)."""
    return {s.id: repo.offers_for_store(s.id, day) for s in repo.list_stores()}


def price_coverage(basket_id: int) -> dict[str, tuple[int, int]]:
    """store_code -> (позиций с ценой, всего позиций). Для подсказки в UI."""
    lines = build_basket_lines(basket_id)
    out: dict[str, tuple[int, int]] = {}
    for store in repo.list_stores():
        have = sum(1 for ln in lines if store.code in ln.prices)
        out[store.code] = (have, len(lines))
    return out


def calculate(basket_id: int, refresh: bool = True) -> tuple[list[Variant], float]:
    """Главный расчёт: (топ-N вариантов, baseline).

    refresh=True сначала обновляет цены коннекторами по подтверждённым сопоставлениям.
    Падение коннектора не блокирует расчёт — идём на последних известных ценах (раздел 9).
    """
    items = repo.basket_items(basket_id)
    if not items:
        return [], 0.0

    if refresh:
        try:
            from app.matcher import refresh_prices

            store_codes = [s.code for s in repo.list_stores()]
            refresh_prices([it["product_id"] for it in items], store_codes)
        except Exception as exc:  # коннектор/матчер недоступен — работаем на снимках цен
            log.warning("Обновление цен не удалось, считаем по последним снимкам: %s", exc)

    lines = build_basket_lines(basket_id)
    baseline = baseline_total(basket_id)

    from app.optimizer import optimize

    variants: list[Variant] = optimize(
        lines=lines,
        stores=repo.list_stores(),
        offers=offers_map(),
        baseline=baseline,
        penalty=float(config.get("extra_order_penalty_rub", 150.0)),
        top_n=int(config.get("optimizer.top_n", 3)),
        max_stores=int(config.get("optimizer.max_stores", 2)),
    )
    _resolve_card_names(variants)
    return variants, baseline


def _resolve_card_names(variants: list[Variant]) -> None:
    """Оптимизатор знает только card_id — имя карты подставляем здесь."""
    names = {c.id: f"{c.bank} {c.name}" for c in repo.list_cards()}
    for v in variants:
        for sb in v.stores:
            if sb.card_id and not sb.card_name:
                sb.card_name = names.get(sb.card_id)
            for ln in sb.lines:
                if ln.card_id and not ln.card_name:
                    ln.card_name = names.get(ln.card_id)


def save_best(basket_id: int, variants: list[Variant]) -> list[int]:
    """Сохраняет варианты в БД (таблицы variants / variant_lines)."""
    by_code = {s.code: s.id for s in repo.list_stores()}
    ids = []
    for v in variants:
        rows = []
        for sb in v.stores:
            for ln in sb.lines:
                rows.append((by_code.get(sb.store_code), sb.card_id, ln.product_id, ln.qty, ln.price, ln.discount))
        ids.append(repo.save_variant(basket_id, v.total, v.baseline, v.savings_rub, v.savings_pct, rows))
    return ids


# ---------- передача корзины в магазин ----------
CART_LINK_STORES = ("vkusvill", "lenta")   # где сеть сама умеет принять готовый список


def _cart_builder(store_code: str):
    """Функция магазина, собирающая ссылку. Импорт внутри — коннектор тянет за собой сеть.

    Перечислено руками, а не собрано по имени модуля: сюда попадает только то, что
    проверено живьём, и список должен ломаться заметно, а не молча пытаться найти
    несуществующее.
    """
    if store_code == "vkusvill":
        from app.connectors.vkusvill import cart_link as build
        return build
    if store_code == "lenta":
        from app.connectors.lenta import cart_link as build
        return build
    return None


def cart_link(store_code: str, lines) -> str | None:
    """Ссылка, по которой человек откроет этот чек уже собранным в магазине.

    Работает там, где сеть сама такое предлагает: ВкусВилл (vkusvill_cart_link_create)
    и Лента (storefront_cart_link_create, появился 15.09.2026). Ничего чужого мы при
    этом не трогаем — ссылка открывается в его браузере, дальше его аккаунт, его
    карта, его адрес.

    None означает «этот магазин так не умеет» и это нормальный ответ, а не ошибка:
    интерфейс тогда показывает список позиций, а не кнопку.
    """
    if store_code not in CART_LINK_STORES:
        return None
    build = _cart_builder(store_code)
    if build is None:
        return None
    store = repo.get_store(store_code)
    if not store:
        return None
    items: list[tuple[int, float]] = []
    for line in lines or []:
        mapping = repo.confirmed_mapping(getattr(line, "product_id", 0), store.id)
        sku = (mapping or {}).get("sku")
        if not sku or not str(sku).isdigit():
            continue
        items.append((int(sku), float(getattr(line, "qty", 1) or 1)))
    if not items:
        return None
    try:
        return build(items)
    except Exception as exc:  # noqa: BLE001  — магазин недоступен, это не повод ронять экран
        log.warning("Ссылку на корзину %s получить не удалось: %s", store_code, exc)
        return None


# ---------- насколько можно верить цифре ----------
CONFIDENT_SCORE = 0.75


def basket_doubts(basket_id: int) -> list[dict]:
    """Сопоставления корзины, в которых есть сомнения.

    Экономию мы считаем по ценам тех товаров, которые сопоставили сами. Если
    «Страчателла» уехала в мороженое, а пюре — в сок, то итоговая цифра красивая,
    но неправдивая. Пока приложение об этом молчало, проверить это было негде.

    Возвращает по строке на каждое сомнительное сопоставление: что с чем связано,
    в каком магазине и что именно не сходится.
    """
    from app.matcher.normalize import similarity
    from app.matcher.quality import doubts

    stores = {s.id: s for s in repo.list_stores()}
    out: list[dict] = []
    for item in repo.basket_items(basket_id):
        product = repo.get_product(item["product_id"])
        if product is None:
            continue
        for store_id, store in stores.items():
            mapping = repo.confirmed_mapping(product.id, store_id)
            if not mapping:
                continue
            raw = mapping.get("raw_name") or ""
            flags = doubts(product, raw, mapping.get("weight_g"))
            score = round(similarity(product.name, raw), 3)
            if score < CONFIDENT_SCORE:
                flags.append("похожесть названий низкая")
            if not flags:
                continue
            out.append({
                "product_id": product.id,
                "product": product.name,
                "store_code": store.code,
                "store": store.name,
                "matched": raw,
                "score": score,
                "flags": flags,
            })
    return out


def doubts_summary(basket_id: int) -> dict:
    """Сколько позиций корзины опираются на сомнительные сопоставления."""
    rows = basket_doubts(basket_id)
    return {
        "rows": rows,
        "products": len({r["product_id"] for r in rows}),
        "total": len(repo.basket_items(basket_id)),
    }
