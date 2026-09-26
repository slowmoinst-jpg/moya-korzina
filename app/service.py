"""Интеграционный слой: собирает корзину, цены, акции и зовёт оптимизатор.

Единственная точка входа для UI: calculate(basket_id).
"""
from __future__ import annotations

import logging
from datetime import datetime

from app import config, location, repo
from app.models import BasketLine, Offer, Store, Variant

log = logging.getLogger(__name__)


# Приведение фасовок: разница меньше этой доли — это одна и та же фасовка
# («930 мл» и «0,93 л»), и цену полки не трогаем.
PACK_SAME = 0.03


def _days(value: str | None) -> float | None:
    """Сколько дней назад снята цена. None — дату не разобрать."""
    text = str(value or "").strip().replace(" ", "T")
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text[:19])
    except ValueError:
        try:
            when = datetime.fromisoformat(text[:10])
        except ValueError:
            return None
    return (datetime.now() - when).total_seconds() / 86400.0


def stale_after_days() -> float:
    """Старше скольких дней цена считается несвежей и помечается в расчёте."""
    try:
        return float(config.get("prices.stale_after_days", 7) or 7)
    except (TypeError, ValueError):
        return 7.0


def _grams(value) -> float | None:
    try:
        grams = float(value)
    except (TypeError, ValueError):
        return None
    return grams if grams > 0 else None


def line_price(product_id: int, store: Store, qty: float, unit: str,
               ref_weight_g: float | None = None) -> dict | None:
    """Во что обойдётся позиция корзины в магазине. None — сравнимой цены нет.

    Возвращает {"value", "in_stock", "fetched_at", "stale", "stale_label", "note"}.
    stale_label — дата несвежего снимка или «справочная цена» для цены из CSV.

    ЦЕНА ФАСОВКИ — НЕ ЦЕНА КИЛОГРАММА. Раньше весовой товар без price_per_kg
    брал цену упаковки как цену килограмма: сыр 400 г за 300 ₽ на 0,7 кг выходил
    в 210 ₽ вместо 525 ₽, и сеть ложно выигрывала. Теперь цена килограмма берётся
    из снимка, из единицы товара сети (весовой товар сеть и так называет в рублях
    за кг) или пересчитывается по фасовке; а если ничего из этого не известно,
    честнее сказать «сравнить нечем», чем подставить неправду.

    РАЗНЫЕ ФАСОВКИ СРАВНИВАЮТСЯ ПО ВЕСУ. Сопоставление допускает разницу в
    граммовке (weight_tolerance_pct), и «800 г за 90 ₽» выигрывало у «1 кг за
    100 ₽», хотя килограмм у него на 12 % дороже. Если вес известен у обеих
    сторон и заметно разнится, цена приводится к весу эталона, а строка несёт
    пояснение — на полке будет своя цена.
    """
    snap = repo.latest_price_for(product_id, store.id)
    if not snap:
        return None
    price = snap.get("price")
    if price is None:
        return None
    price = float(price)
    per_kg = snap.get("price_per_kg")
    shop_unit = (snap.get("sp_unit") or "").lower()
    pack_g = _grams(snap.get("sp_weight_g"))
    ref_g = _grams(ref_weight_g)
    note = None

    if unit == "kg":
        if per_kg:
            base = float(per_kg)
        elif shop_unit == "kg":
            base = price                      # весовой товар сеть называет в рублях за кг
        elif pack_g:
            base = price * 1000.0 / pack_g
            note = f"цена за кг из фасовки {pack_g:g} г за {price:.2f} ₽"
        elif shop_unit == "pcs":
            return None                       # цена упаковки неизвестного веса
        else:
            # Про товар сети не известно ничего — ни единицы, ни фасовки (старые
            # сопоставления, прайс, чек). Чек и прайс весовой товар называют в рублях
            # за кг, поэтому так и читаем.
            base = price
    elif shop_unit == "kg":
        # Эталон — штука, а сеть продаёт на вес: цена у неё за килограмм.
        if not ref_g:
            return None
        base = price * ref_g / 1000.0
        note = f"на вес: {price:.2f} ₽/кг, взято на {ref_g:g} г"
    elif ref_g and pack_g and abs(pack_g - ref_g) / ref_g > PACK_SAME:
        base = price * ref_g / pack_g
        note = (f"на полке {pack_g:g} г за {price:.2f} ₽, "
                f"цена приведена к {ref_g:g} г")
    else:
        base = price

    age = _days(snap.get("fetched_at"))
    reference = snap.get("source") == "fallback"
    stale = reference or (age is not None and age > stale_after_days())
    if reference:
        label = "справочная цена"
    elif stale:
        label = str(snap.get("fetched_at") or "")[:10]
    else:
        label = None
    return {
        "value": round(base * float(qty), 2),
        "in_stock": bool(snap.get("in_stock", 1)),
        "fetched_at": snap.get("fetched_at"),
        "stale": stale,
        "stale_label": label,
        "note": note,
    }


def _price_for_line(product_id: int, store: Store, qty: float, unit: str,
                    ref_weight_g: float | None = None) -> tuple[float | None, bool]:
    """Стоимость позиции целиком в магазине: (цена * qty, в наличии)."""
    found = line_price(product_id, store, qty, unit, ref_weight_g)
    if not found:
        return None, False
    return found["value"], found["in_stock"]


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
            found = line_price(line.product_id, store, line.qty, line.unit, item.get("weight_g"))
            if found is None:
                continue
            line.prices[store.code] = found["value"]
            line.in_stock[store.code] = found["in_stock"]
            if found["stale"]:
                line.stale[store.code] = found["stale_label"] or ""
            if found["note"]:
                line.notes[store.code] = found["note"]
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


def baseline_by_product(basket_id: int) -> dict[int, float]:
    """Во что обошлась бы каждая позиция в базовом магазине без акций.

    По позициям, а не одной суммой: вариант, где части корзины нет, сравнивается
    с базой БЕЗ этой части. Иначе недостающий товар за 900 ₽ записывался
    варианту в «экономию» — дешевле ведь, раз его не купили.

    Порядок источников цены: цена базового магазина -> последняя цена из истории
    покупок -> минимальная известная цена среди остальных магазинов.
    """
    base_store = repo.get_store(config.get("baseline_store", "pyaterochka"))
    out: dict[int, float] = {}
    for item in repo.basket_items(basket_id):
        qty, unit, pid = float(item["qty"]), item["unit"] or "pcs", item["product_id"]
        grams = item.get("weight_g")
        price = None
        if base_store:
            found = line_price(pid, base_store, qty, unit, grams)
            # Товара нет в базовом магазине — его «цена» там не база: Магнит на
            # отсутствующий товар пишет справочную цену или ноль с пометкой «нет».
            if found and found["in_stock"] and found["value"] > 0:
                price = found["value"]
        if price is None:
            hp = _history_price(pid)
            price = round(hp * qty, 2) if hp is not None else None
        if price is None:
            others = []
            for store in repo.list_stores():
                p, _ = _price_for_line(pid, store, qty, unit, grams)
                if p:
                    others.append(p)
            price = min(others) if others else 0.0
        out[int(pid)] = round(float(price), 2)
    return out


def baseline_total(basket_id: int) -> float:
    """Baseline: стоимость всей корзины в одном базовом магазине без акций (раздел 2 спецификации)."""
    return round(sum(baseline_by_product(basket_id).values()), 2)


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
    by_product = baseline_by_product(basket_id)
    baseline = round(sum(by_product.values()), 2)

    from app import handover
    from app.optimizer import optimize

    variants: list[Variant] = optimize(
        lines=lines,
        stores=repo.list_stores(),
        offers=offers_map(),
        baseline=baseline,
        baseline_lines=by_product,
        penalty=float(config.get("extra_order_penalty_rub", 150.0)),
        top_n=int(config.get("optimizer.top_n", 3)),
        max_stores=int(config.get("optimizer.max_stores", 2)),
        # Сколько стоит завести корзину в каждый магазин руками. Без этого расчёт
        # видит только деньги и охотно дробит корзину на магазины, куда её потом
        # придётся перебивать позиция за позицией.
        handover=handover.penalty_by_store(),
    )
    _resolve_card_names(variants)
    return variants, baseline


def effortless_variants(basket_id: int) -> tuple[list[Variant], float]:
    """Варианты, которые НЕ ПРИДЁТСЯ ПЕРЕБИВАТЬ РУКАМИ.

    Зачем отдельный расчёт. Самый дешёвый вариант вообще и самый дешёвый из
    удобных — разные вещи, и разница между ними есть цена перебивания. Одной
    ссылкой корзину принимают только те сети, которые сами дали такой инструмент:
    сегодня Лента и ВкусВилл. В остальных человек кладёт товары по одному, и на
    шестнадцати позициях это уже не «чуть дольше», а отдельное занятие.

    Поэтому мы считаем оба ответа и показываем оба: «вот дешевле всего» и «вот
    дешевле всего без единого перебивания, разница такая-то». Выбор остаётся за
    человеком, а не за нашим представлением о том, что ему дороже — деньги или
    полчаса вечера.

    Пустой список — не ошибка: он означает, что ни одна из принимающих сетей не
    покрывает корзину, и честнее сказать это, чем подсунуть половину заказа.
    """
    from app import handover
    from app.optimizer import optimize

    принимающие = [s for s in repo.list_stores()
                   if handover.KIND_BY_STORE.get(s.code) == handover.LINK]
    if not принимающие:
        return [], 0.0

    lines = build_basket_lines(basket_id)
    by_product = baseline_by_product(basket_id)
    baseline = round(sum(by_product.values()), 2)
    variants = optimize(
        lines=lines,
        stores=принимающие,
        offers=offers_map(),
        baseline=baseline,
        baseline_lines=by_product,
        penalty=float(config.get("extra_order_penalty_rub", 150.0)),
        top_n=1,
        max_stores=len(принимающие),
        handover=handover.penalty_by_store(),
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
    # ЕДИНИЦА ЕДЕТ ВМЕСТЕ С КОЛИЧЕСТВОМ, И ЭТО НЕ УКРАШЕНИЕ. У Ленты развесной
    # товар считается в ГРАММАХ (её собственное описание storefront_cart_link_create),
    # и без единицы 0,7 кг сыра уезжали как «1» — то есть один грамм. Корзина
    # выглядела собранной, а сумма не сходилась с расчётом.
    items: list[tuple[int, float, str | None]] = []
    for line in lines or []:
        mapping = repo.confirmed_mapping(getattr(line, "product_id", 0), store.id)
        sku = (mapping or {}).get("sku")
        if not sku or not str(sku).isdigit():
            continue
        items.append((int(sku), float(getattr(line, "qty", 1) or 1),
                      (mapping or {}).get("unit")))
    if not items:
        return None
    try:
        # Адрес нужен Ленте, чтобы спросить у себя размер фасовки развесного товара
        # (её карточка отвечает только по точке). Без него количество считается
        # по-старому, одной фасовкой на килограмм, — работает, но грубее.
        return build(items, location.for_store(store_code))
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
