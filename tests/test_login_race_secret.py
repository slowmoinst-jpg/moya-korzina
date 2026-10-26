"""Защита от параллельного подбора PIN и отзыв секрета при смене PIN (W7).

1) Атомарное занятие попытки до медленной сверки хэша PBKDF2 не даёт обойти
   предел FAIL_LIMIT пачкой одновременных запросов с одного адреса.
2) Смена или снятие личного PIN рабочего места отзывает секрет api.secret и link.secret,
   чтобы старые секреты больше не отпирали /api/* и ссылки входа.
"""
from __future__ import annotations

import os
import sys
import threading
import time

import pytest

ROOT =os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import api, repo, users  # noqa: E402
from app.web import auth  # noqa: E402
from app.web import login as web_login  # noqa: E402
from app.web.screens import accounts  # noqa: E402
from conftest import sign_in  # noqa: E402
from test_login_pin import (MY_PIN, NEW_PIN, PHONE, WRONG,  # noqa: E402
                            has_a_pin, step1, step2, web)  # noqa: F401


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


def test_concurrent_second_step_under_limit(web, monkeypatch):
    """Тестовый клиент Flask: 10 одновременных шагов 2 с одного адреса (3 круга)."""
    old_switch = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for _ in range(3):
            monkeypatch.setattr(web_login, "_FAILS", {})
            monkeypatch.setattr(web_login, "_INSTALL_FAILS", [])

            checks = 0
            checks_lock = threading.Lock()

            def slow_check(phone, pin):
                nonlocal checks
                with checks_lock:
                    checks += 1
                time.sleep(0.2)
                return False

            monkeypatch.setattr(users, "check_workspace_pin", slow_check)
            has_a_pin(PHONE, MY_PIN)

            clients = [web.application.test_client() for _ in range(10)]
            for c in clients:
                resp = c.post(
                    "/login",
                    data={"phone": PHONE, "next": "/"},
                    environ_base={"REMOTE_ADDR": "10.0.0.2"},
                )
                assert resp.status_code == 200

            responses = []
            resp_lock = threading.Lock()
            barrier = threading.Barrier(10, timeout=10)

            def worker(client):
                barrier.wait()
                ans = client.post(
                    "/login",
                    data={"step": "2", "phone": PHONE, "next": "/", "my_pin": WRONG},
                    environ_base={"REMOTE_ADDR": "10.0.0.2"},
                )
                with resp_lock:
                    responses.append(ans)

            threads = [threading.Thread(target=worker, args=(c,), daemon=True) for c in clients]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
                assert not t.is_alive(), "поток не завершился за 30 с"

            codes = [r.status_code for r in responses]
            assert checks == web_login.FAIL_LIMIT
            assert codes.count(400) == web_login.FAIL_LIMIT
            assert codes.count(429) == 10 - web_login.FAIL_LIMIT
    finally:
        sys.setswitchinterval(old_switch)


def test_right_pin_after_three_wrong_leaves_three_failures(web):
    """Верный PIN после трёх неверных: вход удался, в счёте адреса осталось 3 отказа."""
    has_a_pin(PHONE, MY_PIN)
    step1(web)

    for _ in range(3):
        assert step2(web, WRONG).status_code == 400

    ans = step2(web, MY_PIN)
    assert ans.status_code == 302

    # Ровно 3 отказа осталось в счёте адреса
    addr = "127.0.0.1"
    assert len(web_login._FAILS.get(addr, [])) == 3
    assert web_login.failure_counts(addr)[0] == 3
    assert web_login.failure_counts(addr)[1] == 3

    # Следующие 2 неверные попытки дают 4-й и 5-й отказ (400), а 6-я блокируется (429)
    web.post("/logout")
    step1(web)
    assert step2(web, WRONG).status_code == 400
    assert step2(web, WRONG).status_code == 400
    assert step2(web, MY_PIN).status_code == 429


def test_changing_pin_revokes_api_secret_and_new_secret_works(web):
    """Смена PIN отзывает секрет api.secret, workplace_secret() выдаёт новый, другой."""
    has_a_pin(PHONE, MY_PIN)
    users.open_workspace(PHONE)
    secret1 = api.workplace_secret()
    users.deactivate()

    packet = {
        "workplace": PHONE,
        "secret": secret1,
        "store": "pyaterochka",
        "collected_at": "2026-10-18T22:00:00",
        "items": [{"sku": "123", "name": "Хлеб", "price": 40.0, "in_stock": True}],
    }
    # Прежний секрет отпирает дверь
    assert web.post("/api/prices", json=packet).status_code == 200

    # 1. Смена PIN через set_workspace_pin без keep_existing
    assert users.set_workspace_pin(PHONE, NEW_PIN, keep_existing=False) is True

    # Прежний секрет больше не отпирает
    stale = web.post("/api/prices", json=packet)
    assert stale.status_code == 401

    # workplace_secret() выдаёт новый, другой секрет
    users.open_workspace(PHONE)
    secret2 = api.workplace_secret()
    users.deactivate()
    assert secret2 and secret2 != secret1

    packet["secret"] = secret2
    assert web.post("/api/prices", json=packet).status_code == 200

    # 2. Смена PIN через страницу /pin со старым PIN
    sign_in(web, PHONE, pin=NEW_PIN)
    change = web.post(
        "/pin",
        data={"do": "my", "my_old": NEW_PIN, "my_pin": "846201", "my_pin2": "846201"},
    )
    assert change.status_code == 302 and "my_changed" in change.headers.get("Location", "")

    # Секрет secret2 больше не отпирает
    packet["secret"] = secret2
    assert web.post("/api/prices", json=packet).status_code == 401

    # workplace_secret() выдаёт новый, другой секрет
    users.open_workspace(PHONE)
    secret3 = api.workplace_secret()
    users.deactivate()
    assert secret3 and secret3 != secret2
    packet["secret"] = secret3
    assert web.post("/api/prices", json=packet).status_code == 200


def test_first_pin_revokes_existing_secret(web):
    """Место без PIN с уже выданным секретом: первая установка PIN отзывает секрет."""
    users.open_workspace(PHONE)
    secret = api.workplace_secret()
    users.deactivate()

    packet = {
        "workplace": PHONE,
        "secret": secret,
        "store": "pyaterochka",
        "collected_at": "2026-10-18T22:00:00",
        "items": [{"sku": "123", "name": "Хлеб", "price": 40.0, "in_stock": True}],
    }
    assert web.post("/api/prices", json=packet).status_code == 200

    assert users.set_workspace_pin(PHONE, MY_PIN, keep_existing=True) is True
    assert web.post("/api/prices", json=packet).status_code == 401
    users.open_workspace(PHONE)
    assert repo.get_setting(api.SECRET_KEY) is None
    users.deactivate()


def test_clear_workspace_pin_and_keep_existing_edge_cases(web):
    """clear_workspace_pin тоже снимает секрет. set_workspace_pin(keep_existing=True),
    вернувший False, секрет не трогает. Место без секрета меняет PIN без ошибок.
    """
    has_a_pin(PHONE, MY_PIN)
    users.open_workspace(PHONE)
    sec = api.workplace_secret()
    users.deactivate()

    packet = {
        "workplace": PHONE,
        "secret": sec,
        "store": "pyaterochka",
        "collected_at": "2026-10-18T22:00:00",
        "items": [{"sku": "123", "name": "Хлеб", "price": 40.0, "in_stock": True}],
    }

    # set_workspace_pin(keep_existing=True), вернувший False, секрет не трогает
    assert users.set_workspace_pin(PHONE, NEW_PIN, keep_existing=True) is False
    assert web.post("/api/prices", json=packet).status_code == 200

    # clear_workspace_pin тоже снимает секрет
    users.clear_workspace_pin(PHONE)
    assert web.post("/api/prices", json=packet).status_code == 401
    users.open_workspace(PHONE)
    assert repo.get_setting(api.SECRET_KEY) is None
    users.deactivate()

    # Место без секрета меняет PIN без ошибок
    assert users.set_workspace_pin(PHONE, MY_PIN) is True
    users.open_workspace(PHONE)
    assert repo.get_setting(api.SECRET_KEY) is None
    # Выдаём секрет перед последним вызовом (keep_existing=False), чтобы проверить отзыв
    fresh_sec = api.workplace_secret()
    assert fresh_sec is not None
    assert repo.get_setting(api.SECRET_KEY) == fresh_sec
    users.deactivate()

    assert users.set_workspace_pin(PHONE, NEW_PIN, keep_existing=False) is True
    users.open_workspace(PHONE)
    assert repo.get_setting(api.SECRET_KEY) is None
    users.deactivate()


def test_link_secret_revoked_on_pin_change_and_clear(web):
    """Секрет ссылки входа: set_workspace_pin и clear_workspace_pin отзывают его."""
    has_a_pin(PHONE, MY_PIN)
    users.open_workspace(PHONE)
    old_secret = users.link_secret(PHONE)
    users.deactivate()
    assert users.link_secret_matches(PHONE, old_secret) is True

    # set_workspace_pin отзывает секрет ссылки
    assert users.set_workspace_pin(PHONE, NEW_PIN) is True
    assert users.link_secret_matches(PHONE, old_secret) is False

    users.open_workspace(PHONE)
    sec2 = users.link_secret(PHONE)
    users.deactivate()
    assert users.link_secret_matches(PHONE, sec2) is True

    # clear_workspace_pin отзывает секрет ссылки
    users.clear_workspace_pin(PHONE)
    assert users.link_secret_matches(PHONE, sec2) is False


def test_revoke_secrets_before_pin_write(web, monkeypatch):
    """Отзыв до записи: сбой записи PIN оставляет старый PIN без обоих секретов."""
    has_a_pin(PHONE, MY_PIN)
    users.open_workspace(PHONE)
    api.workplace_secret()
    users.link_secret(PHONE)
    assert repo.get_setting(api.SECRET_KEY) is not None
    assert repo.get_setting(users.LINK_SECRET_KEY) is not None
    users.deactivate()

    real_set_setting = repo.set_setting

    def fail_on_login_pin(key, val):
        if key == users.LOGIN_PIN_KEY:
            raise RuntimeError("ошибка записи PIN")
        return real_set_setting(key, val)

    monkeypatch.setattr(repo, "set_setting", fail_on_login_pin)

    with pytest.raises(RuntimeError):
        users.set_workspace_pin(PHONE, NEW_PIN)

    users.open_workspace(PHONE)
    assert repo.get_setting(api.SECRET_KEY) is None
    assert repo.get_setting(users.LINK_SECRET_KEY) is None
    users.deactivate()

    assert users.check_workspace_pin(PHONE, MY_PIN) is True
    assert users.check_workspace_pin(PHONE, NEW_PIN) is False


def test_grab_stale_cookie_gives_no_secret(web):
    """_grab с устаревшей кукой: secret None и в базе нет; с верным отпечатком секрет есть."""
    has_a_pin(PHONE, MY_PIN)
    users.open_workspace(PHONE)
    try:
        assert repo.get_setting(api.SECRET_KEY) is None
        with web.application.test_request_context("/accounts"):
            from flask import session

            session[auth.KEY_PHONE] = PHONE
            session[auth.KEY_PIN_STAMP] = "0" * 16

            res_stale = accounts._grab()
            assert res_stale["secret"] is None
            assert repo.get_setting(api.SECRET_KEY) is None

            session[auth.KEY_PIN_STAMP] = auth.pin_stamp()
            res_fresh = accounts._grab()
            assert res_fresh["secret"] is not None
            assert repo.get_setting(api.SECRET_KEY) == res_fresh["secret"]
    finally:
        users.deactivate()


def test_grab_demo_gives_no_secret(web):
    """Демо на экране: secret None, и в базе демо секрета нет."""
    users.open_workspace(users.DEMO)
    try:
        assert repo.get_setting(api.SECRET_KEY) is None
        with web.application.test_request_context("/accounts"):
            from flask import session

            session[auth.KEY_PHONE] = users.DEMO

            res = accounts._grab()
            assert res["secret"] is None
            assert repo.get_setting(api.SECRET_KEY) is None
    finally:
        users.deactivate()
