"""Домашний выход: к сетям, которые не пускают сервер, — через интернет владельца.

ЗАЧЕМ. Дикси отказывает серверу по его адресу: адрес дата-центра, «возможно, у вас
включён ВПН». Через домашний интернет владельца та же витрина открывается со всеми
ценами (замер 25.09.2026). Ноутбук владельца держит исходящее ssh-соединение с
сервером (tools/tunnel/laptop-setup.sh), и на сервере от этого появляется
SOCKS-выход, запросы через который уходят в интернет из дома.

КАК ДО НЕГО ДОТЯНУТЬСЯ ИЗ КОНТЕЙНЕРА. Туннель слушает только loopback хоста, а
приложение живёт в контейнерах, поэтому рядом стоит мост korzina-tunnel-relay
(tools/tunnel/relay.py): 172.17.0.1:1080 в сети docker → 127.0.0.1:1080 на хосте.
Адрес выхода и сети, которые ходят через него, — в config.yaml, connectors.home_exit.

ВЫХОД БЫВАЕТ ВЫКЛЮЧЕН, И ЭТО ОБЫЧНОЕ СОСТОЯНИЕ, А НЕ АВАРИЯ. Ноутбук спит, выключен,
владелец вышел из системы — туннеля нет. Тогда сеть обходится так, как обходилась
без него, а в журнал уходит, почему цен сегодня нет. Проверка — рукопожатие SOCKS5
(RFC 1928): мост принимает соединение и тогда, когда туннеля за ним нет, поэтому
одного открытого порта мало — отвечать должен сам ssh на ноутбуке.

ТЕМП ДЕРЖИМ ЧЕЛОВЕЧЕСКИЙ. Через этот выход идёт адрес владельца. Сеть, заподозрив
его, покажет проверку «я не робот» ему самому, в его собственном браузере.
"""
from __future__ import annotations

import logging
import socket
from urllib.parse import urlsplit

from app import config

log = logging.getLogger(__name__)

HELLO = b"\x05\x01\x00"        # SOCKS5: одна схема входа — «без пароля»
AGREED = b"\x05\x00"           # сервер согласен на неё


def configured(chain: str) -> str | None:
    """Адрес выхода, если эта сеть должна ходить через дом. Иначе None."""
    settings = config.get("connectors.home_exit") or {}
    url = str(settings.get("proxy") or "").strip()
    chains = settings.get("chains") or []
    if not url or chain not in chains:
        return None
    return url


def alive(url: str, timeout: float = 5.0) -> bool:
    """Отвечает ли выход — не порт моста, а сам SOCKS-сервер ssh за ним."""
    parts = urlsplit(url)
    if not parts.hostname or not parts.port:
        return False
    try:
        with socket.create_connection((parts.hostname, parts.port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(HELLO)
            return sock.recv(2) == AGREED
    except OSError:
        return False


def for_chain(chain: str) -> str | None:
    """Выход для этой сети прямо сейчас. None — идём без него, и в журнале сказано почему."""
    url = configured(chain)
    if not url:
        return None
    if alive(url):
        return url
    log.warning("%s: домашний выход %s не отвечает — ноутбук выключен, спит или владелец "
                "вышел из системы; цен этой сети в этот раз не будет", chain, url)
    return None


def requests_proxies(chain: str) -> dict[str, str] | None:
    """Словарь proxies для requests (с socks5h:// для разрешения имён на стороне выхода).

    None — выход не настроен или не отвечает; запросы пойдут напрямую.
    """
    url = for_chain(chain)
    if not url:
        return None
    req_url = url
    if req_url.startswith("socks5://"):
        req_url = "socks5h://" + req_url[len("socks5://"):]
    return {"http": req_url, "https": req_url}


__all__ = ["configured", "alive", "for_chain", "requests_proxies"]
