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
import threading
import warnings

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
    """Войти одним POST /login; вернуть ответ (302 — вошли)."""
    data = {"phone": phone, "next": next}
    if install_pin is not None:
        data["pin"] = install_pin
    return client.post("/login", data=data, **kwargs)


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


@pytest.fixture(autouse=True)
def no_warm_delay(monkeypatch):
    """Прогрев «Где дешевле» в тестах не ждёт затишья: иначе его поток просыпался бы в чужом тесте."""
    from app.web.phone import cheaper

    monkeypatch.setattr(cheaper, "WARM_DELAY_SEC", 0)


# Фоновые работы, которые кончаются сами и открывают рабочее место человека в своём потоке:
# прогрев «Где дешевле», расчёт корзины, поиск позиций, загрузка из ФНС, сдача корзины в сетях,
# сбор цен и передача корзины человеку. Остальные потоки (`korzina-api`, `pricewatch`) живут, пока
# жив процесс, и ждать их нельзя.
_BACKGROUND = ("cheaper-warm-", "korzina-", "link-", "fns-sync", "cartrun-", "prices-", "handover-")
_FOREVER = {"korzina-api"}
_GAVE_UP: set[threading.Thread] = set()     # кого не дождались, второй раз не ждём: каждый тест платил бы таймаут


@pytest.fixture(autouse=True)
def wait_for_background_threads(monkeypatch, request):
    """Фоновые потоки теста кончаются до отката его подмен путей (12.11.2026).

    Поток, переживший тест, читает `users.USERS_DIR` и `config.ROOT` в момент вызова: после отката
    это настоящая папка данных, а в следующем тесте с тем же номером телефона он открывал свежую базу
    рядом со входом и ломал его («database is locked» в `tests/test_phone_newcomer.py`). Так же в
    `data/users` попали папки тестовых номеров 7999000*.

    `monkeypatch` в списке ради порядка: фикстура, зависящая от него, закрывается раньше, чем он
    откатит подмены, то есть поток досчитывает ещё на путях теста.
    """
    yield
    for _ in range(5):      # заново после каждого круга: ждомый поток мог запустить следующий (например, прогрев)
        pending = [t for t in threading.enumerate()
                   if t.name.startswith(_BACKGROUND) and t.name not in _FOREVER
                   and t is not threading.current_thread() and t not in _GAVE_UP]
        if not pending:
            return
        for thread in pending:
            thread.join(timeout=10)
            if thread.is_alive():
                _GAVE_UP.add(thread)
                warnings.warn(f"поток {thread.name} не кончился за 10 с после {request.node.nodeid}",
                              RuntimeWarning, stacklevel=2)
