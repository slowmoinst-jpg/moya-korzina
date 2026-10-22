"""Вход по номеру телефона, PIN владельца и витрина «Посмотреть демо».

Отдельным файлом от auth.py нарочно: auth отвечает на вопрос «чья база открыта»
и висит на каждом запросе, а здесь всего несколько страниц. Смешать их — значит
поставить редко меняющийся сторож рядом с часто меняющимся экраном.

ВХОД БЕЗ ПОДТВЕРЖДЕНИЯ, и на экране это сказано прямо. Решение владельца
16.09.2026: «просто по номеру телефона, без пароля, без подтверждения». Мера
временная и осознанная; код из СМС появится вместе с загрузкой чеков из кабинета
ФНС, где он нужен и так. Пока его нет, честная строка на экране — единственное,
что отличает осознанную меру от дыры, о которой забыли.

ЗАМОК ВХОДА (21.10.2026, план docs/plans/mobilnaya-versiya-2026-10-21.md, Z0).
Пока адрес открыт по http на публичном IP, «просто по номеру» значит, что чужой,
знающий номер, видит чеки, адрес и куки кабинетов сетей. Поэтому появились две
необязательные вещи, обе выключены по умолчанию (обратная совместимость):

    PIN ВЛАДЕЛЬЦА (app/users.py). Задан — вход требует его вместе с номером.
    Не задан — здесь всё как было. Хранится не открытым текстом (PBKDF2), снимается
    там же, где ставится, — на странице /pin (только для вошедшего).

    ПРЕДЕЛ ПОПЫТОК. Пять отказов за 15 минут с одного адреса — жёсткий отказ:
    следующая попытка не проверяется. Счёт один на все номера: PIN проверяется ДО
    номера, поэтому перебирающий меняет номер (или шлёт мусор в phone), и с ключом
    «номер + адрес» каждая новая строка получала бы свои пять попыток. Второй счёт —
    общий на установку: 20 отказов за 15 минут НЕ запирают вход (иначе четыре чужих
    адреса держали бы владельца снаружи), а добавляют паузу в 1,5 секунды перед
    обычной проверкой PIN: перебор с многих мест замедляется, а верный PIN проходит.
    Счётчики живут в памяти процесса: это защита от перебора, а не учёт, и перезапуск
    сервера их обнуляет — зато они не требуют ни базы, ни файла и не могут сломать вход.

ДЕМО — отдельное рабочее место с вымышленными покупками. Оно нужно, чтобы человек
увидел работающий расчёт до того, как принесёт свои чеки. Настоящие рабочие места
начинаются ПУСТЫМИ: подсовывать живому человеку выдуманные покупки в его
собственную историю нельзя.
"""
from __future__ import annotations

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
# Ключ — адрес, а НЕ пара «номер + адрес». PIN проверяется до номера (см. enter),
# поэтому перебирающий может слать любой номер или мусор в phone: с ключом по паре
# каждая новая строка получала бы новые пять попыток, и PIN подбирался бы заметно
# быстрее. Для счёта номер не нужен вовсе.
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
    """
    value = (raw or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


# ---------- предел попыток входа ----------

def _address() -> str:
    """Откуда пришли: адрес, один на все номера.

    Номера в ключе нет нарочно (см. выше). Пустой remote_addr — тоже ключ: за
    обратным прокси без заголовка адреса все такие запросы делят один счёт, и это
    лучше, чем не считать их вовсе.
    """
    return (request.remote_addr or "").strip()


def _recent(times: list[float], now: float) -> list[float]:
    """Только отказы внутри окна. Старшие забываются: «15 минут» — не «навсегда»."""
    return [at for at in times if now - at < FAIL_WINDOW_SEC]


def failure_counts() -> tuple[int, int]:
    """Сколько отказов внутри окна: (с этого адреса, на всей установке).

    Адресный счёт не даёт перебирать с одного места, общий — со многих: иначе
    перебор, разложенный по десятку адресов, не встретил бы предела вовсе. Заодно
    убираем протухшее: словарь адресов не должен расти вечно.
    """
    now = time.monotonic()
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


def clear_failures() -> None:
    """Удачный вход обнуляет счёт ЭТОГО адреса: человек вспомнил, перебор кончился.

    Общий счёт установки не трогаем: иначе один верный вход развязывал бы руки
    перебору, который идёт одновременно со многих мест.
    """
    address = _address()
    with _FAILS_LOCK:
        _FAILS.pop(address, None)


def _login_page(next_path: str, typed: str, error: str | None, status: int = 400):
    return render_template("login.html", next=next_path, error=error, typed=typed,
                           pin_required=users.pin_is_set()), status


# ---------- вход ----------

@bp.get("/login")
def login():
    if auth.current_phone():
        return redirect(_safe_next(request.args.get("next")))
    return render_template("login.html", next=_safe_next(request.args.get("next")),
                           error=None, typed="", pin_required=users.pin_is_set())


@bp.post("/login")
def enter():
    typed = request.form.get("phone", "")
    next_path = _safe_next(request.form.get("next"))
    address_fails, install_fails = failure_counts()

    # Адресный предел — жёсткий отказ: с одного места перебор не продолжается.
    if address_fails >= FAIL_LIMIT:
        log.warning("вход: предел попыток с адреса %s (%d отказов за 15 минут), "
                    "отказ без проверки", _address(), address_fails)
        return _login_page(next_path, typed, TOO_MANY, 429)

    # Общий счёт установки владельца не запирает: пауза замедляет перебор со многих
    # адресов, а PIN проверяется как обычно. Иначе четыре чужих адреса держали бы
    # владельца снаружи его же установки.
    if install_fails >= INSTALL_FAIL_LIMIT:
        log.warning("вход: общий предел установки (%d отказов за 15 минут), пауза %.1f с "
                    "и проверка PIN, адрес %s", install_fails, INSTALL_PAUSE_SEC, _address())
        time.sleep(INSTALL_PAUSE_SEC)

    # PIN проверяется ДО номера: посторонний, не знающий PIN, не должен узнавать
    # по разнице ответов, есть ли такой номер в установке и разрешён ли он.
    if users.pin_is_set() and not users.check_pin(request.form.get("pin", "")):
        note_failure()
        log.info("вход отклонён: неверный PIN")
        return _login_page(next_path, typed, BAD_PIN, 400)

    phone, complaint = users.resolve_login(typed)
    if not phone:
        note_failure()
        return _login_page(next_path, typed, complaint, 400)
    clear_failures()

    users.open_workspace(phone)
    users.deactivate()                 # база снимается тут же: её выберет before_request
    auth.sign_in(phone)
    log.info("вход: %s", users.display(phone))
    # Товары из чеков, ещё не привязанные к единой базе, находят себя в ней фоном
    # (app/catalog/link.py): привязанные не трогаются, вход не ждёт.
    try:
        from app.catalog import link

        link.link_soon(phone)
    except Exception:  # noqa: BLE001 — сопоставление не повод не пустить человека
        log.exception("единая база: не удалось запустить сопоставление при входе")
    return redirect(next_path)


@bp.get("/pin")
@auth.needs_phone
def pin():
    """Замок входа: поставить, сменить, снять. Только вошедшему.

    Что получилось, приходит не текстом, а короткой меткой (?saved=set|changed|
    cleared): страница после действия перечитывается переводом, и метка говорит,
    какое из трёх действий состоялось, — иначе пришлось бы угадывать по состоянию.
    """
    return render_template("pin.html", pin_set=users.pin_is_set(),
                           saved=(request.args.get("saved") or "").strip().lower() or None,
                           error=None)


@bp.post("/pin")
@auth.needs_phone
def pin_save():
    """Что сделать с замком. Нажатие приходит словом: clear — снять, иначе поставить.

    Действия разные, а поле PIN одно — и это не мелочь: разное поле для «сменить» и
    «снять» человек заполнял бы наугад, а сервер по пустому полю угадывал бы
    намерение. Пустое поле — ошибка, а не тихое снятие замка: случайно пустая форма
    не должна оставить установку открытой.

    ПРЕДЕЛ ЗДЕСЬ ТОТ ЖЕ, ЧТО НА ВХОДЕ. Неверный old_pin — тоже подбор PIN, и без
    счёта страница /pin стала бы обходным путём: перебирай старый PIN сколько хочешь,
    пока не подберёшь. Поэтому счёт — по адресу, до всякой проверки PIN; общий счёт
    установки, как и на входе, только замедляет проверку, а не запирает её.
    """
    current = users.pin_is_set()
    action = (request.form.get("do") or "").strip()
    new = request.form.get("new_pin", "")

    def page(message: str, status: int = 400):
        return render_template("pin.html", pin_set=current, saved=None, error=message), status

    address_fails, install_fails = failure_counts()
    if address_fails >= FAIL_LIMIT:
        log.warning("замок входа: предел попыток с адреса %s (%d отказов за 15 минут), "
                    "отказ без проверки", _address(), address_fails)
        return page(TOO_MANY, 429)
    if install_fails >= INSTALL_FAIL_LIMIT:
        log.warning("замок входа: общий предел установки (%d отказов за 15 минут), пауза "
                    "%.1f с и проверка PIN, адрес %s", install_fails, INSTALL_PAUSE_SEC,
                    _address())
        time.sleep(INSTALL_PAUSE_SEC)

    # Старый PIN обязателен и на смену, и на снятие: иначе открытая вкладка (или
    # украденная кука) снимает замок, который ставили именно от этого.
    if current:
        if not users.check_pin(request.form.get("old_pin", "")):
            note_failure()
            log.info("замок входа: отказ, старый PIN неверен")
            return page(BAD_PIN)
        # Старый PIN верен — это не подбор: счёт ЭТОГО адреса сбрасываем.
        clear_failures()

    if action == "clear":
        if not current:
            return page("Замок и так не поставлен")
        users.set_pin("")
        log.info("замок входа снят")
        return redirect(url_for("auth.pin", saved="cleared"))

    if not new.strip():
        return page("Введите новый PIN" if current else "Введите PIN — пустой замком не станет")

    try:
        users.set_pin(new)
    except ValueError as why:
        # Короткий или нецифровой PIN не ставим молча: человек должен понять,
        # почему замок не сработал, а не искать его на следующем входе.
        return page(str(why))
    log.info("замок входа сменён" if current else "замок входа поставлен")
    return redirect(url_for("auth.pin", saved="changed" if current else "set"))


@bp.post("/demo")
def demo():
    """Витрина. Заводится и наполняется при первом открытии, дальше живёт как есть."""
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
    """Налить демо-данные, если их ещё нет. Тихо: пустая витрина не повод падать."""
    from app import repo

    try:
        if repo.list_products():
            return
        import os
        import sys

        tools = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import seed

        seed.main()
    except Exception as exc:  # noqa: BLE001 — витрина без данных лучше, чем отказ войти
        log.warning("демо-данные не налились (%s)", exc)


__all__ = ["bp"]
