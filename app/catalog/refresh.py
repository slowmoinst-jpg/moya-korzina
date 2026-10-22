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

fill_name_variants() — наполнить словарь альтернативных названий (name_variants):
«как пишет сеть» из chain_products.name и «как пишет чек» из подтверждённых человеком
пар product_mapping.seen_name. Зовётся из match_all после пересборки; вручную —
`python -m app.catalog.refresh --variants`. Сама привязка товаров от словаря не зависит
(её читает T4), поэтому поведение сопоставления здесь не меняется.
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import os
import pathlib
import sqlite3
import time
from typing import Callable

from app.catalog import match as matching
from app.catalog import store
from app.catalog.model import CrawlBlocked, Crawler

log = logging.getLogger(__name__)


def _save_batch(chain: str, batch: list, started: str, say: Callable[[str], None]) -> tuple[int, int, int]:
    """Записать пачку строк, подождав, если база занята чужой записью.

    ЗАЧЕМ (20.10.2026). Сети теперь можно вести полосами (catalog.lanes), и два потока
    пишут в один SQLite: запись в нём одна на всех. store.connect ждёт до 30 с и потом
    падает «database is locked» — пачку, которую не успели записать, терять незачем:
    ждём и повторяем. Ошибка не про замок (диск, схема) — пробрасываем сразу, её лечить
    повтором бессмысленно.
    """
    for attempt in range(1, 4):
        try:
            return store.upsert_products(chain, batch, seen_at=started)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 3:
                raise
            say(f"база занята чужой записью ({exc}) — жду и повторяю ({attempt}/3)")
            time.sleep(5.0)
    raise RuntimeError("пачка не записалась")     # недостижимо: цикл либо вернёт, либо бросит


def run_chain(crawler: Crawler, progress: Callable[[str], None] | None = None,
              partial: bool = False, budget_min: float | None = None) -> dict:
    """Один обход одной сети. Возвращает сводку; исключения наружу не выпускает.

    partial=True — обход НЕ всей сети, а части её точек (загрузка по одному адресу).
    Такой обход ничего не объявляет пропавшим и свежим не считается (статус
    partial): иначе адрес в Новосибирске выключал бы все краснодарские товары
    Магнита, а ночной обход, сочтя сеть свежей, их бы не вернул.

    budget_min — потолок времени на сеть. Обходы идут ПО ОЧЕРЕДИ, и одна застрявшая
    сеть съедала бы всю ночь: следующие за ней до утра не начинались бы. Замер
    18.10.2026: ночь целиком — пять часов (METRO 1,7 ч, Лента выросла до 2,2 ч с
    добором подробностей). Потолок 180 мин выше любой здоровой сети, а упёршись в него,
    обход аккуратно останавливается и записывается НЕПОЛНЫМ (partial): пропавшими
    ничего не объявляется, следующей ночью сеть продолжит с того же места.
    """
    say = progress or (lambda msg: log.info("%s: %s", crawler.code, msg))
    run_id = store.start_run(crawler.code)
    started = store.now()
    seen = added = updated = 0
    batch: list = []
    status, note = "ok", None
    deadline = (time.monotonic() + budget_min * 60.0) if budget_min else None
    # Сборщик должен знать потолок: доборные проходы (свежесть цен, марка) считают по нему,
    # сколько карточек ещё влезет, и не съедают остаток — иначе обход упирался в потолок на
    # последнем проходе и записывался неполным, хотя мог закончиться с меньшим добором.
    try:
        crawler.deadline = deadline
    except AttributeError:  # сборщик без такого поля — не беда
        pass
    over = False
    try:
        for product in crawler.crawl(say):
            batch.append(product)
            if len(batch) >= 200:
                s, a, u = _save_batch(crawler.code, batch, started, say)
                seen, added, updated = seen + s, added + a, updated + u
                batch = []
                say(f"в каталоге {seen} товаров…")
                if deadline and time.monotonic() > deadline:
                    over = True
                    break
        if batch:
            s, a, u = _save_batch(crawler.code, batch, started, say)
            seen, added, updated = seen + s, added + a, updated + u
    except CrawlBlocked as exc:
        status, note = "blocked", str(exc)
    except Exception as exc:  # noqa: BLE001 — одна сеть не должна ронять остальные
        log.exception("%s: обход упал", crawler.code)
        status, note = "failed", f"{type(exc).__name__}: {exc}"

    gone = 0
    if status == "ok" and over:
        status = "partial"
        note = (f"обход остановлен по времени: потолок {budget_min:.0f} мин на сеть — "
                "остальное доберём следующим обходом")
        log.warning("%s: %s", crawler.code, note)
    if status == "ok" and partial:
        status = "partial"
    # Сборщик сам сказал, что обход неполный (оборвался раздел, часть листьев не
    # обошлась) — значит пропавшими объявлять нечего: их просто не искали. Статус
    # partial виден и человеку на экране сетей, и плановому обходу (свежим считается
    # только ok, store.fresh_chains).
    if status == "ok" and getattr(crawler, "incomplete", False):
        status = "partial"
        note = note or getattr(crawler, "incomplete_note", "") or "обход неполный"
    if status == "ok" and seen > 0:
        # пропавшими считаем только после ПОЛНОГО обхода: половинный обход пометил бы
        # пропавшей половину каталога
        gone = store.retire_unseen(crawler.code, started)
    store.finish_run(run_id, status, seen=seen, added=added, updated=updated, gone=gone, note=note)
    # Сколько разобрано из перечня сети: сборщик знает это, получив перечень, и без записи
    # «все товары разобраны» не проверить (замер 20.10.2026: у Ленты знакомо 37 334 из
    # 82 953, осталось 45 617 — девять ночей).
    total = getattr(crawler, "map_total", None)
    if total is not None and status not in ("failed", "blocked"):
        store.mark_map(crawler.code, total=int(total),
                       known=int(getattr(crawler, "map_known", 0) or 0),
                       left=int(getattr(crawler, "map_left", 0) or 0),
                       added=added)
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
        # Что у строки СЕЙЧАС: номер, способ и оценка связи. По этому набору решаем, надо
        # ли вообще что-то писать (инкрементальная пересборка 20.10.2026).
        links: dict[int, tuple] = {}
        for r in conn.execute("SELECT id, item_id, prev_item_id, link_method, link_score"
                              " FROM chain_products"):
            links[int(r["id"])] = (r["item_id"], r["link_method"], r["link_score"])
            if r["item_id"] is not None:
                live[int(r["id"])] = int(r["item_id"])
            elif r["prev_item_id"] is not None:
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
        alive = {n for n in keep if n is not None}
        # ПОСЛЕДНИЙ НОМЕР КАЖДОЙ СТРОКИ — В ЕЁ ПАМЯТЬ, но только если он сейчас меняется:
        # прежде эта запись шла по всем 308 тыс. строк каждый прогон.
        _keep_last_records(conn, alive, started)
        # МЕНЯЕМ ТОЛЬКО ИЗМЕНИВШЕЕСЯ (20.10.2026). Прежняя пересборка стирала ВСЮ таблицу
        # единых товаров и вставляла заново, а каждой строке сети переписывала item_id —
        # 284 тыс. записей и 308 тыс. строк за прогон, хотя за ночь меняются сотни. Именно
        # это держало запись к базе заблокированной ~15 минут (замер: прогон 880 с, проба
        # записи в это время получала «database is locked»). Теперь вставляем только новые
        # товары, обновляем только изменившиеся, удаляем только исчезнувшие, а неизменённое
        # пропускаем: та же работа, но транзакция длится секунды, и запись в каталог
        # (карточки с телефона, отчёты о корзине) не ждёт её.
        existing = {int(r[0]) for r in conn.execute("SELECT id FROM catalog_items")}
        stale = [item_id for item_id in existing if item_id not in alive]
        # ПРЕЖДЕ ЧЕМ УДАЛИТЬ ТОВАР — ЗАПОМНИТЬ ЕГО НОМЕР В СТРОКАХ, КОТОРЫЕ НА НЕГО ССЫЛАЮТСЯ.
        # Внешний ключ обнулит их item_id (ON DELETE SET NULL), и «спящий номер» держится
        # только на prev_item_id: без него товар человека потерял бы привязку (ревью 18.10.2026).
        # Раньше это делала запись по ВСЕМ 308 тыс. строк каждый прогон; теперь — только по
        # строкам исчезающих товаров, их единицы.
        for i in range(0, len(stale), 500):
            chunk = stale[i:i + 500]
            conn.execute("UPDATE chain_products SET prev_item_id=item_id"
                         f" WHERE item_id IN ({','.join('?' * len(chunk))})", chunk)
        for i in range(0, len(stale), 500):
            chunk = stale[i:i + 500]
            conn.execute(f"DELETE FROM catalog_items WHERE id IN ({','.join('?' * len(chunk))})", chunk)
        # ЗНАЧЕНИЯ ТОВАРОВ — ВО ВРЕМЕННУЮ ТАБЛИЦУ, А ЗАПИСЬ — ДВУМЯ ЗАПРОСАМИ.
        # 284 тыс. отдельных UPDATE (даже «условных», которые ничего не меняют) — это 284 тыс.
        # захватов блокировки записи: замер 20.10.2026 показал, что так прогон не укладывается
        # и в 12 минут, тогда как прежняя полная пересборка укладывалась в 880 с. Временная
        # таблица живёт в отдельном файле и замка основной базы не берёт, поэтому:
        #   1) недостающие записи (новые и проснувшиеся) вставляем одним INSERT … SELECT;
        #   2) изменившиеся обновляем одним UPDATE … FROM.
        conn.execute("CREATE TEMP TABLE staged_items (id INTEGER PRIMARY KEY, name TEXT, norm TEXT,"
                     " brand TEXT, weight_g REAL, unit TEXT, category TEXT, barcode TEXT, fnorm TEXT)")
        multi = kept = 0
        numbers: list[int] = []
        canons: list[dict] = []
        staged: list[tuple] = []
        links_to_write: list[tuple] = []
        for group, number in zip(groups, keep):
            canon = matching.canonical(group)
            canons.append(canon)
            values = (canon["name"], canon["norm"], canon["brand"], canon["weight_g"], canon["unit"],
                      canon["category"], canon["barcode"], canon["fnorm"])
            if number is None:
                cur = conn.execute(
                    "INSERT INTO catalog_items (name, norm, brand, weight_g, unit, category, barcode,"
                    " fnorm, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (*values, started, started))
                item_id = int(cur.lastrowid)
            else:
                item_id = number
                kept += 1
                staged.append((item_id, *values))
            numbers.append(item_id)
            chains = {row.chain for row, _, _ in group}
            multi += int(len(chains) > 1)
            for row, method, score in group:
                was = links.get(row.id, (None, None, None))
                if was == (item_id, method, score):
                    continue        # строка уже связана так же — писать нечего
                links_to_write.append((row.id, item_id, method, score, was[0]))
        if staged:
            conn.executemany("INSERT INTO staged_items (id, name, norm, brand, weight_g, unit,"
                             " category, barcode, fnorm) VALUES (?,?,?,?,?,?,?,?,?)", staged)
            conn.execute(
                "INSERT INTO catalog_items (id, name, norm, brand, weight_g, unit, category, barcode,"
                " fnorm, created_at, updated_at)"
                " SELECT s.id, s.name, s.norm, s.brand, s.weight_g, s.unit, s.category,"
                " s.barcode, s.fnorm, ?, ? FROM staged_items s"
                " LEFT JOIN catalog_items c ON c.id = s.id WHERE c.id IS NULL", (started, started))
            conn.execute(
                "UPDATE catalog_items SET name=s.name, norm=s.norm, brand=s.brand,"
                " weight_g=s.weight_g, unit=s.unit, category=s.category, barcode=s.barcode,"
                " fnorm=s.fnorm, updated_at=?"
                " FROM staged_items s WHERE catalog_items.id = s.id"
                " AND (catalog_items.name IS NOT s.name OR catalog_items.norm IS NOT s.norm"
                " OR catalog_items.brand IS NOT s.brand"
                " OR catalog_items.weight_g IS NOT s.weight_g"
                " OR catalog_items.unit IS NOT s.unit"
                " OR catalog_items.category IS NOT s.category"
                " OR catalog_items.barcode IS NOT s.barcode"
                " OR catalog_items.fnorm IS NOT s.fnorm)", (started,))
        for row_id, item_id, method, score, prev in links_to_write:
            if prev is not None and prev != item_id:
                # Номер строки меняется — прежний остаётся в её памяти (спящий номер).
                conn.execute("UPDATE chain_products SET prev_item_id=?, item_id=?, link_method=?,"
                             " link_score=? WHERE id=?", (prev, item_id, method, score, row_id))
            else:
                conn.execute("UPDATE chain_products SET item_id=?, link_method=?, link_score=?"
                             " WHERE id=?", (item_id, method, score, row_id))
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
    # Словарь альтернативных названий — сразу после пересборки, а не в ней: он ложится
    # на уже проставленные номера и сама пересборка его не стирает (name_variants не
    # ссылается на catalog_items), поэтому словарь копится между пересборками.
    # Не наполнился — сопоставление всё равно состоялось: пересборка уже записана, и
    # словарь (подсказка поиску) не должен ронять ночной обход.
    try:
        variants = fill_name_variants(progress=say)
    except Exception:  # noqa: BLE001
        log.exception("словарь альтернативных названий не наполнился")
        variants = {}
    return {"products": len(products), "items": len(groups), "multi": multi,
            "kept": kept, "moved": redirected, "variants": variants}


# ---------- словарь альтернативных названий ----------
#
# ЗАЧЕМ (18.10.2026). Сокращение чека («МОЛ.ПАСТ.ПРОСТОКВАШИНО 2,5%») считалось только
# алгоритмом на лету, и он не берёт строку, когда чек молчит о вкусе или сорте
# (link._abbrev), и кандидата без марки (у Дикси 9,7 % строк без марки). Здесь оседает
# написанное: строки сетей и подтверждённые человеком пары из чеков. Хранилище —
# name_variants в общей базе (app/catalog/store.py), чтение для привязки — T4
# (store.search_variants).
def folded_for_sql(raw: str | None) -> str:
    """store.folded_variant для SQLite: одна странная строка не должна ронять наполнение."""
    try:
        return store.folded_variant(raw)
    except Exception:  # noqa: BLE001 — не сводится — значит, вариант просто не записываем
        return ""


def _fill_chain_variants(conn: sqlite3.Connection) -> int:
    """Сложить в словарь названия строк сетей. Возвращает, сколько написаний увидено.

    Одним запросом, а не строкой за строкой: строк каталога десятки тысяч. GROUP BY
    сводит повторы (одно название у разных артикулов), hits считает написания. Повтор
    наполнения без нового обхода hits не двигает: last_seen строки не менялся — значит,
    нового написания в этот прогон не было.
    """
    conn.create_function("korzina_variant_folded", 1, folded_for_sql, deterministic=True)
    seen = conn.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM chain_products"
        " WHERE item_id IS NOT NULL AND name IS NOT NULL AND name != ''"
        " GROUP BY item_id, chain, name)").fetchone()[0]
    if not seen:
        return 0
    # variant переписывается только свежим написанием: если у живого номера вариант уже
    # есть, а строка сети с тех пор не обновлялась, показание остаётся прежним.
    conn.execute(
        "INSERT INTO name_variants (item_id, variant, folded, source, chain, first_seen,"
        " last_seen, hits)"
        " SELECT item_id, variant, folded, 'chain', chain, first_seen, last_seen, hits FROM ("
        "   SELECT item_id AS item_id, name AS variant,"
        "          korzina_variant_folded(name) AS folded, chain AS chain,"
        "          MIN(first_seen) AS first_seen, MAX(last_seen) AS last_seen, COUNT(*) AS hits"
        "   FROM chain_products"
        "   WHERE item_id IS NOT NULL AND name IS NOT NULL AND name != ''"
        "   GROUP BY item_id, chain, name)"
        " WHERE folded != ''"
        " ON CONFLICT(item_id, folded, source, chain) DO UPDATE SET"
        " last_seen=MAX(name_variants.last_seen, excluded.last_seen),"
        " variant=CASE WHEN excluded.last_seen > name_variants.last_seen"
        "              THEN excluded.variant ELSE name_variants.variant END,"
        " hits=name_variants.hits + CASE WHEN excluded.last_seen > name_variants.last_seen"
        "                                THEN excluded.hits ELSE 0 END")
    return int(seen)


def _is_receipt_row(sku: str) -> bool:
    """Строка ЧЕКА ли это: чеком пишет только hist- (app/connectors/history.PREFIX).

    list-, preview- и «<сеть>-…» — тоже заглушки (app/catalog/model.placeholder_sku),
    но это источники ЦЕНЫ, а не чеки: их название не то, как пишет чек, и в словарь
    чеков они не идут. hist- — единственная заглушка, которая чеком и является.
    """
    from app.connectors.history import PREFIX as HISTORY_PREFIX

    return sku.startswith(HISTORY_PREFIX)


def _history_rows_sql(conn: sqlite3.Connection) -> str:
    """Хвост UNION ALL со строками истории покупок — или пустая строка, если их нет.

    ЧЕК И ЕСТЬ СТРОКА ИСТОРИИ (18.10.2026). Снимок product_mapping.seen_name — это
    написание СТРОКИ СЕТИ, и настоящих строк чека (hist-) на боевом рабочем месте
    больше нет вовсе: кабинет ФНС пишет покупки в purchase_history.raw_name (замер:
    1 656 чеков, 4 504 строки, а hist-заглушек 0 — то есть прежний источник физически
    пуст). Оттуда и берём «как пишет чек» — ОДНИМ запросом на базу, а не запросом на
    строку. Годится только товар с проставленным master_id и не слабой связью
    («words?», «variant?» — самоподкрепление, которое уже закрывали); NULL в
    master_method — человек или живой поиск, это довод. Сеть — из
    purchase_history.store_id: не названа — варианта нет, выдумывать нечего.
    """
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "purchase_history" not in tables:
        return ""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(purchase_history)")}
    if not {"raw_name", "store_id", "product_id"} <= columns:
        return ""                    # база прежней версии: истории покупок ещё нет
    return (" UNION ALL"
            " SELECT h.raw_name AS variant, '' AS sku, s.code AS chain,"
            " p.master_id AS item_id"
            " FROM purchase_history h"
            " JOIN stores s ON s.id = h.store_id"
            " JOIN products p ON p.id = h.product_id"
            " WHERE h.raw_name IS NOT NULL AND h.raw_name != ''"
            " AND p.master_id IS NOT NULL"
            " AND (p.master_method IS NULL OR INSTR(p.master_method, '?') = 0)")


def _receipt_variant_rows(path: str) -> list[tuple[int, str, str]]:
    """Написания ЧЕКА одной личной базы: (номер единого товара, название, сеть).

    ДВА ИСТОЧНИКА. Строка чека лежит в purchase_history.raw_name (так её пишет кабинет
    ФНС) — это главный источник. Прежний, product_mapping.seen_name, оставлен как был:
    он годится только для строки чека (hist-) — у настоящего артикула снимок хранит
    написание СЕТИ, а не чека.

    БЕЗОПАСНОСТЬ ДАННЫХ (решение 18.10.2026). База человека открывается ТОЛЬКО НА
    ЧТЕНИЕ (mode=ro) и из неё берутся ровно три поля: название, код сети и номер
    единого товара. Ни номера человека, ни id его товара, ни времени покупки в общий
    словарь не уходит: это словарь «как пишут чеки», а не история покупок. База
    прежней версии (нет seen_name, master_id или истории), занятая, битая или
    отсутствующая — пропускается молча: обход сетей и сопоставление от неё не зависят.

    ЧТО ЗДЕСЬ ДОВОД, А ЧТО НЕТ (ревью diff 18.10.2026). Брать все подтверждённые пары
    нельзя — выходило самоподкрепление: слабая догадка базы («words?», «variant?»)
    оседала в словаре как «написание чека» и снова шла кандидатом. Поэтому пара
    годится, только если:
      * m.method IS NULL — связь записал человек или живой поиск, а не сама база
        (она пишет method='master');
      * master_method известен и без «?» — товар найден не слабой связью;
      * строка ЧЕКА: у пары это hist-, у строки истории это она и есть.
    """
    try:
        if not os.path.exists(path):
            return []
        # as_uri() требует абсолютный путь: на относительном он бросает ValueError, и
        # исключение ниже проглатывало его — словарь чеков молча оставался пустым
        # (ревью 18.10.2026).
        uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except (sqlite3.Error, OSError, ValueError):
        return []
    try:
        conn.row_factory = sqlite3.Row
        mapping = {r["name"] for r in conn.execute("PRAGMA table_info(product_mapping)")}
        products = {r["name"] for r in conn.execute("PRAGMA table_info(products)")}
        if "seen_name" not in mapping or "master_id" not in products:
            return []                 # база прежней версии: писать сюда ещё нечего
        rows = conn.execute(
            "SELECT m.seen_name AS variant, sp.sku AS sku, s.code AS chain,"
            " p.master_id AS item_id"
            " FROM product_mapping m"
            " JOIN store_products sp ON sp.id = m.store_product_id"
            " JOIN stores s ON s.id = sp.store_id"
            " JOIN products p ON p.id = m.product_id"
            " WHERE m.confirmed = 1 AND m.seen_name IS NOT NULL AND m.seen_name != ''"
            " AND p.master_id IS NOT NULL AND m.method IS NULL"
            " AND p.master_method IS NOT NULL AND INSTR(p.master_method, '?') = 0"
            + _history_rows_sql(conn)).fetchall()
        out: list[tuple[int, str, str]] = []
        for r in rows:
            sku = str(r["sku"] or "")
            # Пустой sku — строка истории покупок: она и есть написание чека. У пары
            # артикул обязан быть строкой чека (hist-); list-, preview- и «<сеть>-…» —
            # заглушки-источники цены, чеком они не являются.
            if sku and not _is_receipt_row(sku):
                continue
            out.append((int(r["item_id"]), str(r["variant"]), str(r["chain"] or "")))
        return out
    except (sqlite3.Error, ValueError, TypeError):   # битая запись — как битая база
        return []
    finally:
        conn.close()


def _fill_receipt_variants(conn: sqlite3.Connection, stamp: str) -> int:
    """Сложить в словарь написания чеков по всем рабочим местам.

    ЗАЧЕМ ПО ЛИЧНЫМ БАЗАМ, А НЕ ПО ОБЩЕЙ. «Как пишет чек» знает только личная база:
    строки чеков лежат в ней (purchase_history.raw_name — так пишет кабинет ФНС;
    строка hist- — так пишет разбор чека), а прежний снимок product_mapping.seen_name
    годится лишь для hist-. Что именно считается доводом, решает _receipt_variant_rows.
    Рабочие места перебираются так же, как это делают app/cartfill.py и app/places.py,
    а вымышленная витрина «demo» пропускается: её товары — не чек живого человека.

    hits — число НАБЛЮДЕНИЙ этого написания (покупки в истории, подтверждения
    человеком), а не прогонов: повторное наполнение пересчитывает то же число и hits не
    накручивает (ревью 18.10.2026; у chain-источника повтор и так ничего не менял).
    Даты покупки словарь не знает: last_seen — время ЭТОГО прогона, а не покупки.
    Дубли по (item_id, folded, source, chain) не плодятся: ключ один на все наблюдения.
    Возвращает, сколько ключей увидено.
    """
    from app import users

    found: dict[tuple[int, str, str], list] = {}
    for phone in users.list_workspaces():
        if phone == users.DEMO:
            continue                 # вымышленные товары витрины в общий словарь не идут
        for item_id, variant, chain in _receipt_variant_rows(users.db_path_for(phone)):
            folded = store.folded_variant(variant)
            if not folded:
                continue
            key = (item_id, folded, chain)
            row = found.get(key)
            if row is None:
                found[key] = [variant, 1]
            else:
                row[1] += 1
    if not found:
        return 0
    conn.executemany(
        "INSERT INTO name_variants (item_id, variant, folded, source, chain, first_seen,"
        " last_seen, hits) VALUES (?,?,?,'receipt',?,?,?,?)"
        " ON CONFLICT(item_id, folded, source, chain) DO UPDATE SET"
        " last_seen=excluded.last_seen, hits=excluded.hits",
        [(item_id, variant, folded, chain, stamp, stamp, count)
         for (item_id, folded, chain), (variant, count) in found.items()])
    return len(found)


def fill_name_variants(progress: Callable[[str], None] | None = None) -> dict:
    """Наполнить словарь альтернативных названий и вернуть понятный итог.

    Источники: строки сетей (source='chain') и подтверждённые человеком пары из чеков
    (source='receipt'). После наполнения номера слившихся товаров переносятся по
    item_aliases, а исчезнувшие убираются (store.reconcile_variants).

    Итог: chain/receipt — сколько написаний увидено в этом прогоне, moved/dropped —
    сколько номеров переехало и убрано, total — строк в словаре, stats — разбивка по
    источникам. Зовётся из match_all после пересборки; вручную — `python -m
    app.catalog.refresh --variants`: пересборка идёт десять минут, для словаря не нужна.
    """
    say = progress or (lambda msg: log.info("варианты названий: %s", msg))
    store.init()
    stamp = store.now()
    with store.connect() as conn:
        chains = _fill_chain_variants(conn)
        receipts = _fill_receipt_variants(conn, stamp)
        moved, dropped = store.reconcile_variants(conn)
        conn.commit()
    stats = store.variant_stats()
    report = {"chain": chains, "receipt": receipts, "moved": moved, "dropped": dropped,
              "total": sum(int(r["variants"]) for r in stats), "stats": stats}
    say(f"записано: от сетей {chains}, из чеков {receipts}; всего вариантов {report['total']},"
        f" переехало номеров {moved}, убрано {dropped}")
    return report


def main(argv: list[str] | None = None) -> int:
    """Ручной прогон наполнения словаря: python -m app.catalog.refresh --variants."""
    parser = argparse.ArgumentParser(description="Наполнить словарь альтернативных названий.")
    parser.add_argument("--variants", action="store_true",
                        help="собрать варианты из строк сетей и подтверждённых чеков")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if not args.variants:
        parser.print_help()
        return 1
    report = fill_name_variants(progress=print)
    for row in report["stats"]:
        print(f"{row['source']}: вариантов {row['variants']}, наблюдений {row['hits']},"
              f" сетей {row['chains']}")
    print(f"всего вариантов: {report['total']}; переехало номеров {report['moved']},"
          f" убрано {report['dropped']}")
    return 0


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
    # «＋» из каталога (product_id не задан) — явный выбор человека; привязка товара из
    # чека (phonecart) выбором не считается и правил №8 не получает (T1, 18.10.2026).
    chosen = product_id is None
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
    # Под link.picked() строка без фасовки (яйца «10 шт», батон без веса) подтверждается
    # выбором человека, а не остаётся кандидатом — иначе «＋» не кладёт её ни в одну сеть.
    product = repo.get_product(product_id)
    if product is not None:
        with store.connect() as conn, (link.picked() if chosen else contextlib.nullcontext()):
            confirmed, _ = link.materialize(product, item_id, True, conn=conn)
        if repo.master_of(product_id) is None:
            # Метка — по правилу link: «words?», если ни одной сети не подтвердилось.
            human = any(link._real(m["store_code"], m["sku"])
                        for m in link._human(repo.product_mappings(product_id)))
            repo.set_master(product_id, item_id, "words" if (confirmed or human) else "words?")
    return product_id


# Сколько позиций отчёта с телефона за один раз заводим в единую базу. Отчёт — не
# каталог: это то, что человек положил в корзину за один заход, десятка позиций хватает,
# а предел не даёт случайному отчёту залить базу мусором.
DEVICE_LEARN_LIMIT = 20


def learn_device_rows(chain: str, rows: list[dict], limit: int | None = None) -> int:
    """Завести в единой базе товары, которые человек нашёл сам, со своего телефона.

    ЗАЧЕМ (замер 18.10.2026). Пятёрочку и Самокат сервер не пускает ни с серверного
    адреса, ни с домашнего — их каталога в базе нет, и сопоставлять их строки не с чем.
    Человек со своего телефона витрину видит и в корзину кладёт; приложение присылает
    адрес карточки, из него берётся настоящий артикул сети (app/catalog/fromurl.py), и
    товар заводится здесь. Это не подмена каталога: строка появляется только та, которую
    человек действительно держал в руках, и только с настоящим артикулом.

    Известные строки не трогаем: у сети может быть полнее название и цена от сборщика, а
    отчёт телефона их обеднил бы. Заглушки не пишем — по ним в корзину сети ничего не
    положить (model.placeholder_sku). Возвращает, сколько строк завелось.

    limit — свой предел у того, кто зовёт. Отчёт о корзине мал (DEVICE_LEARN_LIMIT), а
    прочитанная страница раздела — это десятки карточек: предел отчёта обрезал бы её
    почти целиком (app/phonecart.py, accept_cards).
    """
    from app.catalog.model import ChainProduct, placeholder_sku
    from app.matcher.normalize import parse_weight, unit_from_name

    cap = DEVICE_LEARN_LIMIT if limit is None else max(0, int(limit))
    known = store.known_skus(chain)
    batch = []
    for row in rows[:cap]:
        sku = str(row.get("sku") or "").strip()
        # ИМЯ — СЕТИ, А НЕ НАШЕ (21.10.2026). Если отчёт несёт имя с найденной карточки
        # (`found_name`), берём его: записать в каталог «артикул сети + наше название»
        # значит соврать базе, что сеть называет товар так же. У Дикси «Балийский цветок»
        # против нашего «Bali Flower» — ровно этот случай. `name` — запасной путь для
        # отчётов, где карточку не открывали.
        name = str(row.get("found_name") or row.get("name") or "").strip()
        if not sku or not name or sku in known or placeholder_sku(sku, chain):
            continue
        weight_g, _ = parse_weight(name)
        batch.append(ChainProduct(sku=sku, name=name, url=row.get("url") or None,
                                  weight_g=weight_g, unit=unit_from_name(name),
                                  price=row.get("price"), in_stock=row.get("in_stock")))
        known.add(sku)
    if not batch:
        return 0
    store.upsert_products(chain, batch)
    log.info("единая база: с телефона заведено %d товаров сети %s", len(batch), chain)
    return len(batch)


# Сколько позиций отчёта за один раз обновляют наличие. Отчёт — не каталог: это то,
# что человек положил в корзину за один заход, и сотен позиций в нём не бывает.
DEVICE_STOCK_LIMIT = 200

# Чем сеть говорит «товара нет» в отчёте телефона. Слова те же, что на экране «Сборка
# корзин» (app/cartrun.py): «карточка говорит, что товара нет в наличии», «корзина сети
# показывает: нет в наличии».
NO_STOCK_WORDS = ("нет в наличии", "не в наличии", "нет товара")


def note_device_stock(chain: str, items: list[dict]) -> int:
    """Наличие из отчёта телефона — в единую базу: что легло, а чего сеть не дала.

    ЗАЧЕМ (решение владельца 18.10.2026). Наличие и цены приложение получает, формируя
    корзину в фоне в каждой подключённой сети. Для Пятёрочки и Самоката это единственный
    источник наличия: сервер к ним не пробивается (замер 18.10.2026), и ни каталога, ни
    коннектора у них нет. Позиция легла — товар есть; сеть сказала «нет в наличии» —
    товара нет; любая другая причина (сеть отказала, не видно, что легло) наличия не
    говорит, и его мы не трогаем. С 21.10.2026 приложение 1.13 присылает наличие с
    карточки отдельным полем `in_stock` (Z7h): оно важнее этих выводов — витрина
    говорит «Нет в наличии/Больше нет» и тогда, когда позиция не легла по другой
    причине («кнопки не нашлось»).

    Заглушки не пишем: по ним сеть товара не знает (model.placeholder_sku).
    """
    from app.catalog.model import placeholder_sku

    rows: list[tuple[bool, str]] = []
    for item in (items or [])[:DEVICE_STOCK_LIMIT]:
        sku = str(item.get("sku") or "").strip()
        if not sku or placeholder_sku(sku, chain):
            continue
        # ЯВНОЕ НАЛИЧИЕ С КАРТОЧКИ ВАЖНЕЕ ВЫВОДА ИЗ `ok`/`why` (приложение 1.13, Z7h).
        # Телефон присылает `in_stock` отдельно: «Нет в наличии/Больше нет» на карточке
        # даёт false и тогда, когда позиция не легла по другой причине («кнопки не
        # нашлось»), — а по `ok`/`why` такой товар оставался бы неотмеченным. Значение
        # принимаем только настоящим bool: строка «false» и число 0 наличием не
        # считаются. Поля нет (старые отчёты) — работает прежняя логика по `ok`/`why`.
        in_stock = item.get("in_stock")
        if isinstance(in_stock, bool):
            rows.append((in_stock, sku))
            continue
        why = str(item.get("why") or "").lower()
        if item.get("ok"):
            rows.append((True, sku))
        elif any(word in why for word in NO_STOCK_WORDS):
            rows.append((False, sku))
    if not rows:
        return 0
    changed = store.set_stock(chain, rows)
    if changed:
        log.info("%s: наличие с телефона обновилось у %d товаров", chain, changed)
    return changed


# Сколько позиций отчёта за один раз обновляют ЦЕНУ. Тот же предел, что у наличия:
# отчёт — это корзина человека за один заход.
DEVICE_PRICE_LIMIT = DEVICE_STOCK_LIMIT


def note_device_prices(chain: str, items: list[dict]) -> int:
    """Цены из отчёта телефона — в единую базу: сколько сеть просит за эту позицию.

    ЗАЧЕМ (решение владельца 18.10.2026). Цены и наличие приложение получает, формируя
    корзину в фоне в каждой подключённой сети. Наличие отсюда уже берётся
    (note_device_stock), а цены — нет, и в базе у Перекрёстка, Vprok и Монетки ноль цен
    на 194 тысячи строк: их обход отдаёт только названия. Корзина владельца — то
    единственное место, где цена этих сетей вообще видна.

    Берём цену только у позиции, которая ЛЕГЛА (ok): у не легшей цена может быть ценой
    другой упаковки или отсутствовать вовсе, и подставлять её товару нельзя. Цена должна
    быть положительным числом — ноль и пустое место цены не значат.
    """
    from app.catalog.model import placeholder_sku

    rows: list[tuple[float, str]] = []
    for item in (items or [])[:DEVICE_PRICE_LIMIT]:
        sku = str(item.get("sku") or "").strip()
        if not sku or placeholder_sku(sku, chain) or not item.get("ok"):
            continue
        try:
            price = float(item.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        rows.append((price, sku))

    # ВТОРАЯ ДОРОГА: ЦЕНА С КАРТОЧКИ, КОТОРУЮ ЧЕЛОВЕК ВИДЕЛ, ДАЖЕ ЕСЛИ ПОЗИЦИЯ НЕ ЛЕГЛА.
    # Замер 21.10.2026: телефон присылает price с открытой карточки и у не легших позиций
    # (Papia 139.99 при ok=false), а для Пятёрочки и Самоката это единственные цены — сетей,
    # куда сервер не пробивается. Но приписать её НАШЕМУ товару нельзя: телефон мог открыть
    # карточку похожего товара другой линейки. Поэтому берём артикул ИЗ АДРЕСА карточки
    # (fromurl.sku_of) и пишем цену строке СЕТИ с этим артикулом: цена относится к тому SKU,
    # с чьей карточки её прочитали, — это честно, даже если SKU не наш.
    from_url: list[tuple[float, str]] = []
    card_stock: list[tuple[bool, str]] = []
    for item in (items or [])[:DEVICE_PRICE_LIMIT]:
        if item.get("ok"):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        try:
            price = float(item.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        try:
            from app.catalog import fromurl

            real = fromurl.sku_of(chain, url)
        except Exception:  # noqa: BLE001 — артикул не достался: пропускаем позицию
            real = None
        if not real or placeholder_sku(str(real), chain):
            continue
        from_url.append((price, str(real)))
        # НАЛИЧИЕ ТОМУ ЖЕ АРТИКУЛУ: отчёт теперь несёт in_stock и у не легших позиций
        # (замер 21.10.2026: Дикси сказала in_stock=false на карточке). Наличие относится к
        # тому же артикулу, чью карточку читали, поэтому идёт за ценой, а не нашему товару.
        stock = item.get("in_stock")
        if isinstance(stock, bool):
            card_stock.append((stock, str(real)))
    # (from_url пишется отдельно, оживляющей записью: set_prices не видит погашенные строки)

    if card_stock:
        fresh = store.set_stock_seen(chain, card_stock)
        if fresh:
            log.info("%s: наличие с карточек не легших позиций обновилось у %d товаров",
                     chain, fresh)
    # ПО КАРТОЧКАМ (не легшие позиции) — отдельной записью, которая ОЖИВЛЯЕТ строку: она
    # могла быть погашена обходом, а человек видел её сегодня (замер 21.10.2026: у Магнита
    # строки 8000104060 и 1000443710 лежали с active=0 с 16.09, и обычный set_prices с
    # `AND active=1` молча пропускал цену с телефона).
    changed = store.set_prices_seen(chain, from_url) if from_url else 0
    if changed:
        log.info("%s: цены с карточек не легших позиций обновились у %d товаров",
                 chain, changed)
    if not rows:
        return changed
    # ПЕРВАЯ ДОРОГА (позиция легла): та же запись, что и у карточек — с оживлением строки.
    # Позиция легла в корзину сети, значит товар у неё есть; погашенная обходом строка не
    # должна из-за этого терять цену — ровно та ловушка, что была у второй дороги.
    changed += store.set_prices_seen(chain, rows)
    if changed:
        log.info("%s: цены с телефона обновились у %d товаров", chain, changed)
    return changed


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


if __name__ == "__main__":
    raise SystemExit(main())



