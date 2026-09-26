"""Обход сетей и сопоставление: то, что планировщик делает раз в сутки, а человек — по кнопке.

run_chain(code) — перечислить каталог одной сети сборщиком, сложить в базу, пометить
пропавшие. Блокировка (капча, антибот) — не ошибка кода, а состояние источника: обход
записывается как blocked, товары сети остаются прежними, остальные сети идут своим
чередом, а дорогу к закрытой сети ищем другую — через браузер владельца, его учётку
или чеки.

match_all() — заново разложить все живые строки каталогов по единым товарам.
Пересчёт целиком, а не приращением, и это осознанно: каталоги в десятки тысяч строк
кластеризуются за секунды, а «приращение» хранило бы состояние, которое расходится с
правдой при каждой правке правил сопоставления.

adopt(item_id) — положить единый товар в базу ЧЕЛОВЕКА: эталон плюс сопоставления во
всех сетях, где он известен. Единственная дверь из общего каталога в личную базу.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from app.catalog import match as matching
from app.catalog import store
from app.catalog.model import CrawlBlocked, Crawler

log = logging.getLogger(__name__)


def run_chain(crawler: Crawler, progress: Callable[[str], None] | None = None,
              partial: bool = False) -> dict:
    """Один обход одной сети. Возвращает сводку; исключения наружу не выпускает.

    partial=True — обход НЕ всей сети, а части её точек (загрузка по одному адресу).
    Такой обход ничего не объявляет пропавшим и свежим не считается (статус
    partial): иначе адрес в Новосибирске выключал бы все краснодарские товары
    Магнита, а ночной обход, сочтя сеть свежей, их бы не вернул.
    """
    say = progress or (lambda msg: log.info("%s: %s", crawler.code, msg))
    run_id = store.start_run(crawler.code)
    started = store.now()
    seen = added = updated = 0
    batch: list = []
    status, note = "ok", None
    try:
        for product in crawler.crawl(say):
            batch.append(product)
            if len(batch) >= 200:
                s, a, u = store.upsert_products(crawler.code, batch, seen_at=started)
                seen, added, updated = seen + s, added + a, updated + u
                batch = []
                say(f"в каталоге {seen} товаров…")
        if batch:
            s, a, u = store.upsert_products(crawler.code, batch, seen_at=started)
            seen, added, updated = seen + s, added + a, updated + u
    except CrawlBlocked as exc:
        status, note = "blocked", str(exc)
    except Exception as exc:  # noqa: BLE001 — одна сеть не должна ронять остальные
        log.exception("%s: обход упал", crawler.code)
        status, note = "failed", f"{type(exc).__name__}: {exc}"

    gone = 0
    if status == "ok" and partial:
        status = "partial"
    if status == "ok" and seen > 0:
        # пропавшими считаем только после ПОЛНОГО обхода: половинный обход пометил бы
        # пропавшей половину каталога
        gone = store.retire_unseen(crawler.code, started)
    store.finish_run(run_id, status, seen=seen, added=added, updated=updated, gone=gone, note=note)
    say(f"обход {status}: увидено {seen}, новых {added}, изменённых {updated}, пропало {gone}")
    return {"chain": crawler.code, "status": status, "seen": seen, "added": added,
            "updated": updated, "gone": gone, "note": note}


def match_all(progress: Callable[[str], None] | None = None) -> dict:
    """Пересобрать единые товары из всех живых строк каталогов."""
    say = progress or (lambda msg: log.info("сопоставление: %s", msg))
    store.init()
    started = store.now()
    t0 = time.time()
    products = store.all_products(active_only=True)
    say(f"строк каталогов: {len(products)}")
    groups = matching.cluster(products)

    with store.connect() as conn:
        run = conn.execute("INSERT INTO match_runs (started_at, products) VALUES (?, ?)",
                           (started, len(products)))
        run_id = int(run.lastrowid)
        conn.execute("DELETE FROM catalog_items")
        multi = 0
        for group in groups:
            canon = matching.canonical(group)
            cur = conn.execute(
                "INSERT INTO catalog_items (name, norm, brand, weight_g, unit, category, barcode,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (canon["name"], canon["norm"], canon["brand"], canon["weight_g"], canon["unit"],
                 canon["category"], canon["barcode"], started, started))
            item_id = int(cur.lastrowid)
            chains = {row.chain for row, _, _ in group}
            multi += int(len(chains) > 1)
            for row, method, score in group:
                conn.execute("UPDATE chain_products SET item_id=?, link_method=?, link_score=? WHERE id=?",
                             (item_id, method, score, row.id))
        conn.execute("UPDATE match_runs SET finished_at=?, items=?, multi=?, note=? WHERE id=?",
                     (store.now(), len(groups), multi, f"{time.time() - t0:.1f} с", run_id))
        conn.commit()
    say(f"единых товаров: {len(groups)}, известных нескольким сетям: {multi}")
    return {"products": len(products), "items": len(groups), "multi": multi}


def adopt(item_id: int, product_id: int | None = None) -> int:
    """Единый товар — в базу текущего человека: эталон и подтверждённые сопоставления.

    Возвращает id эталона. product_id — привязать к уже существующему эталону человека
    (так делает link_products для товаров из чеков); без него эталон ищется по имени
    и заводится, если его нет. Повторный вызов не плодит дублей: сопоставления —
    upsert по (магазин, артикул).
    """
    from app import repo
    from app.models import Product

    data = store.item(item_id)
    if not data:
        raise KeyError(f"нет единого товара {item_id}")
    unit = data.get("unit") or "pcs"
    if product_id is None:
        existing = repo.find_product_by_name(data["name"])
        product_id = existing.id if existing else repo.upsert_product(Product(
            id=None, name=data["name"], brand=data.get("brand"), barcode=data.get("barcode"),
            weight_g=data.get("weight_g"), unit=unit if unit in ("pcs", "kg") else "pcs",
            category=data.get("category")))
    for row in data["chains"]:
        st = repo.get_store(row["chain"])
        if not st:
            continue
        sp_id = repo.upsert_store_product(st.id, str(row["sku"]), row["name"],
                                          weight_g=row.get("weight_g"), unit=row.get("unit"),
                                          url=row.get("url"))
        repo.confirm_mapping(product_id, sp_id, confirmed=True)
        found = price_for_me(row)
        if found is not None:
            price, in_stock, seen = found
            repo.save_price(sp_id, price, in_stock=in_stock, fetched_at=seen)
    return product_id


def _my_point(chain: str) -> str | None:
    """Точка этой сети у текущего человека — та, что обход подобрал к его адресу."""
    from app import location, places

    if chain not in places.BY_POINT:
        return None
    try:
        address = location.address()
    except Exception:  # noqa: BLE001 — нет базы человека — нет и точки
        return None
    return store.point_of(address, chain) if address else None


def price_for_me(row: dict) -> tuple[float, bool, str | None] | None:
    """Цена строки каталога для текущего человека: (цена, в наличии, когда увидена).

    None — цены для него нет, и выдумывать её не надо.

    Цена своя в каждой точке. Если обход знает цены артикула по точкам, берётся
    цена ТОЧКИ ЧЕЛОВЕКА, а нет её там — нет и цены: чужой город показал бы чужую
    цену, ничем не отличимую от своей. Точек нет (сети без них, каталог прежней
    версии) — берётся цена строки каталога, как раньше.

    Наличие, о котором сеть промолчала (NULL), — не «нет»: Дикси и Перекрёсток
    наличия не отдают вовсе, и bool(None) делал все их товары отсутствующими.
    Время — когда увидена ИМЕННО ЦЕНА, а не строка: оно уходит в снимок, и расчёт
    сам пометит несвежую.
    """
    by_point = store.point_prices(row["chain"], row["sku"])
    if by_point:
        mine = _my_point(row["chain"])
        if mine is None and len(by_point) == 1:
            mine = next(iter(by_point))
        spot = by_point.get(mine) if mine else None
        if spot is None or spot.get("price") is None:
            return None
        stock = spot.get("in_stock")
        return (float(spot["price"]), True if stock is None else bool(stock),
                spot.get("seen_at"))
    if row.get("price") is None:
        return None
    stock = row.get("in_stock")
    # Даты цены нет (каталог прежней версии) — берём самую раннюю: last_seen
    # продлевает и ночной проход по карте сайта, который цену не перечитывает,
    # и старая цена выдала бы себя за вчерашнюю.
    return (float(row["price"]), True if stock is None else bool(stock),
            row.get("price_seen") or row.get("first_seen"))


def resolve(name: str, brand: str | None = None, weight_g: float | None = None,
            unit: str | None = None, barcode: str | None = None) -> dict | None:
    """Найти единый товар по описанию товара человека — теми же правилами, что и между сетями.

    Товар из чека («Страчателла 200 г») сравнивается с кандидатами каталога как ещё одна
    «сеть»: фасовка, марка, слова. Кандидаты — по первым двум смысловым словам.
    Возвращает единый товар с его строками по сетям либо None.
    """
    mine = matching.prepare({"id": 0, "chain": "mine", "name": name, "brand": brand,
                             "weight_g": weight_g, "unit": unit, "barcode": barcode})
    if not mine.order:
        return None
    candidates = store.search_items(" ".join(mine.order[:2]), limit=40)
    rows = [matching.prepare({"id": c["id"], "chain": "catalog", "name": c["name"],
                              "brand": c.get("brand"), "weight_g": c.get("weight_g"),
                              "unit": c.get("unit"), "barcode": c.get("barcode")})
            for c in candidates]
    vocab = matching.brand_vocabulary(rows + [mine])
    best: tuple[float, dict] | None = None
    for row, cand in zip(rows, candidates):
        ok, _, score = matching.same_product(mine, row, vocab)
        if ok and (best is None or score > best[0]):
            best = (score, cand)
    return store.item(int(best[1]["id"])) if best else None


def link_products() -> dict:
    """Опознать товары человека в едином каталоге и привязать артикулы всех сетей.

    Берутся товары, известные не во всех сетях, где каталог есть. Возвращает
    {tried, linked}. Ничего не удаляет и не переспрашивает: найденное — подтверждено,
    как решил владелец 15.09.2026 для живого поиска.
    """
    from app import repo
    from app.catalog.worker import chains

    covered = set(chains())
    stores = {s.code: s.id for s in repo.list_stores()}
    matrix = repo.mapping_matrix()
    tried = linked = 0
    for product in repo.list_products():
        known = {code for code in covered if matrix.get((product.id, stores.get(code)))}
        if known >= covered:
            continue
        tried += 1
        item = resolve(product.name, product.brand, product.weight_g, product.unit, product.barcode)
        if item:
            adopt(int(item["id"]), product_id=product.id)
            linked += 1
    return {"tried": tried, "linked": linked}
