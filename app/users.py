"""Рабочие места: каждому номеру телефона — своя база.

Зачем. Приложение выросло из установки «для себя», где база одна и человек один.
Как только адрес появился на сервере, туда придут разные люди, и их чеки, корзины и
карты не должны лежать в одной куче. Простейшее разделение, которое не требует
переписывать ни репозиторий, ни экраны, — отдельный файл базы на человека:
data/users/<номер>/basket.db. Схема та же, справочник магазинов тот же, а вот
покупки, товары, сопоставления, корзины и адрес — свои.

Как это работает. Путь к базе подставляется через контекст (app.config.set_db_override)
на каждом прогоне интерфейса, до первого обращения к репозиторию. Репозиторий по-прежнему
зовёт config.db_path() и получает базу того человека, чей прогон идёт. Потоков
приложение не заводит, поэтому контекст не теряется; если заведёт — контекст надо
будет передать в поток явно (contextvars.copy_context().run).

Вход пока БЕЗ ПОДТВЕРЖДЕНИЯ: решение владельца 16.09.2026 — «просто по номеру
телефона, без пароля, без подтверждения». Значит, кто знает номер, тот и войдёт.
Это осознанная временная мера, и на экране входа об этом сказано словами; подтверждение
по коду появится вместе с загрузкой чеков из кабинета ФНС, где код из СМС нужен и так.

ЗАМОК ВХОДА (21.10.2026, план docs/plans/mobilnaya-versiya-2026-10-21.md, Z0).
Пока адрес открыт по http на публичном IP, вход по одному номеру значит, что человек,
знающий номер, видит чужие чеки, адрес и куки кабинетов сетей. Поэтому у установки
появился НЕОБЯЗАТЕЛЬНЫЙ PIN владельца: он один на установку (это личный сервер, а не
сервис), лежит на томе данных рядом с базами (data/pin.json) и хранится не открытым
текстом — PBKDF2-HMAC-SHA256, 200 000 повторов, своя соль. Пустой PIN = замка нет, и
вход работает ровно как прежде: обратная совместимость обязательна. Вторая половина
замка — список разрешённых номеров (users.allowed в config.yaml): без него на установку
войдёт любой, кто знает любой номер и PIN. Пустой список = пускать всех, как было.

ССЫЛКА СО СВОИМ СЕКРЕТОМ (21.10.2026, та же задача Z0). Закладке «Передать вход» кука
недоступна: она уходит на сайт сети и возвращается на наш адрес с номером в самом
адресе (?phone=…&vhod=…). Раньше такой адрес открывал рабочее место по одному номеру,
то есть всякий, кто знал номер, получал чеки, адрес и куки кабинетов. Запереть этот
путь на PIN нельзя — PIN в закладку не вписать. Поэтому у рабочего места есть свой
секрет (link.secret в его настройках): он едет в ссылке параметром t, а ссылка без
него не открывает ничего. Секрет живёт в базе рабочего места, а не в git.

Демо. Рабочее место «demo» — витрина с вымышленным чеком и условиями карт: оно
наполняется само при первом открытии. Настоящие рабочие места начинаются ПУСТЫМИ:
подсовывать живому человеку вымышленные покупки в его собственную историю нельзя.
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading

from app import config

log = logging.getLogger(__name__)

DEMO = "demo"
USERS_DIR = os.path.join(config.ROOT, "data", "users")

# Замок входа: файл на томе данных (в git не попадает — он в .gitignore) и
# параметры хранения PIN. Число повторов — не украшение: оно превращает перебор
# шести цифр из быстрого в очень долгий, а файл с хэшем всё равно не отдаётся наружу.
PIN_FILE = "pin.json"
PIN_ITERATIONS = 200_000
PIN_MIN_DIGITS = 6                    # четыре цифры при 20 отказах в 15 минут — дни перебора
_PIN_LOCK = threading.Lock()

# Отказы set_pin. Текст идёт человеку на страницу /pin (app/web/login.py), поэтому
# он объясняет, что не так, а не просто «ошибка».
PIN_NOT_DIGITS = "PIN может состоять только из цифр."
PIN_TOO_SHORT = "PIN короче шести цифр — такой слишком легко подобрать."

# Секрет ссылки входа: ключ в настройках рабочего места (таблица settings его базы).
# Имя с точкой — как у api.secret и ключей кабинета ФНС: видно, что настройка
# служебная. Открытым текстом он лежит только в базе самого человека, а в ссылке —
# параметром t; в git не попадает никогда.
LINK_SECRET_KEY = "link.secret"
LINK_SECRET_BYTES = 16                        # 128 бит: 32 шестнадцатеричных знака
_LINK_LOCK = threading.Lock()

# Один текст на два разных отказа — «это не номер» и «этого номера нет в списке
# разрешённых». Разные тексты сказали бы постороннему, что номер существует.
NOT_A_PHONE = ("Это не похоже на номер: нужно десять цифр после кода страны, "
               "например +7 999 123-45-67.")

_CURRENT: contextvars.ContextVar[str | None] = contextvars.ContextVar("korzina_phone", default=None)


def normalize_phone(raw: str | None) -> str | None:
    """«+7 (999) 123-45-67» -> «79991234567»; «demo» -> «demo»; иначе None.

    Принимаем всё, что человек привык вводить: с плюсом, скобками, дефисами, с
    восьмёркой вместо семёрки, без кода страны вовсе. Не принимаем то, что телефоном
    быть не может: короче десяти цифр или длиннее одиннадцати. Один номер должен
    давать одну и ту же папку, иначе человек с «8 999…» и «+7 999…» получит две базы.
    """
    text = (raw or "").strip()
    if text.lower() == DEMO:
        return DEMO
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits[0] in "78":
        return "7" + digits[1:]
    if len(digits) == 10:
        return "7" + digits
    return None


def display(phone: str | None) -> str:
    """Как номер показывается в шапке: «+7 999 123-45-67» или «Демо»."""
    if not phone:
        return ""
    if phone == DEMO:
        return "Демо"
    if len(phone) == 11:
        return f"+{phone[0]} {phone[1:4]} {phone[4:7]}-{phone[7:9]}-{phone[9:]}"
    return phone


def workspace_dir(phone: str) -> str:
    return os.path.join(USERS_DIR, phone)


def db_path_for(phone: str) -> str:
    return os.path.join(workspace_dir(phone), "basket.db")


def exists(phone: str) -> bool:
    return os.path.exists(db_path_for(phone))


def activate(phone: str) -> str:
    """Направить репозиторий в базу этого человека. Возвращает путь к базе.

    Вызывается на КАЖДОМ прогоне интерфейса: контекст живёт в потоке прогона, а
    Streamlit между прогонами его не хранит.
    """
    path = db_path_for(phone)
    config.set_db_override(path)
    _CURRENT.set(phone)
    return path


def deactivate() -> None:
    config.set_db_override(None)
    _CURRENT.set(None)


def current() -> str | None:
    """Чьё рабочее место активно в этом контексте; None — общая база."""
    return _CURRENT.get()


def open_workspace(phone: str) -> str:
    """Создать (если нет) и открыть базу человека: папка, схема, справочник магазинов.

    Идемпотентно. Демо-данные здесь НЕ наливаются — это забота интерфейса, и только
    для рабочего места demo.
    """
    from app.db import init_db

    path = activate(phone)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    init_db()
    return path


def list_workspaces() -> list[str]:
    """Номера, у которых уже есть база. Нужно утилитам, а не интерфейсу."""
    if not os.path.isdir(USERS_DIR):
        return []
    return sorted(name for name in os.listdir(USERS_DIR)
                  if os.path.exists(db_path_for(name)))


def _installation_db() -> str:
    """База УСТАНОВКИ, а не человека: путь из настроек, без подмены на рабочее место.

    config.db_path() на каждом запросе отдаёт базу того, чьё рабочее место открыто
    (app.config.set_db_override). Для замка входа это неверная база: PIN один на
    установку. Возьми мы config.db_path() — PIN, поставленный со страницы /pin, лёг
    бы в папку человека, а на странице входа искался бы в папке установки, и замок
    молча перестал бы пускать владельца в его же установку.
    """
    path = config.get("db_path", "data/basket.db")
    return path if os.path.isabs(path) else os.path.join(config.ROOT, path)


def pin_path() -> str:
    """Где лежит замок входа. Считается на каждый вызов: config.ROOT подменяют тесты.

    Файл лежит РЯДОМ С БАЗОЙ, на томе данных, а не в корне установки. В контейнере
    корень /app — эфемерный слой образа, а том только /app/data (Dockerfile,
    DEPLOY.md): PIN, записанный в /app, исчезал бы при каждом пересоздании
    контейнера, и установка молча открывалась бы по одному номеру. Каталога может
    ещё не быть (совсем свежая установка) — тогда корень, как было раньше.
    """
    folder = os.path.dirname(_installation_db())
    if not folder or not os.path.isdir(folder):
        folder = config.ROOT
    return os.path.join(folder, PIN_FILE)


def _read_pin() -> dict | None:
    """Прочитать запись о PIN; None — замка нет.

    Битая или недописанная запись считается отсутствием замка. Это не послабление
    для чужого: испортить файл можно только с доступом к серверу, а с таким
    доступом его проще удалить. Зато «файл побился» не запирает владельца снаружи
    его же установки, где PIN иначе не сбросить.
    """
    path = pin_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("замок входа не прочитался (%s): %s", path, exc)
        return None
    if isinstance(data, dict) and data.get("salt") and data.get("hash"):
        return data
    log.warning("замок входа испорчен (%s): нет соли или хэша", path)
    return None


def pin_is_set() -> bool:
    """Задан ли PIN. Ложь — вход как раньше, по одному номеру."""
    return _read_pin() is not None


def set_pin(pin: str) -> None:
    """Поставить или сменить PIN; пустая строка снимает замок.

    Открытым текстом PIN не ложится никуда: в файле соль и хэш. Запись идёт через
    временный файл и os.replace — оборванная запись не оставит полфайла, из
    которого замок не прочитать.

    Что НЕ принимается и почему. PIN короче шести цифр — это не замок: четыре
    цифры при двадцати отказах в 15 минут подбираются за дни, а «1» — за одну
    догадку. Нецифровой PIN тоже
    отклоняется: поле на экране числовое, и буквы там означают не «сложный PIN»,
    а ошибку ввода. Отказ — ValueError с текстом для человека, а не молчание:
    иначе страница /pin сказала бы «поставлен», а замка не было бы.
    """
    value = (pin or "").strip()
    if value and not re.fullmatch(r"[0-9]+", value):
        raise ValueError(PIN_NOT_DIGITS)
    if value and len(value) < PIN_MIN_DIGITS:
        raise ValueError(PIN_TOO_SHORT)
    path = pin_path()
    with _PIN_LOCK:
        if not value:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            return
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), salt, PIN_ITERATIONS)
        record = {
            "version": 1,
            "algorithm": "pbkdf2_hmac_sha256",
            "iterations": PIN_ITERATIONS,
            "salt": salt.hex(),
            "hash": digest.hex(),
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        os.replace(tmp, path)


def check_pin(pin: str) -> bool:
    """Совпадает ли введённое с PIN. Замка нет — ложь: проверять нечего."""
    record = _read_pin()
    if record is None:
        return False
    value = (pin or "").strip()
    if not value:
        return False
    try:
        iterations = int(record.get("iterations") or PIN_ITERATIONS)
        salt = bytes.fromhex(str(record["salt"]))
        expected = str(record["hash"])
    except (KeyError, TypeError, ValueError):
        log.warning("замок входа испорчен (%s): не разобрать соль или число повторов",
                    pin_path())
        return False
    digest = hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), salt, iterations).hex()
    # Сравнение постоянного времени: обычное == по хэшу при переборе рассказывает,
    # сколько первых байт совпало, и сокращает перебор.
    return hmac.compare_digest(digest, expected)


# ---------- секрет ссылки входа ----------
def link_secret(phone: str) -> str:
    """Секрет рабочего места для ссылки, которая открывает его без куки.

    Зачем отдельный секрет, если есть PIN. Закладка «Передать вход» работает на
    чужом сайте (в кабинете сети) и возвращается к нам адресом ?phone=…&vhod=….
    Куки у неё нет, PIN она не знает и знать не может. Поэтому в ссылке едет свой
    секрет этого рабочего места: без него адрес с номером не открывает ничего.

    Где живёт. В настройках рабочего места (ключ link.secret его базы), а не в
    файле и не в git: секрет принадлежит человеку, а не установке, и у каждого свой.
    Создаётся при первом обращении, то есть тогда, когда сервер собрал первую
    закладку; раньше незачем — непоказанный секрет ничего не отпирает.

    Рабочее место уже открыто в этом потоке — второй раз его не открываем: сборка
    закладки идёт внутри запроса, где база человека выбрана выше (auth).
    """
    norm = normalize_phone(phone)
    if not norm:
        raise ValueError("секрет ссылки: номер не разобрался")
    from app import repo                       # поздно: repo тянет за собой app.db

    if current() != norm:
        open_workspace(norm)                   # заводит папку и схему, если их нет
    with _LINK_LOCK:
        secret = (repo.get_setting(LINK_SECRET_KEY) or "").strip()
        if not secret:
            secret = secrets.token_hex(LINK_SECRET_BYTES)
            repo.set_setting(LINK_SECRET_KEY, secret)
            log.info("закладке входа выдан секрет рабочего места")
    return secret


def link_secret_matches(phone: str, given: str) -> bool:
    """Подходит ли секрет из ссылки к этому рабочему месту.

    Порядок проверок — как у замка API (app/api._unlock): непустой секрет, известное
    рабочее место, и только потом чтение базы. Ложь здесь не создаёт рабочего места:
    иначе чужой стук по адресу с любым номером заводил бы папки и базы.

    Сравнение постоянного времени и БАЙТАМИ, а не строками: hmac.compare_digest на
    строках требует ASCII и на кириллице бросает TypeError — подстановка «t=привет»
    кончалась бы не отказом, а пятисотым ответом.
    """
    norm = normalize_phone(phone)
    value = (given or "").strip()
    if not norm or not value:
        return False
    if not exists(norm):
        return False
    try:
        expected = link_secret(norm)
    except Exception:                          # noqa: BLE001 — отказ, а не падение
        log.exception("секрет ссылки рабочего места не прочитался")
        return False
    return hmac.compare_digest(expected.encode("utf-8"), value.encode("utf-8"))


def allowed_phones() -> set[str] | None:
    """Кому можно входить: users.allowed из config.yaml; None — пускать всех.

    Ключ принимается и списком, и строкой через запятую. Номера нормализуются
    тем же normalize_phone, что и ввод: «8 999…» в конфиге и «+7 999…» на экране
    должны означать один номер. Пустой или не заданный список — None, то есть
    прежнее поведение; незнакомый тип ключа — тоже None, но с предупреждением.
    """
    raw = config.get("users.allowed")
    if raw is None:
        return None
    # Режем по запятой и точке с запятой, но НЕ по пробелу: «+7 999 000-00-51»
    # пишут с пробелами, и по ним номер развалился бы на куски, которых не
    # нормализовать, — список молча стал бы пустым, то есть пускал бы всех.
    parts: list[str]
    if isinstance(raw, str):
        parts = re.split(r"[,;\n]+", raw)
    elif isinstance(raw, (list, tuple)):
        parts = [chunk for item in raw for chunk in re.split(r"[,;\n]+", str(item))]
    else:
        log.warning("users.allowed в config.yaml не список и не строка (%s) — не применяю",
                    type(raw).__name__)
        return None
    parts = [part.strip() for part in parts if part and part.strip()]
    phones = {phone for phone in (normalize_phone(part) for part in parts) if phone}
    if phones:
        return phones
    if parts:
        # Список задан, а разобрать из него нечего (опечатка, буквы вместо цифр).
        # Запирать всех нельзя: владельца некому впустить, и правка конфига на
        # сервере — единственный выход. Поэтому пускаем всех, но говорим вслух:
        # молча выключившийся замок — худший вид поломки.
        log.warning("users.allowed не дал ни одного разборчивого номера — не применяю")
    return None


def resolve_login(raw: str | None) -> tuple[str | None, str | None]:
    """Что делать с тем, что человек ввёл: (номер, None) либо (None, объяснение)."""
    if not (raw or "").strip():
        return None, "Введите номер телефона."
    phone = normalize_phone(raw)
    if not phone:
        return None, NOT_A_PHONE
    allowed = allowed_phones()
    if allowed is not None and phone not in allowed:
        # Тем же текстом, что и «не похоже на номер»: иначе по разнице ответов
        # посторонний узнаёт, что номер в установке есть, и начинает перебор PIN.
        return None, NOT_A_PHONE
    return phone, None
