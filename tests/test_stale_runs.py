"""Брошенные обходы: строка «running» не должна висеть неделями.

Замер 19.10.2026: после выкладок в журнале накопилось 15 строк со статусом running,
самые старые — от 16.09.2026 (METRO, ВкусВилл, Лента, Магнит). По ним нельзя понять,
обошлась сеть или нет.
"""
from __future__ import annotations

import pytest

from app.catalog import store


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


def _age(run_id: int, hours: float) -> None:
    """Отодвинуть начало прогона в прошлое — так выглядят брошенные обходы."""
    from datetime import datetime, timedelta

    started = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="microseconds")
    with store.connect() as conn:
        conn.execute("update crawl_runs set started_at=? where id=?", (started, run_id))
        conn.commit()


def test_an_abandoned_run_is_closed(base):
    old = store.start_run("lenta")
    _age(old, 10)

    closed = store.close_stale_runs(hours=6)

    assert closed == 1
    with store.connect() as conn:
        row = conn.execute("select status, note, finished_at from crawl_runs where id=?",
                           (old,)).fetchone()
    assert row["status"] == "failed"
    assert "не завершился" in row["note"]
    assert row["finished_at"], "у закрытого прогона должно быть время конца"


def test_a_live_run_of_another_container_is_not_touched(base):
    """Обход бывает живым и в другом контейнере: чужой прогон не объявляем провалившимся."""
    live = store.start_run("dixy")

    assert store.close_stale_runs(hours=6) == 0
    with store.connect() as conn:
        row = conn.execute("select status from crawl_runs where id=?", (live,)).fetchone()
    assert row["status"] == "running"


def test_a_finished_run_is_left_alone(base):
    done = store.start_run("metro")
    store.finish_run(done, "ok", seen=10, added=2)
    _age(done, 30)

    assert store.close_stale_runs(hours=6) == 0
    with store.connect() as conn:
        row = conn.execute("select status from crawl_runs where id=?", (done,)).fetchone()
    assert row["status"] == "ok"
