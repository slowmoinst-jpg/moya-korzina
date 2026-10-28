"""Защита от параллельного подбора PIN установки и запрет секрета для демо."""
from __future__ import annotations

import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import api, repo, users  # noqa: E402
from app.web import login as web_login  # noqa: E402
from app.web.screens import accounts  # noqa: E402


@pytest.fixture
def web(tmp_path, monkeypatch):
    """Приложение на временном корне, счётчики попыток обнулены, витрину демо никто не собирает."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    monkeypatch.setattr(users, "PIN_ITERATIONS", 1_000)

    from app import demo as showcase
    from app.web import create_app

    monkeypatch.setattr(web_login, "_FAILS", {})
    monkeypatch.setattr(web_login, "_INSTALL_FAILS", [])
    monkeypatch.setattr(showcase, "ensure", lambda: False)

    application = create_app()
    application.config.update(TESTING=True)
    yield application.test_client()
    users.deactivate()


def test_twenty_threads_reserve_attempt_with_barrier(monkeypatch):
    """20 потоков разом (threading.Barrier) занимают попытку с одного адреса: ровно FAIL_LIMIT."""
    old_switch = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for _ in range(20):
            monkeypatch.setattr(web_login, "_FAILS", {})
            monkeypatch.setattr(web_login, "_INSTALL_FAILS", [])

            results: list[float | None] = []
            lock = threading.Lock()
            barrier = threading.Barrier(20, timeout=10)

            def worker():
                barrier.wait()
                res = web_login.reserve_attempt("10.0.0.1")
                with lock:
                    results.append(res)

            threads = [threading.Thread(target=worker, daemon=True) for _ in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
                assert not t.is_alive(), "поток не завершился за 30 с"

            successful = [r for r in results if r is not None]
            assert len(successful) == web_login.FAIL_LIMIT
            assert len(results) == 20
            assert len(web_login._FAILS["10.0.0.1"]) == web_login.FAIL_LIMIT
    finally:
        sys.setswitchinterval(old_switch)


def test_grab_demo_gives_no_secret(web):
    """Демо на экране: secret None, и в базе демо секрета нет."""
    users.open_workspace(users.DEMO)
    try:
        assert repo.get_setting(api.SECRET_KEY) is None
        with web.application.test_request_context("/accounts"):
            from flask import session

            session["phone"] = users.DEMO

            res = accounts._grab()
            assert res["secret"] is None
            assert repo.get_setting(api.SECRET_KEY) is None
    finally:
        users.deactivate()
