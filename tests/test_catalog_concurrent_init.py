"""Первое открытие одного каталога несколькими потоками разом (тот же дефект, что у базы человека:
tests/test_db_concurrent_init.py, 12.11.2026).

Каталог открывают веб (`store._ready` при первом чтении), `korzina-jobs` (дозор цен, worker) и разовые
команды: на общем томе несколько процессов, в процессе несколько потоков. Мест три:
- `connect`: `PRAGMA journal_mode = WAL` у новой базы у двух потоков разом. SQLite отвечает «database is
  locked» сразу, мимо `timeout`: иначе оба ждали бы друг друга вечно;
- `init`: колонку из `LATE_COLUMNS` оба видят отсутствующей, и второй `ALTER` падает «duplicate column name»;
- `_migrate`: шаги не идемпотентны (шаг 2 пересчитывает цену развесной Ленты), и второй проход, начатый
  до коммита первого, пересчитал бы её второй раз.
"""
from __future__ import annotations

import threading
import time

import pytest

from app.catalog import store
from app.matcher import normalize

THREADS = 2       # потоков на один каталог: стресс-проверка гонок идёт не больше чем в треть ядер (cpu-load.md)
ROUNDS = 40       # новых каталогов подряд: без правки гонка ловится почти всегда
PATIENCE = 30     # секунд: взаимная блокировка должна упасть тестом, а не повесить весь набор


def _old_file() -> None:
    """Каталог до миграции: схема из SCHEMA (без поздней колонки prev_item_id), версия 0 (как после выкладки)."""
    conn = store.connect()
    try:
        conn.executescript(store.SCHEMA)
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("old", [False, True], ids=["new-file", "file-before-migration"])
def test_threads_opening_one_catalog_at_once_all_get_in(tmp_path, monkeypatch, old):
    """Веб и фоновые потоки открывают один каталог разом: ни один не падает, версия и колонки доведены."""
    errors: list[str] = []

    def open_it(gate: threading.Barrier) -> None:
        try:
            gate.wait(PATIENCE)
            store.init()
        except Exception as exc:  # noqa: BLE001 — собираем все отказы, чтобы показать их разом
            errors.append(f"{type(exc).__name__}: {exc}")

    for n in range(ROUNDS):
        file = str(tmp_path / str(n) / "catalog.db")
        monkeypatch.setattr(store, "path", lambda f=file: f)
        if old:
            _old_file()
        gate = threading.Barrier(THREADS)
        threads = [threading.Thread(target=open_it, args=(gate,)) for _ in range(THREADS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(PATIENCE)
        assert not any(thread.is_alive() for thread in threads), "поток завис: взаимная блокировка?"
        assert errors == []

        conn = store.connect()
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
            assert "prev_item_id" in {row["name"] for row in conn.execute("PRAGMA table_info(chain_products)")}
        finally:
            conn.close()


def test_a_second_opener_queued_during_the_migration_does_not_repeat_it(tmp_path, monkeypatch):
    """Второй вход встал в очередь, пока первый мигрирует: прождав, он видит готовую версию и не повторяет шаги.

    Шаг 2 пересчитывает цену развесной Ленты: порция 500 г за 100 ₽ это 200 ₽ за кг, и второй проход
    сделал бы из неё 400. Без `BEGIN IMMEDIATE` второй проходит всё сам, пока первый не пишет; без
    повторного чтения версии после очереди он повторяет шаги за первым.
    """
    file = str(tmp_path / "catalog.db")
    monkeypatch.setattr(store, "path", lambda: file)
    store.init()
    conn = store.connect()
    try:
        conn.executemany(
            "INSERT INTO chain_products (chain, sku, name, unit, weight_g, price, first_seen, last_seen)"
            " VALUES (?, ?, ?, 'kg', ?, ?, '2026-01-01', '2026-01-01')",
            # Дикси — чтобы шаг 1 дошёл до проверки названия (там и открывает каталог второй);
            # Лента — порция 500 г за 100 ₽, после шага 2 это 200 ₽ за килограмм.
            [("dixy", "d1", "Яблоки", None, 50.0), ("lenta", "l1", "Огурцы весовые", 500.0, 100.0)])
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
    finally:
        conn.close()

    real = normalize.sold_by_weight
    errors: list[str] = []
    seconds: list[threading.Thread] = []

    def open_it() -> None:
        try:
            store.init()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    def second_opens_meanwhile(raw):
        if not seconds:         # шаг 1 дошёл до проверки названия: второй открывает каталог в этот самый момент
            seconds.append(threading.Thread(target=open_it))
            seconds[0].start()
            time.sleep(0.6)     # ему хватает, чтобы дойти до очереди (со всей правкой он ждёт нашего коммита)
        return real(raw)

    monkeypatch.setattr(normalize, "sold_by_weight", second_opens_meanwhile)

    store.init()

    assert seconds          # хук сработал: тест проверяет то, что задумано
    seconds[0].join(PATIENCE)
    assert not seconds[0].is_alive(), "второй вход завис: взаимная блокировка?"
    assert errors == []
    conn = store.connect()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION
        assert conn.execute("SELECT price FROM chain_products WHERE sku = 'l1'").fetchone()[0] == 200.0
    finally:
        conn.close()
