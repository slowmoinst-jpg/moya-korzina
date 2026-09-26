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
from app.catalog.model import ChainProduct

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
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
LATE_COLUMNS = (("chain_products", "price_seen", "TEXT"),)


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, column, kind in LATE_COLUMNS:
            have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        conn.commit()


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
            if row is None:
                conn.execute(
                    "INSERT INTO chain_products (chain, sku, name, brand, weight_g, unit, category,"
                    " url, image, barcode, price, in_stock, price_seen, first_seen, last_seen, active)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (chain, str(p.sku), p.name, p.brand, p.weight_g, p.unit, p.category, p.url,
                     p.image, p.barcode, p.price, stock, price_seen, stamp, stamp))
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
                " price=COALESCE(?, price), in_stock=COALESCE(?, in_stock),"
                " price_seen=COALESCE(?, price_seen), last_seen=?, active=1"
                + (", item_id=NULL, link_method=NULL, link_score=NULL" if changed else "")
                + " WHERE id=?",
                (p.name, p.brand, p.weight_g, p.unit, p.category, p.url, p.image, p.barcode, p.price,
                 stock, price_seen, stamp, row["id"]))
            updated += int(changed)
        conn.commit()
    return seen, added, updated


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
            "SELECT * FROM chain_products WHERE item_id=? AND active=1 ORDER BY chain", (item_id,))]
        return out


def search_items(query: str, limit: int = 20) -> list[dict]:
    """Поиск по единым товарам: все слова запроса должны войти в название.

    Порядок: сначала те, у которых название НАЧИНАЕТСЯ с первого слова запроса («молоко»
    — это молоко, а не творог марки «Лебедянь молоко»), внутри — известные большему
    числу сетей (их корзина уедет в магазины без ручного поиска), потом короткие названия.
    """
    from app.catalog.match import ordered_words

    words = ordered_words(query)
    if not words:
        return []
    init()
    where = " AND ".join("i.norm LIKE ?" for _ in words)
    params = [f"%{w}%" for w in words] + [max(limit * 6, 60)]
    with connect() as conn:
        rows = conn.execute(
            "SELECT i.*, COUNT(DISTINCT p.chain) AS chains_n,"
            " GROUP_CONCAT(DISTINCT p.chain) AS chain_codes"
            " FROM catalog_items i LEFT JOIN chain_products p ON p.item_id=i.id AND p.active=1"
            f" WHERE {where} GROUP BY i.id ORDER BY chains_n DESC, LENGTH(i.name) ASC LIMIT ?",
            params).fetchall()
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
