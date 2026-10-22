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

СБОРЩИК КАТАЛОГА СПРАШИВАЕТ МАРШРУТ ЗДЕСЬ ЖЕ (18.10.2026). Пятёрочка и Самокат ходят
витриной через Playwright, и без выхода серверу отдаётся та же заглушка ServicePipe:
маршрут «нет» значит «браузер не поднимаем вовсе». Поэтому route() отвечает не просто
«нет», а «нет, потому что…» — причину сборщик кладёт в CrawlBlocked и в статус обхода,
чтобы потом было видно, чего не было: туннеля или доступа. А pace_rps() держит темп
сети домашнего выхода и для сборщика — через дом идёт адрес владельца.
"""
from __future__ import annotations

import logging
import os
import socket
import ssl
import time
from urllib.parse import urlsplit

from app import config

log = logging.getLogger(__name__)

HELLO = b"\x05\x01\x00"        # SOCKS5: одна схема входа — «без пароля»
AGREED = b"\x05\x00"           # сервер согласен на неё
# Куда просим туннель соединиться при проверке. Хост ПОСТОРОННИЙ и намеренно не из
# наших сетей: проверяем не доступность магазина, а то, что выход проводит трафик.
# Порт 443 — потому что сети у нас по HTTPS, и проверять надо ровно то, что нужно.
CONNECT_TARGET = ("example.com", 443)
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
    """Проводит ли выход трафик — а именно HTTPS, которым ходят все наши сети.

    ПОЧЕМУ HTTPS, А НЕ ПРОСТО ПОРТ. Открытый порт моста ничего не говорит о проводе:
    мост принимает соединение и без туннеля за ним. Приветствие SOCKS — уже лучше, но и
    оно не обещает, что дальше пойдут байты. Сети у нас все по HTTPS, поэтому проверка
    доводит дело до настоящего TLS-рукопожатия через туннель: не прошло — значит для
    сборщика выход мёртв, как бы бодро он ни здоровался.

    САМА ПРОВЕРКА 19.10.2026 (чтобы не путать с прошлой бедой). Выход был жив и в тот
    день: HTTPS через него шёл за 0,1–0,2 с. А вот сети через него отвечали отказом —
    samokat.ru, 5ka.ru и api-web.samokat.ru дали 403 Forbidden с той же защитой, что и
    напрямую. То есть «нет ответа» тогда означало не мёртвый провод, а отказ магазина.

    Подлинность дальнего хоста здесь не проверяем (CERT_NONE): проверяется провод, а не
    хозяин ноутбука, и подменённый домашним антивирусом сертификат не должен делать
    выход «мёртвым». Настоящую проверку сертификатов делает браузер сборщика.
    """
    parts = urlsplit(url)
    if not parts.hostname or not parts.port:
        return False
    try:
        with socket.create_connection((parts.hostname, parts.port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(HELLO)
            if sock.recv(2) != AGREED:
                return False
            host, port = CONNECT_TARGET
            name = host.encode("idna")
            sock.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name
                         + int(port).to_bytes(2, "big"))
            answer = sock.recv(10)
            # 05 00 — туннель соединил; любой другой код или пустота — не проводит.
            if not (len(answer) >= 2 and answer[0] == 5 and answer[1] == 0):
                return False
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            with context.wrap_socket(sock, server_hostname=host) as tls:
                return tls.version() is not None
    except (OSError, ValueError):
        return False


def for_chain(chain: str) -> str | None:
    """Выход для этой сети прямо сейчас. None — идём без него, и в журнале сказано почему."""
    url = configured(chain)
    if not url:
        return None
    if alive(url):
        return url
    log.warning("%s: %s; цен этой сети в этот раз не будет", chain, _dead_note(url))
    return None


def _dead_note(url: str) -> str:
    return (f"домашний выход {url} не проводит трафик (порт открыт, а запросы в таймаут) —"
            " ноутбук владельца спит, вышел из системы или потерял сеть")


def route(chain: str) -> tuple[str | None, str]:
    """Маршрут сборщика каталога: (адрес выхода или None, причина словами).

    Отличие от for_chain — пауза после проверки (refusal_pause_min) и вторая половина
    ответа. Сборщику причина нужна не для журнала, а для CrawlBlocked и статуса обхода:
    без неё отказ выглядит одинаково и когда туннеля нет, и когда сеть закрыта, и
    разобраться потом нечем (ревью 18.10.2026, T5).
    """
    url = configured(chain)
    if not url:
        # Сеть не настроена на дом: идём напрямую, как ходили до появления выхода.
        return None, ""
    until = paused_until(chain)
    if until:
        minutes = max(1, int((until - time.time() + 59) / 60))
        note = (f"через дом сеть недавно показала проверку «я не робот» — пауза "
                f"ещё {minutes} мин")
        log.warning("%s: %s", chain, note)
        return None, note
    if alive(url):
        return url, ""
    note = _dead_note(url)
    log.warning("%s: %s; витрины этой сети в этот раз не будет", chain, note)
    return None, note


def pace_rps(chain: str, general: float | None = None) -> float:
    """Потолок темпа сборщика каталога этой сети, запросов в секунду.

    Через дом идёт адрес владельца, и сеть, заподозрив его, покажет проверку ему
    самому, в его браузере. Поэтому общий потолок (catalog.rate_limit_rps) остаётся
    потолком для всех, а сеть домашнего выхода идёт ещё реже — столько, сколько
    сказано в connectors.home_exit.rate_limit_rps. То же правило у коннекторов
    (app/connectors/cache.throttle), и по той же причине: сборщик и живой поиск не
    должны ходить в одну сеть каждый в своём темпе.
    """
    try:
        rps = float(general if general is not None
                    else config.get("catalog.rate_limit_rps", 1.0) or 1.0)
    except (TypeError, ValueError):
        rps = 1.0
    rps = max(rps, 0.05)
    interval = pace_interval(chain)
    if interval > 0:
        rps = min(rps, 1.0 / interval)
    return rps


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


__all__ = ["configured", "alive", "for_chain", "route", "pace_rps", "requests_proxies",
           "pace_interval", "note_refusal", "paused_until"]
