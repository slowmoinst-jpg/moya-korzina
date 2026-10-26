"""SQLite: схема (раздел 4 спецификации), подключение, инициализация, сиды."""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Iterable

from app import config

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    barcode TEXT,
    brand TEXT,
    weight_g REAL,
    unit TEXT NOT NULL CHECK (unit IN ('pcs', 'kg')),
    category TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    delivery_fee REAL NOT NULL DEFAULT 0,
    free_delivery_from REAL NOT NULL DEFAULT 0,
    min_order REAL NOT NULL DEFAULT 0,
    connector_type TEXT NOT NULL DEFAULT 'stub'
);

CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank TEXT NOT NULL,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    percent REAL NOT NULL,
    cap_rub REAL NOT NULL,
    min_check_rub REAL NOT NULL DEFAULT 0,
    valid_from TEXT,
    valid_to TEXT,
    requires_activation INTEGER NOT NULL DEFAULT 0,
    activated INTEGER NOT NULL DEFAULT 1,
    cap_used REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS purchase_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    store_id INTEGER REFERENCES stores(id),
    product_id INTEGER REFERENCES products(id),
    raw_name TEXT NOT NULL,
    qty REAL NOT NULL,
    unit_price REAL NOT NULL,
    total REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_product ON purchase_history(product_id);

CREATE TABLE IF NOT EXISTS store_products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    sku TEXT NOT NULL,
    raw_name TEXT NOT NULL,
    ean TEXT,
    weight_g REAL,
    unit TEXT,
    url TEXT,
    UNIQUE (store_id, sku)
);

CREATE TABLE IF NOT EXISTS product_mapping (
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    store_product_id INTEGER NOT NULL REFERENCES store_products(id) ON DELETE CASCADE,
    confirmed INTEGER NOT NULL DEFAULT 0,
    confirmed_at TEXT,
    PRIMARY KEY (product_id, store_product_id)
);

CREATE TABLE IF NOT EXISTS store_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_product_id INTEGER NOT NULL REFERENCES store_products(id) ON DELETE CASCADE,
    price REAL NOT NULL,
    price_per_kg REAL,
    in_stock INTEGER NOT NULL DEFAULT 1,
    fetched_at TEXT NOT NULL,
    source TEXT,                    -- NULL — живая цена; fallback — справочная из CSV
    -- Точка и вид цены: «широта,долгота» места человека и delivery/pickup.
    -- NULL — цены сети не зависят от места (или место не задано). Нужны, чтобы
    -- цена одного магазина не выдавалась за цену другого (app/freshness.price_for).
    point_key TEXT,
    mode TEXT
);

CREATE TABLE IF NOT EXISTS baskets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'average', 'history'))
);

CREATE TABLE IF NOT EXISTS basket_items (
    basket_id INTEGER NOT NULL REFERENCES baskets(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    qty REAL NOT NULL,
    PRIMARY KEY (basket_id, product_id)
);

CREATE TABLE IF NOT EXISTS variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    basket_id INTEGER NOT NULL REFERENCES baskets(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    total REAL NOT NULL,
    baseline REAL NOT NULL,
    savings_rub REAL NOT NULL,
    savings_pct REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS variant_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    variant_id INTEGER NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    card_id INTEGER REFERENCES cards(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    qty REAL NOT NULL,
    price REAL NOT NULL,
    discount REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS receipts (
    key TEXT PRIMARY KEY,           -- отпечаток чека; у ФНС это её собственный ключ
    date TEXT,
    store TEXT,
    total REAL,
    rows INTEGER,                   -- позиций загружено; NULL — чек только виден, но не загружен
    source TEXT,                    -- lkdr | file | paste
    seen_at TEXT,                   -- когда чек впервые попался на глаза
    imported_at TEXT                -- когда позиции легли в историю; NULL — ещё не загружен
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_prices_sp ON store_prices(store_product_id, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_sp_store ON store_products(store_id);
CREATE INDEX IF NOT EXISTS idx_mapping_product ON product_mapping(product_id);

-- Связи, которые человек убрал. Без этой памяти единая база вернула бы их при
-- следующем сопоставлении, и обещание «до нового подтверждения товар в расчёт по
-- этой сети не попадёт» стало бы неправдой (критика проекта 17.10.2026).
CREATE TABLE IF NOT EXISTS link_rejects (
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    sku TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (product_id, store_id, sku)
);
CREATE INDEX IF NOT EXISTS idx_history_date ON purchase_history(date);
CREATE INDEX IF NOT EXISTS idx_receipts_date ON receipts(date);
"""

SEED_STORES: list[tuple] = [
    # code, name, delivery_fee, free_delivery_from, min_order, connector_type
    ("magnit", "Магнит", 149.0, 2000.0, 500.0, "magnit"),
    ("vkusvill", "ВкусВилл", 99.0, 1500.0, 400.0, "vkusvill"),
    ("pyaterochka", "Пятёрочка", 199.0, 2500.0, 600.0, "pyaterochka"),
    ("lenta", "Лента", 199.0, 3000.0, 1000.0, "lenta"),
    ("dixy", "Дикси", 149.0, 2000.0, 500.0, "dixy"),
    ("samokat", "Самокат", 0.0, 0.0, 0.0, "samokat"),
    # METRO — оптовый формат: вход по карте клиента, поэтому пороги заметно выше
    # обычной сети. Цифры взяты с сайта сети и правятся настройкой, а не кодом.
    ("metro", "METRO", 0.0, 5000.0, 1500.0, "metro"),
    # Перекрёсток заведён 21.09.2026 вместе с окном для его проверки. Цены доставки
    # взяты с сайта сети и правятся настройкой, а не кодом; коннектора у него нет
    # (stub), потому что карточку наш сервер не видит — каталог приходит обходом.
    ("perekrestok", "Перекрёсток", 199.0, 3000.0, 800.0, "stub"),
    # FixPrice заведён 21.10.2026 (task-19): сеть уже была в приложении и в обходе
    # каталога (6 624 строки с ценой), но её не было в справочнике — и на телефоне
    # она не показывалась вовсе. Коннектора у Fix Price нет и не появится: цены
    # приходят обходом витрины (app/catalog/crawlers/fixprice.py), поэтому stub.
    # ПОРОГИ — ПРО ДОСТАВКУ И МИНИМАЛЬНЫЙ ЗАКАЗ, и они НЕ ЗАМЕРЕНЫ у этой сети:
    # взяты как у соседней сети того же формата и служат отправной точкой. Цифры
    # здесь — только для новой базы; на живой они правятся в справочнике магазинов
    # (таблица stores), и init_db их НЕ перезаписывает (см. ниже).
    ("fixprice", "FixPrice", 199.0, 2500.0, 500.0, "stub"),
    # Впрок и Монетка заведены 25.10.2026 тем же путём, что FixPrice: обе сети уже были
    # в приложении (этап 5) и в обходе каталога (app/catalog/crawlers/vprok.py,
    # monetka.py), а справочник магазинов их не знал — на телефоне они не показывались.
    # Коннекторов нет: каталог приходит обходом карты сайта, цену приносит телефон,
    # поэтому stub. ПОРОГИ — ПРО ДОСТАВКУ И МИНИМАЛЬНЫЙ ЗАКАЗ, и они НЕ ЗАМЕРЕНЫ у этих
    # сетей: Впрок взят как Перекрёсток (одна группа), Монетка — как FixPrice. Это
    # отправная точка, а не цифры сетей; на живой базе они правятся в справочнике
    # магазинов, и init_db их НЕ перезаписывает.
    ("vprok", "Впрок", 199.0, 3000.0, 800.0, "stub"),
    ("monetka", "Монетка", 199.0, 2500.0, 500.0, "stub"),
]


def get_conn() -> sqlite3.Connection:
    """Соединение с базой рабочего места.

    Две настройки здесь не косметические, они про скорость записи. Через это место
    проходит каждая строка истории, а пишутся они по одной, с commit на строку. В
    обычном режиме каждый такой commit — это сброс на диск, и загрузка чеков платит
    за него вдвое дороже, чем за сам поход в ФНС: замерено 16.09.2026, 4500 строк —
    21 секунда против 2 секунд с этими двумя строками.

    journal_mode = WAL — запись идёт в отдельный журнал, читатели не ждут писателя.
    Режим хранится в самой базе, так что задаётся он фактически один раз.
    synchronous = NORMAL — настройка соединения, поэтому повторяется каждый раз: при
    падении приложения база цела, потерять можно только последние сделки и только при
    отключении питания. Для истории покупок это верный размен.

    Обратная сторона WAL: рядом с базой появляются файлы -wal и -shm, и копировать её
    целиком теперь означает копировать все три, иначе свежие записи останутся в журнале.
    """
    path = config.db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Сколько ждать чужую запись, прежде чем ответить «database is locked». Обновление
    # цен пишет снимки из нескольких потоков сразу (matcher.refresh_prices_parallel), и
    # без ожидания часть снимков терялась бы на короткой блокировке соседа. 15 с —
    # потолок ниже 25 с, отведённых на сеть: ожидание не съедает весь срок.
    # Ставится ДО journal_mode: переключение в WAL само берёт короткую блокировку.
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


# Колонки, добавленные после первых выпусков. CREATE TABLE IF NOT EXISTS их не донесёт
# до уже существующей базы, поэтому досыпаем отдельно.
LATE_COLUMNS: list[tuple[str, str, str]] = [
    # таблица, колонка, тип
    ("products", "barcode", "TEXT"),
    ("store_products", "ean", "TEXT"),
    ("purchase_history", "receipt_key", "TEXT"),
    ("store_prices", "source", "TEXT"),
    # Точка снимка и вид цены (21.10.2026): «цена в моменте» должна помнить, где она
    # снята, иначе цена одного адреса выдаётся за цену другого. NULL — цена сети не
    # зависит от места или место не задано.
    ("store_prices", "point_key", "TEXT"),
    ("store_prices", "mode", "TEXT"),
    # Привязка товара человека к единой базе (решение владельца 17.10.2026): номер
    # единого товара в data/catalog.db, как найден (barcode, bridge, words, abbrev)
    # и когда. Номер постоянный между пересборками каталога (refresh.match_all).
    ("products", "master_id", "INTEGER"),
    ("products", "master_method", "TEXT"),
    ("products", "master_at", "TEXT"),
    # Откуда сопоставление: NULL — человек или живой поиск, «master» — из единой
    # базы. Выбор человека единая база не перебивает (app/catalog/link.py).
    ("product_mapping", "method", "TEXT"),
    # Название товара сети в момент подтверждения не базой. Сеть может отдать
    # артикул другому товару; тогда выбор человека больше не мост к другим сетям
    # (link._choices, ревью 18.10.2026). store_products.raw_name для этого не годится:
    # его переписывают и коннекторы, и единая база.
    ("product_mapping", "seen_name", "TEXT"),
]


def _add_late_columns(conn: sqlite3.Connection) -> None:
    """Досыпает недостающие колонки в базу, созданную прежней версией."""
    for table, column, kind in LATE_COLUMNS:
        have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def _add_late_indexes(conn: sqlite3.Connection) -> None:
    """Индексы по поздно добавленным колонкам.

    В SCHEMA_SQL их не поставить: на базе прежней версии колонки ещё нет, и
    executescript упал бы целиком — вместе со всей схемой. Поэтому здесь, после
    _add_late_columns.
    """
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prices_point ON store_prices(point_key)")


# Сети, чьи сборщики ставили unit="kg" по ОТСУТСТВИЮ граммовки в названии (так
# отвечал normalize.parse_weight): Пятёрочка и Самокат (витрина и кабинет), Fix Price,
# Дикси. У Магнита, Ленты, ВкусВилла и METRO единица шла от самой сети — её не трогаем.
FALSE_KG_CHAINS = ("pyaterochka", "samokat", "fixprice", "dixy")

SCHEMA_VERSION = 3


def _migrate(conn: sqlite3.Connection) -> None:
    """Разовые исправления данных, записанных прежними версиями. По PRAGMA user_version.

    Каждый шаг идёт ровно один раз: база, уже прошедшая шаг, его не повторяет. Иначе
    новый шаг заодно прогонял бы все прежние — на живой базе это лишняя работа и
    решения, принятые по сегодняшним данным, а не по тем, ради которых шаг писался.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        _migrate_false_kg(conn)
    if version < 2:
        _migrate_preview_rows(conn)
    if version < 3:
        _migrate_store_sizes(conn)
    if version < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _migrate_store_sizes(conn: sqlite3.Connection) -> None:
    """Версия 3 (18.10.2026) — фасовка в строках магазинов, записанных без неё.

    У METRO сборщик перестал понимать форму ответа сети (`packing` приходит как
    `{"size": 1, "type": "шт"}`), а у Перекрёстка, Впрока и Монетки имя собирается из
    адреса карты сайта и не разбиралось вовсе. В личную базу такие строки легли без веса
    и без единицы: замер на рабочем месте владельца — 140 привязок METRO и ни одной с
    весом. По весу наряд считает, сколько упаковок класть в корзину сети (cartplan._pack),
    а без него упаковку не пересчитать.

    Берём вес из названия строки (raw_name): parse_weight читает и «, 1кг», и «500 g».
    Единицу ставим только там, где название само говорит «весов…» — иначе догадка «pcs»
    отсекла бы верные пары «kg ↔ pcs» при сопоставлении.
    """
    from app.matcher.normalize import parse_weight, sold_by_weight

    weights: list[tuple[float, int]] = []
    units: list[tuple[str, int]] = []
    for row in conn.execute("SELECT id, raw_name, weight_g, unit FROM store_products"
                            " WHERE weight_g IS NULL OR unit IS NULL"):
        text = row["raw_name"] or ""
        if row["weight_g"] is None:
            value, _ = parse_weight(text)
            if value:
                weights.append((value, row["id"]))
        if row["unit"] is None and sold_by_weight(text):
            units.append(("kg", row["id"]))
    if weights:
        conn.executemany("UPDATE store_products SET weight_g=? WHERE id=?", weights)
    if units:
        conn.executemany("UPDATE store_products SET unit=? WHERE id=?", units)


def _migrate_false_kg(conn: sqlite3.Connection) -> None:
    """Версия 1 (ревизия 26.09.2026) — ложные «весовые»:
      * у товаров сетей из FALSE_KG_CHAINS без слова «весовой»/«кг» в названии
        единица «kg» снимается (NULL — «сеть не сказала»): её поставил разбор
        названия, а не сеть;
      * эталоны с единицей «kg», которые по доводам штучные (service.is_weighed:
        чеки целыми штуками, везде фасовка или штука), становятся «pcs». Иначе
        «Хлеб Бородинский нарезка» так и показывался бы «1 кг», а расчёт и
        наряд считали бы его килограммами.
    """
    from app.matcher.normalize import sold_by_weight

    placeholders = ",".join("?" * len(FALSE_KG_CHAINS))
    rows = conn.execute(
        "SELECT sp.id, sp.raw_name FROM store_products sp JOIN stores s ON s.id = sp.store_id"
        f" WHERE sp.unit='kg' AND s.code IN ({placeholders})", FALSE_KG_CHAINS).fetchall()
    reset = [(r["id"],) for r in rows if not sold_by_weight(r["raw_name"] or "")]
    conn.executemany("UPDATE store_products SET unit=NULL WHERE id=?", reset)

    from app.service import weighed_by_evidence

    # Одним проходом по таблицам, а не запросом на каждый эталон: миграция идёт в
    # первом запросе после выкладки, и на большой базе поштучные запросы держали
    # запись секундами — соседний запрос падал с «database is locked».
    kg = {r["id"]: r["name"] or "" for r in conn.execute("SELECT id, name FROM products WHERE unit='kg'")}
    bought: dict[int, list] = {}
    for r in conn.execute("SELECT product_id, qty, raw_name FROM purchase_history"
                          " WHERE product_id IN (SELECT id FROM products WHERE unit='kg')"):
        bought.setdefault(r["product_id"], []).append((r["qty"], r["raw_name"]))
    sold: dict[int, list] = {}
    for r in conn.execute("SELECT m.product_id, sp.unit, sp.weight_g, sp.raw_name"
                          " FROM product_mapping m JOIN store_products sp ON sp.id = m.store_product_id"
                          " WHERE m.confirmed=1 AND m.product_id IN"
                          " (SELECT id FROM products WHERE unit='kg')"):
        sold.setdefault(r["product_id"], []).append((r["unit"], r["weight_g"], r["raw_name"]))
    # Дробное количество в корзине — тоже довод: человек набрал «0,7», видя «кг»,
    # и перевод в штуки молча сделал бы из 0,7 кг одну пачку.
    in_baskets = {r["product_id"] for r in conn.execute(
        "SELECT DISTINCT product_id FROM basket_items WHERE ABS(qty - ROUND(qty)) > 0.0001")}

    flipped = []
    for pid, name in kg.items():
        if pid in in_baskets:
            continue
        if not bought.get(pid) and not sold.get(pid):
            continue                  # ни чеков, ни сетей — судить не по чему, верим отметке
        if not weighed_by_evidence(name, bought.get(pid, []), sold.get(pid, [])):
            flipped.append((pid,))
    conn.executemany("UPDATE products SET unit='pcs' WHERE id=?", flipped)
    if reset or flipped:
        import logging

        logging.getLogger(__name__).info(
            "миграция 1: у %d товаров сетей снята ложная единица «кг», %d эталонов стали штучными",
            len(reset), len(flipped))


# Артикул строки-призрака стенда предпросмотра: «preview-<код сети>-N» (так они
# называются в живых базах) и «<код сети>-preview-N» (так их пишут соседние стенды).
# Обе формы узнаются по самому артикулу, без сверки с сетью строки: призрак бывает
# записан не в свою сеть, и подмены сети для решения не нужно — сеть по такому
# артикулу товара всё равно не знает.
PREVIEW_ANYWHERE = re.compile(r"^preview-[^-]+-\d+$")
PREVIEW_AFTER_CODE = re.compile(r"-preview-\d+$")


def _migrate_preview_rows(conn: sqlite3.Connection) -> None:
    """Версия 2 (18.10.2026) — строки-призраки стенда предпросмотра «preview-<сеть>-N».

    ЗАЧЕМ. Стенд предпросмотра заводил в store_products строки, чьи имена буквально
    равны строкам чеков, а артикулы начинаются с «preview-». Настоящим артикулом сети
    такая строка не является: сеть по ней товара не узнаёт, в её корзину ничего не
    положить, а замер сопоставления и «сколько сетей опознано» она завышала, и связи
    человека на неё подтверждались как настоящие (отчёт о готовности 18.10.2026, 48
    строк из 293). placeholder_sku её теперь ловит, но строка оставалась бы видна
    живому поиску кандидатов и могла бы снова попасть в подтверждённые. Поэтому
    строки удаляются вместе со своими связями и снимками цен (ON DELETE CASCADE).

    ЭТО УДАЛЕНИЕ ДАННЫХ, и оно нарочно узкое: уходят только строки самого стенда —
    «preview-<сеть>-<число>» и «<сеть>-preview-<число>», узнаваемые по тексту
    артикула. Сеть строки при этом НЕ сверяется: призрак попадал и не в свою сеть
    («lenta-preview-9» в магазине Магнита), и в каталоге Магнита ему всё равно не
    место (adversarial-проверка 18.10.2026, F3). Строка справочника вида
    «magnit-огурцы-450» (источник цены, а не призрак) остаётся: слова «preview» в ней
    нет, и настоящие артикулы сетей его не содержат. Подтверждение, которое человек
    успел поставить на призрака, уходит вместе с ним — оно всё равно не положило бы
    товар в корзину сети, а держало бы цену из ниоткуда. Что именно уходит, видно в
    логе: строка на каждого кандидата (артикул, связи, снимки цен) до удаления и одна
    итоговая после.

    Побочный случай: две подтверждённые заглушки у одной пары (строка чека и призрак)
    — уходит только призрак, строка чека остаётся.
    """
    rows = conn.execute(
        "SELECT id, sku FROM store_products"
        " WHERE lower(sku) LIKE 'preview-%' OR lower(sku) LIKE '%-preview-%'").fetchall()
    ghosts = []
    for r in rows:
        sku = str(r["sku"] or "").lower()
        if PREVIEW_ANYWHERE.match(sku) or PREVIEW_AFTER_CODE.search(sku):
            ghosts.append(r["id"])
    if not ghosts:
        return
    import logging

    log = logging.getLogger(__name__)
    marks = ",".join("?" * len(ghosts))
    # Сначала называем КАЖДОГО кандидата и считаем, что уйдёт вместе с ним, и только
    # потом удаляем: копии тома на момент миграции нет, и по одной итоговой строке
    # потом не понять, что именно было у человека. После удаления считать уже нечего.
    candidates = conn.execute(
        "SELECT sp.id, sp.sku,"
        " (SELECT COUNT(*) FROM product_mapping m WHERE m.store_product_id = sp.id) AS links,"
        " (SELECT COUNT(*) FROM store_prices p WHERE p.store_product_id = sp.id) AS prices"
        f" FROM store_products sp WHERE sp.id IN ({marks}) ORDER BY sp.id", ghosts).fetchall()
    links = prices = 0
    for r in candidates:
        links += int(r["links"])
        prices += int(r["prices"])
        log.info("миграция 2: кандидат %s — связей %d, снимков цен %d",
                 r["sku"], r["links"], r["prices"])
    conn.executemany("DELETE FROM store_products WHERE id=?", [(sp_id,) for sp_id in ghosts])
    log.info("миграция 2: удалено %d строк-призраков стенда (артикулы «preview-…»), вместе"
             " с ними %d связей и %d снимков цен", len(ghosts), links, prices)


def init_db(conn: sqlite3.Connection | None = None) -> None:
    """Создаёт схему и справочник магазинов. Идемпотентно."""
    own = conn is None
    conn = conn or get_conn()
    try:
        conn.executescript(SCHEMA_SQL)
        _add_late_columns(conn)
        _add_late_indexes(conn)
        _migrate(conn)
        # ДОСЫПКА, А НЕ ПЕРЕЗАПИСЬ. На боевом сервере база уже живёт: сети заведены,
        # а пороги доставки и минимального заказа человек настроил под себя. Поэтому
        # конфликт по коду сети гасится (DO NOTHING): недостающая сеть появляется в
        # справочнике, а существующая строка остаётся такой, какой её оставили. Так
        # 21.10.2026 доехал FixPrice — без миграции по PRAGMA user_version и без
        # правки уже настроенных записей.
        for row in SEED_STORES:
            conn.execute(
                "INSERT INTO stores (code, name, delivery_fee, free_delivery_from, min_order, connector_type)"
                " VALUES (?,?,?,?,?,?) ON CONFLICT(code) DO NOTHING",
                row,
            )
        conn.commit()
    finally:
        if own:
            conn.close()


def query(sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(sql, tuple(params)).fetchall()


def execute(sql: str, params: Iterable = ()) -> int:
    with get_conn() as conn:
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur.lastrowid


if __name__ == "__main__":
    init_db()
    print("DB initialized at", config.db_path())
