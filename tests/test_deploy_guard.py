"""Проверка перед выкладкой: идёт ли обход каталога.

Замер 19.10.2026: две выкладки подряд оборвали ночной обход (перезапуск контейнера
korzina-jobs), и Лента за сутки не обошлась ни разу. Поэтому deploy.ps1 спрашивает
сервер ключом `--runs`, и код возврата 3 означает «обход идёт — не выкладывай».
"""
from __future__ import annotations

import pytest

from app.catalog import store, worker


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "path", lambda: str(tmp_path / "catalog.db"))
    store.init()
    return store


def test_no_crawl_means_quiet(base, capsys):
    assert worker.main(["--runs"]) == 0
    assert "обходов идёт: нет" in capsys.readouterr().out


def test_a_live_crawl_is_reported_with_code_three(base, capsys):
    store.start_run("lenta")

    assert worker.main(["--runs"]) == 3
    out = capsys.readouterr().out
    assert "lenta — обход идёт с" in out
    assert "выкладка оборвёт его" in out


def test_running_runs_shows_every_live_crawl(base):
    store.start_run("lenta")
    store.start_run("dixy")
    done = store.start_run("metro")
    store.finish_run(done, "ok", seen=1)

    assert [row["chain"] for row in store.running_runs()] == ["lenta", "dixy"]


def test_a_closed_run_is_not_live_anymore(base):
    run_id = store.start_run("vkusvill")
    store.finish_run(run_id, "failed", note="обрыв")

    assert store.running_runs() == []
    assert worker.main(["--runs"]) == 0


def test_a_sweep_between_chains_still_counts_as_busy(base, capsys):
    """Замер 20.10.2026: между сетями идёт сопоставление, строки «running» нет.

    Сторож отвечал «обходов нет», и выкладка в этот момент оборвала бы ночь. Отметка
    обхода целиком закрывает эту дыру.
    """
    store.mark_sweep("running")

    assert worker.main(["--runs"]) == 3
    out = capsys.readouterr().out
    assert "идёт обход сетей целиком" in out
    assert "выкладка оборвёт его" in out


def test_a_finished_sweep_is_not_busy(base, capsys):
    store.mark_sweep("running")
    store.mark_sweep("done")

    assert worker.main(["--runs"]) == 0
    assert "обходов идёт: нет" in capsys.readouterr().out


def test_a_stale_sweep_marker_is_not_busy(base, capsys):
    """Процесс мог умереть: отметка «идёт» старше 12 часов не запирает выкладку навсегда."""
    from datetime import datetime, timedelta

    store.mark_sweep("running")
    old = (datetime.now() - timedelta(hours=20)).isoformat(timespec="microseconds")
    with store.connect() as conn:
        conn.execute("update catalog_state set at=? where key='sweep'", (old,))
        conn.commit()

    assert store.sweep_state() == "stale"
    assert worker.main(["--runs"]) == 0
    assert "считаю, что его нет" in capsys.readouterr().out
