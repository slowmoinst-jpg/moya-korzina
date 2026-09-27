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
его, покажет проверку «я не робот» ему самому, в его собственном браузере. Поэтому
живые запросы коннекторов к сетям домашнего выхода идут реже общего темпа —
connectors.home_exit.rate_limit_rps (pace_interval ниже, его берёт
app/connectors/cache.throttle), и через дом ходят только страницы с ценой и скрипты,
без которых их не отдают: ни картинок, ни видео, ни шрифтов, ни поиска, который сеть
отдаёт серверу и так.

ПРОВЕРКА — ЗНАЧИТ ПАУЗА ДЛЯ ВСЕХ. Так и случилось 27.09.2026: тринадцать простых
запросов карточек Дикси за полминуты, и Qrator стал показывать «Поставь галочку в поле
„Я не робот“» всему адресу владельца, настоящему браузеру тоже. Постучать снова
через минуту значит продлить метку. Поэтому отказ сети через дом записывается в
data/pace/<сеть>.refused (том общий, видят оба контейнера), и connectors.home_exit.
refusal_pause_min минут никто из приложения к этой сети через дом не ходит.
"""
from __future__ import annotations

import logging
import os
import socket
import time
from urllib.parse import urlsplit

from app import config

log = logging.getLogger(__name__)

HELLO = b"\x05\x01\x00"        # SOCKS5: одна схема входа — «без пароля»
AGREED = b"\x05\x00"           # сервер согласен на неё
HOME_RPS = 0.5                 # через дом — не чаще запроса в две секунды на сеть
PAUSE_MIN = 60.0               # после проверки «я не робот» через дом — час тишины
STATE_DIR = os.path.join(config.ROOT, "data", "pace")


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


def pace_interval(chain: str) -> float:
    """Сколько секунд держать между живыми запросами к сети домашнего выхода. 0 — сеть не через дом.

    Общий темп (connectors.rate_limit_rps) остаётся потолком для всех; этот — реже, потому
    что через дом идёт адрес владельца. Сеть настроена на выход, а туннеля нет — темп всё
    равно реже: запрос уйдёт напрямую, и спешить ему некуда.
    """
    if not configured(chain):
        return 0.0
    settings = config.get("connectors.home_exit") or {}
    try:
        rps = float(settings.get("rate_limit_rps") or HOME_RPS)
    except (TypeError, ValueError):
        rps = HOME_RPS
    return 1.0 / rps if rps > 0 else 0.0


# ---------- пауза после проверки ----------
def _pause_sec() -> float:
    settings = config.get("connectors.home_exit") or {}
    try:
        minutes = float(settings.get("refusal_pause_min") or PAUSE_MIN)
    except (TypeError, ValueError):
        minutes = PAUSE_MIN
    return max(0.0, minutes) * 60.0


def _refused_path(chain: str) -> str:
    return os.path.join(STATE_DIR, f"{chain}.refused")


def note_refusal(chain: str) -> None:
    """Сеть через дом показала проверку или отказ: пауза для всех процессов приложения."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(_refused_path(chain), "w", encoding="utf-8") as fh:
            fh.write(repr(time.time()))
    except OSError as exc:
        log.warning("%s: отметка паузы не записалась (%s)", chain, exc)
    log.warning("%s: через домашний выход сеть показала проверку или отказ — %.0f мин её "
                "через дом не спрашиваем, чтобы не продлевать метку на адресе владельца",
                chain, _pause_sec() / 60.0)


def paused_until(chain: str) -> float | None:
    """До какого момента (time.time) сеть домашнего выхода на паузе. None — не на паузе."""
    try:
        with open(_refused_path(chain), encoding="utf-8") as fh:
            refused = float(fh.read().strip())
    except (OSError, ValueError):
        return None
    until = refused + _pause_sec()
    # Метка из будущего — сбитые часы, а не пауза: держать сеть из-за неё незачем.
    if refused > time.time() + 60 or until <= time.time():
        return None
    return until


__all__ = ["configured", "alive", "for_chain", "requests_proxies", "pace_interval",
           "note_refusal", "paused_until"]
