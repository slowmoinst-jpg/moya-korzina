"""HTTPS за Caddy: кука сессии с Secure только на HTTPS и перевод http → https по имени.

С 26.10.2026 сайт отдаётся по имени korzina.<IP>.sslip.io через Caddy на хосте (DEPLOY.md,
пункт 3), а http://<IP> остаётся живым: на нём приложение 1.17 и старые ссылки. Поэтому
Secure у куки — по схеме запроса, а перевод на https — только для имени и только за Caddy
(переменная TRUSTED_PROXY).
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import users  # noqa: E402
from app.web import _is_name_host, create_app  # noqa: E402

NAME = "korzina.example.sslip.io"
HTTPS = {"wsgi.url_scheme": "https"}


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Клиент приложения на временном корне; proxy — значение TRUSTED_PROXY, None — без Caddy."""
    monkeypatch.setattr("app.config.ROOT", str(tmp_path))
    monkeypatch.setattr(users, "USERS_DIR", str(tmp_path / "users"))
    monkeypatch.setattr(users, "PIN_ITERATIONS", 1_000)

    from app import demo as showcase
    from app.web import login as web_login

    monkeypatch.setattr(web_login, "_FAILS", {})
    monkeypatch.setattr(web_login, "_INSTALL_FAILS", [])
    monkeypatch.setattr(showcase, "ensure", lambda: False)

    def make(proxy: str | None = None):
        if proxy:
            monkeypatch.setenv("TRUSTED_PROXY", proxy)
        else:
            monkeypatch.delenv("TRUSTED_PROXY", raising=False)
        application = create_app()
        application.config.update(TESTING=True)
        return application.test_client()

    yield make
    users.deactivate()


def test_login_address_ipv6_and_ipv4():
    from app.web import login
    application = create_app()
    cases = [
        ("2001:db8:1:2::5", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:ffff::9", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.5", "203.0.113.5"),
        ("203.0.113.5", "203.0.113.5"),
        ("", ""),
    ]
    for remote, expected in cases:
        with application.test_request_context(environ_base={"REMOTE_ADDR": remote}):
            assert login._address() == expected


@pytest.mark.parametrize("host, named", [
    (NAME, True), (NAME + ":443", True), ("203.0.113.5", False), ("203.0.113.5:80", False),
    ("localhost", False), ("[2001:db8::1]:8501", False), ("[кривой", False), ("", False)])
def test_name_host(host, named):
    assert _is_name_host(host) is named
