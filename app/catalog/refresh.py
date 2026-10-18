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


def _greedy(votes: dict[tuple[int, int], int], voters: dict[int, int], groups,
            keep: list[int | None], taken: set[int], allowed: set[int] | None = None) -> None:
    """Раздать номера по голосам: крупнейшее совпадение, затем чистота, затем размер."""
    def key(kv):
        (gi, old), count = kv
        purity = count / max(1, voters.get(gi, 0))
        return (-count, -purity, -len(groups[gi]), old)

    for (gi, old), _ in sorted(votes.items(), key=key):
        if keep[gi] is None and old not in taken and (allowed is None or old in allowed):
            keep[gi] = old
            taken.add(old)


def _keep_numbers(groups, live: dict[int, int], dropped: dict[int, int] | None = None,
                  allow=None) -> tuple[list[int | None], dict[int, int]]:
    """Какой прежний номер достаётся каждой новой группе и куда ведут исчезнувшие.

    ЗАЧЕМ ПОСТОЯННЫЕ НОМЕРА (решение владельца 17.10.2026). Единая база — это
    заранее сопоставленные товары всех сетей, а товары людей привязываются к ней
    один раз номером единого товара. Пересборка же шла с чистого листа и выдавала
    каждой группе новый номер: привязки людей рвались бы каждую ночь.

    ГОЛОСА ДВУХ СОРТОВ (ревью 18.10.2026). Живой — номер, который строка носила до
    пересборки. Сброшенный — номер, который строка помнит после сброса привязки
    (смена названия, фасовки или штрихкода, ночь вне обхода). Сброшенный весит
    меньше: строка, переименованная в другой товар, иначе уводила бы номер кефира
    к ряженке, а неизменный кефир получал бы новый номер.

    Правило: сначала номера раздаются по живым голосам — группе достаётся номер, с
    которым у неё больше всего общих строк; при равенстве — той, что чище (больше
    доля её голосов за этот номер), и только потом крупнее. Сброшенными голосами
    добираются лишь номера, на которые не претендует ни одна живая строка. Прежний
    номер, не доставшийся никому (группы слились или развалились), уходит в
    item_aliases и ведёт туда, куда переехала большая часть его строк. Номер только
    со сброшенными голосами достаётся группе и переадресуется к ней, лишь если группа
    — тот же товар (allow(индекс группы, номер), ревью 18.10.2026): строка,
    переименованная в ряженку, не уносит номер кефира, даже если остальные строки
    кефира спят.

    live и dropped — {id строки сети: номер}. Возвращает список номеров по группам
    (None — нужен новый) и {прежний номер: индекс группы} для переадресации.
    """
    dropped = dropped or {}
    votes: dict[tuple[int, int], int] = {}
    late: dict[tuple[int, int], int] = {}
    voters: dict[int, int] = {}
    late_voters: dict[int, int] = {}
    for gi, group in enumerate(groups):
        for row, _, _ in group:
            if row.id in live:
                key = (gi, live[row.id])
                votes[key] = votes.get(key, 0) + 1
                voters[gi] = voters.get(gi, 0) + 1
            elif row.id in dropped:
                key = (gi, dropped[row.id])
                late[key] = late.get(key, 0) + 1
                late_voters[gi] = late_voters.get(gi, 0) + 1
    keep: list[int | None] = [None] * len(groups)
    taken: set[int] = set()
    _greedy(votes, voters, groups, keep, taken)
    claimed = {old for (_, old) in votes}
    if allow is not None:
        late = {pair: n for pair, n in late.items() if pair[1] in claimed or allow(*pair)}
    _greedy(late, late_voters, groups, keep, taken,
            allowed={old for (_, old) in late if old not in claimed})
    moved: dict[int, int] = {}
    for (gi, old), _ in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0][1])):
        if old not in taken and old not in moved:
            moved[old] = gi
    for (gi, old), _ in sorted(late.items(), key=lambda kv: (-kv[1], kv[0][1])):
        if old not in taken and old not in claimed and old not in moved:
            moved[old] = gi
    return keep, moved


def _same_item(before: dict | None, canon: dict) -> bool:
    """Прежний единый товар и новая группа — один товар: можно вести к ней прежний номер.

    Судят слова, фасовка и марка, а не штрихкод: сброс привязки часто и вызван
    исправлением штрихкода, и прежний код спорил бы с новым.
    """
    if not before:
        return False
    fields = ("name", "brand", "weight_g", "unit")
    old = matching.prepare({"id": 1, "chain": "before", **{k: before.get(k) for k in fields}})
    new = matching.prepare({"id": 2, "chain": "after", **{k: canon.get(k) for k in fields}})
    return matching.same_product(old, new)[0]


def _last_records(conn, numbers) -> dict[int, dict]:
    """Последняя запись номеров: из catalog_items, а ушедших — из item_last."""
    out: dict[int, dict] = {}
    numbers = sorted(set(numbers))
    for i in range(0, len(numbers), 500):
        chunk = numbers[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT id, name, brand, weight_g, unit FROM item_last"
                              f" WHERE id IN ({marks})", chunk):
            out[int(r["id"])] = dict(r)
        for r in conn.execute(f"SELECT id, name, brand, weight_g, unit FROM catalog_items"
                              f" WHERE id IN ({marks})", chunk):
            out[int(r["id"])] = dict(r)
    return out


def _keep_last_records(conn, kept: set[int], stamp: str) -> None:
    """Номера, не доставшиеся никому в этой пересборке, оставляют свою последнюю запись.

    Спящий номер записи в catalog_items не имеет, и без неё пересборка не узнала бы,
    тот ли товар вернулся (ревью 18.10.2026). Номер, снова живой, свою запись теряет.
    """
    gone = [int(r[0]) for r in conn.execute("SELECT id FROM catalog_items") if int(r[0]) not in kept]
    for i in range(0, len(gone), 500):
        chunk = gone[i:i + 500]
        conn.execute("INSERT INTO item_last (id, name, brand, weight_g, unit, since)"
                     " SELECT id, name, brand, weight_g, unit, ? FROM catalog_items"
                     f" WHERE id IN ({','.join('?' * len(chunk))})"
                     " ON CONFLICT(id) DO UPDATE SET name=excluded.name, brand=excluded.brand,"
                     " weight_g=excluded.weight_g, unit=excluded.unit, since=excluded.since",
                     [stamp, *chunk])
    alive = sorted(kept)
    for i in range(0, len(alive), 500):
        chunk = alive[i:i + 500]
        conn.execute(f"DELETE FROM item_last WHERE id IN ({','.join('?' * len(chunk))})", chunk)


def match_all(progress: Callable[[str], None] | None = None) -> dict:
    """Пересобрать единые товары из всех живых строк каталогов.

    Номера единых товаров сохраняются между пересборками (_keep_numbers): к ним
    привязаны товары людей.
    """
    say = progress or (lambda msg: log.info("сопоставление: %s", msg))
    store.init()
    started = store.now()
    t0 = time.time()
    products = store.all_products(active_only=True)
    say(f"строк каталогов: {len(products)}")
    groups = matching.cluster(products)

    with store.connect() as conn:
        live: dict[int, int] = {}
        dropped: dict[int, int] = {}
        for r in conn.execute("SELECT id, item_id, prev_item_id FROM chain_products"
                              " WHERE item_id IS NOT NULL OR prev_item_id IS NOT NULL"):
            if r["item_id"] is not None:
                live[int(r["id"])] = int(r["item_id"])
            else:
                dropped[int(r["id"])] = int(r["prev_item_id"])
        # «Занят живой строкой» — только по строкам групп. Строка, снятая с витрины в
        # этот обход, номер не занимает: иначе номер, уведённый сброшенным голосом,
        # прошёл бы мимо сверки _same_item (ревью 18.10.2026).
        in_groups = [row.id for group in groups for row, _, _ in group]
        claimed = {live[i] for i in in_groups if i in live}
        late_numbers = sorted({dropped[i] for i in in_groups if i in dropped} - claimed)
        before = _last_records(conn, late_numbers)
        canon_of: dict[int, dict] = {}

        def allow(gi: int, old: int) -> bool:
            record = before.get(old)
            if record is None:            # о номере ничего не помнят: строки возвращают свой
                return True
            if gi not in canon_of:
                canon_of[gi] = matching.canonical(groups[gi])
            return _same_item(record, canon_of[gi])

        keep, moved = _keep_numbers(groups, live, dropped, allow=allow)
        late_set = {old for old in moved if old not in claimed}
        run = conn.execute("INSERT INTO match_runs (started_at, products) VALUES (?, ?)",
                           (started, len(products)))
        run_id = int(run.lastrowid)
        # Последний номер каждой строки — в её память, пока DELETE его не стёр: строка,
        # выпавшая из обхода на ночь, принесёт его назад (store.resolve_alias, спящий номер).
        conn.execute("UPDATE chain_products SET prev_item_id=item_id WHERE item_id IS NOT NULL")
        _keep_last_records(conn, {n for n in keep if n is not None}, started)
        conn.execute("DELETE FROM catalog_items")
        multi = kept = 0
        numbers: list[int] = []
        canons: list[dict] = []
        for group, number in zip(groups, keep):
            canon = matching.canonical(group)
            canons.append(canon)
            values = (canon["name"], canon["norm"], canon["brand"], canon["weight_g"], canon["unit"],
                      canon["category"], canon["barcode"], canon["fnorm"], started, started)
            if number is None:
                cur = conn.execute(
                    "INSERT INTO catalog_items (name, norm, brand, weight_g, unit, category, barcode,"
                    " fnorm, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", values)
                item_id = int(cur.lastrowid)
            else:
                conn.execute(
                    "INSERT INTO catalog_items (id, name, norm, brand, weight_g, unit, category,"
                    " barcode, fnorm, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (number, *values))
                item_id = number
                kept += 1
            numbers.append(item_id)
            chains = {row.chain for row, _, _ in group}
            multi += int(len(chains) > 1)
            for row, method, score in group:
                conn.execute("UPDATE chain_products SET item_id=?, link_method=?, link_score=? WHERE id=?",
                             (item_id, method, score, row.id))
        # Номер, который снова жив, старой переадресацией никуда больше не ведёт.
        alive = [n for n in keep if n is not None]
        for i in range(0, len(alive), 500):
            chunk = alive[i:i + 500]
            conn.execute(f"DELETE FROM item_aliases WHERE old_id IN ({','.join('?' * len(chunk))})",
                         chunk)
        redirected = 0
        for old, gi in moved.items():
            if old in late_set and not _same_item(before.get(old), canons[gi]):
                continue        # строку переименовали в другой товар: к чужому номер не ведёт
            target = numbers[gi]
            # Прежние переадресации на слившийся номер ведут теперь сразу к живому.
            conn.execute("UPDATE item_aliases SET new_id=? WHERE new_id=?", (target, old))
            conn.execute("INSERT INTO item_aliases (old_id, new_id, since) VALUES (?,?,?)"
                         " ON CONFLICT(old_id) DO UPDATE SET new_id=excluded.new_id,"
                         " since=excluded.since", (old, target, started))
            redirected += 1
        conn.execute("UPDATE match_runs SET finished_at=?, items=?, multi=?, note=? WHERE id=?",
                     (store.now(), len(groups), multi,
                      f"{time.time() - t0:.1f} с; номеров сохранено {kept}, переадресовано {redirected}",
                      run_id))
        conn.commit()
    say(f"единых товаров: {len(groups)}, известных нескольким сетям: {multi},"
        f" прежних номеров сохранено: {kept}")
    return {"products": len(products), "items": len(groups), "multi": multi,
            "kept": kept, "moved": redirected}


def adopt(item_id: int, product_id: int | None = None) -> int:
    """Единый товар — в базу текущего человека: эталон и подтверждённые сопоставления.

    Возвращает id эталона. product_id — привязать к уже существующему эталону человека
    (так делает link_products для товаров из чеков); без него эталон ищется по имени
    и заводится, если его нет. Повторный вызов не плодит дублей: сопоставления —
    upsert по (магазин, артикул).
    """
    from app import repo
    from app.models import Product

    from app.matcher.normalize import sold_by_weight

    from app.catalog import link

    data = store.item(item_id)
    if not data:
        raise KeyError(f"нет единого товара {item_id}")
    # Единица — по строкам сетей, а не по сохранённой единице единого товара: та
    # пересчитывается только при сопоставлении и могла остаться от ложных «kg»
    # прежних сборщиков. Весовой — если хоть одна сеть продаёт его на вес.
    rows = data.get("chains") or []
    by_weight = any((r.get("unit") or "").lower() == "kg" or sold_by_weight(r.get("name") or "")
                    for r in rows) or sold_by_weight(data.get("name") or "")
    unit = "kg" if by_weight and not data.get("weight_g") else "pcs"
    if product_id is None:
        existing = repo.find_product_by_name(data["name"])
        product_id = existing.id if existing else repo.upsert_product(Product(
            id=None, name=data["name"], brand=data.get("brand"), barcode=data.get("barcode"),
            weight_g=data.get("weight_g"), unit=unit if unit in ("pcs", "kg") else "pcs",
            category=data.get("category")))
    # Строки сетей — по правилам единой базы (link.materialize), а не все подряд:
    # в одном едином товаре бывают и «Йогурт Активиа 2,9 %», и «…клубника 2,9 %», и
    # выбранный человеком вариант не должен уехать в корзину другим (ревью 18.10.2026).
    # Строка, похожая на товар сама, подтверждается; прочие ждут на «Связях»
    # кандидатами. Выбор человека в сети и его отказ от сети materialize соблюдает.
    product = repo.get_product(product_id)
    if product is not None:
        with store.connect() as conn:
            link.materialize(product, item_id, True, conn=conn)
        if repo.master_of(product_id) is None:
            repo.set_master(product_id, item_id, "words")
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
    """Опознать товары человека в единой базе и разложить артикулы сетей.

    Кнопка «Связать с каталогом» на «Связях». Делает то же, что фоновое
    сопоставление после чеков и при входе (app/catalog/link.py), и заодно
    перекладывает артикулы уже привязанных товаров по обновлённой базе.
    Возвращает {tried, linked} и подробности прохода.
    """
    from app.catalog import link

    return link.link_products(sync=True).as_dict()
