"""Экран «Мои чеки»: загрузка истории покупок из сервиса ФНС.

Первая из четырёх опор приложения: пока чеков нет, считать нечего — корзина
собирается из того, что человек уже покупал. Поэтому экран отвечает на четыре
вопроса сразу: сколько уже загружено, чем подключён кабинет, что делать, если он
не подключается, и чем кончилась последняя загрузка.

ЧТО ЗДЕСЬ ТОЛЬКО РИСУЕТСЯ. Вся суть лежит в app/fns (разговор с кабинетом) и
app/importers (разбор чеков) и переезда со Streamlit не требовала. Этот файл
показывает их ответы и переводит нажатия в их вызовы — ни одного собственного
правила про чеки здесь нет.

ЛЮБОЙ ОТВЕТ КАБИНЕТА ПОКАЗЫВАЕТСЯ ДОСЛОВНО. Живого входа отсюда не было ни разу
(см. app/fns/client.py): ни учётной записи, ни номера, на который придёт СМС.
Человек — единственный, кто увидит настоящий ответ кабинета, и прятать его за
«что-то пошло не так» значило бы лишить нас единственного источника правды. Это
правило, а не небрежность.

КЛЮЧ ПРОВЕРКИ (challengeToken) ЖИВЁТ В СКРЫТОМ ПОЛЕ ФОРМЫ, А НЕ В КУКЕ-СЕССИИ.
Между «получить код» и «ввести код» ключ нужно где-то удержать. Кука казалась бы
проще, но она уезжает на сервер с КАЖДЫМ запросом — за картинкой, за стилем, за
соседним экраном — и лежит на диске браузера, пока человек не выйдет. Скрытое
поле живёт ровно в той одной странице, где стоит поле для кода, и уходит ровно в
одном запросе — в том, который этот код проверяет. Меньше мест, где ключ можно
забыть. Второе следствие: шаг «получить код» отвечает СТРАНИЦЕЙ, а не переводом
на неё, — иначе ключ пришлось бы куда-то положить на время перевода, то есть
вернуться к куке или к адресной строке.

Код из СМС не хранится нигде и в журнал не пишется: ни здесь, ни в app/fns.
В адресную строку не попадает ни код, ни ключ проверки — оба ходят только телом
POST. Единственное исключение — ключ ДОСТУПА из закладки: она открывает
приложение адресом вида /receipts?keys=…, так устроена сама закладка
(docs/grab.src.js, location.replace). Мы его забираем и тут же отвечаем переводом
на чистый адрес, поэтому в адресной строке и в истории браузера он не остаётся;
в журнал сервера он не попадает потому, что waitress запросы не журналирует.
Переписать эту часть закладки нельзя, не сломав уже заведённые у людей закладки.

ДОЛГАЯ ЗАГРУЗКА НЕ ДОЛЖНА ВЕШАТЬ СТРАНИЦУ. Обход кабинета — это минуты: 1632
чека владельца брались 2,1 минуты впятером (app/fns/sync.py). Обычная страница
столько ждать не может: браузер покажет белый экран, обратный прокси оборвёт
запрос по таймауту, а человек решит, что приложение умерло, и нажмёт ещё раз.
Поэтому загрузка идёт ОТДЕЛЬНЫМ ПОТОКОМ, а страница, пока поток работает,
отдаётся с заголовком «Refresh: 3»: браузер сам перезапрашивает её каждые три
секунды и показывает, сколько чеков уже взято. Ни строчки JavaScript, работает
на телефоне, переживает закрытие вкладки — поток от неё не зависит.

    Почему не потоковый ответ (Response с генератором). Он рисует ход дела без
    потока, но платит дорого: страницу нельзя обновить, нельзя уйти с неё и
    вернуться, а закрытая вкладка обрывает загрузку на середине. Ровно то, чего
    мы и хотели избежать.

    Почему не очередь в базе, как у цен по адресу (app/catalog/store.py,
    address_queue). Ту очередь разбирает отдельный контейнер korzina-jobs, и это
    правильно для работы, которая нужна всем. Здесь работа нужна ОДНОМУ человеку,
    ей нужен его ключ кабинета и его база — то есть всё то, что живёт в его
    рабочем месте.

ЛОВУШКА, КОТОРУЮ ЭТОТ ПОТОК ОБХОДИТ. Путь к базе человека живёт в contextvars
(app/config.py, app/users.py), а новый поток начинает с ЧИСТОГО контекста: в нём
никакой базы не выбрано, и загрузка молча ушла бы в общую базу из config.yaml.
Поэтому поток открывает рабочее место сам, первой же строкой, и снимает его в
finally — теми же двумя движениями, что делает app/web/auth.py на каждом запросе.

ЧТО ЗНАЕТ ПАМЯТЬ ПРОЦЕССА, А ЧТО БАЗА. В памяти (_JOBS) лежит только ход дела:
какая ступень, сколько взято, чем кончилось. Перезапуск сервера её теряет — и это
не потеря: чеки пишутся в историю пачками по ходу загрузки (app/fns/sync.py),
а «когда синхронизировались и чем кончилось» лежит в настройках рабочего места.
После перезапуска экран честно покажет последнюю проверку из базы.

ЧТО СО STREAMLIT НЕ ПОЕХАЛО. Вставленные чеки прежде копились стопкой в
session_state и сохранялись разом. Стопка была не удобством, а обходом: в
Streamlit поле нельзя очистить после отправки, и приходилось растить номер поля и
держать накопленное рядом. Здесь у каждого чека своя отправка — вставил, увидел
разбор, сохранил, вставил следующий. Шаг, ради которого всё затевалось —
«сначала показать, потом сохранять», — остался на месте.

ДЕМО КАБИНЕТА НЕ ИМЕЕТ. Рабочее место demo — общая витрина: её открывает любой,
кто нажал «Посмотреть демо». Положить туда ключ от чьего-то настоящего кабинета
значит отдать его всем, поэтому все действия с кабинетом в демо отклоняются
словами.

ЧЕГО НЕ ПРОВЕРЕНО. Живой вход по коду из СМС — по-прежнему ни разу, и на этом
шаге кабинет требовал капчу (16.09.2026, {"code": "empty.captcha"}). Форма
оставлена на случай, если капчу снимут, и честно говорит об этом. Главный путь —
ключ из закладки, он на живом кабинете проверен: 1632 чека с 2018 года.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass, field

from flask import flash, g, make_response, redirect, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from app import receipts as history
from app import repo, users
from app.web.views import SCREEN_BY_KEY

log = logging.getLogger(__name__)

SCREEN = SCREEN_BY_KEY["receipts"]
PATH = SCREEN.path
CABINET_URL = "https://lkdr.nalog.ru/"

KEYS_PARAM = "keys"          # так закладка передаёт ключ доступа, см. docs/grab.src.js
REFRESH_SEC = 3              # через столько секунд страница перезапросит себя во время загрузки
NBSP = " "

# Закладка лежит в docs рядом с приложением. Путь считается от ЭТОГО файла, а не от
# config.ROOT: в тестах корень подменяется на временную папку, и закладка «пропала бы»
# ровно там, где её проверяют.
BOOKMARKLET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "docs", "grab.min.txt")

# Что понимает app/importers/ofd_pdf.py. Список повторён здесь только для проверки
# присланного файла и подсказки в форме — разбор всё равно выбирает сам импортёр.
SUPPORTED = (".pdf", ".txt", ".json", ".csv", ".xlsx", ".xlsm", ".html", ".htm", ".eml")

DEMO_REFUSAL = ("Демо — общая витрина: её открывает любой, кто нажал «Посмотреть демо». "
                "Ключ от настоящего кабинета ФНС сюда класть нельзя. Войдите по своему номеру.")
TOO_BIG = ("Файл не принят: он больше 32 МБ. Это предел сервера, а не файла — "
           "заберите чеки закладкой, она отдаёт их прямо в приложение.")


# ---------- ход долгой загрузки ----------
@dataclass
class _Job:
    """Что прямо сейчас происходит с загрузкой чеков у одного человека.

    Живёт в памяти процесса и умирает вместе с ним — нарочно: это не данные, а ход
    дела. Сами чеки пачками ложатся в историю по дороге, и оборванная загрузка
    ничего не теряет, кроме вот этой полоски.
    """

    stage: str = "list"
    done: int = 0
    total: int = 0
    started: float = field(default_factory=time.monotonic)
    news: float = field(default_factory=time.monotonic)
    result: dict | None = None
    error: str | None = None

    @property
    def running(self) -> bool:
        return self.result is None and self.error is None

    @property
    def saying(self) -> str:
        """Ход дела словами. У описи конца заранее не знает никто — полоса там бегущая."""
        if self.stage == "list":
            return f"Читаю опись кабинета: чеков {self.done}…"
        return f"Беру позиции: {self.done} из {self.total}…"

    @property
    def elapsed(self) -> str:
        seconds = int(time.monotonic() - self.started)
        if seconds < 60:
            return f"{seconds} с"
        return f"{seconds // 60} мин {seconds % 60} с"

    @property
    def silent(self) -> int:
        """Сколько секунд от кабинета нет вестей.

        Застывшее «640 из 1632» и работающая загрузка выглядят одинаково, и молчать
        об этом нельзя: человек должен видеть разницу между «медленно отвечают» и
        «всё встало», а не гадать по неподвижному числу.
        """
        return int(time.monotonic() - self.news)


_JOBS: dict[str, _Job] = {}
_LOCK = threading.Lock()


def _job_of(phone: str) -> _Job | None:
    with _LOCK:
        return _JOBS.get(phone)


def _start_sync(phone: str) -> bool:
    """Запустить загрузку в отдельном потоке. False — она уже идёт.

    Ключ — номер человека, а не вкладка браузера: две вкладки должны видеть одну и
    ту же загрузку, а не запускать вторую поверх первой.
    """
    with _LOCK:
        running = _JOBS.get(phone)
        if running and running.running:
            return False
        job = _Job()
        _JOBS[phone] = job
    # daemon=True: выключение сервера не должно ждать обхода кабинета. Уже взятые
    # чеки к этому моменту в истории — sync пишет пачками.
    threading.Thread(target=_work, args=(phone, job), daemon=True, name="fns-sync").start()
    return True


def _work(phone: str, job: _Job) -> None:
    """Обход кабинета. Базу открывает САМ — новый поток приходит с чистым контекстом.

    Без первой строки загрузка ушла бы в общую базу из config.yaml и выглядела бы
    как «чеки загрузились, а их нигде нет». Без finally база осталась бы висеть на
    потоке, но поток свой и одноразовый — снимаем всё равно, чтобы правило «база
    снимается всегда» не знало исключений.
    """
    from app.fns.client import CabinetError
    from app.fns.sync import sync

    def step(stage: str, done: int, total: int) -> None:
        # Сюда стучат на каждый чек, и это нормально: тут только присваивания.
        # Ограничивать частоту, как в Streamlit, не нужно — страницу двигает не этот
        # поток, а браузер, раз в REFRESH_SEC секунд.
        job.stage, job.done, job.total = stage, done, total
        job.news = time.monotonic()

    try:
        users.open_workspace(phone)
        result = sync(on_step=step)
        job.result = result
        log.info("чеки из кабинета: взято %s, осталось %s",
                 result.get("receipts"), result.get("pending"))
    except CabinetError as exc:
        job.error = str(exc)
    except Exception as exc:  # noqa: BLE001 — упасть тихо в чужом потоке нельзя
        log.exception("загрузка чеков сорвалась")
        job.error = f"Загрузка сорвалась: {exc}"
    finally:
        users.deactivate()


# ---------- страница ----------
def page():
    """Экран целиком: GET рисует, POST делает и переводит обратно.

    Один адрес и одна функция на всё, потому что views.py вешает экран одним
    правилом; какое действие нажато, говорит скрытое поле «do». Flask смотрит на
    page.methods внизу файла и разрешает этому правилу POST.
    """
    phone = g.phone
    if request.method == "GET" and request.args.get(KEYS_PARAM):
        return _take_key(phone, request.args[KEYS_PARAM])

    extra: dict = {}
    if request.method == "POST":
        try:
            outcome = _act(phone)
        except RequestEntityTooLarge:
            # Проверять размер раньше нельзя: он всплывает при первом же чтении формы.
            flash(TOO_BIG, "bad")
            outcome = None
        if outcome is None:
            # Перевод после действия: обновление страницы не повторит отправку, а
            # ни код, ни ключ не останутся в адресе.
            return redirect(PATH, 303)
        extra = outcome
    return _show(phone, **extra)


def _take_key(phone: str, packed: str):
    """Ключ доступа, приехавший от закладки прямо в адресе.

    Отвечаем переводом на чистый адрес при любом исходе: ключ не должен остаться ни
    в адресной строке, ни в истории браузера, ни в чужой закладке. Закладка приходит
    сюда через location.replace, поэтому после перевода следа не останется вовсе.

    Чеки забираем сразу же: человек нажал в кабинете «Подключить к приложению» и
    ждёт свои покупки, а не кнопку «а теперь загрузите».
    """
    from app.fns.client import CabinetError
    from app.fns import sync as cabinet

    if phone == users.DEMO:
        flash(DEMO_REFUSAL, "bad")
        return redirect(PATH, 303)
    try:
        info = cabinet.connect_with_keys(cabinet.unpack_keys(packed), phone=_own_phone(phone))
    except CabinetError as exc:
        flash(f"Ключ кабинета из закладки не подошёл: {exc}", "bad")
        return redirect(PATH, 303)
    flash("Кабинет подключён ключом из закладки" + (
        ", чеки в нём видны." if info.get("seen") else ", но чеков в нём пока не видно."), "ok")
    _start_sync(phone)
    return redirect(PATH, 303)


def _act(phone: str) -> dict | None:
    """Выполнить нажатое. None — перевести обратно, словарь — нарисовать страницу сейчас.

    Страницей отвечают только те шаги, которым надо удержать на виду то, что нельзя
    класть ни в адрес, ни в куку: ключ проверки из кабинета и вставленный текст чека.
    Остальные переводят обратно, чтобы обновление страницы не повторяло действие.
    """
    what = (request.form.get("do") or "").strip()
    doing = {"sync": _do_sync, "disconnect": _do_disconnect, "keys": _do_keys,
             "code": _do_code, "confirm": _do_confirm, "file": _do_file,
             "preview": _do_preview, "save": _do_save}.get(what)
    if doing is None:
        log.warning("на экране чеков нажато неизвестное действие: %r", what[:40])
        return None
    return doing(phone)


def _show(phone: str, challenge: dict | None = None, preview: dict | None = None,
          typed: str = "", store: str = ""):
    """Нарисовать экран. Пока загрузка идёт — с заголовком Refresh, чтобы он ожил сам."""
    from app.fns import sync as cabinet

    data = history.summary()
    have, left = data["loaded"], data["pending"]
    state = cabinet.status()
    job = _job_of(phone)

    chips = [("Позиций", str(have["rows"]))]
    # «Чеков 0» рядом с «Позиций 16» выглядит поломкой, а это не поломка: так видны
    # покупки, загруженные до того, как появился учёт чеков. Плашку не рисуем вовсе,
    # а причину объясняем строкой ниже.
    if have["receipts"]:
        chips.insert(0, ("Чеков", str(have["receipts"])))
    if have["total"]:
        chips.append(("На сумму", _rub(have["total"])))
    if have["months"]:
        chips.append(("Срок", f"{have['months']} мес."))
    if left["receipts"]:
        chips.append(("Не загружено", str(left["receipts"])))

    loose = ""
    if have["rows_without_receipt"] and not have["receipts"]:
        # Сказано числом отдельной строкой, а не «16 позиций загружены»: при одной
        # позиции такая фраза разваливается, а число тут важнее оборота.
        loose = (f"Позиций, загруженных до того, как появился учёт чеков: "
                 f"{have['rows_without_receipt']}. Какому чеку они принадлежат, "
                 "теперь уже не узнать.")

    body = render_template(
        "receipts.html",
        screen=SCREEN,
        data=data, chips=chips, loose=loose,
        state=state,
        connected_phone=users.display(state.get("phone")),
        # «2026-09-17T12:33:05» человеку читать незачем: буква T посреди времени
        # выглядит как сбой вёрстки, а секунды тут ничего не решают.
        synced=(state.get("last_sync_at") or "").replace("T", " ")[:16],
        last=cabinet.headline(state["last_result"]) if state.get("last_result") else "",
        job=job,
        report=_report_lines(job.result) if job and job.result else [],
        challenge=challenge,
        preview=preview, typed=typed, store=store,
        # Склоняем и здесь: «понял 3 позиций» в отчёте о своей же работе читается
        # как небрежность ровно так же, как «загружено 2 чеков».
        preview_said=history.plural(len(preview["rows"]), "позиция", "позиции",
                                    "позиций") if preview else "",
        stores=repo.list_stores(),
        bookmarklet=_bookmarklet(),
        cabinet_url=CABINET_URL,
        supported=", ".join(SUPPORTED),
        refresh=REFRESH_SEC,
        history_path=SCREEN_BY_KEY["history"].path,
        own_phone=users.display(_own_phone(phone)) if _own_phone(phone) else "",
    )
    answer = make_response(body)
    if job and job.running:
        # Заголовок, а не <meta> в разметке: общая рама страниц (layout.html) своего
        # места в <head> не даёт, а http-equiv в теле держится на снисходительности
        # браузеров. Заголовок — то же самое правило, только сказанное там, где ему
        # и положено быть.
        answer.headers["Refresh"] = str(REFRESH_SEC)
    return answer


# ---------- действия ----------
def _do_sync(phone: str) -> None:
    from app.fns import sync as cabinet

    if phone == users.DEMO:
        flash(DEMO_REFUSAL, "bad")
    elif not cabinet.is_connected():
        flash("Кабинет не подключён — подключите его ключом из закладки.", "warn")
    elif not _start_sync(phone):
        flash("Загрузка уже идёт. Её видно ниже; страницу можно закрыть, она не оборвётся.",
              "info")
    return None


def _do_disconnect(phone: str) -> None:  # noqa: ARG001 — база уже выбрана по человеку
    from app.fns import sync as cabinet

    cabinet.disconnect()
    flash("Кабинет отключён. Загруженные чеки остались в истории.", "ok")
    return None


def _do_keys(phone: str) -> None:
    """Ключ, скопированный закладкой и вставленный руками. Запасной путь к главному пути."""
    from app.fns.client import CabinetError
    from app.fns import sync as cabinet

    if phone == users.DEMO:
        flash(DEMO_REFUSAL, "bad")
        return None
    try:
        info = cabinet.connect_with_keys(request.form.get("keys", ""), phone=_own_phone(phone))
    except CabinetError as exc:
        flash(f"Кабинет ответил: {exc}", "bad")
        return None
    flash("Кабинет подключён, ключ принят" + (
        ", чеки в нём видны." if info.get("seen") else ", но чеков в нём пока не видно."), "ok")
    _start_sync(phone)
    return None


def _do_code(phone: str) -> dict | None:
    """«Получить код»: просим кабинет отправить СМС и остаёмся на странице с ключом проверки."""
    from app.fns.client import CabinetError, CaptchaRequired
    from app.fns import sync as cabinet

    if phone == users.DEMO:
        flash(DEMO_REFUSAL, "bad")
        return None
    number = users.normalize_phone(request.form.get("phone", ""))
    if not number or number == users.DEMO:
        flash("Введите номер телефона.", "bad")
        return None
    try:
        info = cabinet.request_code(number)
    except CaptchaRequired:
        flash("Кабинет требует капчу на этом шаге — с сервера её не пройти. Подключите "
              "кабинет ключом из закладки, форма выше.", "bad")
        return None
    except CabinetError as exc:
        flash(f"Кабинет ответил: {exc}", "bad")
        return None
    where = f" на {info['sent_to']}" if info.get("sent_to") else ""
    flash(f"Код отправлен{where}. Введите шесть цифр из СМС.", "ok")
    return {"challenge": {"token": info["challenge_token"], "phone": number,
                          "sent_to": info.get("sent_to"), "expires_in": info.get("expires_in")}}


def _do_confirm(phone: str) -> dict | None:
    """Код из СМС. Ключ проверки приехал скрытым полем и дальше этого запроса не идёт."""
    from app.fns.client import CabinetError
    from app.fns import sync as cabinet

    if phone == users.DEMO:
        flash(DEMO_REFUSAL, "bad")
        return None
    token = request.form.get("challenge", "")
    number = users.normalize_phone(request.form.get("phone", "")) or ""
    code = request.form.get("code", "")
    again = {"challenge": {"token": token, "phone": number, "sent_to": None, "expires_in": None}}

    if len("".join(ch for ch in code if ch.isdigit())) < 4:
        flash("Введите код из СМС.", "bad")
        return again
    try:
        cabinet.confirm_code(token, number, code)
    except CabinetError as exc:
        flash(f"Кабинет ответил: {exc}", "bad")
        return again
    flash("Кабинет подключён. Дальше чеки берутся сами.", "ok")
    _start_sync(phone)
    return None


def _do_file(phone: str) -> None:  # noqa: ARG001
    """Файл: пакет из закладки, чек PDF или ОФД, таблица CSV или XLSX, письмо из доставки.

    Разбор выбирает сам импортёр по расширению (app/importers/ofd_pdf.py,
    import_receipt), включая случай «в .json приехал целый пакет с описью кабинета».
    Поэтому поле одно: человеку не нужно знать, чем его файл считает приложение.
    """
    item = request.files.get("file")
    if item is None or not (item.filename or "").strip():
        flash("Выберите файл.", "bad")
        return None
    ext = os.path.splitext(item.filename)[1].lower()
    if ext not in SUPPORTED:
        flash(f"Такие файлы приложение не читает. Понимает: {', '.join(SUPPORTED)}.", "bad")
        return None

    store = request.form.get("store") or None
    path = _save_upload(item, ext)
    try:
        from app.importers import import_receipt

        result = import_receipt(path, store)
    except Exception as exc:  # noqa: BLE001 — показываем человеку, а не падаем
        log.exception("файл с чеками не разобрался")
        flash(f"Не удалось разобрать файл: {exc}", "bad")
        return None
    finally:
        _drop(path)
    for line in _report_lines(result):
        flash(line["text"], line["kind"])
    return None


def _do_preview(phone: str) -> dict:  # noqa: ARG001
    """Вставленный текст чека: сначала показать, что поняли, и только потом сохранять.

    В базу здесь не пишется ничего. Если разбор ошибся, человек увидит это ДО того,
    как мусор попадёт в историю покупок, — ради этого шаг и существует.
    """
    text = request.form.get("text", "")
    store = request.form.get("store") or ""
    if not text.strip():
        flash("Вставьте текст чека.", "bad")
        return {"typed": "", "store": store}
    try:
        from app.importers.text_import import preview

        seen = preview(text, store or None)
    except Exception as exc:  # noqa: BLE001
        log.exception("вставленный текст не разобрался")
        flash(f"Не удалось разобрать текст: {exc}", "bad")
        return {"typed": text, "store": store}
    if not seen["rows"]:
        flash("Ни одной строки с товаром и ценой не нашлось. Скопируйте чек вместе с "
              "ценами — без них позицию не отличить от заголовка.", "warn")
        return {"typed": text, "store": store}
    return {"preview": seen, "typed": text, "store": store}


def _do_save(phone: str) -> None:  # noqa: ARG001
    """Сохранить показанный чек. Текст приехал скрытым полем — он же и был на виду."""
    text = request.form.get("text", "")
    store = request.form.get("store") or None
    try:
        from app.importers.text_import import import_order_text

        result = import_order_text(text, store)
    except Exception as exc:  # noqa: BLE001
        log.exception("вставленный чек не сохранился")
        flash(f"Не удалось сохранить: {exc}", "bad")
        return None

    if result["rows"]:
        flash(f"Добавлено {history.plural(result['rows'], 'позиция', 'позиции', 'позиций')} "
              "в историю покупок.", "ok")
    else:
        flash("Ни одной позиции не добавилось — сохранять было нечего.", "warn")
    if result.get("products_created"):
        flash(f"Новых товаров в справочнике: {result['products_created']}.", "info")
    if result.get("skipped_receipts"):
        flash("Этот чек уже был в истории — второй раз его не добавляли.", "info")
    return None


# ---------- мелкая обслуга ----------
def _own_phone(phone: str | None) -> str | None:
    """Номер, который можно назвать кабинету. У демо своего номера нет."""
    return phone if phone and phone != users.DEMO else None


def _rub(value) -> str:
    """1234.56 → «1 234,56 ₽». Свой, а не из app/ui/helpers: тот тянет за собой Streamlit."""
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    return f"{amount:,.2f}".replace(",", NBSP).replace(".", ",") + NBSP + "₽"


def _save_upload(item, ext: str) -> str:
    """Присланный файл — во временный, потому что импортёр работает с путём."""
    fd, path = tempfile.mkstemp(prefix="korzina_upload_", suffix=ext)
    os.close(fd)
    item.save(path)
    return path


def _drop(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _bookmarklet() -> str:
    """Закладка «Забрать все чеки» с подставленным адресом ЭТОГО экрана.

    Адрес подставляем из заголовков запроса, а не из настроек: приложение живёт и на
    сервере, и на чужом ноутбуке, а забытую настройку никто не поправит. Метим
    именно /receipts, чтобы ключ приезжал туда, где его ждут.

    Здесь же исчезла целая подпорка Streamlit: он вырезал адреса вида javascript: из
    своей разметки, и ссылку приходилось рисовать отдельным компонентом со своим
    документом внутри. На обычной странице ссылка — просто ссылка.
    """
    try:
        with open(BOOKMARKLET, encoding="utf-8") as fh:
            href = fh.read().strip()
    except OSError:
        log.warning("закладка не собрана: %s", BOOKMARKLET)
        return ""
    if "__APP_URL__" not in href:
        return href
    # Адрес кодируем целиком: закладка — это javascript:-ссылка, уже пропущенная через
    # процентное кодирование, и незакодированная решётка оборвала бы её на середине.
    target = request.url_root.rstrip("/") + PATH
    return href.replace("__APP_URL__", urllib.parse.quote(target, safe=""))


def _report_lines(result: dict) -> list[dict]:
    """Итог загрузки — числами, а не словом «готово».

    Числа склоняем: «2 чеков» в отчёте о собственной работе читается как небрежность.
    Каждый исход назван отдельно: взятое, уже бывшее, не прочитавшееся и то, чего у
    ФНС больше нет, — это четыре разных дела, и сваливать их в одну строку значило бы
    соврать в трёх случаях из четырёх.
    """
    if not result.get("receipts") and not result.get("skipped"):
        return [{"kind": "warn", "text": "Ни одного чека с позициями не нашлось."}]

    say, day = history.plural, history.human
    lines: list[dict] = []
    if result.get("receipts"):
        line = (f"Загружено {say(result['receipts'], 'чек', 'чека', 'чеков')} · "
                f"{say(result.get('rows') or 0, 'позиция', 'позиции', 'позиций')} "
                f"на {_rub(result.get('total'))}")
        if result.get("period"):
            first, last = result["period"]
            line += f" · с {day(first)} по {day(last)}" if first != last else f" · за {day(last)}"
        lines.append({"kind": "ok", "text": line})
    if result.get("skipped"):
        lines.append({"kind": "info",
                      "text": f"Пропущено {say(result['skipped'], 'чек', 'чека', 'чеков')} — "
                              "они уже были загружены раньше."})
    if result.get("products_created"):
        lines.append({"kind": "info",
                      "text": f"Новых товаров в справочнике: {result['products_created']}."})
    if result.get("failed"):
        lines.append({"kind": "warn",
                      "text": f"Не удалось разобрать {say(len(result['failed']), 'чек', 'чека', 'чеков')}. "
                              "Они остались в списке невзятых — попробуйте забрать их ещё раз."})
    if result.get("pending"):
        lines.append({"kind": "info",
                      "text": f"В кабинете видно ещё {say(result['pending'], 'чек', 'чека', 'чеков')}, "
                              "которых у вас нет."})
    if result.get("no_data"):
        lines.append({"kind": "info",
                      "text": f"Ещё по {say(result['no_data'], 'чеку', 'чекам', 'чекам')} ФНС больше "
                              "не хранит состав: в ленте они видны, а позиций за ними уже нет. Это "
                              "не ошибка загрузки, и повторный заход их не вернёт — приложение "
                              "больше о них не спрашивает."})
    return lines


# Flask смотрит на этот список, когда views.py вешает экран одним правилом
# (add_url_rule без methods). Без него POST с этого же экрана получил бы 405, и
# пришлось бы заводить отдельные адреса под каждое нажатие.
page.methods = ("GET", "POST")

__all__ = ["page"]
