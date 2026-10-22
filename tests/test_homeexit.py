"""Домашний выход: какие сети через него ходят и как понять, что он жив.

Жив — это когда отвечает сам SOCKS-сервер ssh на ноутбуке владельца. Мост
korzina-tunnel-relay принимает соединение и без туннеля за ним, поэтому открытого
порта мало: нужен ответ на рукопожатие SOCKS5. Здесь это проверяется на локальных
сокетах, в сеть тесты не ходят (tests/conftest.py).
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import socket
import ssl
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, homeexit  # noqa: E402


@pytest.fixture
def exit_settings(monkeypatch):
    def set_(proxy="socks5://127.0.0.1:1080", chains=("dixy",)):
        real = config.get
        monkeypatch.setattr(config, "get", lambda key, default=None: (
            {"proxy": proxy, "chains": list(chains)} if key == "connectors.home_exit"
            else real(key, default)))
    return set_


SOCKS_OK = b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"     # туннель соединил


def _tls_pair(tmp_path):
    """Серверный TLS-контекст с самоподписанным сертификатом — для проверки провода."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "example.com")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    return context


def _server(reply: bytes | None, connect: bytes | None = SOCKS_OK,
            tls: ssl.SSLContext | None = None):
    """Локальный слушатель-туннель: приветствие → reply, CONNECT → connect, дальше TLS.

    Три беды различимы, как в жизни:
      reply=None        — мост принимает соединение и молчит (ноутбук спит);
      connect=None      — приветствие прошло, на CONNECT молчание;
      tls=None          — CONNECT подтверждён, а HTTPS через туннель не идёт —
                          ровно так выглядел выход 19.10.2026 (порт 80 работал, 443 нет).
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        conn, _ = listener.accept()
        with conn:
            conn.recv(3)
            if reply:
                conn.sendall(reply)
                if connect:
                    conn.recv(64)          # CONNECT: адрес и порт
                    conn.sendall(connect)
                    if tls is not None:
                        try:
                            with tls.wrap_socket(conn, server_side=True) as secure:
                                secure.recv(64)
                                secure.sendall(b"HTTP/1.0 200 OK\r\n\r\n")
                        except OSError:
                            pass
        listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return f"socks5://127.0.0.1:{listener.getsockname()[1]}"


def test_only_listed_chains_go_through_home(exit_settings):
    exit_settings()
    assert homeexit.configured("dixy") == "socks5://127.0.0.1:1080"
    assert homeexit.configured("magnit") is None, "остальные сети идут как шли"


def test_no_proxy_means_no_home_exit(exit_settings):
    exit_settings(proxy="")
    assert homeexit.configured("dixy") is None


def test_agreement_alone_is_not_enough():
    """«Согласен без пароля» — ещё не живой выход: HTTPS через него не идёт."""
    assert homeexit.alive(_server(b"\x05\x00"), timeout=2) is False


def test_a_tunnel_that_carries_https_is_alive(tmp_path):
    """Провод есть: приветствие, CONNECT и настоящее TLS-рукопожатие через туннель."""
    assert homeexit.alive(_server(b"\x05\x00", tls=_tls_pair(tmp_path)), timeout=5) is True


def test_a_tunnel_that_carries_http_but_not_https_is_not_alive():
    """Синтетический случай: приветствие и CONNECT есть, а TLS через туннель не идёт.

    Проверка обязана доводить дело до TLS: сети у нас все по HTTPS, и «порт открыт» с
    «приветствие прошло» о проводе ничего не говорят. (На живом выходе 19.10.2026 HTTPS
    как раз шёл — отказывали сами сети: 403 и с сервера, и через дом.)
    """
    assert homeexit.alive(_server(b"\x05\x00", tls=None), timeout=2) is False


def test_a_tunnel_silent_on_connect_is_not_alive():
    assert homeexit.alive(_server(b"\x05\x00", connect=None), timeout=2) is False


def test_a_tunnel_that_refuses_the_connection_is_not_alive():
    assert homeexit.alive(_server(b"\x05\x00", connect=b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00"),
                          timeout=2) is False


def test_an_open_port_without_a_tunnel_behind_is_not_alive():
    """Так выглядит мост, когда ноутбук спит: соединение есть, ответа нет."""
    assert homeexit.alive(_server(None), timeout=2) is False


def test_nothing_listening_is_not_alive():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    assert homeexit.alive(f"socks5://127.0.0.1:{port}", timeout=2) is False


def test_a_dead_exit_is_named_and_skipped(exit_settings, caplog):
    exit_settings(proxy=_server(None))
    with caplog.at_level("WARNING"):
        assert homeexit.for_chain("dixy") is None
    assert "не проводит трафик" in caplog.text


def test_the_relay_carries_bytes_both_ways():
    """Мост из сети контейнеров к туннелю на хосте перекладывает байты, не читая их."""
    spec = importlib.util.spec_from_file_location("relay", os.path.join(ROOT, "tools", "tunnel", "relay.py"))
    relay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)

    async def scenario():
        async def echo(reader, writer):
            writer.write(await reader.read(100))
            await writer.drain()
            writer.close()

        target = await asyncio.start_server(echo, "127.0.0.1", 0)
        target_port = target.sockets[0].getsockname()[1]
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        relay_port = probe.getsockname()[1]
        probe.close()
        task = asyncio.create_task(relay.serve(("127.0.0.1", relay_port), ("127.0.0.1", target_port)))
        await asyncio.sleep(0.2)
        reader, writer = await asyncio.open_connection("127.0.0.1", relay_port)
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        got = await asyncio.wait_for(reader.read(100), 5)
        writer.close()
        task.cancel()
        target.close()
        return got

    assert asyncio.run(scenario()) == b"\x05\x01\x00"


def test_requests_proxies_format(exit_settings, monkeypatch):
    """requests_proxies переводит socks5:// в socks5h:// для разрешения имён на стороне туннеля."""
    exit_settings(proxy="socks5://172.17.0.1:1080", chains=("magnit", "dixy"))
    monkeypatch.setattr(homeexit, "alive", lambda url, timeout=5.0: True)
    proxies = homeexit.requests_proxies("magnit")
    assert proxies == {
        "http": "socks5h://172.17.0.1:1080",
        "https": "socks5h://172.17.0.1:1080",
    }


def test_requests_proxies_none_when_dead(exit_settings, monkeypatch):
    exit_settings(proxy="socks5://172.17.0.1:1080", chains=("magnit",))
    monkeypatch.setattr(homeexit, "alive", lambda url, timeout=5.0: False)
    assert homeexit.requests_proxies("magnit") is None

