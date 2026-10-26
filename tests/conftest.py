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


def sign_in(client, phone: str, pin: str = "482915", next: str = "/",
            install_pin: str | None = None, **kwargs):
    """Войти обоими шагами страницы входа; вернуть ответ шага 2 (302 — вошли).

    С 25.10.2026 вход — номер и личный PIN рабочего места, а не один номер (app/web/login.py).
    Помощник один на все тесты, которым вход нужен только как вход: новый номер здесь
    придумывает PIN, у номера с PIN он вводится — поэтому поле повтора шлётся всегда (на
    вводе сервер его не читает). Шаг 2 идёт тем же клиентом, и кука шага 1 едет в нём
    сама. install_pin — PIN установки на шаге 1, если тест его ставил; kwargs уходят в оба
    запроса (например, environ_base с другим адресом). Тесты самого входа шлют шаги
    руками, чтобы проверять каждый ответ отдельно.

    Повторов PBKDF2 на время входа 1000, а не боевые 200 000: хэш занимает ~80 мс на каждый
    вход, а входов в наборе тысячи, и на минуты набора это не нужно — число повторов
    лежит в самой записи PIN, и проверка идёт по нему же. Сам хэш проверяют тесты PIN
    (tests/test_login_pin.py), вызывая users.set_workspace_pin напрямую.
    """
    from app import users

    full, users.PIN_ITERATIONS = users.PIN_ITERATIONS, 1_000
    try:
        first = {"phone": phone, "next": next}
        if install_pin is not None:
            first["pin"] = install_pin
        client.post("/login", data=first, **kwargs)
        return client.post("/login", data={"step": "2", "phone": phone, "next": next,
                                           "my_pin": pin, "my_pin2": pin}, **kwargs)
    finally:
        users.PIN_ITERATIONS = full


@pytest.fixture(autouse=True)
def no_background_linking(monkeypatch):
    """Фоновое сопоставление с единой базой в тестах не запускается само.

    Вход и загрузка чеков зовут app/catalog/link.link_soon, а тот поднимает поток,
    который открыл бы настоящий data/catalog.db и пережил бы сам тест. Тесты,
    которым сопоставление нужно, зовут link.link_products прямо и со своим
    временным каталогом; проверить сам вызов можно, подменив link_soon.
    """
    from app.catalog import link

    monkeypatch.setattr(link, "link_soon", lambda phone, sync=False: False)
