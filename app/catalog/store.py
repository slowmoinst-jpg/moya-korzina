"""Общая база каталога: схема и доступ. Один файл на сервер, не на человека.

Почему отдельная база, а не таблицы в базе человека. Каталог — общее знание о мире
(«у Ленты есть молоко 930 мл под артикулом 80424»), а база человека — его личное
(«он покупает это молоко каждую неделю»). Общее обновляет фоновый процесс без участия
людей, личное меняется только руками человека. Смешать их — значит либо обновлять
каталог в каждой из сотен баз, либо пустить фоновый процесс в личные данные.

Путь: data/catalog.db рядом с папкой рабочих мест; переопределяется config
catalog.db_path (тесты подставляют временный файл через monkeypatch).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from typing import Iterable

from app import config
from app.catalog.model import ChainProduct, PLACEHOLDER_PREFIXES
from app.matcher.normalize import gtin

SCHEMA = """
CREATE TABLE IF NOT EXISTS chain_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,                 -- код сети: magnit, vkusvill, lenta, dixy, pyaterochka, samokat
    sku TEXT NOT NULL,                   -- идентификатор товара в сети
    name TEXT NOT NULL,
    brand TEXT,
    weight_g REAL,
    unit TEXT,
    category TEXT,
    url TEXT,
    image TEXT,
    barcode TEXT,
    gtin TEXT,                           -- тот же штрихкод в одном виде, 14 цифр (normalize.gtin)
    price REAL,                          -- последняя увиденная цена, для справки; история цен не здесь
    in_stock INTEGER,
    price_seen TEXT,                     -- когда эта цена увидена: last_seen продлевается и без цены
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,   -- 0 — пропал из каталога сети
    item_id INTEGER REFERENCES catalog_items(id) ON DELETE SET NULL,
    link_method TEXT,                    -- barcode | words | seed — как отнесён к единому товару
    link_score REAL,
    UNIQUE (chain, sku)
);

CREATE TABLE IF NOT EXISTS catalog_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                  -- каноническое название: самое подробное из сетей
    norm TEXT,                           -- слова названия строчными для поиска: SQLite LOWER() кириллицу не берёт
    brand TEXT,
    weight_g REAL,
    unit TEXT,
    category TEXT,
    barcode TEXT,
    fnorm TEXT,                          -- слова названия и марки в общем своде письма, через пробел
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Куда переехал единый товар. Номер единого товара постоянный: пересборка отдаёт
-- группе номер той прежней, с которой у неё больше всего общих строк сетей. Когда
-- две прежние группы слились в одну, номер второй остаётся здесь и ведёт на номер
-- первой — товары людей, привязанные к нему, не теряют свой объект.
CREATE TABLE IF NOT EXISTS item_aliases (
    old_id INTEGER PRIMARY KEY,
    new_id INTEGER NOT NULL,
    since TEXT NOT NULL
);

-- Последняя запись номера, которого нет в catalog_items: уснул, слился или умер.
-- По ней пересборка решает, тот ли это товар, когда строки номера вернулись в
-- группу двойника (refresh._same_item): у спящего номера записи в catalog_items нет.
CREATE TABLE IF NOT EXISTS item_last (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    brand TEXT,
    weight_g REAL,
    unit TEXT,
    since TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,                -- running | ok | blocked | failed
    seen INTEGER NOT NULL DEFAULT 0,
    added INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    gone INTEGER NOT NULL DEFAULT 0,
    note TEXT
);

CREATE TABLE IF NOT EXISTS match_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    products INTEGER NOT NULL DEFAULT 0,
    items INTEGER NOT NULL DEFAULT 0,
    multi INTEGER NOT NULL DEFAULT 0,    -- единых товаров, известных двум и более сетям
    note TEXT
);

-- Цена и наличие ПО ТОЧКАМ. У сетей они свои в каждом магазине, а строка каталога
-- одна на артикул: без этой таблицы цена второго города затирала цену первого.
CREATE TABLE IF NOT EXISTS chain_prices (
    chain TEXT NOT NULL,
    sku TEXT NOT NULL,
    point TEXT NOT NULL,                 -- код точки в сети: storeCode Магнита, центр METRO
    price REAL,
    in_stock INTEGER,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (chain, sku, point)
);

-- Какая точка сети досталась какому адресу. Пишет обход — он подбирает точки к
-- адресам рабочих мест; читает перенос товара в базу человека (refresh.adopt),
-- которому нужна цена ЕГО магазина, а спрашивать сеть ради этого незачем.
CREATE TABLE IF NOT EXISTS address_points (
    address TEXT NOT NULL,               -- адрес рабочего места, пробелы схлопнуты, строчными
    chain TEXT NOT NULL,
    point TEXT NOT NULL,
    label TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (address, chain)
);

CREATE TABLE IF NOT EXISTS address_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,               -- адрес человека, по которому надо загрузить цены
    requested_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    status TEXT NOT NULL,                -- waiting | running | done | failed
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_cp_chain ON chain_products(chain, active);
CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_pending
    ON address_queue(address) WHERE status IN ('waiting', 'running');
CREATE INDEX IF NOT EXISTS idx_cp_item ON chain_products(item_id);
CREATE INDEX IF NOT EXISTS idx_cp_barcode ON chain_products(barcode);
CREATE INDEX IF NOT EXISTS idx_items_barcode ON catalog_items(barcode);
"""


def now() -> str:
    # Микросекунды не для красоты: метка «увидено в этом обходе» должна отличать два
    # обхода, даже если они начались в одну секунду (так бывает в тестах и при перезапуске).
    return datetime.now().isoformat(timespec="microseconds")


def path() -> str:
    p = config.get("catalog.db_path") or "data/catalog.db"
    return p if os.path.isabs(p) else os.path.join(config.ROOT, p)


def connect() -> sqlite3.Connection:
    file = path()
    os.makedirs(os.path.dirname(file), exist_ok=True)
    conn = sqlite3.connect(file, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Каталог пишет фоновый процесс, читает интерфейс — WAL даёт им не мешать друг другу.
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# Колонки, добавленные после первых выпусков: CREATE TABLE IF NOT EXISTS до уже
# существующей базы их не донесёт.
LATE_COLUMNS = (("chain_products", "price_seen", "TEXT"),
                ("chain_products", "gtin", "TEXT"),
                # Прежний номер единого товара у строки, чья привязка сброшена сменой
                # названия, фасовки или штрихкода: по нему пересборка сохраняет номер
                # и ведёт переадресацию (refresh._keep_numbers).
                ("chain_products", "prev_item_id", "INTEGER"),
                ("catalog_items", "fnorm", "TEXT"))

# Индексы по поздним колонкам: в SCHEMA их ставить нельзя — у старой базы колонки
# ещё нет, и скрипт упал бы раньше, чем ALTER её добавит.
LATE_INDEXES = ("CREATE INDEX IF NOT EXISTS idx_cp_gtin ON chain_products(gtin)",
                "CREATE INDEX IF NOT EXISTS idx_cp_prev ON chain_products(prev_item_id)")


# Версия данных каталога (PRAGMA user_version). 1 — сняты ложные «kg» у строк сетей,
# чьи сборщики ставили единицу по отсутствию граммовки (app/db.FALSE_KG_CHAINS).
# 2 — у развесного Ленты цена порции заменена ценой килограмма.
# 3 — у строк со штрихкодом заполнен gtin: тот же код в одном виде (17.10.2026).
# 4 — убраны строки-заглушки (hist-, list-, «<сеть>-…»): сборщики Пятёрочки и
#     Самоката переносили их из store_products, а артикулом сети они не бывают (18.10.2026).
SCHEMA_VERSION = 4
LENTA_PER_KG_SINCE = "2026-09-27"   # с этого дня сборщик Ленты пишет развесному цену за кг


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, column, kind in LATE_COLUMNS:
            have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        for ddl in LATE_INDEXES:
            conn.execute(ddl)
        _migrate(conn)
        conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Разовые исправления строк, записанных прежними сборщиками.

    Без этого ложное «kg» жило бы вечно: обновление строки берёт единицу через
    COALESCE, и новая «не сказано» (None) старую «kg» не перетирает.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    if version < 1:
        from app.db import FALSE_KG_CHAINS
        from app.matcher.normalize import sold_by_weight

        placeholders = ",".join("?" * len(FALSE_KG_CHAINS))
        rows = conn.execute(f"SELECT id, name FROM chain_products WHERE unit='kg'"
                            f" AND chain IN ({placeholders})", FALSE_KG_CHAINS).fetchall()
        conn.executemany("UPDATE chain_products SET unit=NULL WHERE id=?",
                         [(r["id"],) for r in rows if not sold_by_weight(r["name"] or "")])
    if version < 2:
        # Ленту сборщик перечитывает только по новым артикулам, и старые развесные
        # строки хранили бы цену ПОРЦИИ как цену килограмма ещё долго. Фасовка
        # известна — пересчитываем; нет — цену снимаем: перенос в базу человека
        # тогда не запишет ложную, а живая придёт при первом расчёте.
        # Только строки, заведённые до исправления сборщика: у новых цена уже за кг.
        old = "chain='lenta' AND unit='kg' AND price IS NOT NULL AND first_seen < ?"
        conn.execute("UPDATE chain_products SET price = ROUND(price * 1000.0 / weight_g, 2)"
                     f" WHERE {old} AND weight_g > 0", (LENTA_PER_KG_SINCE,))
        conn.execute("UPDATE chain_products SET price = NULL, price_seen = NULL"
                     f" WHERE {old} AND (weight_g IS NULL OR weight_g <= 0)",
                     (LENTA_PER_KG_SINCE,))
    if version < 3:
        # Штрихкод в одном виде для строк, записанных до 17.10.2026. Одна правка
        # на десятки тысяч строк: считает сама SQLite через нашу функцию.
        from app.matcher.normalize import gtin

        conn.create_function("korzina_gtin", 1, gtin, deterministic=True)
        conn.execute("UPDATE chain_products SET gtin = korzina_gtin(barcode)"
                     " WHERE barcode IS NOT NULL AND barcode != ''")
    if version < 4:
        # Удалить, а не выключить: выключенная строка помнит номер (спящий номер в
        # resolve_alias) и держала бы его вечно, а настоящей строкой сети она не была.
        conn.execute("DELETE FROM chain_products WHERE " + " OR ".join(
            [f"sku LIKE '{prefix}%'" for prefix in PLACEHOLDER_PREFIXES]
            + ["substr(sku, 1, length(chain) + 1) = chain || '-'"]))
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


# ---------- запись обхода ----------
def start_run(chain: str) -> int:
    init()
    with connect() as conn:
        cur = conn.execute("INSERT INTO crawl_runs (chain, started_at, status) VALUES (?,?,'running')",
                           (chain, now()))
        conn.commit()
        return int(cur.lastrowid)


def finish_run(run_id: int, status: str, *, seen: int = 0, added: int = 0, updated: int = 0,
               gone: int = 0, note: str | None = None) -> None:
    with connect() as conn:
        conn.execute("UPDATE crawl_runs SET finished_at=?, status=?, seen=?, added=?, updated=?,"
                     " gone=?, note=? WHERE id=?",
                     (now(), status, seen, added, updated, gone, note, run_id))
        conn.commit()


def fresh_chains(hours: float) -> dict[str, str]:
    """Сети, обойденные без ошибок за последние `hours` часов: код -> конец обхода.

    Упавший или заблокированный обход свежим не считается: его сеть осталась с
    прежними ценами, и плановый обход обязан попробовать её снова.
    """
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="microseconds")
    with connect() as conn:
        rows = conn.execute("SELECT chain, MAX(finished_at) AS at FROM crawl_runs"
                            " WHERE status = 'ok' AND finished_at >= ? GROUP BY chain", (since,))
        return {row["chain"]: row["at"] for row in rows}


def upsert_products(chain: str, products: Iterable[ChainProduct], seen_at: str | None = None
                    ) -> tuple[int, int, int]:
    """Сложить товары сети в каталог. Возвращает (увидено, новых, изменённых).

    Товар узнаётся по (chain, sku). Изменённым считается тот, у кого поменялось
    название, фасовка, категория или штрихкод — то, от чего зависит сопоставление;
    у такого сбрасывается привязка к единому товару, и сопоставление отнесёт его заново.
    Цена и наличие обновляются молча: это справочные поля, не ключи.
    """
    stamp = seen_at or now()
    seen = added = updated = 0
    with connect() as conn:
        for p in products:
            seen += 1
            stock = None if p.in_stock is None else int(p.in_stock)
            if p.point and (p.price is not None or stock is not None):
                conn.execute(
                    "INSERT INTO chain_prices (chain, sku, point, price, in_stock, seen_at)"
                    " VALUES (?,?,?,?,?,?) ON CONFLICT(chain, sku, point) DO UPDATE SET"
                    " price=excluded.price, in_stock=excluded.in_stock, seen_at=excluded.seen_at",
                    (chain, str(p.sku), str(p.point), p.price, stock, stamp))
            row = conn.execute("SELECT id, name, weight_g, category, barcode FROM chain_products"
                               " WHERE chain=? AND sku=?", (chain, str(p.sku))).fetchone()
            if p.seen_only:
                if row is not None:
                    conn.execute("UPDATE chain_products SET last_seen=?, active=1 WHERE id=?",
                                 (stamp, row["id"]))
                continue
            price_seen = stamp if p.price is not None else None
            code = gtin(p.barcode)
            if row is None:
                conn.execute(
                    "INSERT INTO chain_products (chain, sku, name, brand, weight_g, unit, category,"
                    " url, image, barcode, gtin, price, in_stock, price_seen, first_seen, last_seen,"
                    " active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (chain, str(p.sku), p.name, p.brand, p.weight_g, p.unit, p.category, p.url,
                     p.image, p.barcode, code, p.price, stock, price_seen, stamp, stamp))
                added += 1
                continue
            changed = (row["name"] != p.name or row["weight_g"] != p.weight_g
                       or row["category"] != p.category or (row["barcode"] or None) != (p.barcode or None))
            # Цена, которой обход не принёс, остаётся прежней — но со СВОЕЙ датой
            # (price_seen), а не с датой этого обхода: иначе недельная цена уезжала в
            # базу человека как свежая.
            conn.execute(
                "UPDATE chain_products SET name=?, brand=COALESCE(?, brand), weight_g=?, unit=COALESCE(?, unit),"
                " category=?, url=COALESCE(?, url), image=COALESCE(?, image), barcode=COALESCE(?, barcode),"
                # gtin держится в согласии с barcode: сеть сменила код на внутренний или
                # пустой — прежний GTIN не должен находить строку «по штрихкоду».
                " gtin=CASE WHEN ? IS NULL THEN gtin ELSE ? END,"
                " price=COALESCE(?, price), in_stock=COALESCE(?, in_stock),"
                " price_seen=COALESCE(?, price_seen), last_seen=?, active=1"
                + (", prev_item_id=COALESCE(item_id, prev_item_id), item_id=NULL,"
                   " link_method=NULL, link_score=NULL" if changed else "")
                + " WHERE id=?",
                (p.name, p.brand, p.weight_g, p.unit, p.category, p.url, p.image, p.barcode,
                 p.barcode, code, p.price, stock, price_seen, stamp, row["id"]))
            updated += int(changed)
        conn.commit()
    return seen, added, updated


def unmatched_count() -> int:
    """Живые строки каталога, ещё не отнесённые к единому товару.

    Обход, оборванный выкладкой, успевает сложить строки и не успевает их
    сопоставить; следующий обход той же сети может ничего не поменять — и без
    этого счёта строки остались бы без единого товара до случайной правки.
    """
    try:
        with connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM chain_products"
                               " WHERE active=1 AND item_id IS NULL").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["n"] or 0)


def product_url(chain: str, sku: str) -> str | None:
    """Адрес товара сети в каталоге. None — каталога нет или товара в нём нет.

    Спрашивает его коннектор попутно, за ценой (app/connectors/dixy.py), поэтому базу
    не создаёт: в установке без каталога — тесты, первый запуск — пустой файл был бы
    лишним. Не открылась — адреса просто нет.
    """
    file = path()
    if not os.path.exists(file):
        return None
    try:
        conn = sqlite3.connect(file, timeout=5)
        try:
            row = conn.execute("SELECT url FROM chain_products WHERE chain=? AND sku=?",
                               (chain, str(sku))).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return (row[0] or None) if row else None


def point_prices(chain: str, sku: str) -> dict[str, dict]:
    """Цена и наличие артикула по точкам: код точки -> {price, in_stock, seen_at}."""
    try:
        with connect() as conn:
            rows = conn.execute("SELECT point, price, in_stock, seen_at FROM chain_prices"
                                " WHERE chain=? AND sku=?", (chain, str(sku))).fetchall()
    except sqlite3.OperationalError:          # база прежней версии, таблицы ещё нет
        return {}
    return {row["point"]: dict(row) for row in rows}


def _address_key(address: str) -> str:
    return " ".join((address or "").split()).lower()


def remember_points(points) -> None:
    """Запомнить, какая точка сети досталась какому адресу (places.Point)."""
    rows = [(_address_key(p.address), p.chain, str(p.code), p.label or None, now())
            for p in points or () if getattr(p, "address", None) and getattr(p, "code", None)]
    if not rows:
        return
    init()
    with connect() as conn:
        conn.executemany(
            "INSERT INTO address_points (address, chain, point, label, updated_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT(address, chain) DO UPDATE SET point=excluded.point, label=excluded.label,"
            " updated_at=excluded.updated_at", rows)
        conn.commit()


def point_of(address: str, chain: str) -> str | None:
    """Точка сети, подобранная обходом к этому адресу. None — обход её ещё не подбирал."""
    key = _address_key(address)
    if not key:
        return None
    try:
        with connect() as conn:
            row = conn.execute("SELECT point FROM address_points WHERE address=? AND chain=?",
                               (key, chain)).fetchone()
    except sqlite3.OperationalError:
        return None
    return str(row["point"]) if row else None


def retire_unseen(chain: str, seen_at: str) -> int:
    """Пометить пропавшими товары сети, которых не было в этом обходе. Возвращает число."""
    with connect() as conn:
        cur = conn.execute("UPDATE chain_products SET active=0 WHERE chain=? AND active=1 AND last_seen<?",
                           (chain, seen_at))
        conn.commit()
        return cur.rowcount


# ---------- чтение ----------
def chain_counts() -> dict[str, dict]:
    """По каждой сети: сколько товаров в каталоге, когда обновлялся, чем кончился обход."""
    init()
    out: dict[str, dict] = {}
    with connect() as conn:
        for row in conn.execute("SELECT chain, COUNT(*) AS n, SUM(item_id IS NOT NULL) AS linked"
                                " FROM chain_products WHERE active=1 GROUP BY chain"):
            out[row["chain"]] = {"products": row["n"], "linked": row["linked"] or 0}
        for row in conn.execute(
                "SELECT r.chain, r.finished_at, r.status, r.note, r.seen FROM crawl_runs r"
                " WHERE r.id IN (SELECT MAX(id) FROM crawl_runs GROUP BY chain)"):
            out.setdefault(row["chain"], {"products": 0, "linked": 0}).update(
                {"last_run": row["finished_at"], "status": row["status"], "note": row["note"],
                 "seen": row["seen"]})
    return out


def known_skus(chain: str) -> set[str]:
    """Артикулы сети, о которых каталог уже знает название. Нужно сборщикам по sitemap."""
    init()
    with connect() as conn:
        return {str(r["sku"]) for r in conn.execute(
            "SELECT sku FROM chain_products WHERE chain=?", (chain,))}


def products_of_chain(chain: str, active_only: bool = True) -> list[dict]:
    with connect() as conn:
        sql = "SELECT * FROM chain_products WHERE chain=?" + (" AND active=1" if active_only else "")
        return [dict(r) for r in conn.execute(sql, (chain,))]


def all_products(active_only: bool = True) -> list[dict]:
    with connect() as conn:
        sql = "SELECT * FROM chain_products" + (" WHERE active=1" if active_only else "")
        return [dict(r) for r in conn.execute(sql + " ORDER BY chain, id")]


def item(item_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM catalog_items WHERE id=?", (item_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["chains"] = [dict(r) for r in conn.execute(
            f"SELECT p.* FROM chain_products p WHERE p.item_id=? AND p.active=1{_NOT_PLACEHOLDER}"
            " ORDER BY p.chain", (item_id,))]
        return out


# ---------- единая база: чтение под сопоставление товаров людей ----------
#
# Товар человека привязывается к единому товару НОМЕРОМ (products.master_id в его
# базе). Номер постоянный (refresh.match_all), а слившиеся номера ведут сюда через
# item_aliases. Всё ниже только читает и заглушек (hist-, list-) не отдаёт.
_NOT_PLACEHOLDER = " AND " + " AND ".join(
    [f"p.sku NOT LIKE '{prefix}%'" for prefix in PLACEHOLDER_PREFIXES]
    + ["substr(p.sku, 1, length(p.chain) + 1) != p.chain || '-'"])


def resolve_alias(item_id: int | None, conn: sqlite3.Connection | None = None) -> int | None:
    """Где сейчас живёт единый товар с этим номером. None — его больше нет.

    Номер ведёт сам на себя, пока единый товар есть; слившийся — по цепочке
    item_aliases к тому, в который влился. Цепочка короткая, но на всякий случай
    ограничена: зацикленный адрес не должен вешать запрос человека.

    СПЯЩИЙ НОМЕР. Все строки товара могут на одну пересборку выпасть из обхода
    (сеть сняла товар, точка его не показала). Пересборка их не видит, но строки
    помнят номер (prev_item_id) и вернут его, когда товар вернётся. Пока они спят,
    номер жив: иначе привязанные к нему товары людей искались бы заново и уходили
    к соседней фасовке (ревью 18.10.2026). Спящий номер отвечается сам собой, а
    store.item(номер) у него пуст — строк, которые можно положить в корзину, нет.
    """
    if item_id is None:
        return None
    own = conn is None
    conn = conn or connect()
    try:
        current = int(item_id)
        for _ in range(16):
            if conn.execute("SELECT 1 FROM catalog_items WHERE id=?", (current,)).fetchone():
                return current
            moved = conn.execute("SELECT new_id FROM item_aliases WHERE old_id=?",
                                 (current,)).fetchone()
            if not moved or int(moved["new_id"]) == current:
                asleep = conn.execute("SELECT 1 FROM chain_products WHERE prev_item_id=?"
                                      " AND item_id IS NULL AND active=0 LIMIT 1",
                                      (current,)).fetchone()
                return current if asleep else None
            current = int(moved["new_id"])
        return None
    finally:
        if own:
            conn.close()


def rows_by_gtin(codes, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Живые строки сетей с этими штрихкодами (коды уже в виде normalize.gtin)."""
    codes = sorted({c for c in codes if c})
    if not codes:
        return []
    own = conn is None
    conn = conn or connect()
    try:
        marks = ",".join("?" * len(codes))
        return [dict(r) for r in conn.execute(
            f"SELECT p.* FROM chain_products p WHERE p.active=1 AND p.item_id IS NOT NULL"
            f" AND p.gtin IN ({marks}){_NOT_PLACEHOLDER}", codes)]
    finally:
        if own:
            conn.close()


def rows_by_skus(pairs, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Живые строки по парам (сеть, артикул). Заглушки отброшены ещё до запроса."""
    from app.catalog.model import placeholder_sku

    pairs = sorted({(str(c), str(s)) for c, s in pairs if c and s and not placeholder_sku(s, c)})
    if not pairs:
        return []
    own = conn is None
    conn = conn or connect()
    try:
        out: list[dict] = []
        for chain, sku in pairs:
            row = conn.execute("SELECT p.* FROM chain_products p WHERE p.chain=? AND p.sku=?"
                               " AND p.active=1 AND p.item_id IS NOT NULL", (chain, sku)).fetchone()
            if row:
                out.append(dict(row))
        return out
    finally:
        if own:
            conn.close()


def item_rows(item_id: int, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Живые строки сетей единого товара, без заглушек."""
    own = conn is None
    conn = conn or connect()
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT p.* FROM chain_products p WHERE p.item_id=? AND p.active=1{_NOT_PLACEHOLDER}"
            " ORDER BY p.chain, p.sku", (int(item_id),))]
    finally:
        if own:
            conn.close()


def retired_rows(pairs=(), codes=(), conn: sqlite3.Connection | None = None) -> list[dict]:
    """Снятые с витрины строки (active=0) по парам (сеть, артикул) и штрихкодам.

    У каждой — number: номер единого товара, который строка помнит (item_id или,
    после пересборки, prev_item_id). Нужны, когда якорь товара человека — его
    артикул или штрихкод — на ночь выпал из обхода: связь не должна от этого рваться.
    """
    from app.catalog.model import placeholder_sku

    pairs = sorted({(str(c), str(s)) for c, s in pairs if c and s and not placeholder_sku(s, c)})
    codes = sorted({c for c in codes if c})
    if not pairs and not codes:
        return []
    own = conn is None
    conn = conn or connect()
    try:
        out: list[dict] = []
        head = ("SELECT p.*, COALESCE(p.item_id, p.prev_item_id) AS number FROM chain_products p"
                " WHERE p.active=0 AND COALESCE(p.item_id, p.prev_item_id) IS NOT NULL")
        for chain, sku in pairs:
            out += [dict(r) for r in conn.execute(head + " AND p.chain=? AND p.sku=?", (chain, sku))]
        if codes:
            marks = ",".join("?" * len(codes))
            out += [dict(r) for r in conn.execute(
                head + f" AND p.gtin IN ({marks}){_NOT_PLACEHOLDER}", codes)]
        return out
    finally:
        if own:
            conn.close()


def asleep_since(item_id: int, conn: sqlite3.Connection | None = None) -> str | None:
    """Когда в последний раз видели строки спящего номера (их last_seen). None — не спит."""
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute("SELECT MAX(last_seen) AS seen FROM chain_products WHERE prev_item_id=?"
                           " AND item_id IS NULL AND active=0", (int(item_id),)).fetchone()
        return row["seen"] if row else None
    finally:
        if own:
            conn.close()


def asleep_rows(item_id: int, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Строки спящего номера: так товар выглядел, пока его не сняли с витрины."""
    own = conn is None
    conn = conn or connect()
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT p.* FROM chain_products p WHERE p.prev_item_id=? AND p.item_id IS NULL"
            f" AND p.active=0{_NOT_PLACEHOLDER}", (int(item_id),))]
    finally:
        if own:
            conn.close()


def folded_terms(terms) -> list[str]:
    """Три основы, по которым ищет search_folded: самые длинные, при равной длине — по алфавиту."""
    return sorted({t for t in terms if t and len(t) >= 3}, key=lambda t: (-len(t), t))[:3]


def search_folded(terms, limit: int = 80, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Единые товары, в чьём названии есть слова, НАЧИНАЮЩИЕСЯ с этих основ.

    Ищет по fnorm — словам названия и марки в общем своде письма, — поэтому
    кириллица чека встречает латиницу Перекрёстка, а сокращение чека «простокв»
    находит «Простоквашино». Берутся три самые длинные основы (при равной длине —
    по алфавиту, иначе выбор зависел бы от случайного порядка множества в процессе),
    и товар годится, если в нём есть хоть одна. Первыми идут товары, где совпало
    больше основ: требовать сразу все значило бы терять товар из-за одного слова
    чека, которого в названии сети нет («пастер» в «МОЛОКО ПАСТЕР ДОМИК В ДЕРЕВНЕ»,
    ревью 18.10.2026). Это отбор кандидатов, а не решение: решает сопоставление
    (match.same_product и сокращения чека).
    """
    picked = folded_terms(terms)
    if not picked:
        return []
    own = conn is None
    conn = conn or connect()
    try:
        likes = [f"% {t}%" for t in picked]
        hits = " + ".join("(i.fnorm LIKE ?)" for _ in picked)
        where = " OR ".join("i.fnorm LIKE ?" for _ in picked)
        return [dict(r) for r in conn.execute(
            f"SELECT i.* FROM catalog_items i WHERE {where}"
            f" ORDER BY ({hits}) DESC, LENGTH(i.name), i.id LIMIT ?",
            likes + likes + [int(limit)])]
    finally:
        if own:
            conn.close()


def search_items(query: str, limit: int = 20, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Поиск по единым товарам: все слова запроса должны войти в название.

    Порядок: сначала те, у которых название НАЧИНАЕТСЯ с первого слова запроса («молоко»
    — это молоко, а не творог марки «Лебедянь молоко»), внутри — известные большему
    числу сетей (их корзина уедет в магазины без ручного поиска), потом короткие названия.
    """
    from app.catalog.match import ordered_words

    words = ordered_words(query)
    if not words:
        return []
    where = " AND ".join("i.norm LIKE ?" for _ in words)
    params = [f"%{w}%" for w in words] + [max(limit * 6, 60)]
    sql = ("SELECT i.*, COUNT(DISTINCT p.chain) AS chains_n,"
           " GROUP_CONCAT(DISTINCT p.chain) AS chain_codes"
           " FROM catalog_items i LEFT JOIN chain_products p ON p.item_id=i.id AND p.active=1"
           f" WHERE {where} GROUP BY i.id ORDER BY chains_n DESC, LENGTH(i.name) ASC LIMIT ?")
    if conn is not None:                 # сопоставление зовёт тысячи раз: init уже был
        rows = conn.execute(sql, params).fetchall()
    else:
        init()
        with connect() as own:
            rows = own.execute(sql, params).fetchall()
    head = words[0]
    ranked = sorted((dict(r) for r in rows),
                    key=lambda r: (0 if (r.get("norm") or "").startswith(head) else 1,
                                   -(r.get("chains_n") or 0), len(r.get("name") or "")))
    return ranked[:limit]


def item_count() -> dict:
    init()
    with connect() as conn:
        items = conn.execute("SELECT COUNT(*) AS n FROM catalog_items").fetchone()["n"]
        multi = conn.execute("SELECT COUNT(*) AS n FROM (SELECT item_id FROM chain_products"
                             " WHERE item_id IS NOT NULL AND active=1 GROUP BY item_id"
                             " HAVING COUNT(DISTINCT chain) > 1)").fetchone()["n"]
        last = conn.execute("SELECT finished_at FROM match_runs ORDER BY id DESC LIMIT 1").fetchone()
    return {"items": items, "multi": multi, "matched_at": last["finished_at"] if last else None}


# ---------- очередь адресов ----------
# Человек указал адрес — цены по нему должны поехать СРАЗУ, а не в три часа ночи.
# Но сам экран ждать не должен: подбор точек и обход — это минуты сети. Поэтому
# экран делает одну вставку в очередь, а всю работу забирает планировщик.
def enqueue_address(address: str) -> bool:
    """Поставить адрес в очередь на загрузку цен. False — он там уже есть.

    Повтор отсекается уникальным индексом по ждущим и идущим записям: человек
    может поправить адрес трижды подряд, и каждая правка не должна превращаться
    в свой обход. Уже загруженный адрес в очередь вернуть можно — это и есть
    обновление цен.
    """
    address = " ".join((address or "").split())
    if not address:
        return False
    init()
    try:
        with connect() as conn:
            conn.execute("INSERT INTO address_queue (address, requested_at, status)"
                         " VALUES (?,?,'waiting')", (address, now()))
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def take_address() -> dict | None:
    """Взять из очереди самый давний ждущий адрес и пометить его идущим.

    Пометка нужна не для красоты: без неё перезапуск планировщика посреди обхода
    оставил бы адрес ждущим, и следующий запуск начал бы его заново с нуля — а он
    как раз наполовину загружен.
    """
    init()
    with connect() as conn:
        row = conn.execute("SELECT * FROM address_queue WHERE status='waiting'"
                           " ORDER BY requested_at, id LIMIT 1").fetchone()
        if not row:
            return None
        conn.execute("UPDATE address_queue SET status='running', started_at=? WHERE id=?",
                     (now(), row["id"]))
        conn.commit()
        return dict(row)


def finish_address(queue_id: int, status: str, note: str | None = None) -> None:
    with connect() as conn:
        conn.execute("UPDATE address_queue SET status=?, finished_at=?, note=? WHERE id=?",
                     (status, now(), note, queue_id))
        conn.commit()


def revive_stuck() -> int:
    """Вернуть в очередь то, что осталось идущим после падения или выкладки.

    Планировщик живёт в контейнере, который перезапускается при каждой выкладке.
    Без этого адрес, застигнутый перезапуском, остался бы «идущим» навсегда, и
    цены по нему никто бы не догрузил.
    """
    init()
    with connect() as conn:
        cur = conn.execute("UPDATE address_queue SET status='waiting', started_at=NULL"
                           " WHERE status='running'")
        conn.commit()
        return cur.rowcount


def queue_state(limit: int = 20) -> list[dict]:
    """Что в очереди — для экрана «Магазины» и для журнала."""
    init()
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM address_queue ORDER BY requested_at DESC, id DESC LIMIT ?", (limit,))]


def address_status(address: str) -> dict | None:
    """Последнее, что известно про загрузку цен по этому адресу."""
    address = " ".join((address or "").split())
    if not address:
        return None
    init()
    with connect() as conn:
        row = conn.execute("SELECT * FROM address_queue WHERE address=?"
                           " ORDER BY requested_at DESC, id DESC LIMIT 1", (address,)).fetchone()
    return dict(row) if row else None
