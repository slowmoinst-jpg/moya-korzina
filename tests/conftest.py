"""Общее для всех тестов: в сеть отсюда не ходят.

Правило есть с самого начала — «сеть не трогаем, внутренний API подменяем
monkeypatch», — но держалось оно на внимательности, и 16.09.2026 дважды
подвело. Коннектор Магнита научился спрашивать JSON-шлюз magnit.ru/webgate, а
тесты подменяли только прежний путь по вёрстке: `_get_html`. Ни один из них не
покраснел. Один молча брал живые цены Магнита и сверял их с ожиданием, которое
случайно совпало; второй проверял «модель зовётся раньше CSV», а на деле не
доходил ни до модели, ни до CSV.

Это худший сорт поломки: тест зелёный, а проверяет не то. Поэтому запрет
переехал из договорённости в код. Любая попытка открыть соединение наружу
падает с адресом, по которому сразу видно, кто и куда пошёл.

Локальные адреса оставлены: на них живут временные базы и локальные серверы,
и к «ходить в сеть» они отношения не имеют.
"""
from __future__ import annotations

import socket

import pytest

_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex

LOCAL = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}


def _host(address) -> str:
    return str(address[0]) if isinstance(address, tuple) else str(address)


def _blocked(original):
    def guard(self, address, *args, **kwargs):
        if _host(address) in LOCAL:
            return original(self, address, *args, **kwargs)
        raise AssertionError(
            f"тест пошёл в сеть: {address}. Живые запросы в тестах запрещены — "
            "подмените сетевой вызов через monkeypatch (см. tests/conftest.py)")
    return guard


@pytest.fixture(autouse=True, scope="session")
def no_network():
    socket.socket.connect = _blocked(_connect)
    socket.socket.connect_ex = _blocked(_connect_ex)
    yield
    socket.socket.connect = _connect
    socket.socket.connect_ex = _connect_ex
