"""Вход по номеру телефона, PIN установки и витрина «Посмотреть демо».

Отдельным файлом от auth.py нарочно: auth отвечает на вопрос «чья база открыта»
и висит на каждом запросе, а здесь всего несколько страниц.

Вход по номеру: кода из СМС нет, кто первым пришёл с номером, тот его и занял.
Решение владельца 16.09.2026 («просто по номеру телефона, без пароля, без
подтверждения») и снова 27.10.2026: личный PIN рабочего места, введённый 25.10.2026,
убран («убери нафиг этот пинкод»). Записи login.pin в базах мест остались, их больше
никто не читает. У установки может быть общий PIN (users.set_pin): если он задан, вход
требует номер и PIN установки. Пять неверных попыток за 15 минут с одного адреса
закрывают вход на 15 минут.

ДЕМО — отдельное рабочее место с покупками, собранными из единой базы сетей (app/demo.py).
Оно нужно, чтобы человек увидел работающий расчёт до того, как принесёт свои чеки.
Настоящие рабочие места начинаются ПУСТЫМИ: подсовывать живому человеку выдуманные
покупки в его собственную историю нельзя. Демо одно на всех, поэтому оно только для
чтения (запись закрыта на двери, app/web/auth.py), а набрать «demo» в форме входа
нельзя — туда ведёт только кнопка.
"""
from __future__ import annotations

import ipaddress
import logging
import threading
import time

from flask import Blueprint, redirect, render_template, request, url_for

from app import users
from app.web import auth

log = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)

# Предел попыток входа: 5 отказов за 15 минут с ОДНОГО АДРЕСА — на все номера
# сразу, и 20 отказов за те же 15 минут на всю установку. Адресный предел — жёсткий
# отказ; общий — пауза перед обычной проверкой, чтобы не запереть владельца.
FAIL_LIMIT = 5
INSTALL_FAIL_LIMIT = 20
FAIL_WINDOW_SEC = 15 * 60
INSTALL_PAUSE_SEC = 1.5

# Почему в памяти процесса, а не в базе: перебор — событие минут, а не история.
# Запись в базу на каждую чужую догадку была бы работой, которой можно помешать
# ровно тем же потоком догадок. Перезапуск сервера счётчик обнуляет — это
# осознанно: иначе ошибка в ключе заперла бы владельца до перезапуска.
#
# Ключ — адрес, а НЕ пара «номер + адрес». PIN установки проверяется до номера (см. enter),
# поэтому перебирающий может слать любой номер или мусор в phone: с ключом по паре
# каждая новая строка получала бы новые пять попыток.
_FAILS: dict[str, list[float]] = {}
_INSTALL_FAILS: list[float] = []
_FAILS_LOCK = threading.Lock()

TOO_MANY = "Слишком много попыток, подождите 15 минут"
BAD_PIN = "Неверный PIN"


def _safe_next(raw: str | None) -> str:
    """Куда вернуть человека после входа. Чужие адреса сюда не пролезут.

    Открытый перевод по чужому адресу — классическая беда страниц входа: ссылка
    вида /login?next=https://зло.example приводит человека на подделку сразу
    после того, как он ввёл номер, и выглядит это как продолжение работы. Поэтому
    берём только путь внутри сайта, начинающийся с одной косой черты.

    Обратная косая и управляющие знаки (табуляция, перевод строки) не пускаются: браузер
    читает «/\\зло.example» как «//зло.example», а табуляцию из адреса выбрасывает, и
    «/<таб>/зло.example» тоже становится чужим адресом.
    """
    value = (raw or "").strip()
    if (value.startswith("/") and not value.startswith("//")
            and "\\" not in value and value.isprintable()):
        return value
    return "/"


# ---------- предел попыток входа ----------

def _address() -> str:
    """Откуда пришли: адрес, один на все номера.

    Номера в ключе нет нарочно (см. выше). Пустой remote_addr — тоже ключ: за
    обратным прокси без заголовка адреса все такие запросы делят один счёт, и это
    лучше, чем не считать их вовсе.

    У человека с IPv6 целая сеть /64, и сменой адреса в ней он снимал бы предел
    5 попыток PIN на адрес.
    """
    raw = (request.remote_addr or "").strip()
    if not raw:
        return ""
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return raw
    if ip.version == 6:
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return raw


def _recent(times: list[float], now: float) -> list[float]:
    """Только отказы внутри окна. Старшие забываются: «15 минут» — не «навсегда»."""
    return [at for at in times if now - at < FAIL_WINDOW_SEC]


def failure_counts(address: str | None = None) -> tuple[int, int]:
    """Сколько отказов внутри окна: (с этого адреса, на всей установке).

    Адресный счёт не даёт перебирать с одного места, общий — со многих: иначе
    перебор, разложенный по десятку адресов, не встретил бы предела вовсе. Заодно
    убираем протухшее: словарь адресов не должен расти вечно.
    """
    now = time.monotonic()
    if address is None:
        address = _address()
    with _FAILS_LOCK:
        times = _recent(_FAILS.get(address, []), now)
        if times:
            _FAILS[address] = times
        else:
            _FAILS.pop(address, None)
        install = _recent(_INSTALL_FAILS, now)
        _INSTALL_FAILS[:] = install
    return len(times), len(install)


def reserve_attempt(address: str | None = None) -> float | None:
    """Занять попытку атомарно перед сверкой PIN.

    Под _FAILS_LOCK проверяет адресный счёт за FAIL_WINDOW_SEC.
    Если он меньше FAIL_LIMIT, сразу записывает попытку в _FAILS[адрес] и в _INSTALL_FAILS
    и возвращает отметку времени. Если счёт полон (>= FAIL_LIMIT), возвращает None —
    сверку делать нельзя, это отказ (429).

    Зачем так: сервер отвечает пулом потоков, и пачка одновременных запросов с одного
    адреса иначе успевала бы пройти проверку до того, как первая неверная сверка PBKDF2
    запишет отказ. Заняв попытку до сверки, мы не пускаем больше FAIL_LIMIT сверок разом.
    Верный PIN снимает свою отметку через release_attempt; неверный оставляет её
    (это и есть отказ, второй раз note_failure не зовётся).
    """
    if address is None:
        address = _address()
    now = time.monotonic()
    with _FAILS_LOCK:
        times = _recent(_FAILS.get(address, []), now)
        if len(times) >= FAIL_LIMIT:
            _FAILS[address] = times
            return None
        stamp = now
        while stamp in times or stamp in _INSTALL_FAILS:
            stamp += 1e-6
        times.append(stamp)
        _FAILS[address] = times
        _INSTALL_FAILS[:] = _recent(_INSTALL_FAILS, now) + [stamp]
        if len(_FAILS) > 512:
            for key in [k for k, v in _FAILS.items() if not _recent(v, now)]:
                _FAILS.pop(key, None)
        return stamp


def release_attempt(stamp: float, address: str | None = None) -> None:
    """Верный PIN: снимаем ровно занятую попытку (это был не отказ).

    Снимается ровно одна запись из счёта адреса и из общего счёта установки.
    """
    if address is None:
        address = _address()
    with _FAILS_LOCK:
        times = _FAILS.get(address)
        if times and stamp in times:
            times.remove(stamp)
            if not times:
                _FAILS.pop(address, None)
        if stamp in _INSTALL_FAILS:
            _INSTALL_FAILS.remove(stamp)


def note_failure() -> None:
    """Записать отказ. Счёт идёт по адресу, номер в него не входит.

    Заодно убираем протухшее: словарь адресов не должен расти вечно.
    """
    now = time.monotonic()
    address = _address()
    with _FAILS_LOCK:
        times = _recent(_FAILS.get(address, []), now)
        times.append(now)
        _FAILS[address] = times
        _INSTALL_FAILS[:] = _recent(_INSTALL_FAILS, now) + [now]
        if len(_FAILS) > 512:
            for key in [k for k, v in _FAILS.items() if not _recent(v, now)]:
                _FAILS.pop(key, None)


def _pause_before_check() -> None:
    """Общий счёт установки полон — перед СВЕРКОЙ PIN ждём INSTALL_PAUSE_SEC; иначе не ждём.

    Звать надо непосредственно перед сверкой хэша и больше нигде. Пауза держит поток сервера
    (waitress раздаёт запросы пулом из нескольких потоков): поставь её на любой POST, и поток
    за 1,5 с отдавался бы пустому запросу, а несколько таких разом занимали бы весь пул и не
    пускали владельца. Сверка же всё равно стоит денег (PBKDF2) и считается отказом, если неверна.

    Поскольку попытка уже занята атомарно (reserve_attempt), в счёте установки она уже
    учтена: прошлая сумма отказов — install_fails - 1. Пауза начинается, когда прошлых
    отказов накопилось не меньше INSTALL_FAIL_LIMIT (то есть install_fails > INSTALL_FAIL_LIMIT).
    """
    install_fails = failure_counts()[1]
    if install_fails > INSTALL_FAIL_LIMIT:
        log.warning("вход: общий предел установки (%d отказов за 15 минут), пауза %.1f с "
                    "и проверка PIN, адрес %s", install_fails - 1, INSTALL_PAUSE_SEC, _address())
        time.sleep(INSTALL_PAUSE_SEC)


def _login_page(next_path: str, typed: str, error: str | None, status: int = 400):
    """Страница входа."""
    return render_template("login.html", next=next_path, error=error, typed=typed,
                           pin_required=users.pin_is_set()), status


# ---------- вход ----------

@bp.get("/login")
def login():
    if auth.current_phone():
        return redirect(_safe_next(request.args.get("next")))
    return render_template("login.html", next=_safe_next(request.args.get("next")),
                           error=None, typed="", pin_required=users.pin_is_set())


def _signed_in(phone: str, next_path: str):
    """Верный номер: рабочее место заводится, сессия открывается.

    Счёт неверных попыток адреса здесь НЕ обнуляется: иначе перебор PIN установки обходил бы
    предел, входя между пачками догадок. Окно в 15 минут истекает само (_recent).
    """
    users.open_workspace(phone)
    auth.sign_in(phone)
    users.deactivate()                 # базу на этот запрос снимаем: её выберет before_request
    log.info("вход: %s", users.display(phone))
    # Товары из чеков, ещё не привязанные к единой базе, находят себя в ней фоном
    # (app/catalog/link.py): привязанные не трогаются, вход не ждёт.
    try:
        from app.catalog import link

        link.link_soon(phone)
    except Exception:  # noqa: BLE001 — сопоставление не повод не пустить человека
        log.exception("единая база: не удалось запустить сопоставление при входе")
    return redirect(next_path)


@bp.post("/login")
def enter():
    typed = request.form.get("phone", "")
    next_path = _safe_next(request.form.get("next"))
    address_fails = failure_counts()[0]

    # Адресный предел — жёсткий отказ: с одного места перебор не продолжается.
    if address_fails >= FAIL_LIMIT:
        log.warning("вход: предел попыток с адреса %s (%d отказов за 15 минут), "
                    "отказ без проверки", _address(), address_fails)
        return _login_page(next_path, typed, TOO_MANY, 429)

    # PIN установки проверяется ДО номера: посторонний, не знающий PIN, не должен узнавать
    # по разнице ответов, есть ли такой номер в установке и разрешён ли он. Общий счёт
    # установки владельца не запирает, а только замедляет сверку (_pause_before_check):
    # иначе четыре чужих адреса держали бы владельца снаружи его же установки.
    if users.pin_is_set():
        attempt = reserve_attempt()
        if attempt is None:
            log.warning("вход: предел попыток с адреса %s, отказ перед сверкой PIN установки",
                        _address())
            return _login_page(next_path, typed, TOO_MANY, 429)
        _pause_before_check()
        if not users.check_pin(request.form.get("pin", "")):
            log.info("вход отклонён: неверный PIN")
            return _login_page(next_path, typed, BAD_PIN, 400)
        release_attempt(attempt)

    phone, complaint = users.resolve_login(typed)
    if not phone:
        note_failure()
        return _login_page(next_path, typed, complaint, 400)

    return _signed_in(phone, next_path)


@bp.post("/demo")
def demo():
    """Витрина. Заводится и наливается при первом открытии (и при смене версии витрины).

    Входят в неё только отсюда и только для чтения: запись закрыта на двери (auth.py).
    """
    users.open_workspace(users.DEMO)
    _fill_demo()
    users.deactivate()
    auth.sign_in(users.DEMO)
    return redirect("/")


@bp.post("/logout")
def logout():
    auth.sign_out()
    return redirect(url_for("auth.login"))


def _fill_demo() -> None:
    """Налить демо, если оно не налито этой версией (app/demo.py). Тихо: пустая витрина не повод падать.

    Зовётся при открытом рабочем месте demo, на каждое «Посмотреть демо»: налитая витрина
    узнаётся двумя лёгкими запросами, и ничего не происходит. Вымышленный набор на случай
    пустой единой базы уже внутри ensure. Модуль подключается внутри функции: имя demo
    здесь занято обработчиком выше.
    """
    try:
        from app import demo as showcase

        showcase.ensure()
    except Exception as exc:  # noqa: BLE001 — витрина без данных лучше, чем отказ войти
        log.warning("демо-данные не налились (%s)", exc)


__all__ = ["bp"]
