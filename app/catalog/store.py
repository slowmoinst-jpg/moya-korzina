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

import json
import os
import sqlite3
import threading
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

-- Словарь альтернативных названий: «как пишет СЕТЬ» и «как пишет ЧЕК». Нужен потому,
-- что сокращение чека («МОЛ.ПАСТ.ПРОСТОКВАШИНО») не выводится из имени единого товара
-- алгоритмом: чек молчит о сорте, а у строки сети нет марки. Варианты копятся между
-- пересборками (first_seen, hits) и служат поиску кандидатами (store.search_variants).
--
-- ЗАЧЕМ БЕЗ ССЫЛКИ НА catalog_items. Пересборка (refresh.match_all) стирает таблицу
-- единых товаров и пишет её заново: ON DELETE CASCADE уносил бы весь словарь каждую
-- ночь, а строгая ссылка ломала бы саму пересборку. Целостность держит наполнение —
-- номер, которого больше нет, переезжает по item_aliases или уходит прочь
-- (store.reconcile_variants).
--
-- БЕЗОПАСНОСТЬ ДАННЫХ (решение 18.10.2026). Сюда попадают названия из ЛИЧНЫХ чеков,
-- но обезличенно: только название товара, код сети и номер единого товара. Ни номера
-- человека, ни id его товара, ни даты покупки здесь нет и быть не должно: это словарь
-- «как пишут чеки», а не история покупок (refresh._receipt_variant_rows читает личные
-- базы только на чтение и только эти три поля).
CREATE TABLE IF NOT EXISTS name_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,            -- catalog_items.id; номер постоянный
    variant TEXT NOT NULL,               -- название как написали: строка сети или чека
    folded TEXT NOT NULL,                -- слова варианта в общем своде письма, с пробелами по краям
    source TEXT NOT NULL,                -- chain — строка сети | receipt — чек | human — человек
    chain TEXT NOT NULL DEFAULT '',      -- код сети; '' — сеть не названа
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 1,     -- сколько раз вариант попался при наполнении
    UNIQUE (item_id, folded, source, chain)
);

-- «Спросили — товара нет». Сеть сама сказала, что такого артикула в её витрине нет
-- (Лента: карточка MCP отвечает «товар N не найден»). Запоминаем, чтобы не спрашивать
-- одно и то же каждый обход: у Ленты sitemap содержит 82 908 адресов, а витрина хаба
-- знает не все, и «мёртвые» артикулы, стоящие в начале карты, вечно занимали потолок
-- обхода (2000 в сутки), из-за чего каталог прирастал на ~500 за ночь вместо полутора
-- тысяч. Через retry_days артикул спрашивают снова: товар мог вернуться в витрину.
CREATE TABLE IF NOT EXISTS chain_misses (
    chain TEXT NOT NULL,
    sku TEXT NOT NULL,
    first_try TEXT NOT NULL,
    last_try TEXT NOT NULL,              -- когда спросили в последний раз
    tries INTEGER NOT NULL DEFAULT 1,
    note TEXT,
    PRIMARY KEY (chain, sku)
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

-- ИСТОРИЯ ЦЕНЫ: строка на каждое ИЗМЕНЕНИЕ, а не на каждый обход. Замер 19.10.2026:
-- в chain_prices у каждого ключа ровно одна строка при 18–24 ночных снимках — прежняя
-- цена затиралась, и ответить «сейчас дороже обычного, подожди» было нечем.
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,
    sku TEXT NOT NULL,
    point TEXT,
    price REAL NOT NULL,
    seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS price_history_key ON price_history (chain, sku, seen_at);

-- Снимки проверки здоровья базы (app/catalog/checkup.py). Нужны, чтобы сравнить сегодня
-- с прошлым разом и заметить, что наполнение просело: «постоянная основа» проверяется не
-- глазами, а сравнением.
CREATE TABLE IF NOT EXISTS catalog_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    at TEXT NOT NULL
);

-- КЭШ СЕРВЕРНОГО ПОИСКА ПО СЕТЯМ (app/storefind.py, 21.10.2026). Телефон ищет товар в
-- витрине и упирается в стены (403, пустая SPA, «Выключите VPN»), поэтому ищет сервер —
-- шлюзом Магнита, MCP Ленты и Diginetica Дикси. Имя→артикул в сети меняется редко, так
-- что ответ живёт сутки: это бережёт и сеть, и время человека.
CREATE TABLE IF NOT EXISTS storefind_cache (
    store TEXT NOT NULL,
    name_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    at REAL NOT NULL,
    PRIMARY KEY (store, name_key)
);

-- ЦЕНУ, КОТОРУЮ ВИДЕЛИ, НЕЛЬЗЯ СТЕРЕТЬ МОЛЧА. price_seen ставится тогда, когда источник
-- цену назвал. Замер 20.10.2026: у Самоката price_seen стоял у 1 494 строк, а цена
-- осталась у 884 — 610 значений стёрли, не тронув отметку, и товары выпали из сравнения.
-- Наш код так не делает (в upsert обновление идёт через COALESCE), но в базу пишут и
-- другие инструменты. Триггер превращает такую запись в ничто: строка не меняется.
-- Законная чистка (перенос цены Ленты, миграции) гасит price и price_seen ВМЕСТЕ — её
-- триггер не трогает.
CREATE TRIGGER IF NOT EXISTS keep_seen_price
BEFORE UPDATE OF price ON chain_products
WHEN NEW.price IS NULL AND OLD.price IS NOT NULL AND NEW.price_seen IS NOT NULL
BEGIN
    SELECT RAISE(IGNORE);
END;

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
-- Поиск идёт по началам слов варианта (LIKE '% основа%'), как search_folded по fnorm.
-- Отдельного индекса по item_id не нужно: он уже первый в уникальном ключе таблицы.
CREATE INDEX IF NOT EXISTS idx_variants_folded ON name_variants(folded);

-- Полнотекстовые индексы FTS5 для мгновенного поиска по 300+ тыс. товаров (вместо full-table LIKE '%…%').
CREATE VIRTUAL TABLE IF NOT EXISTS catalog_items_fts USING fts5(
    fnorm, norm, name,
    content='catalog_items', content_rowid='id',
    tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS catalog_items_ai AFTER INSERT ON catalog_items BEGIN
    INSERT INTO catalog_items_fts(rowid, fnorm, norm, name)
    VALUES (new.id, COALESCE(new.fnorm, ''), COALESCE(new.norm, ''), COALESCE(new.name, ''));
END;
CREATE TRIGGER IF NOT EXISTS catalog_items_ad AFTER DELETE ON catalog_items BEGIN
    INSERT INTO catalog_items_fts(catalog_items_fts, rowid, fnorm, norm, name)
    VALUES ('delete', old.id, COALESCE(old.fnorm, ''), COALESCE(old.norm, ''), COALESCE(old.name, ''));
END;
CREATE TRIGGER IF NOT EXISTS catalog_items_au AFTER UPDATE OF fnorm, norm, name ON catalog_items BEGIN
    INSERT INTO catalog_items_fts(catalog_items_fts, rowid, fnorm, norm, name)
    VALUES ('delete', old.id, COALESCE(old.fnorm, ''), COALESCE(old.norm, ''), COALESCE(old.name, ''));
    INSERT INTO catalog_items_fts(rowid, fnorm, norm, name)
    VALUES (new.id, COALESCE(new.fnorm, ''), COALESCE(new.norm, ''), COALESCE(new.name, ''));
END;

CREATE VIRTUAL TABLE IF NOT EXISTS name_variants_fts USING fts5(
    folded,
    content='name_variants', content_rowid='id',
    tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS name_variants_ai AFTER INSERT ON name_variants BEGIN
    INSERT INTO name_variants_fts(rowid, folded)
    VALUES (new.id, COALESCE(new.folded, ''));
END;
CREATE TRIGGER IF NOT EXISTS name_variants_ad AFTER DELETE ON name_variants BEGIN
    INSERT INTO name_variants_fts(name_variants_fts, rowid, folded)
    VALUES ('delete', old.id, COALESCE(old.folded, ''));
END;
CREATE TRIGGER IF NOT EXISTS name_variants_au AFTER UPDATE OF folded ON name_variants BEGIN
    INSERT INTO name_variants_fts(name_variants_fts, rowid, folded)
    VALUES ('delete', old.id, COALESCE(old.folded, ''));
    INSERT INTO name_variants_fts(rowid, folded)
    VALUES (new.id, COALESCE(new.folded, ''));
END;
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
    fresh = file not in _WAL or not os.path.exists(file)
    if fresh:
        os.makedirs(os.path.dirname(file), exist_ok=True)
    conn = sqlite3.connect(file, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Каталог пишет фоновый процесс, читает интерфейс — WAL даёт им не мешать друг другу.
    # Режим живёт в файле: раз за процесс, повтор стоил 0,3 мс на соединение (app/db.get_conn).
    if fresh:
        # Двое разом на новой базе получают «database is locked» сразу, мимо timeout: по очереди
        # (подробно и про предел замка — app/db.get_conn).
        with _WAL_LOCKS.setdefault(file, threading.Lock()):
            conn.execute("PRAGMA journal_mode = WAL")
        _WAL.add(file)
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -64000")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA mmap_size = 268435456")
    return conn


_WAL: set[str] = set()
_WAL_LOCKS: dict[str, threading.Lock] = {}


# Колонки, добавленные после первых выпусков: CREATE TABLE IF NOT EXISTS до уже
# существующей базы их не донесёт.
LATE_COLUMNS = (("chain_products", "price_seen", "TEXT"),
                ("chain_products", "gtin", "TEXT"),
                ("chain_products", "detail_checked_at", "TEXT"),
                ("chain_products", "detail_requested_at", "TEXT"),
                ("chain_products", "photo_digest", "TEXT"),
                ("chain_products", "photo_after", "INTEGER NOT NULL DEFAULT 0"),
                # Прежний номер единого товара у строки, чья привязка сброшена сменой
                # названия, фасовки или штрихкода: по нему пересборка сохраняет номер
                # и ведёт переадресацию (refresh._keep_numbers).
                ("chain_products", "prev_item_id", "INTEGER"),
                ("catalog_items", "fnorm", "TEXT"))

# Индексы по поздним колонкам: в SCHEMA их ставить нельзя — у старой базы колонки
# ещё нет, и скрипт упал бы раньше, чем ALTER её добавит.
LATE_INDEXES = ("CREATE INDEX IF NOT EXISTS idx_cp_gtin ON chain_products(gtin)",
                "CREATE INDEX IF NOT EXISTS idx_cp_photo_due ON chain_products(chain,photo_after)"
                " WHERE active=1",
                "CREATE INDEX IF NOT EXISTS idx_cp_prev ON chain_products(prev_item_id)")


# Версия данных каталога (PRAGMA user_version). 1 — сняты ложные «kg» у строк сетей,
# чьи сборщики ставили единицу по отсутствию граммовки (app/db.FALSE_KG_CHAINS).
# 2 — у развесного Ленты цена порции заменена ценой килограмма.
# 3 — у строк со штрихкодом заполнен gtin: тот же код в одном виде (17.10.2026).
# 4 — убраны строки-заглушки (hist-, list-, «<сеть>-…»): сборщики Пятёрочки и
#     Самоката переносили их из store_products, а артикулом сети они не бывают (18.10.2026).
# 5 — заведён словарь альтернативных названий name_variants (18.10.2026): сокращение
#     чека перестаёт выводиться заново на лету и запоминается. Таблицу создаёт SCHEMA
#     (CREATE TABLE IF NOT EXISTS идемпотентен), данных для переноса нет — словарь
#     наполняет refresh.fill_name_variants, его зовёт match_all после пересборки.
# 6 — заведена память «спросили — товара нет» chain_misses (18.10.2026): сборщик
#     Ленты спрашивал одни и те же «мёртвые» артикулы каждый обход, и потолок
#     details_per_run уходил на них, а не на новые товары. Таблицу создаёт SCHEMA.
# 7 — у строк, записанных без фасовки и единицы, они разобраны из названия (18.10.2026):
#     METRO сменил форму packing и отдавал {"size": 1, "type": "шт"} вместо прежних
#     ключей, а Перекрёсток, Впрок и Монетка собирают имя из адреса и не разбирали его
#     вовсе. 226 854 строки остались без веса и единицы — и привязки человека к ним
#     тоже (у METRO 140 привязок, ни одной с весом), поэтому наряд не мог пересчитать,
#     сколько упаковок класть в корзину сети.
# 9 — артикулы Дикси в заглавном виде (16.11.2026): карта сайта отдавала их строчными, и тот же
#     товар жил в каталоге двумя строками, «DI…» с ценой и «di…» без неё (_dixy_upper).
SCHEMA_VERSION = 10
LENTA_PER_KG_SINCE = "2026-09-27"   # с этого дня сборщик Ленты пишет развесному цену за кг


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, column, kind in LATE_COLUMNS:
            have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
                except sqlite3.OperationalError as err:
                    if "duplicate column name" not in str(err):     # второй поток добавил её первым
                        raise
        for ddl in LATE_INDEXES:
            conn.execute(ddl)
        _migrate(conn)
        conn.commit()
    _READY.add(path())


_READY: set[str] = set()


def _ready() -> None:
    """Схема каталога проверена этим процессом — не прогонять её на каждом чтении.

    init() стоит 1 мс, а наряд телефону читает каталог десятки раз (замер 09.11.2026).
    Явный init() по-прежнему прогоняет схему и миграции целиком.
    """
    if path() not in _READY or not os.path.exists(path()):
        init()


def _migrate(conn: sqlite3.Connection) -> None:
    """Разовые исправления строк, записанных прежними сборщиками.

    Без этого ложное «kg» жило бы вечно: обновление строки берёт единицу через
    COALESCE, и новая «не сказано» (None) старую «kg» не перетирает.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    if not conn.in_transaction:
        # Пишущая транзакция сразу, версия читается заново: второй вход, прождав очереди, видит готовую
        # версию и не повторяет шаги. Шаг 2 не идемпотентен: цену развесной Ленты он пересчитал бы дважды.
        conn.execute("BEGIN IMMEDIATE")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return                  # init() закроет пустую транзакцию
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
    if version < 7:
        # Фасовка у строк, записанных сборщиками, которые её не разбирали. Правило то
        # же, что в crawlers.slug_size: вес берём из названия («500g», «750ml», «, 1кг»),
        # весовой товар узнаём по слову «весов…» — и по латинскому «vesov» из адресов X5.
        # Заводим только там, где поля пусты: у Ленты, Дикси, ВкусВилла и Магнита их уже
        # проставили сборщики.
        #
        # ЕДИНИЦУ ЗАПОЛНЯЕМ ТОЛЬКО ДЛЯ ВЕСОВОГО. «pcs» по умолчанию было бы догадкой:
        # весовой товар, у которого в названии нет слова «весов…» (METRO узнаёт его по
        # форме упаковки), стал бы штучным, и compatible() начал бы отсекать верные
        # пары «kg ↔ pcs». Пустая единица значит «сеть не сказала», и это честнее.
        from app.matcher.normalize import parse_weight, sold_by_weight

        weights: list[tuple[float, int]] = []
        units: list[tuple[str, int]] = []
        for row in conn.execute("SELECT id, name, weight_g, unit FROM chain_products"
                                " WHERE weight_g IS NULL OR unit IS NULL"):
            text = row["name"] or ""
            if row["weight_g"] is None:
                value, _ = parse_weight(text)
                if value:
                    weights.append((value, row["id"]))
            if row["unit"] is None and (sold_by_weight(text) or "vesov" in text.lower()):
                units.append(("kg", row["id"]))
        if weights:
            conn.executemany("UPDATE chain_products SET weight_g=? WHERE id=?", weights)
        if units:
            conn.executemany("UPDATE chain_products SET unit=? WHERE id=?", units)
    if version < 8:
        # МЕТКА ЦЕНЫ БЕЗ САМОЙ ЦЕНЫ — ОСТАТОК СТАРОГО СБОЯ (21.10.2026). 19.10.2026 у
        # Самоката стёрли цены, оставив price_seen: большую часть вернули чтением
        # карточек, но 26 строк остались с меткой «цену видели» и пустой ценой — и
        # проверка базы каждое утро ругалась именно на них. Правило самого триггера
        # keep_seen_price: законная чистка гасит price и price_seen ВМЕСТЕ. Метка без
        # цены ничего не значит — гасим её. Новых таких строк не будет: триггер не даёт
        # стереть цену, пока метка жива.
        conn.execute("UPDATE chain_products SET price_seen = NULL"
                     " WHERE price IS NULL AND price_seen IS NOT NULL")
    if version < 9:
        _dixy_upper(conn)
    if version < 10:
        rebuild_fts(conn)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _dixy_upper(conn: sqlite3.Connection) -> None:
    """Артикулы Дикси — в заглавный вид, каким их знает сама сеть (xml_id витрины и корзины).

    Адрес карточки пишет артикул строчным («…-di00078791/»), и сборщик заводил по карте сайта
    вторую строку того же товара: латинское название из адреса, без цены и фасовки (замер
    15.11.2026: 122 пары, 91 разведена по разным единым товарам). Строчному артикулу сеть
    отвечает «в наличии 0». Строчная строка без заглавной пары переименовывается. Если есть обе,
    остаётся заглавная: она забирает у строчной рабочий адрес карточки, даты и признак «на
    витрине», а без своего единого товара — и его. Единый товар строчной строки, оставшийся без
    строк, переадресуется к товару заглавной, чтобы привязки людей к нему не рвались.
    """
    low_only = "chain='dixy' AND sku != upper(sku) AND sku NOT LIKE '%-%'"
    for low in conn.execute(f"SELECT * FROM chain_products WHERE {low_only}").fetchall():
        top = conn.execute("SELECT * FROM chain_products WHERE chain='dixy' AND sku=?",
                           (str(low["sku"]).upper(),)).fetchone()
        if top is None:
            conn.execute("UPDATE chain_products SET sku=upper(sku) WHERE id=?", (low["id"],))
            continue
        orphan = None
        if top["item_id"] is None:
            conn.execute("UPDATE chain_products SET item_id=?, link_method=?, link_score=? WHERE id=?",
                         (low["item_id"], low["link_method"], low["link_score"], top["id"]))
        elif low["item_id"] is not None and low["item_id"] != top["item_id"] and not conn.execute(
                "SELECT 1 FROM chain_products WHERE id != ? AND (item_id = ? OR prev_item_id = ?) LIMIT 1",
                (low["id"], low["item_id"], low["item_id"])).fetchone():
            orphan = int(low["item_id"])
        conn.execute("UPDATE chain_products SET url=COALESCE(?, url), active=MAX(active, ?),"
                     " last_seen=MAX(last_seen, ?), first_seen=MIN(first_seen, ?) WHERE id=?",
                     (low["url"], low["active"], low["last_seen"], low["first_seen"], top["id"]))
        conn.execute("DELETE FROM chain_products WHERE id=?", (low["id"],))
        if orphan is not None:
            conn.execute("DELETE FROM catalog_items WHERE id=?", (orphan,))
            conn.execute("UPDATE item_aliases SET new_id=? WHERE new_id=?", (top["item_id"], orphan))
            conn.execute("INSERT INTO item_aliases (old_id, new_id, since) VALUES (?,?,?)"
                         " ON CONFLICT(old_id) DO UPDATE SET new_id=excluded.new_id, since=excluded.since",
                         (orphan, top["item_id"], now()))
    for table in ("price_history", "chain_prices", "chain_misses"):
        conn.execute(f"UPDATE OR IGNORE {table} SET sku=upper(sku) WHERE {low_only}")
        conn.execute(f"DELETE FROM {table} WHERE {low_only}")


def rebuild_fts(conn: sqlite3.Connection | None = None) -> None:
    """Пересобрать полнотекстовые индексы FTS5 (безопасно вызывать на любой базе)."""
    own = conn is None
    conn = conn or connect()
    try:
        conn.execute("INSERT INTO catalog_items_fts(catalog_items_fts) VALUES ('rebuild')")
        conn.execute("INSERT INTO name_variants_fts(name_variants_fts) VALUES ('rebuild')")
        if own:
            conn.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        if own:
            conn.close()


# ---------- запись обхода ----------
def start_run(chain: str) -> int:
    _ready()
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


def known_pairs() -> set[tuple[str, str]]:
    """Все (сеть, артикул), что есть в базе: по ним проверяется, можно ли принять цену.

    Нужно переносу наблюдений приложения (app/catalog/observations.py): цена по артикулу,
    которого в общей базе нет, легла бы в историю товара-призрака.
    """
    _ready()
    with connect() as conn:
        return {(str(row["chain"]), str(row["sku"]))
                for row in conn.execute("SELECT chain, sku FROM chain_products")}


def record_observation(chain: str, sku: str, price: float, at: str) -> bool:
    """Записать НАБЛЮДЕНИЕ цены в историю: (сеть, артикул, цена, когда).

    Отличие от `upsert_products`: там цена «сейчас», а тут — «в такое-то время видели
    столько-то». Повтор с тем же временем ничего не добавляет: ключ наблюдения — время.

    ЗАЧЕМ. Приложение видит цены в сети человека, в том числе там, куда серверу хода нет
    (Пятёрочка, Самокат). До 20.10.2026 эти наблюдения оставались в личной базе и в общую
    историю не попадали — то есть «обычная цена» по телефонным сетям не смогла бы
    появиться никогда (замер: у владельца 101 наблюдение по Пятёрочке, 86 артикулов).
    """
    if not price or price <= 0 or price > 1_000_000 or not at:
        return False
    _ready()
    with connect() as conn:
        was = conn.execute("SELECT 1 FROM price_history WHERE chain=? AND sku=? AND seen_at=?"
                           " LIMIT 1", (chain, str(sku), str(at))).fetchone()
        if was is not None:
            return False
        conn.execute("INSERT INTO price_history (chain, sku, point, price, seen_at)"
                     " VALUES (?,?,?,?,?)", (chain, str(sku), None, float(price), str(at)))
        conn.commit()
    return True


SWEEP_KEY = "sweep"


def mark_sweep(state: str) -> None:
    """Отметить: обход сетей идёт ('running') или закончился ('done').

    ЗАЧЕМ (замер 20.10.2026). Сторож выкладки спрашивал «какая СЕТЬ обходится», и в
    промежутке между сетями — когда шло сопоставление, минут тринадцать — честно отвечал
    «обходов нет», хотя обход шёл. Выкладка в этот момент перезапустила бы контейнер и
    оборвала ночь. Отметка говорит про ОБХОД ЦЕЛИКОМ, а не про одну сеть.
    """
    _ready()
    with connect() as conn:
        conn.execute("INSERT INTO catalog_state (key, value, at) VALUES (?,?,?)"
                     " ON CONFLICT(key) DO UPDATE SET value=excluded.value, at=excluded.at",
                     (SWEEP_KEY, str(state), now()))
        conn.commit()


def sweep_state(hours: float = 12.0) -> str | None:
    """Идёт ли обход сетей: 'running', 'done', 'stale' (отметка слишком старая) или None."""
    _ready()
    with connect() as conn:
        row = conn.execute("SELECT value, at FROM catalog_state WHERE key=?",
                           (SWEEP_KEY,)).fetchone()
    if not row:
        return None
    value = str(row["value"] or "")
    if value != "running":
        return value or None
    try:
        age = (datetime.now() - datetime.fromisoformat(str(row["at"]))).total_seconds() / 3600.0
    except ValueError:
        return None
    # Отметку старше hours не считаем живой: процесс мог умереть, а «идёт» висело бы вечно.
    return "running" if age <= hours else "stale"


def sweep_at() -> str | None:
    """Когда обход сетей был отмечен последний раз (любым состоянием)."""
    _ready()
    with connect() as conn:
        row = conn.execute("SELECT at FROM catalog_state WHERE key=?",
                           (SWEEP_KEY,)).fetchone()
    return str(row["at"]) if row else None


SWEEP_STEP_KEY = "sweep.chain"


def mark_sweep_step(chain: str) -> None:
    """Отметить, какую сеть обход трогает прямо сейчас.

    ЗАЧЕМ (разбор 20.10.2026). Ночь встала после Пятёрочки — Самокат не начался, и по
    журналу было не понять, где именно оборвалось: `finally` спасает только от исключений,
    а убитый процесс (OOM, перезапуск контейнера) не оставляет ничего. Отметка «сейчас иду
    по такой-то сети» переживает смерть процесса — утром видно, на ком оборвалось.
    """
    _ready()
    payload = json.dumps({"chain": str(chain), "at": now()}, ensure_ascii=False)
    with connect() as conn:
        conn.execute("INSERT INTO catalog_state (key, value, at) VALUES (?,?,?)"
                     " ON CONFLICT(key) DO UPDATE SET value=excluded.value, at=excluded.at",
                     (SWEEP_STEP_KEY, payload, now()))
        conn.commit()


def sweep_step() -> dict:
    """Последняя сеть, которую трогал обход: {chain, at}. Пусто — отметки нет."""
    _ready()
    with connect() as conn:
        row = conn.execute("SELECT value FROM catalog_state WHERE key=?",
                           (SWEEP_STEP_KEY,)).fetchone()
    if not row:
        return {}
    try:
        value = json.loads(str(row["value"]))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


DEVICE_KEY = "device.%s"


def mark_device(chain: str, kind: str, got: int, learned: int = 0) -> None:
    """Отметить: телефон принёс этой сети данные (карточки или отчёт) — когда и сколько.

    ЗАЧЕМ. Пятёрочка и Самокат живут только телефоном, и «а телефон-то ещё работает?» —
    вопрос, на который нечем ответить: строки есть, а пришли они вчера или месяц назад,
    не видно. Проверка базы показывает эти отметки (app/catalog/checkup.py).
    """
    _ready()
    payload = json.dumps({"kind": str(kind), "got": int(got), "learned": int(learned),
                          "at": now()}, ensure_ascii=False)
    with connect() as conn:
        conn.execute("INSERT INTO catalog_state (key, value, at) VALUES (?,?,?)"
                     " ON CONFLICT(key) DO UPDATE SET value=excluded.value, at=excluded.at",
                     (DEVICE_KEY % chain, payload, now()))
        conn.commit()


def device_marks() -> dict[str, dict]:
    """Что и когда приносил телефон по сетям: {сеть: {kind, got, learned, at}}."""
    _ready()
    with connect() as conn:
        rows = list(conn.execute("SELECT key, value FROM catalog_state"
                                 " WHERE key LIKE 'device.%'"))
    out: dict[str, dict] = {}
    for row in rows:
        chain = str(row["key"]).split(".", 1)[1]
        try:
            value = json.loads(row["value"])
        except ValueError:
            continue
        if isinstance(value, dict):
            out[chain] = value
    return out


MAP_KEY = "map.%s"


def mark_map(chain: str, total: int, known: int, left: int, added: int = 0) -> None:
    """Отметить: сколько артикулов в перечне сети, сколько уже в базе и сколько осталось.

    ЗАЧЕМ (замер 20.10.2026). «Все товары разобраны» — пункт цели, а проверить его было
    нечем: в базе 37 336 строк Ленты, и совершенно не видно, много это или половина. Замер
    кодом самого обхода: в перечне Ленты 82 953 артикула, знакомо 37 334, осталось 45 617 —
    при 6 000 карточек за обход и ~5 000 новых за ночь это девять ночей. У ВкусВилла
    осталось 2 артикула: перечень разобран.

    Числа берутся у сборщика (он уже получил перечень, отдельных запросов не нужно) и
    ложатся в catalog_state, чтобы их показала проверка базы.
    """
    _ready()
    payload = json.dumps({"total": int(total), "known": int(known), "left": int(left),
                          "added": int(added), "at": now()}, ensure_ascii=False)
    with connect() as conn:
        conn.execute("INSERT INTO catalog_state (key, value, at) VALUES (?,?,?)"
                     " ON CONFLICT(key) DO UPDATE SET value=excluded.value, at=excluded.at",
                     (MAP_KEY % chain, payload, now()))
        conn.commit()


def map_marks() -> dict[str, dict]:
    """Сколько разобрано по сетям: {сеть: {total, known, left, added, at}}."""
    _ready()
    with connect() as conn:
        rows = list(conn.execute("SELECT key, value FROM catalog_state"
                                 " WHERE key LIKE 'map.%'"))
    out: dict[str, dict] = {}
    for row in rows:
        chain = str(row["key"]).split(".", 1)[1]
        try:
            value = json.loads(row["value"])
        except ValueError:
            continue
        if isinstance(value, dict):
            out[chain] = value
    return out


def phone_new_rows(hours: float = 24.0) -> dict[str, int]:
    """Сколько НОВЫХ строк принёс телефон: сети, которые сервер в это окно не обходил.

    ЗАЧЕМ (21.10.2026). Телефон — единственный источник товаров у Пятёрочки и Самоката
    (сервер к ним не пробивается), и его вклад до сих пор нигде не был посчитан:
    проверка базы показывала только «что и когда телефон приносил» (отметки, магазины).
    Замер: за сутки телефон дал 107 новых строк Самоката, 4 Пятёрочки, 3 Магнита —
    но увидеть это можно было только запросом руками.

    Считаем по newest: строка впервые увидена в окне, а УДАЧНОГО обхода этой сети в том же
    окне не было. Именно удачного: Пятёрочку и Самокат ночь трогает каждую ночь и каждый
    раз записывает «неполный» (стена по IP), строк там не появляется — и по грубому
    признаку «сети не было в обходе» их вклад пропадал бы.
    """
    _ready()
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="microseconds")
    with connect() as conn:
        rows = conn.execute(
            "SELECT p.chain AS chain, COUNT(*) AS rows_count"
            " FROM chain_products p"
            " WHERE p.first_seen > ?"
            "   AND p.chain NOT IN (SELECT chain FROM crawl_runs"
            "                        WHERE started_at > ? AND status = 'ok')"
            " GROUP BY p.chain ORDER BY rows_count DESC", (since, since)).fetchall()
    return {str(row["chain"]): int(row["rows_count"]) for row in rows}


def chain_minutes() -> dict[str, float]:
    """Сколько минут длился последний законченный обход каждой сети.

    Нужно раскладке по полосам (app/catalog/worker._split_lanes): полосы должны быть
    загружены ровно, иначе длинная сеть (Лента ~166 мин) стоит в полосе одна, а короткие
    ждут в другой. Берём последний прогон — он честнее давнего: каталог растёт, и обход
    вместе с ним удлиняется.
    """
    _ready()
    with connect() as conn:
        rows = conn.execute(
            "SELECT r.chain, r.started_at, r.finished_at FROM crawl_runs r"
            " WHERE r.id IN (SELECT MAX(id) FROM crawl_runs GROUP BY chain)"
            " AND r.finished_at IS NOT NULL").fetchall()
    out: dict[str, float] = {}
    for row in rows:
        try:
            began = datetime.fromisoformat(str(row["started_at"]))
            ended = datetime.fromisoformat(str(row["finished_at"]))
        except (TypeError, ValueError):
            continue
        out[str(row["chain"])] = max(0.0, (ended - began).total_seconds() / 60.0)
    return out


def close_stale_runs(hours: float = 6.0) -> int:
    """Закрыть брошенные обходы: строка «running» старше hours — не обход, а память о сбое.

    ЗАЧЕМ. Планировщик живёт в контейнере, который перезапускается при каждой выкладке,
    и каждый перезапуск посреди обхода оставлял строку «running» навсегда. Замер
    19.10.2026: таких строк накопилось 15, самые старые — от 16.09.2026 (METRO, ВкусВилл,
    Лента, Магнит). По ним нельзя понять, обошлась сеть или нет, а «идёт обход» на экране
    сетей висит неделями.

    ПОЧЕМУ ПОРОГ ПО ВРЕМЕНИ, А НЕ «ВСЁ running». Обход бывает живым и в другом
    контейнере (так его возобновляли 19.10.2026 после выкладки), и служба не должна
    объявлять чужой живой обход провалившимся только потому, что сама перезапустилась.
    Потолок времени на сеть — 180 минут, шесть часов выше любого живого обхода.
    """
    limit = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="microseconds")
    _ready()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE crawl_runs SET status='failed', finished_at=?,"
            " note='обход не завершился: служба перезапускалась, время вышло'"
            " WHERE status='running' AND started_at < ?", (now(), limit))
        conn.commit()
        return cur.rowcount


def running_runs() -> list[dict]:
    """Обходы, которые прямо сейчас идут. Ими выкладка проверяет, не оборвёт ли она обход.

    ЗАЧЕМ. Перезапуск контейнера `korzina-jobs` (а выкладка его перезапускает) обрывает
    идущий обход, и следующая попытка будет только по расписанию — сутки без свежих цен у
    тех сетей, до которых очередь не дошла. Замер 19.10.2026: так оборвались два обхода
    подряд, и Лента за сутки не обошлась ни разу.
    """
    _ready()
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT id, chain, started_at FROM crawl_runs WHERE status='running'"
            " ORDER BY id")]


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
                was = conn.execute("SELECT price FROM chain_prices WHERE chain=? AND sku=? AND point=?",
                                   (chain, str(p.sku), str(p.point))).fetchone()
                conn.execute(
                    "INSERT INTO chain_prices (chain, sku, point, price, in_stock, seen_at)"
                    " VALUES (?,?,?,?,?,?) ON CONFLICT(chain, sku, point) DO UPDATE SET"
                    " price=excluded.price, in_stock=excluded.in_stock, seen_at=excluded.seen_at",
                    (chain, str(p.sku), str(p.point), p.price, stock, stamp))
                # ИСТОРИЯ ЦЕНЫ, А НЕ ТОЛЬКО ПОСЛЕДНЯЯ. До 19.10.2026 chain_prices хранила
                # одну цену на (сеть, артикул, точка): за 18–24 ночных снимка у каждого
                # ключа оставалась ровно одна строка, и ответить «сейчас дороже обычного»
                # было нечем. Пишем строку ТОЛЬКО ПРИ СМЕНЕ ЦЕНЫ — рост таблицы от этого
                # ограничен числом изменений, а не числом обходов.
                if p.price is not None and (was is None or was["price"] is None
                                            or abs(float(was["price"]) - float(p.price)) > 0.004):
                    conn.execute("INSERT INTO price_history (chain, sku, point, price, seen_at)"
                                 " VALUES (?,?,?,?,?)",
                                 (chain, str(p.sku), str(p.point), float(p.price), stamp))
            elif p.price is not None:
                # СЕТЬ БЕЗ ТОЧЕК. У Ленты, ВкусВилла, Самоката, Пятёрочки и трёх
                # sitemap-сетей точки нет — каталог один на страну. Замер 20.10.2026:
                # история писалась только в ветке с точкой, и за сутки в ней оказались
                # лишь magnit (970) и metro (135) — «обычная цена» была бы мертва для
                # восьми сетей из одиннадцати. Пишем историю и здесь, на уровне сети.
                was = conn.execute("SELECT price FROM chain_products WHERE chain=? AND sku=?",
                                   (chain, str(p.sku))).fetchone()
                if was is None or was["price"] is None \
                        or abs(float(was["price"]) - float(p.price)) > 0.004:
                    conn.execute("INSERT INTO price_history (chain, sku, point, price, seen_at)"
                                 " VALUES (?,?,?,?,?)",
                                 (chain, str(p.sku), None, float(p.price), stamp))
            row = conn.execute("SELECT id, name, weight_g, category, barcode FROM chain_products"
                               " WHERE chain=? AND sku=?", (chain, str(p.sku))).fetchone()
            if p.seen_only:
                if row is not None:
                    conn.execute("UPDATE chain_products SET last_seen=?, active=1 WHERE id=?",
                                 (stamp, row["id"]))
                continue
            # МЕТКА СВЕЖЕСТИ — ТОЛЬКО ЗА ЦЕНУ, КОТОРУЮ НАЗВАЛА СЕТЬ. Частичный обход
            # добирает известные товары из нашей же базы (price_from_base): у них цена
            # есть, но никто её сегодня не подтверждал, и ставить им `price_seen=now`
            # значило бы подделать свежесть — приложение поверило бы цене, которой сеть
            # не называла (замер 21.10.2026: 93 строки Пятёрочки попали в 6-часовое окно).
            price_seen = (stamp if p.price is not None and not p.price_from_base
                          else None)
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
            same_name = row["name"] == p.name
            weight = row["weight_g"] if same_name and p.weight_g is None else p.weight_g
            category = row["category"] if same_name and not p.category else p.category
            changed = (not same_name or row["weight_g"] != weight or row["category"] != category
                       or bool(p.barcode and row["barcode"] != p.barcode))
            # Цена, которой обход не принёс, остаётся прежней — но со СВОЕЙ датой
            # (price_seen), а не с датой этого обхода: иначе недельная цена уезжала в
            # базу человека как свежая.
            conn.execute(
                "UPDATE chain_products SET name=?, brand=COALESCE(NULLIF(?,''), brand),"
                " weight_g=?, unit=COALESCE(?, unit),"
                " category=?, url=COALESCE(NULLIF(?,''), url),"
                    " photo_after=CASE WHEN NULLIF(?, '') IS NOT NULL AND ? IS NOT image"
                    " THEN 0 ELSE photo_after END,"
                    " image=COALESCE(NULLIF(?,''), image), barcode=COALESCE(?, barcode),"
                # gtin держится в согласии с barcode: сеть сменила код на внутренний или
                # пустой — прежний GTIN не должен находить строку «по штрихкоду».
                " gtin=CASE WHEN ? IS NULL THEN gtin ELSE ? END,"
                " price=COALESCE(?, price), in_stock=COALESCE(?, in_stock),"
                " price_seen=COALESCE(?, price_seen), last_seen=?, active=1"
                + (", prev_item_id=COALESCE(item_id, prev_item_id), item_id=NULL,"
                   " link_method=NULL, link_score=NULL" if changed else "")
                + " WHERE id=?",
                (p.name, p.brand, weight, p.unit, category, p.url, p.image, p.image, p.image, p.barcode or None,
                 p.barcode or None, code, p.price, stock, price_seen, stamp, row["id"]))
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
    _ready()
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


# ---------- «спросили — товара нет» ----------
def note_miss(chain: str, sku: str, note: str | None = None) -> None:
    """Запомнить, что сеть не знает такого артикула. Повторный вызов копит tries."""
    stamp = now()
    with connect() as conn:
        conn.execute("INSERT INTO chain_misses (chain, sku, first_try, last_try, tries, note)"
                     " VALUES (?,?,?,?,1,?) ON CONFLICT(chain, sku) DO UPDATE SET"
                     " last_try=excluded.last_try, tries=chain_misses.tries+1,"
                     " note=excluded.note",
                     (chain, str(sku), stamp, stamp, note))
        conn.commit()


def clear_miss(chain: str, sku: str) -> None:
    """Артикул нашёлся — память о нём больше не нужна (товар вернулся в витрину)."""
    with connect() as conn:
        conn.execute("DELETE FROM chain_misses WHERE chain=? AND sku=?", (chain, str(sku)))
        conn.commit()


def missed_skus(chain: str, retry_days: float = 30.0) -> set[str]:
    """Артикулы, про которые сеть недавно сказала «нет». Их обход не спрашивает.

    Старше retry_days — не возвращаются: товар мог вернуться в витрину, и одна
    попытка в месяц дешевле, чем потерянный товар. retry_days=None — «не спрашивать
    никогда» (так проверяют, сколько их всего).
    """
    query = "SELECT sku FROM chain_misses WHERE chain=?"
    args: list = [chain]
    if retry_days is not None:
        since = (datetime.now() - timedelta(days=float(retry_days))).isoformat(timespec="microseconds")
        query += " AND last_try >= ?"
        args.append(since)
    with connect() as conn:
        return {str(row["sku"]) for row in conn.execute(query, args)}


def miss_counts() -> dict[str, int]:
    """Сколько артикулов каждая сеть объявила несуществующими. Для отчётов и экранов."""
    try:
        with connect() as conn:
            return {str(row["chain"]): int(row["n"]) for row in conn.execute(
                "SELECT chain, COUNT(*) AS n FROM chain_misses GROUP BY chain")}
    except sqlite3.OperationalError:
        return {}


# ---------- чтение ----------
def chain_counts() -> dict[str, dict]:
    """По каждой сети: сколько товаров в каталоге, с чем они, и чем кончился обход.

    Кроме числа строк отдаём, с чем строка пришла: с ценой, с наличием, с маркой и со
    штрихкодом. Это и есть ответ на «наполнение работает?» — его видно на экране
    «Магазины», а не только в журнале обхода.
    """
    _ready()
    out: dict[str, dict] = {}
    with connect() as conn:
        for row in conn.execute(
                "SELECT chain, COUNT(*) AS n, SUM(item_id IS NOT NULL) AS linked,"
                " SUM(price IS NOT NULL) AS priced, SUM(in_stock IS NOT NULL) AS stocked,"
                " SUM(brand IS NOT NULL AND brand <> '') AS branded,"
                " SUM(barcode IS NOT NULL AND barcode <> '') AS barcoded"
                " FROM chain_products WHERE active=1 GROUP BY chain"):
            out[row["chain"]] = {"products": row["n"], "linked": row["linked"] or 0,
                                 "priced": row["priced"] or 0, "stocked": row["stocked"] or 0,
                                 "branded": row["branded"] or 0,
                                 "barcoded": row["barcoded"] or 0}
        for row in conn.execute(
                "SELECT r.chain, r.finished_at, r.status, r.note, r.seen FROM crawl_runs r"
                " WHERE r.id IN (SELECT MAX(id) FROM crawl_runs GROUP BY chain)"):
            out.setdefault(row["chain"], {"products": 0, "linked": 0}).update(
                {"last_run": row["finished_at"], "status": row["status"], "note": row["note"],
                 "seen": row["seen"]})
    return out


def known_skus(chain: str) -> set[str]:
    """Артикулы сети, о которых каталог уже знает название. Нужно сборщикам по sitemap."""
    _ready()
    with connect() as conn:
        return {str(r["sku"]) for r in conn.execute(
            "SELECT sku FROM chain_products WHERE chain=?", (chain,))}


def set_stock(chain: str, rows: list[tuple[bool, str]]) -> int:
    """Наличие по корзине сети: что человек положил, а чего сеть не дала.

    ЗАЧЕМ. Наличие и цены приложение берёт, формируя корзину в фоне в каждой
    подключённой сети (решение владельца 18.10.2026). Для сетей, куда сервер не
    пробивается вовсе (Пятёрочка и Самокат — замер 18.10.2026), корзина человека
    остаётся единственным источником наличия: у них нет ни каталога, ни коннектора.
    Обновляем только живые строки с настоящим артикулом: заглушку сеть товаром не
    считает (model.placeholder_sku), и её наличие ничего не значит. Возвращает,
    сколько строк обновилось.
    """
    if not rows:
        return 0
    _ready()
    with connect() as conn:
        cur = conn.executemany(
            "UPDATE chain_products SET in_stock=? WHERE chain=? AND sku=? AND active=1",
            [(int(bool(stock)), chain, str(sku)) for stock, sku in rows])
        conn.commit()
        return cur.rowcount


def touch_seen(chain: str, skus: list[str]) -> int:
    """Отметить, что ТЕЛЕФОН сегодня видел эти строки: `last_seen=now`, `active=1`.

    ЗАЧЕМ (вопрос мобильной части 21.10.2026). Живость строки ведёт обход, но если человек
    открыл карточку товара, которого обход в перечне не увидел, строка считается мёртвой — и
    сопоставление её не видит, хотя товар в сети есть. Наблюдение человека — доказательство
    жизни, и его надо записывать.

    ВАЖНОЕ УСЛОВИЕ ДЛЯ ЗОВУЩЕГО: вызывать только при ДОКАЗАННОМ наблюдении — когда с
    карточки прочитана цена (`price > 0`). При отказе «страница не отдалась / бот» человек
    карточку НЕ видел, и `last_seen` обновлять нельзя: строка выглядела бы свежей без
    основания. Имя, цену и штрихкод здесь не трогаем: у обхода данные богаче, телефон даёт
    ровно один факт — она жива.
    """
    wanted = [str(sku).strip() for sku in (skus or []) if str(sku).strip()]
    if not wanted:
        return 0
    _ready()
    stamp = now()
    with connect() as conn:
        cur = conn.executemany(
            "UPDATE chain_products SET last_seen=?, active=1 WHERE chain=? AND sku=?",
            [(stamp, chain, sku) for sku in wanted])
        conn.commit()
        return cur.rowcount


def set_prices_seen(chain: str, rows: list[tuple[float, str]]) -> int:
    """Цена с карточки, которую человек САМ видел: пишет цену и оживляет строку.

    ЗАЧЕМ (замер 21.10.2026). Телефон присылает цену и адрес карточки даже у не легших
    позиций — это единственный источник цены для Пятёрочки и Самоката. Но строка сети могла
    быть ПОГАШЕНА обходом (`active=0`: сеть не показала её в перечне), и обычный set_prices
    с его `AND active=1` молча пропускал запись: у Магнита строки 8000104060 и 1000443710
    лежали погашенными с 16.09, и цена с телефона до базы не доезжала.

    Человек открыл карточку сегодня — значит товар у сети ЕСТЬ, и строка должна жить.
    Поэтому здесь `active=1` и `last_seen`, а не только цена.
    """
    if not rows:
        return 0
    _ready()
    stamp = now()
    with connect() as conn:
        cur = conn.executemany(
            "UPDATE chain_products SET price=?, price_seen=?, active=1, last_seen=?"
            " WHERE chain=? AND sku=?",
            [(float(price), stamp, stamp, chain, str(sku)) for price, sku in rows])
        conn.commit()
        return cur.rowcount


def set_stock_seen(chain: str, rows: list[tuple[bool, str]]) -> int:
    """Наличие с карточки, которую человек видел: пишет наличие и оживляет строку.

    Та же причина, что у set_prices_seen: погашенная строка не должна терять данные,
    которые человек принёс своими глазами.
    """
    if not rows:
        return 0
    _ready()
    stamp = now()
    with connect() as conn:
        cur = conn.executemany(
            "UPDATE chain_products SET in_stock=?, active=1, last_seen=?"
            " WHERE chain=? AND sku=?",
            [(int(bool(stock)), stamp, chain, str(sku)) for stock, sku in rows])
        conn.commit()
        return cur.rowcount


def set_prices(chain: str, rows: list[tuple[float, str]]) -> int:
    """Цены по корзине сети: сколько сеть просит за то, что человек в неё положил.

    ЗАЧЕМ. Цены приложение получает, формируя корзину в фоне в каждой подключённой сети
    (решение владельца 18.10.2026). Для сетей, куда сервер не пробивается (Пятёрочка и
    Самокат — замеры 18–19.10.2026) и для сетей, чей обход отдаёт только названия
    (Перекрёсток, Vprok, Монетка — у них в базе 0 цен на 194 тысячи строк), корзина
    человека остаётся единственным источником цены. Обновляем только живые строки с
    настоящим артикулом: у заглушки цены сети нет (model.placeholder_sku). Возвращает,
    сколько строк обновилось.
    """
    if not rows:
        return 0
    _ready()
    stamp = now()
    with connect() as conn:
        cur = conn.executemany(
            "UPDATE chain_products SET price=?, price_seen=? WHERE chain=? AND sku=? AND active=1",
            [(float(price), stamp, chain, str(sku)) for price, sku in rows])
        conn.commit()
        return cur.rowcount


def price_history(chain: str, sku: str, point: str | None = None,
                  days: float = 90.0) -> list[dict]:
    """Что этот товар стоил раньше: строки на каждое изменение цены, новые первыми.

    Появились 19.10.2026: в chain_prices у каждого ключа оставалась одна строка при
    18–24 ночных снимках, и прежняя цена затиралась. Здесь — только изменения, поэтому
    память о цене не растёт с числом обходов.
    """
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="microseconds")
    _ready()
    with connect() as conn:
        sql = ("SELECT price, seen_at FROM price_history WHERE chain=? AND sku=? AND seen_at>=?")
        args: list = [chain, str(sku), since]
        if point is not None:
            sql += " AND point=?"
            args.append(str(point))
        sql += " ORDER BY seen_at DESC"
        return [{"price": row["price"], "seen_at": row["seen_at"]}
                for row in conn.execute(sql, args)]


def usual_price(chain: str, sku: str, point: str | None = None,
                days: float = 30.0) -> float | None:
    """Обычная цена товара за окно дней — медиана изменений, а не последняя.

    Медиана, а не среднее: одна акция «−50 %» на день не должна сдвигать ориентир.
    Нет истории — None, и приложение честно молчит, а не сравнивает с пустотой.
    """
    prices = sorted(row["price"] for row in price_history(chain, sku, point, days)
                    if row["price"] is not None)
    if not prices:
        return None
    return round(prices[len(prices) // 2], 2)


# Сколько дней должна прожить цена без изменений, чтобы её можно было считать обычной.
USUAL_MIN_DAYS = 7.0
# Сколько изменений цены достаточно, чтобы судить, даже если они свежие.
USUAL_MIN_POINTS = 3


def usual_trusted(chain: str, sku: str, point: str | None = None,
                  days: float = 30.0) -> bool:
    """Хватает ли истории, чтобы судить об «обычной цене».

    ЗАМЕР 20.10.2026 — первая ночь с историей (magnit 970 строк, metro 135). История
    пишется ТОЛЬКО ПРИ СМЕНЕ цены, поэтому у товара со стабильной ценой в ней ровно одна
    строка — и правило «не меньше трёх изменений» не сработало бы никогда: «обычная цена»
    осталась бы мёртвой для большинства товаров.

    А одна строка НЕДЕЛЬНОЙ давности значит ровно то, что нужно: цена держится неделю, и
    это честная обычная цена. Поэтому доверяем либо трём изменениям, либо неделе
    наблюдений — смотря что наступит раньше.
    """
    rows = price_history(chain, sku, point, days)
    if len(rows) >= USUAL_MIN_POINTS:
        return True
    if not rows:
        return False
    oldest = str(rows[-1].get("seen_at") or "")
    try:
        age = (datetime.now() - datetime.fromisoformat(oldest)).total_seconds() / 86400.0
    except ValueError:
        return False
    return age >= USUAL_MIN_DAYS


def thin_skus(chain: str, limit: int) -> list[str]:
    """Живые артикулы сети без марки, штрихкода или ЦЕНЫ — кому нужно добрать карточку.

    Нужно добору подробностей (app/catalog/crawlers/lenta.py, _backfill): карточка сети
    отдаёт марку, штрихкод и цену, а прежние сборщики их не брали — 28 574 строки Ленты
    легли без них, и склеивались они только словами.

    ПОЧЕМУ НЕ ПРОСТО «ОТ СТАРЫХ К НОВЫМ» (замер 19.10.2026). Бюджет добора — 6 000
    карточек за ночь, и тратить его на что попало незачем. У Ленты 1 031 строка без цены
    имеет родню в других сетях, а 277 — родню С ЦЕНОЙ: цена такой строки сразу даёт
    сравнение сетей, ради которого единая база и строится. Поэтому очередь такая:
    сначала строки, у которых родня уже с ценой, потом строки с роднёй вовсе, потом
    остальные; внутри группы — от старых к новым.

    В ОЧЕРЕДЬ ВХОДИТ И СТРОКА БЕЗ ЦЕНЫ. Карточка отдаёт и цену, а 813 строк Ленты лежали
    без цены, но с маркой и штрихкодом — и в добор не попадали вовсе.
    """
    _ready()
    cutoff = (datetime.now() - timedelta(hours=20)).isoformat(timespec="microseconds")
    with connect() as conn:
        return [str(r["sku"]) for r in conn.execute(
            "SELECT p.sku,"
            " (SELECT COUNT(*) FROM chain_products q WHERE q.item_id=p.item_id AND q.active=1"
            "   AND q.chain<>p.chain AND q.price IS NOT NULL) AS priced_kin,"
            " (SELECT COUNT(*) FROM chain_products q WHERE q.item_id=p.item_id AND q.active=1"
            "   AND q.chain<>p.chain) AS kin"
            " FROM chain_products p WHERE p.chain=? AND p.active=1"
            " AND (p.brand IS NULL OR p.barcode IS NULL OR COALESCE(p.price,0)<=0)"
            " AND COALESCE(p.detail_checked_at,'')<=?"
            " ORDER BY (p.detail_checked_at IS NOT NULL), (priced_kin>0) DESC, (kin>0) DESC,"
            " COALESCE(p.detail_checked_at,p.first_seen),p.id LIMIT ?",
            (chain, cutoff, int(limit)))]


def stale_price_skus(chain: str, limit: int, older_than_hours: float = 20.0) -> list[str]:
    """Живые артикулы сети, чья цена в базе старше всех — кому пора перечитать карточку.

    ЗАЧЕМ (замер 20.10.2026, боевая база). Сборщики читают карточку только при первом
    знакомстве с артикулом, а знакомым лишь продлевают «видели»: цену никто не
    перечитывает. У ВкусВилла 13 379 строк, из них 10 074 с ценой, записанной до того,
    как у базы появилась её дата, и ещё 3 132 старше недели. Проверка восьми случайных
    карточек вживую: 3 из 8 разошлись с витриной больше чем на процент, худшая —
    129 ₽ в базе против 167 ₽ на витрине (+29,5 %). Сравнение сетей по такой цене
    показывает экономию, которой нет, — а ради него база и строится.

    ПОЧЕМУ ОЧЕРЕДЬ ПО ВОЗРАСТУ, А НЕ ПО ЦЕННОСТИ. Виток обязан замыкаться: каждая
    цена когда-нибудь обновится. Возраст это и даёт — самая старая читается первой,
    и при 1 500 карточек за обход каталог ВкусВилла проходит за девять ночей.

    ПОЧЕМУ ЕСТЬ ПОРОГ ВОЗРАСТА. Без него очередь всегда непуста: только что
    прочитанная цена остаётся «самой старой» среди прочих, и сборщик перечитывал бы
    её и в следующем обходе, и в ручном запуске того же дня — тратя потолок впустую
    (так и поймал старый тест про «не найденные» артикулы Ленты). Порог 20 часов
    держит виток честным: за ночь каждая цена читается один раз.

    Цена без даты (price_seen) считается по дате первого знакомства: у строк,
    записанных до появления колонки, другой даты нет, и они самые старые из всех.
    """
    _ready()
    if limit <= 0:
        return []
    cutoff = (datetime.now() - timedelta(hours=older_than_hours)).isoformat(timespec="microseconds")
    with connect() as conn:
        return [str(r["sku"]) for r in conn.execute(
            "SELECT sku FROM chain_products WHERE chain=? AND active=1"
            " AND price IS NOT NULL"
            " AND COALESCE(price_seen, first_seen) <= ?"
            " ORDER BY COALESCE(price_seen, first_seen) ASC, id ASC LIMIT ?",
            (chain, cutoff, int(limit)))]


def detail_refresh_skus(chain: str, limit: int) -> list[str]:
    """Reserve part of the existing nightly budget for incomplete cards and rotate misses."""
    if limit <= 0:
        return []
    _ready()
    cutoff = (datetime.now() - timedelta(hours=20)).isoformat(timespec="microseconds")
    stock_gap = " OR in_stock IS NULL" if chain == "lenta" else ""
    with connect() as conn:
        gaps = [r[0] for r in conn.execute(
            "SELECT sku FROM chain_products WHERE chain=? AND active=1"
            " AND COALESCE(detail_checked_at, first_seen)<=?"
            f" AND (COALESCE(price,0)<=0 OR COALESCE(image,'')='' {stock_gap})"
            " ORDER BY (detail_requested_at IS NOT NULL) DESC, detail_requested_at,"
            " COALESCE(detail_checked_at, first_seen), id LIMIT ?",
            (chain, cutoff, limit))]
        stale = [r[0] for r in conn.execute(
            "SELECT sku FROM chain_products WHERE chain=? AND active=1 AND price>0"
            " AND COALESCE(price_seen,first_seen)<=? AND COALESCE(detail_checked_at,'')<=?"
            " ORDER BY COALESCE(price_seen,first_seen),id LIMIT ?", (chain, cutoff, cutoff, limit))]
    reserve = max(1, limit // 4)
    return list(dict.fromkeys(gaps[:reserve] + stale + gaps[reserve:]))[:limit]


def mark_detail_checked(chain: str, sku: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE chain_products SET detail_checked_at=?,detail_requested_at=NULL WHERE chain=? AND sku=?",
                     (now(), chain, sku))


def request_details(cards: list[tuple[str, str]]) -> None:
    """Remember incomplete visible cards for the existing, rate-limited detail crawl."""
    if not cards:
        return
    cutoff = (datetime.now() - timedelta(hours=20)).isoformat(timespec="microseconds")
    with connect() as conn:
        conn.execute("PRAGMA busy_timeout=75")
        conn.executemany("UPDATE chain_products SET detail_requested_at=? WHERE chain=? AND sku=?"
                         " AND active=1 AND detail_requested_at IS NULL AND COALESCE(detail_checked_at,'')<=?",
                         [(now(), c, s, cutoff) for c, s in set(cards)])


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


def batch_item_rows(item_ids: Iterable[int],
                    conn: sqlite3.Connection | None = None) -> dict[int, list[dict]]:
    """Пакетно получить живые строки сетей для набора item_id за один запрос (без N+1)."""
    ids = sorted({int(x) for x in item_ids if x})
    if not ids:
        return {}
    own = conn is None
    conn = conn or connect()
    try:
        out: dict[int, list[dict]] = {i: [] for i in ids}
        for pos in range(0, len(ids), 400):
            chunk = ids[pos:pos + 400]
            marks = ",".join("?" * len(chunk))
            for r in conn.execute(
                f"SELECT p.* FROM chain_products p WHERE p.item_id IN ({marks})"
                f" AND p.active=1{_NOT_PLACEHOLDER} ORDER BY p.item_id, p.chain, p.sku",
                chunk,
            ):
                iid = int(r["item_id"])
                out.setdefault(iid, []).append(dict(r))
        return out
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


def _fts_token(raw: str) -> str:
    """Очистить токен для безопасного prefix-поиска в FTS5 (без инъекции операторов)."""
    import re

    clean = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "", (raw or "").strip())
    return clean.lower()


def _fts_expr(terms: Iterable[str], *, col: str | None = None, op: str = "OR") -> str:
    """Собрать безопасное FTS5-выражение `col : term1* OR col : term2*`."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for t in terms:
        tok = _fts_token(t)
        if not tok or tok in seen:
            continue
        seen.add(tok)
        cleaned.append(f"{col} : {tok}*" if col else f"{tok}*")
    return f" {op} ".join(cleaned)


def search_folded(terms, limit: int = 80, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Единые товары, в чьём названии есть слова, НАЧИНАЮЩИЕСЯ с этих основ.

    Использует FTS5 (catalog_items_fts) для поиска за 1–3 мс вместо полного скана 300+ тыс. строк;
    при отсутствии FTS5-таблицы автоматически откатывается на LIKE.
    """
    picked = folded_terms(terms)
    if not picked:
        return []
    own = conn is None
    conn = conn or connect()
    try:
        likes = [f"% {t}%" for t in picked]
        hits = " + ".join("(i.fnorm LIKE ?)" for _ in picked)
        fts_q = _fts_expr(picked, col="fnorm", op="OR")
        if fts_q:
            try:
                rows = conn.execute(
                    "SELECT i.* FROM catalog_items_fts f"
                    " JOIN catalog_items i ON i.id = f.rowid"
                    " WHERE catalog_items_fts MATCH ?"
                    f" ORDER BY ({hits}) DESC, LENGTH(i.name), i.id LIMIT ?",
                    [fts_q, *likes, int(limit)],
                ).fetchall()
                if rows:
                    return [dict(r) for r in rows]
                # Проверим, не пуст ли FTS-индекс при непустой catalog_items (старая база без миграции)
                has_fts = conn.execute("SELECT 1 FROM catalog_items_fts LIMIT 1").fetchone() is not None
                if has_fts:
                    return []
            except sqlite3.OperationalError:
                pass
        where = " OR ".join("i.fnorm LIKE ?" for _ in picked)
        return [dict(r) for r in conn.execute(
            f"SELECT i.* FROM catalog_items i WHERE {where}"
            f" ORDER BY ({hits}) DESC, LENGTH(i.name), i.id LIMIT ?",
            likes + likes + [int(limit)])]
    finally:
        if own:
            conn.close()


def search_items(query: str, limit: int = 20, conn: sqlite3.Connection | None = None, *,
                 by_name: bool = False, chain: str | None = None,
                 purchased: set[int] | None = None) -> list[dict]:
    """Поиск по единым товарам: все слова запроса должны войти в название.

    Сначала ищет через FTS5 (prefix-индекс по norm/name — миллисекунды на 300+ тыс. товаров),
    сохраняя точный порядок ранжирования (совпадение первого слова → число сетей → длина имени).
    Если FTS5 не дал результатов (например, поиск по середине слова), падает обратно на LIKE.
    """
    import re

    from app.catalog.match import ordered_words
    from app.matcher.normalize import NOISE_WORDS

    norm_words = ordered_words(query)
    forms = [("i.norm", (w,)) for w in norm_words]
    noise_words: list[str] = []
    if by_name:
        if any(words[0] in ("яйцо", "яйца") for _, words in forms):
            def egg_forms(word: str) -> tuple[str, ...]:
                if word in ("яйцо", "яйца"):
                    return ("яйцо", "яйца")
                if re.fullmatch("[сc][012]", word):
                    return ("с" + word[1], "c" + word[1])
                return (word,)
            forms = [(col, egg_forms(words[0])) for col, words in forms]
        # Родовые слова обязательны, как остальные: «сыр российский» без «сыр» отдавал шоколад
        # «Российский». LIKE различает регистр у кириллицы, а LOWER() её не берёт: три написания.
        noise_words = list(dict.fromkeys(
            w for w in re.findall(r"[0-9a-zа-яё]+", query.lower()) if w in NOISE_WORDS
        ))
        forms += [("i.name", (w, w.capitalize(), w.upper())) for w in noise_words]
    if not forms:
        return []
    where = " AND ".join("(" + " OR ".join(f"{col} LIKE ?" for _ in f) + ")" for col, f in forms)
    if chain:
        where += (" AND EXISTS (SELECT 1 FROM chain_products cp WHERE cp.item_id=i.id"
                  " AND cp.chain=? AND cp.active=1 AND cp.in_stock=1 AND cp.price>0"
                  + _NOT_PLACEHOLDER.replace("p.", "cp.") + ")")
    column, head = forms[0]       # слова — из [0-9a-zа-я], подстановочных % и _ в них нет
    whole = " OR ".join(f"{column} = ? OR {column} LIKE ?" for _ in head)
    prefix = " OR ".join(f"{column} LIKE ?" for _ in head)
    params = ([f"%{w}%" for _, f in forms for w in f]
              + ([chain] if chain else [])
              + [x for w in head for x in (w, f"{w} %")] + [f"{w}%" for w in head] + [limit])
    sellable = ("MAX(CASE WHEN p.price>0 AND p.in_stock=1 THEN 1 ELSE 0 END) DESC, "
                if by_name else "")
    join = "LEFT JOIN"
    if purchased is not None:
        join = "JOIN"
        if chain:
            where += " AND p.chain=?"
        # SQL ordering must precede LIMIT, including for a favourite that is last by name.
        sellable = ("i.id IN (SELECT value FROM json_each(?)) DESC,"
                    " MAX(CASE WHEN p.in_stock=1 THEN 1 ELSE 0 END) DESC,"
                    " CASE WHEN MAX(CASE WHEN p.in_stock=1 THEN 1 ELSE 0 END)=1"
                    " THEN COALESCE(MIN(CASE WHEN p.in_stock=1 AND p.price>0 THEN p.price END),1e20)"
                    " ELSE COALESCE(MIN(CASE WHEN p.price>0 THEN p.price END),1e20) END, ")
        index = sum(len(f) for _, f in forms) + int(bool(chain))
        params[index:index] = ([chain] if chain else []) + [json.dumps(sorted(purchased))]
    sql = ("SELECT i.*, COUNT(DISTINCT p.chain) AS chains_n,"
           " GROUP_CONCAT(DISTINCT p.chain) AS chain_codes"
           f" FROM catalog_items i {join} chain_products p ON p.item_id=i.id AND p.active=1"
           + (_NOT_PLACEHOLDER if by_name else "") +
           f" WHERE {where} GROUP BY i.id"
           f" ORDER BY {sellable}CASE WHEN {whole} THEN 0 WHEN {prefix} THEN 1 ELSE 2 END,"
           " chains_n DESC, LENGTH(i.name), i.id LIMIT ?")

    fts_groups: list[str] = []
    for col, variants in forms:
        fts_col = col.split(".", 1)[1]
        expr = _fts_expr(variants, col=fts_col, op="OR")
        if expr:
            fts_groups.append(f"({expr})" if " OR " in expr else expr)
    fts_query = " AND ".join(fts_groups)

    fts_sql = (
        "SELECT i.*, COUNT(DISTINCT p.chain) AS chains_n,"
        " GROUP_CONCAT(DISTINCT p.chain) AS chain_codes"
        " FROM catalog_items_fts f"
        " CROSS JOIN catalog_items i ON i.id = f.rowid"
        f" {join} chain_products p ON p.item_id=i.id AND p.active=1"
        + (_NOT_PLACEHOLDER if by_name else "") +
        f" WHERE catalog_items_fts MATCH ? AND {where} GROUP BY i.id"
        f" ORDER BY {sellable}CASE WHEN {whole} THEN 0 WHEN {prefix} THEN 1 ELSE 2 END,"
        " chains_n DESC, LENGTH(i.name), i.id LIMIT ?"
    )

    def _run(db_conn: sqlite3.Connection) -> list[dict]:
        if fts_query:
            try:
                rows = db_conn.execute(fts_sql, [fts_query, *params]).fetchall()
                if rows:
                    return [dict(r) for r in rows]
                # Если FTS5 нашёл кандидатов по словам, но они отсеялись фильтрами сети/наличия,
                # полный LIKE-скан не нужен; откатываемся на LIKE только для поиска в середине слова
                # или пустого (неперестроенного) FTS5-индекса.
                if db_conn.execute(
                    "SELECT 1 FROM catalog_items_fts WHERE catalog_items_fts MATCH ? LIMIT 1",
                    (fts_query,),
                ).fetchone() is not None:
                    return []
            except sqlite3.OperationalError:
                pass
        return [dict(r) for r in db_conn.execute(sql, params).fetchall()]

    if conn is not None:                 # сопоставление зовёт тысячи раз: init уже был
        return _run(conn)
    _ready()
    with connect() as own:
        return _run(own)


def item_count() -> dict:
    _ready()
    with connect() as conn:
        items = conn.execute("SELECT COUNT(*) AS n FROM catalog_items").fetchone()["n"]
        multi = conn.execute("SELECT COUNT(*) AS n FROM (SELECT item_id FROM chain_products"
                             " WHERE item_id IS NOT NULL AND active=1 GROUP BY item_id"
                             " HAVING COUNT(DISTINCT chain) > 1)").fetchone()["n"]
        last = conn.execute("SELECT finished_at FROM match_runs ORDER BY id DESC LIMIT 1").fetchone()
    return {"items": items, "multi": multi, "matched_at": last["finished_at"] if last else None}


# ---------- словарь альтернативных названий ----------
#
# ЗАЧЕМ (18.10.2026). Сокращение чека («МОЛ.ПАСТ.ПРОСТОКВАШИНО 2,5%») считалось только
# алгоритмом на лету, и он не берёт строку чека, когда чек молчит о вкусе или сорте
# (link._abbrev), и кандидата без марки (у Дикси 9,7 % строк без марки). Здесь оседает
# то, что уже подтвердили: как пишет сеть (chain_products.name) и как пишет чек
# (product_mapping.seen_name). Наполняет refresh.fill_name_variants, читает T4 через
# search_variants — привязку товара человека и единого товара эта таблица не делает.
_VARIANT_SOURCES = ("chain", "receipt", "human")


def folded_variant(raw: str | None) -> str:
    """Свёрнутый для поиска вид названия — тот же общий свод письма, что и fnorm.

    Считается теми же шагами, что готовит строку сети match.prepare (glue, ordered_words,
    quiet, fold_words), поэтому словарь вариантов и названия единых товаров ищутся
    одними словами: «простоквашино» чека встречает prostokvasino Перекрёстка.
    Марка из поля brand сюда не подмешивается: вариант — это НАЗВАНИЕ как его написали.
    Служебные слова латиницей («syr», «up»: match._SCRIPT_NOISE) prepare вычитает из свода, а здесь
    они остаются: поиск идёт по основам слов чека, и такие слова в нём не бывают.

    Возвращается с пробелами по краям и пустой строкой, если слов не осталось:
    поиск ищет «% основа%», то есть начало слова, и без крайних пробелов терял бы
    первое и последнее слово.
    """
    from app.catalog import match as matching

    words = matching.quiet(matching.ordered_words(matching.glue(raw or "")))
    folded = matching.fold_words(words)
    return f" {' '.join(sorted(folded))} " if folded else ""


def remember_variant(item_id: int, variant: str, source: str, chain: str | None = None,
                     conn: sqlite3.Connection | None = None) -> bool:
    """Записать один вариант названия. False — записывать нечего.

    Повтор не плодит строк: hits растёт, last_seen обновляется. Для ручных правок,
    кабинета и T4; массовые источники наполняет refresh.fill_name_variants одним
    запросом. chain=None — сеть не названа (хранится пустой строкой: SQLite считает
    NULL-ы различными, и уникальный ключ с NULL пропускал бы дубли).
    """
    folded = folded_variant(variant)
    if not folded or source not in _VARIANT_SOURCES:
        return False
    own = conn is None
    conn = conn or connect()
    try:
        conn.execute(
            "INSERT INTO name_variants (item_id, variant, folded, source, chain, first_seen,"
            " last_seen, hits) VALUES (?,?,?,?,?,?,?,1)"
            " ON CONFLICT(item_id, folded, source, chain) DO UPDATE SET"
            " last_seen=excluded.last_seen, hits=name_variants.hits+1",
            (int(item_id), variant, folded, source, chain or "", now(), now()))
        if own:
            conn.commit()
        return True
    finally:
        if own:
            conn.close()


def search_variants(terms, limit: int = 80, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Единые товары, у которых в словаре есть написание с этими основами слов.

    Зеркало search_folded: берутся те же три самые длинные основы (folded_terms), и
    написание годится, если в нём есть слово, НАЧИНАЮЩЕЕСЯ с любой из них. Это отбор
    кандидатов, а не решение: решает сопоставление (match.same_product).

    Возвращается по строке на КАЖДОЕ написание (item_id, folded, source, chain):
    item_id, source, chain, variant, hits и score — сколько основ совпало. Раньше
    написание схлопывалось до одного на (item_id, source, chain) по MAX(hits), и
    второе написание того же номера было недостижимо: в словаре «…930МЛ» (пять
    наблюдений) и «…БЕЗЛАКТОЗНОЕ 930МЛ» (одно), чек ровно со вторым — связи не
    получал (F2, 18.10.2026). Порядок: больше совпавших основ, затем чаще виденное
    написание, затем номер. Номера только живые (JOIN catalog_items): битый номер
    звать в корзину нельзя, а переадресация (store.resolve_alias) уже применена
    наполнением.
    """
    picked = folded_terms(terms)
    if not picked:
        return []
    own = conn is None
    conn = conn or connect()
    try:
        likes = [f"% {t}%" for t in picked]
        score = " + ".join("(v.folded LIKE ?)" for _ in picked)
        fts_q = _fts_expr(picked, col="folded", op="OR")
        if fts_q:
            try:
                rows = conn.execute(
                    f"SELECT v.item_id, v.source, v.chain, v.variant, v.hits,"
                    f" {score} AS score"
                    " FROM name_variants_fts f"
                    " JOIN name_variants v ON v.id = f.rowid"
                    " JOIN catalog_items i ON i.id = v.item_id"
                    " WHERE name_variants_fts MATCH ?"
                    " ORDER BY score DESC, v.hits DESC, v.item_id LIMIT ?",
                    [*likes, fts_q, int(limit)],
                ).fetchall()
                if rows:
                    return [dict(r) for r in rows]
                has_fts = conn.execute("SELECT 1 FROM name_variants_fts LIMIT 1").fetchone() is not None
                if has_fts:
                    return []
            except sqlite3.OperationalError:
                pass
        where = " OR ".join("v.folded LIKE ?" for _ in picked)
        rows = conn.execute(
            f"SELECT v.item_id, v.source, v.chain, v.variant, v.hits,"
            f" {score} AS score FROM name_variants v JOIN catalog_items i ON i.id = v.item_id"
            f" WHERE {where}"
            f" ORDER BY score DESC, v.hits DESC, v.item_id LIMIT ?", likes + likes + [int(limit)])
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:      # база прежней версии: таблицы ещё нет
        return []
    finally:
        if own:
            conn.close()


def reconcile_variants(conn: sqlite3.Connection) -> tuple[int, int]:
    """Привести варианты в согласие с нынешними едиными товарами.

    Пересборка не удаляет таблицу (в ней нет ссылки на catalog_items, см. SCHEMA), но
    номера меняются: товар мог слиться с соседним. Варианты слившегося номера переезжают
    по item_aliases, варианты исчезнувшего — прочь: иначе поиск звал бы к неживому
    номеру. Возвращает (переехало номеров, убрано номеров). Коммитит вызывающий.
    """
    stale = [int(r["item_id"]) for r in conn.execute(
        "SELECT DISTINCT item_id FROM name_variants"
        " WHERE item_id NOT IN (SELECT id FROM catalog_items)")]
    moved = dropped = 0
    for old in stale:
        alive = resolve_alias(old, conn)
        if alive is None:
            conn.execute("DELETE FROM name_variants WHERE item_id=?", (old,))
            dropped += 1
        elif alive != old:
            # UPDATE OR IGNORE: если у живого номера такой вариант уже есть, дубль не
            # заводим; остаток (строки, которым помешал уникальный ключ) убираем следом.
            conn.execute("UPDATE OR IGNORE name_variants SET item_id=? WHERE item_id=?",
                         (alive, old))
            conn.execute("DELETE FROM name_variants WHERE item_id=?", (old,))
            moved += 1
    return moved, dropped


def variant_stats() -> list[dict]:
    """Сколько вариантов и наблюдений по каждому источнику — итог наполнения."""
    try:
        with connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT source, COUNT(*) AS variants, COALESCE(SUM(hits), 0) AS hits,"
                " COUNT(DISTINCT chain) AS chains FROM name_variants"
                " GROUP BY source ORDER BY source")]
    except sqlite3.OperationalError:      # база прежней версии: таблицы ещё нет
        return []


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
    _ready()
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
    _ready()
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
    _ready()
    with connect() as conn:
        cur = conn.execute("UPDATE address_queue SET status='waiting', started_at=NULL"
                           " WHERE status='running'")
        conn.commit()
        return cur.rowcount


def queue_state(limit: int = 20) -> list[dict]:
    """Что в очереди — для экрана «Магазины» и для журнала."""
    _ready()
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM address_queue ORDER BY requested_at DESC, id DESC LIMIT ?", (limit,))]


def address_status(address: str) -> dict | None:
    """Последнее, что известно про загрузку цен по этому адресу."""
    address = " ".join((address or "").split())
    if not address:
        return None
    _ready()
    with connect() as conn:
        row = conn.execute("SELECT * FROM address_queue WHERE address=?"
                           " ORDER BY requested_at DESC, id DESC LIMIT 1", (address,)).fetchone()
    return dict(row) if row else None




