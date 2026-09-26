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


def _server(reply: bytes | None):
    """Локальный слушатель: на рукопожатие отвечает reply, None — закрывает молча."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        conn, _ = listener.accept()
        with conn:
            conn.recv(3)
            if reply:
                conn.sendall(reply)
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


def test_a_socks_server_that_agrees_is_alive():
    assert homeexit.alive(_server(b"\x05\x00")) is True


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
    assert "не отвечает" in caplog.text


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

