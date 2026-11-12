"""Первое открытие одной базы несколькими потоками разом (мигающий тест новичка, 12.11.2026).

`test_empty_screens_tell_a_newcomer_what_to_do` один раз из пяти полных прогонов упал на входе:
`sqlite3.OperationalError: database is locked` в `app/db.py::_migrate`, не прождав и секунды. Свежую базу
новичка открывал ещё один поток (фоновый, оставшийся от прошлого теста), и SQLite отказал сразу, а не
по `busy_timeout`: соединение, уже прочитавшее базу, просило право записи, когда сосед писал. Ждать тут
нельзя, оба ждали бы друг друга вечно, поэтому SQLite отвечает «locked» без обработчика занятости.

На сервере то же самое: два запроса разом открывают базу, которую ещё не открывали (вход нового
человека, вход и фоновый поток), или первый запрос после выкладки, поднявшей SCHEMA_VERSION. Вход
отвечает ошибкой 500. Мест два, оба в первом открытии базы:
- `get_conn`: `PRAGMA journal_mode = WAL` у новой базы, у двух потоков разом;
- `_migrate`: `PRAGMA user_version = N` после чтения базы, когда снимок уже устарел.
"""
from __future__ import annotations

import sqlite3
import threading

import pytest

from app import config, db

THREADS = 2       # потоков на одну базу: стресс-проверка гонок идёт не больше чем в треть ядер (cpu-load.md)
ROUNDS = 40       # новых баз подряд: без правки гонка ловится почти всегда
PATIENCE = 30     # секунд: взаимная блокировка должна упасть тестом, а не повесить весь набор


@pytest.fixture
def base(tmp_path):
    """База этого теста: путь подменён в контексте потока, как это делает users.activate."""
    before = config.db_override()
    path = str(tmp_path / "basket.db")
    config.set_db_override(path)
    yield path
    config.set_db_override(before)


def test_a_commit_by_a_neighbour_in_the_middle_of_the_migration_does_not_fail_the_opening(base, monkeypatch):
    """Сосед записал, пока шла миграция: без правки `PRAGMA user_version` падал «database is locked»."""
    real = db._migrate_store_sizes

    def neighbour_writes_meanwhile(conn):
        real(conn)
        neighbour = sqlite3.connect(base, timeout=0)    # ждать нельзя: ждал бы тот, кто держит миграцию
        try:
            neighbour.execute("INSERT INTO settings (key, value) VALUES ('neighbour', '1')")
            neighbour.commit()
        except sqlite3.OperationalError:
            pass    # запись держит сама миграция: настоящий сосед подождал бы очереди по busy_timeout
        finally:
            neighbour.close()

    monkeypatch.setattr(db, "_migrate_store_sizes", neighbour_writes_meanwhile)

    db.init_db()

    conn = db.get_conn()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0] == len(db.SEED_STORES)
    finally:
        conn.close()


def test_threads_opening_one_new_database_at_once_all_get_in(tmp_path):
    """Вход нового человека и фоновые потоки открывают его свежую базу разом: ни один не падает."""
    errors: list[str] = []

    def open_it(path: str, gate: threading.Barrier) -> None:
        config.set_db_override(path)    # у нового потока контекст пустой, как в users.open_workspace
        try:
            gate.wait(PATIENCE)
            db.init_db()
        except Exception as exc:  # noqa: BLE001 — собираем все отказы, чтобы показать их разом
            errors.append(f"{type(exc).__name__}: {exc}")

    for n in range(ROUNDS):
        path = str(tmp_path / str(n) / "basket.db")
        gate = threading.Barrier(THREADS)
        threads = [threading.Thread(target=open_it, args=(path, gate)) for _ in range(THREADS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(PATIENCE)
        assert not any(thread.is_alive() for thread in threads), "поток завис: взаимная блокировка?"

    assert errors == []
