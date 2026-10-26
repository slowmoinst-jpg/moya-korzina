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


def test_session_cookie_secure_only_under_https(make_client):
    client = make_client()
    plain = client.post("/demo").headers.get("Set-Cookie", "")
    assert plain.startswith("session=") and "Secure" not in plain, (
        "http://<IP> должен оставаться рабочим"
    )
    secure = client.post("/demo", environ_overrides=HTTPS).headers.get("Set-Cookie", "")
    assert secure.startswith("__Host-session=") and "Secure" in secure


def test_same_origin_posts_protection(make_client):
    client = make_client()
    for site, expected in [("cross-site", 403), ("same-site", 403), ("same-origin", 302)]:
        assert client.post("/demo", headers={"Sec-Fetch-Site": site}).status_code == expected
    for origin, expected in [("https://evil.1-2-3-4.sslip.io", 403), ("null", 403),
                             ("http://localhost", 302)]:
        assert client.post("/demo", headers={"Origin": origin}).status_code == expected
    answer = client.post("/api/prices", headers={"Sec-Fetch-Site": "cross-site"})
    assert "Запрос с чужого сайта" not in answer.text


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


def test_http_by_name_goes_to_https_behind_caddy(make_client):
    client = make_client("172.17.0.1")
    for answer in (client.get("/login?x=1", headers={"Host": NAME}),
                   client.post("/login?x=1", headers={"Host": NAME}, data={"phone": "79991234567"})):
        assert answer.status_code == 307, "307 сохраняет метод и тело"
        assert answer.headers["Location"] == f"https://{NAME}/login?x=1"


@pytest.mark.parametrize("host", ["203.0.113.5", "203.0.113.5:80", "localhost:8501",
                                  "[2001:db8::1]:8501"])
def test_ip_and_localhost_stay_on_http(make_client, host):
    """http://<IP> — приложение 1.17 и старые ссылки, 127.0.0.1 — проверка выкладки."""
    assert make_client("172.17.0.1").get("/login", headers={"Host": host}).status_code == 200


def test_https_by_name_and_no_caddy_are_not_redirected(make_client):
    behind = make_client("172.17.0.1")
    assert behind.get("/login", headers={"Host": NAME}, environ_overrides=HTTPS).status_code == 200
    assert make_client().get("/login", headers={"Host": NAME}).status_code == 200


@pytest.mark.parametrize("host, named", [
    (NAME, True), (NAME + ":443", True), ("203.0.113.5", False), ("203.0.113.5:80", False),
    ("localhost", False), ("[2001:db8::1]:8501", False), ("[кривой", False), ("", False)])
def test_name_host(host, named):
    assert _is_name_host(host) is named
