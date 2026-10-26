"""Вход по номеру и личному PIN, PIN установки и витрина «Посмотреть демо».

Отдельным файлом от auth.py нарочно: auth отвечает на вопрос «чья база открыта»
и висит на каждом запросе, а здесь всего несколько страниц. Смешать их — значит
поставить редко меняющийся сторож рядом с часто меняющимся экраном.

ВХОД В ДВА ШАГА (25.10.2026). До этого дня вход был по одному номеру — решение
владельца 16.09.2026: «просто по номеру телефона, без пароля, без подтверждения».
Владелец раздаёт приложение знакомым, а при таком входе знакомый, знающий его номер,
открывал чеки, адрес, входы в магазины и ключ ФНС. Теперь у каждого рабочего места свой
PIN (app/users.py), и страница входа спрашивает его вторым шагом — без JS, сервер рисует
обе половины одним шаблоном: ту же страницу показывает WebView приложения.

    ШАГ 1 — номер (и PIN установки, если он задан: см. ниже). Проверяется всё, что
    проверялось раньше. Рабочее место на этом шаге НЕ заводится: случайные номера
    наполнили бы диск пустыми базами. Прошедший шаг запоминается в подписанной куке
    сессии (номер и время, 10 минут).

    ШАГ 2 — личный PIN: «Введите PIN», если он у места есть, и «Придумайте PIN» (PIN и
    повтор) для нового номера и для старого места без PIN — так у владельца до первого
    входа после выкладки. Какой из двух случаев, сервер решает заново по базе: скрытому
    полю формы он не верит. Кука шага 1 нужна, чтобы шаг 2 нельзя было прислать мимо
    шага 1 (с его PIN установки и пределом попыток) и чтобы PIN установки не лежал в
    скрытом поле страницы. Рабочее место заводится только верным шагом 2.

Кода из СМС по-прежнему нет: номер никто не подтверждает, и кто первым пришёл с новым
номером, тот его и занял. А шаг 2 выдаёт, есть ли у номера PIN, — номера можно
перебирать; для личного сервера на знакомых это допустимо (номер не секрет, а PIN
подбору не поддаётся из-за пределов ниже).

ЗАМОК ВХОДА (21.10.2026, план docs/plans/mobilnaya-versiya-2026-10-21.md, Z0).
Пока адрес открыт по http на публичном IP, чужой, знающий номер, видел бы чеки, адрес
и куки кабинетов сетей. Поэтому у входа две необязательные части, обе выключены по
умолчанию (обратная совместимость):

    PIN УСТАНОВКИ (app/users.py) — общий замок, один на все рабочие места. Задан —
    шаг 1 требует его вместе с номером. Не задан — этой двери нет, остаётся личный
    PIN. Хранится не открытым текстом (PBKDF2). Ставит, меняет и снимает его только владелец
    с сервера (users.set_pin): на странице /pin его нет вовсе, потому что его знает каждый,
    кого пустили, и любой из них запер бы им всех остальных.

    ПРЕДЕЛ ПОПЫТОК. Пять отказов за 15 минут с одного адреса — жёсткий отказ:
    следующая попытка не проверяется. Счёт один на все номера: PIN проверяется ДО
    номера, поэтому перебирающий меняет номер (или шлёт мусор в phone), и с ключом
    «номер + адрес» каждая новая строка получала бы свои пять попыток. Второй счёт —
    общий на установку: 20 отказов за 15 минут НЕ запирают вход (иначе четыре чужих
    адреса держали бы владельца снаружи), а добавляют паузу в 1,5 секунды перед
    сверкой PIN: перебор с многих мест замедляется, а верный PIN проходит. Пауза стоит
    только перед настоящей сверкой хэша (_pause_before_check), а не на любом POST: пустые
    запросы не должны занимать потоки сервера.
    Считаются неверный PIN установки, неверный личный PIN, неверный старый PIN на /pin и
    «это не номер»; истёкший шаг 1, короткий PIN и несовпавший повтор — не догадки,
    их не считаем. Удачный вход счёт адреса НЕ обнуляет: иначе перебор чужого PIN обходил
    бы предел, входя между пачками догадок в своё место; окно в 15 минут истекает само.
    Счётчики живут в памяти процесса: это защита от перебора, а не учёт, и перезапуск
    сервера их обнуляет — зато они не требуют ни базы, ни файла и не могут сломать вход.

ДЕМО — отдельное рабочее место с покупками, собранными из единой базы сетей (app/demo.py).
Оно нужно, чтобы человек увидел работающий расчёт до того, как принесёт свои чеки.
Настоящие рабочие места начинаются ПУСТЫМИ: подсовывать живому человеку выдуманные
покупки в его собственную историю нельзя. Демо одно на всех, поэтому оно только для
чтения (запись закрыта на двери, app/web/auth.py); личного PIN у него нет, а набрать
«demo» в форме входа нельзя — туда ведёт только кнопка.
"""
from __future__ import annotations

import ipaddress
import logging
import threading
import time

from flask import Blueprint, g, redirect, render_template, request, session, url_for

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
RESTART = "Начните вход заново"
TAKEN = "Пока вы вводили PIN, для этого номера уже придумали свой. Введите его."

# Шаг 1 входа помнит подписанная кука сессии: номер и время. Подделать её нельзя, а срок
# нужен, чтобы вкладка, забытая открытой, не держала дверь шага 2 вечно.
KEY_LOGIN_PHONE = "login_phone"
KEY_LOGIN_AT = "login_at"
LOGIN_STEP_TTL_SEC = 10 * 60


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
    за 1,5 с отдавался бы пустому запросу — шагу 2 без шага 1, короткому PIN, несовпавшему
    повтору, — а несколько таких разом занимали бы весь пул и не пускали владельца. Сверка же
    всё равно стоит денег (PBKDF2) и считается отказом, если неверна.

    Поскольку попытка уже занята атомарно (reserve_attempt), в счёте установки она уже
    учтена: прошлая сумма отказов — install_fails - 1. Пауза начинается, когда прошлых
    отказов накопилось не меньше INSTALL_FAIL_LIMIT (то есть install_fails > INSTALL_FAIL_LIMIT).
    """
    install_fails = failure_counts()[1]
    if install_fails > INSTALL_FAIL_LIMIT:
        log.warning("вход: общий предел установки (%d отказов за 15 минут), пауза %.1f с "
                    "и проверка PIN, адрес %s", install_fails - 1, INSTALL_PAUSE_SEC, _address())
        time.sleep(INSTALL_PAUSE_SEC)


def _login_page(next_path: str, typed: str, error: str | None, status: int = 400, *,
                login_phone: str | None = None, creating: bool = False,
                notice: str | None = None):
    """Страница входа. login_phone задан — это шаг 2 (creating: придумать PIN, иначе ввести).

    Номер шага 2 уходит в шаблон НЕ под именем phone: так зовётся вошедший человек
    (auth._who), и рама страницы по нему рисует меню и кнопку «Выйти».
    """
    return render_template("login.html", next=next_path, error=error, typed=typed,
                           pin_required=users.pin_is_set(), login_phone=login_phone,
                           login_label=users.display(login_phone), creating=creating,
                           notice=notice), status


# ---------- вход ----------

@bp.get("/login")
def login():
    if auth.current_phone():
        return redirect(_safe_next(request.args.get("next")))
    return render_template("login.html", next=_safe_next(request.args.get("next")),
                           error=None, typed="", pin_required=users.pin_is_set())


def _step_one_is_fresh(phone: str) -> bool:
    """Прошёл ли в ЭТОЙ сессии шаг 1 именно этот номер и не больше десяти минут назад."""
    at = session.get(KEY_LOGIN_AT)
    return (session.get(KEY_LOGIN_PHONE) == phone and isinstance(at, (int, float))
            and time.time() - at <= LOGIN_STEP_TTL_SEC)


def _signed_in(phone: str, next_path: str):
    """Верный личный PIN: рабочее место заводится, кука выдаётся.

    Счёт неверных попыток адреса здесь НЕ обнуляется. Иначе перебор чужого PIN обходил бы
    предел: четыре догадки, вход в СВОЁ место с этого же адреса — и снова пять попыток.
    Окно в 15 минут истекает само (_recent).
    """
    users.open_workspace(phone)
    auth.sign_in(phone)                # кука помнит отпечаток PIN, поэтому база пока открыта
    users.deactivate()                 # и снимается тут же: её выберет before_request
    log.info("вход: %s", users.display(phone))
    # Товары из чеков, ещё не привязанные к единой базе, находят себя в ней фоном
    # (app/catalog/link.py): привязанные не трогаются, вход не ждёт.
    try:
        from app.catalog import link

        link.link_soon(phone)
    except Exception:  # noqa: BLE001 — сопоставление не повод не пустить человека
        log.exception("единая база: не удалось запустить сопоставление при входе")
    return redirect(next_path)


def _second_step(typed: str, next_path: str):
    """Шаг 2: личный PIN. Предел попыток уже проверен в enter, как и на шаге 1.

    Здесь заново и на сервере: был ли шаг 1 (номер в куке сессии совпадает с присланным
    и не стар), годится ли номер (список разрешённых мог смениться за десять минут) и
    есть ли у места PIN. Скрытое поле формы ничего из этого не решает: подделанная
    форма «введите PIN» к месту без PIN не даёт придумать его в обход шага 1.

    Истёкший или пропущенный шаг 1 — не догадка, счёт отказов не трогаем. Короткий PIN и
    несовпавший повтор — тоже не догадка: PIN ещё только придумывают.
    """
    posted = users.normalize_phone(typed)
    if not posted or not _step_one_is_fresh(posted):
        return _login_page(next_path, typed, RESTART, 400)
    phone, complaint = users.resolve_login(posted)
    if not phone:
        return _login_page(next_path, typed, complaint, 400)

    pin = request.form.get("my_pin", "")
    if users.workspace_pin_is_set(phone):
        attempt = reserve_attempt()
        if attempt is None:
            log.warning("вход: предел попыток с адреса %s, отказ перед сверкой личного PIN",
                        _address())
            return _login_page(next_path, typed, TOO_MANY, 429)
        _pause_before_check()
        if not users.check_workspace_pin(phone, pin):
            log.info("вход отклонён: неверный личный PIN")
            return _login_page(next_path, "", BAD_PIN, 400, login_phone=phone)
        release_attempt(attempt)
        return _signed_in(phone, next_path)

    def again(error: str):
        return _login_page(next_path, "", error, 400, login_phone=phone, creating=True)

    try:
        value = users.check_new_pin(pin)
    except ValueError as why:
        return again(str(why))
    if request.form.get("my_pin2", "").strip() != value:
        return again(users.PIN_MISMATCH)
    if not users.set_workspace_pin(phone, value, keep_existing=True):
        # Двое пришли с одним новым номером, и первый успел придумать PIN: второй не
        # затирает его, а вводит — и проверяется как обычный вошедший.
        return _login_page(next_path, "", None, 400, login_phone=phone, notice=TAKEN)
    log.info("личный PIN придуман")
    return _signed_in(phone, next_path)


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

    if request.form.get("step") == "2":
        return _second_step(typed, next_path)

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

    # Шаг 1 пройден. Счёт отказов здесь не обнуляется (и нигде не обнуляется удачей): иначе
    # четыре догадки на шаге 2, один верный номер на шаге 1 — и счёт пошёл бы заново, а
    # личный PIN подбирался бы без предела. Рабочее место не заводится: его PIN спрашивают.
    session[KEY_LOGIN_PHONE] = phone
    session[KEY_LOGIN_AT] = time.time()
    return _login_page(next_path, "", None, 200, login_phone=phone,
                       creating=not users.workspace_pin_is_set(phone))


def _pin_page(error: str | None = None, status: int = 200, saved: str | None = None):
    """Страница /pin; что на ней есть, решает состояние: есть ли у места личный PIN.

    Рабочее место вошедшего уже открыто (auth._pick_workspace), поэтому личный PIN
    читается прямо из его настроек, без повторного открытия базы.
    """
    from app import repo

    return render_template("pin.html",
                           my_pin_set=bool(repo.get_setting(users.LOGIN_PIN_KEY)),
                           next=_safe_next(request.values.get("next")),
                           saved=saved, error=error), status


@bp.get("/pin")
@auth.needs_phone
def pin():
    """Личный PIN этого места: придумать или сменить (со старым). Только вошедшему.

    Сюда же ведёт перевод из auth._pick_workspace для места, заведённого до 25.10.2026 и
    вошедшего по старой куке без PIN: пока PIN не придуман, дальше этой страницы он не пускает.

    Что получилось, приходит не текстом, а короткой меткой (?saved=my_changed): страница
    после действия перечитывается переводом, и метка говорит, что действие состоялось, —
    иначе пришлось бы угадывать по состоянию.
    """
    return _pin_page(saved=(request.args.get("saved") or "").strip().lower() or None)


@bp.post("/pin")
@auth.needs_phone
def pin_save():
    """Придумать или сменить личный PIN этого места. Нажатие приходит словом do=my.

    PIN УСТАНОВКИ ОТСЮДА НЕ ТРОГАЕТСЯ: ни поставить, ни сменить, ни снять. Его знает каждый, кого
    пустили, и любой из них, вошедший по своему PIN, запер бы им всех остальных (или снял бы
    общий замок, который владелец поставил от чужих). Им управляет владелец с сервера
    (users.set_pin). Любое другое слово получает отказ словами и ничего не меняет и не считает.

    ПРЕДЕЛ ЗДЕСЬ ТОТ ЖЕ, ЧТО НА ВХОДЕ. Неверный старый PIN — тоже подбор PIN, и без
    счёта страница /pin стала бы обходным путём: перебирай старый PIN сколько хочешь,
    пока не подберёшь. Поэтому счёт — по адресу, до всякой проверки PIN; общий счёт
    установки, как и на входе, только замедляет проверку, а не запирает её.

    СМЕНА PIN ОТЗЫВАЕТ ДРУГИЕ УСТРОЙСТВА. Кука каждого устройства помнит отпечаток записи PIN
    (auth.pin_stamp), и после смены чужие куки с прежним отпечатком выходят на вход заново.
    Это устройство получает новый отпечаток тут же и остаётся внутри.
    """
    def page(message: str, status: int = 400):
        return _pin_page(error=message, status=status)

    address_fails = failure_counts()[0]
    if address_fails >= FAIL_LIMIT:
        log.warning("замок входа: предел попыток с адреса %s (%d отказов за 15 минут), "
                    "отказ без проверки", _address(), address_fails)
        return page(TOO_MANY, 429)
    if (request.form.get("do") or "").strip() != "my":
        return page("PIN установки ставит, меняет и снимает только владелец — на сервере.")

    from app import repo

    # Есть PIN или нет, решает база, а не форма: форма «придумать» не должна затереть
    # чужой PIN, а старый PIN обязателен на смену — иначе открытая вкладка (или
    # украденная кука) меняла бы PIN, поставленный именно от этого.
    has = bool(repo.get_setting(users.LOGIN_PIN_KEY))
    if has:
        attempt = reserve_attempt()
        if attempt is None:
            log.warning("замок входа: предел попыток с адреса %s, отказ перед сверкой старого PIN",
                        _address())
            return page(TOO_MANY, 429)
        _pause_before_check()
        if not users.check_workspace_pin(g.phone, request.form.get("my_old", "")):
            log.info("личный PIN: отказ, старый PIN неверен")
            return page(BAD_PIN)
        release_attempt(attempt)
    try:
        value = users.check_new_pin(request.form.get("my_pin", ""))
    except ValueError as why:
        return page(str(why))
    if request.form.get("my_pin2", "").strip() != value:
        return page(users.PIN_MISMATCH)
    if not users.set_workspace_pin(g.phone, value, keep_existing=not has):
        return page("PIN уже придуман — обновите страницу и смените его, зная прежний.")
    session[auth.KEY_PIN_STAMP] = auth.pin_stamp()
    if has:
        log.info("личный PIN сменён")
        return redirect(url_for("auth.pin", saved="my_changed"))
    log.info("личный PIN придуман")
    return redirect(_safe_next(request.form.get("next")))   # туда, откуда выслали придумывать


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
