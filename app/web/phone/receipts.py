"""Экран «Мои чеки» на телефоне: статус кабинета ФНС и одна кнопка «Загрузить сейчас».

ЗАЧЕМ ОТДЕЛЬНЫЙ ЭКРАН. На компьютере чеки живут на /receipts (app/web/screens/
receipts.py) — экране из шести блоков с закладкой, вставкой текста, файлом и окном
кабинета. На телефоне всё это не помещается и половиной не нужно: человек с телефона
хочет знать, подключён ли кабинет, сколько чеков уже загружено и нажать одну кнопку.
Поэтому здесь ровно четыре вещи: сколько есть, чем подключено, кнопка загрузки и
честный список того, чего экран не умеет. Всё остальное осталось на /receipts, и
ссылка «Полная версия» туда ведёт.

ЧТО ЗДЕСЬ НЕ СВОЁ. Ни одного собственного правила про чеки: состояние кабинета даёт
app/fns/sync.py (status, is_connected, headline), числа — app/receipts.py (summary),
ход долгой загрузки — тот же, что у веб-экрана. Загрузка на телефоне и на компьютере
одна на человека: ход дела лежит в памяти процесса под номером человека
(app/web/screens/receipts.py, _JOBS), и второй поток поверх первого не запускается.
Взять чужой _JOBS и завести свой значило бы показать на телефоне «загрузка идёт»,
пока она идёт на компьютере, — а под ней молча пустая история.

КЛЮЧ ИЗ ЗАКЛАДКИ НА ТЕЛЕФОНЕ. Окно ФНС внутри приложения (MainActivity) — отдельная
задача; до неё ключ приезжает адресом /phone/receipts?keys=… — так же, как на /receipts
(там его принимает _take_key). Забираем ключ и СРАЗУ переводим на чистый адрес при
любом исходе: ни ключ, ни текст ошибки в адресной строке не остаются. Исход и отказ
кабинета показываются плашкой на странице — дословно, как на веб-экране: живого входа
отсюда не было ни разу, и прятать ответ кабинета за «что-то пошло не так» значило бы
лишить человека единственного источника правды.

ДОЛГАЯ ЗАГРУЗКА. Кабинет обходится минутами (замер: 1632 чека — 2,1 минуты впятером).
Пока поток работает, страница отдаётся с заголовком Refresh: браузер сам перезапрашивает
её каждые REFRESH_SEC секунд. Ни строчки JavaScript, работает в WebView телефона и
переживает закрытие вкладки — поток от неё не зависит. Тот же приём, что на /receipts,
и по той же причине.

ЧЕГО ЭКРАН НЕ ДЕЛАЕТ И ЧЕГО НЕ ОБЕЩАЕТ: вход в кабинет без человека (капча и СМС —
только руками), полную историю (часть чеков ФНС уже не хранит состава), привязку всех
позиций к товарам и бессрочный ключ. Всё это сказано словами в шаблоне, а не забыто.
"""
from __future__ import annotations

import logging

from flask import flash, g, make_response, redirect, request, url_for

from app import receipts as history, users
from app.web import device
from app.web.auth import needs_phone

from .common import _render, bp

log = logging.getLogger(__name__)

PATH = "/phone/receipts"
KEYS_PARAM = "keys"          # так закладка передаёт ключ доступа, см. docs/grab.src.js
REFRESH_SEC = 3              # через столько секунд страница перезапросит себя во время загрузки

SYNC_GOING = ("Загрузка уже идёт. Её видно на странице, а страницу можно закрыть — "
              "она не оборвётся.")
NOT_CONNECTED = "Кабинет ФНС не подключён — подключите его ключом из закладки."
FILE_ON_WEB = ("Файл чека загружается в полной версии — здесь только кабинет и кнопка "
               "«Загрузить сейчас».")


# ---------- чужое, но по одному адресу на человека ----------
def _job_of(phone: str):
    """Ход загрузки ЭТОГО человека — тот же, что у веб-экрана.

    Обёртка, а не копия: собственная память на телефоне означала бы две загрузки
    одновременно и два разных ответа на вопрос «идёт ли она». Смотрит туда же, куда
    /receipts, поэтому вкладки и телефон видят одно и то же.
    """
    from app.web.screens import receipts as web_receipts

    return web_receipts._job_of(phone)


def _start_sync(phone: str) -> bool:
    """Запустить загрузку ровно ту же, что с /receipts. False — она уже идёт."""
    from app.web.screens import receipts as web_receipts

    return web_receipts._start_sync(phone)


def _begin_sync(phone: str) -> bool | None:
    """Запустить загрузку, не роняя экран: True — пошла, False — уже идёт, None — сбой.

    Поток может не завестись (кончились ресурсы, сервер выключается) — это не повод
    отдать человеку 500. Говорим словами и оставляем экран на месте.
    """
    try:
        return _start_sync(phone)
    except Exception:  # noqa: BLE001
        log.exception("загрузка чеков на телефоне не запустилась")
        flash("Загрузку не удалось запустить — попробуйте ещё раз.", "bad")
        return None


def _demo_refusal() -> str:
    """Отказ демо — тот же текст, что на веб-экране: витрину открывает любой."""
    from app.web.screens.receipts import DEMO_REFUSAL

    return DEMO_REFUSAL


# ---------- страница ----------
@bp.route("/receipts", methods=("GET", "POST"))
@needs_phone
def receipts():
    """GET рисует, POST делает и переводит обратно на чистый адрес.

    Как и /receipts: один адрес на всё, действие — в скрытом поле «do». Перевод
    после действия нужен, чтобы обновление страницы не повторило отправку, а ключ
    (в форме или в адресе) не остался ни в строке, ни в истории браузера.
    """
    phone = g.phone
    if request.method == "GET" and request.args.get(KEYS_PARAM):
        return _take_key(phone, request.args[KEYS_PARAM])
    if request.method == "POST":
        return _act(phone)
    return _show(phone)


def _take_key(phone: str, packed: str):
    """Ключ, приехавший от закладки прямо в адресе. Отвечаем переводом при любом исходе."""
    from app.fns import sync as cabinet
    from app.fns.client import CabinetError

    if phone == users.DEMO:
        flash(_demo_refusal(), "bad")
        return redirect(PATH, 303)
    try:
        info = cabinet.connect_with_keys(cabinet.unpack_keys(packed), phone=_own_phone(phone))
    except CabinetError as exc:
        flash(f"Ключ кабинета из закладки не подошёл: {exc}", "bad")
        return redirect(PATH, 303)
    except Exception as exc:  # noqa: BLE001 — упасть в чужом формате ключа нельзя
        log.exception("ключ из закладки на телефоне не принялся")
        flash(f"Ключ из закладки не принялся: {exc}", "bad")
        return redirect(PATH, 303)

    flash("Кабинет подключён ключом из закладки" + (
        ", чеки в нём видны." if info.get("seen") else ", но чеков в нём пока не видно."), "ok")
    # Чеки забираем сразу: человек нажал «Подключить к приложению» и ждёт покупки,
    # а не вторую кнопку «а теперь загрузите».
    _begin_sync(phone)
    return redirect(PATH, 303)


def _act(phone: str):
    """Выполнить нажатое и перевести на чистый адрес. Ошибки — словами, не 500."""
    what = (request.form.get("do") or "").strip()
    if what == "sync":
        _do_sync(phone)
    elif what == "disconnect":
        _do_disconnect(phone)
    elif what == "keys":
        _do_keys(phone)
    elif what == "file":
        flash(FILE_ON_WEB, "info")
    elif what:
        log.warning("на телефонном экране чеков нажато неизвестное действие: %r", what[:40])
        flash("Такого действия на этом экране нет. Остальное — в полной версии.", "warn")
    return redirect(PATH, 303)


def _show(phone: str):
    """Нарисовать экран. Пока загрузка идёт — с заголовком Refresh, чтобы он ожил сам."""
    from app.fns import sync as cabinet

    data = _summary()
    have, left = data["loaded"], data["pending"]
    state = _state()
    connected = bool(state.get("connected"))
    job = _job_of(phone)
    # last_result приходит из JSON настроек: кто-то мог записать туда не объект.
    # headline() ждёт словарь, поэтому проверяем, а не падаем на чужой записи.
    last_result = state.get("last_result")
    last = cabinet.headline(last_result) if isinstance(last_result, dict) else ""

    answer = make_response(_render(
        "receipts.html", tab=None,
        have=have, left=left, headline=data.get("headline") or "",
        connected=connected,
        connected_phone=users.display(state.get("phone")),
        # «2026-09-17T12:33:05» человеку читать незачем: буква T посреди времени
        # выглядит как сбой вёрстки, а секунды тут ничего не решают.
        synced=(state.get("last_sync_at") or "").replace("T", " ")[:16],
        last=last, job=job,
        # Позиции, загруженные до появления учёта чеков: какому чеку они принадлежат,
        # теперь не узнать. Сказано отдельной строкой только в этом случае, как на
        # веб-экране (screens/receipts.py, _show).
        loose=(have.get("rows_without_receipt") or 0) if not have.get("receipts") else 0,
        demo=phone == users.DEMO,
        full_url=url_for("screen.receipts"),
        refresh=REFRESH_SEC,
        # Кнопка «Войти в кабинет ФНС» работает только в приложении (мост openFns).
        # Снаружи экран честно говорит «откройте приложение» и оставляет поле ключа.
        on_phone=device.on_phone(),
    ))
    if job is not None and getattr(job, "running", False):
        # Заголовок, а не <meta> в разметке: так же, как на /receipts, и по той же
        # причине — http-equiv в теле держится на снисходительности браузеров.
        answer.headers["Refresh"] = str(REFRESH_SEC)
    return answer


def _summary() -> dict:
    """Числа истории. Сбой чтения — нули и честная строка, а не 500.

    Экран — не место, где падать из-за одного нечитаемого счётчика: человек пришёл
    узнать состояние и нажать кнопку, и остальное должно остаться на месте.
    """
    try:
        return history.summary()
    except Exception:  # noqa: BLE001
        log.warning("история чеков на телефоне не прочиталась", exc_info=True)
        return {"loaded": {"receipts": 0, "rows": 0, "rows_without_receipt": 0,
                           "total": 0.0, "months": 0},
                "pending": {"receipts": 0, "total": 0.0},
                "headline": "История покупок сейчас не читается."}


def _state() -> dict:
    """Состояние кабинета. Не прочиталось — «не подключён», а не падение страницы."""
    from app.fns import sync as cabinet

    try:
        state = cabinet.status()
    except Exception:  # noqa: BLE001
        log.warning("состояние кабинета ФНС на телефоне не прочиталось", exc_info=True)
        return {"connected": False}
    return state if isinstance(state, dict) else {"connected": False}


# ---------- действия ----------
def _do_sync(phone: str) -> None:
    """«Загрузить сейчас». Второе нажатие второй загрузки не запускает."""
    from app.fns import sync as cabinet

    if phone == users.DEMO:
        flash(_demo_refusal(), "bad")
        return
    try:
        connected = bool(cabinet.is_connected())
    except Exception:  # noqa: BLE001
        log.warning("подключение кабинета ФНС не проверилось", exc_info=True)
        connected = False
    if not connected:
        flash(NOT_CONNECTED, "warn")
        return
    job = _job_of(phone)
    if job is not None and getattr(job, "running", False):
        flash(SYNC_GOING, "info")
        return
    started = _begin_sync(phone)
    if started is None:
        return                                   # сбой запуска уже назван словами
    if not started:
        # Гонка двух нажатий: поток уже завели между проверкой и запуском.
        flash(SYNC_GOING, "info")
        return
    flash("Загрузка началась. Чеки пишутся в историю по ходу — страницу можно закрыть.", "ok")


def _do_disconnect(phone: str) -> None:  # noqa: ARG001 — база уже выбрана по человеку
    from app.fns import sync as cabinet

    try:
        cabinet.disconnect()
    except Exception as exc:  # noqa: BLE001 — отключение не должно давать 500
        log.exception("кабинет ФНС на телефоне не отключился")
        flash(f"Отключить кабинет не вышло: {exc}", "bad")
        return
    flash("Кабинет отключён. Загруженные чеки остались в истории.", "ok")


def _do_keys(phone: str) -> None:
    """Ключ, вставленный руками: запасной путь, когда закладки на телефоне нет."""
    from app.fns import sync as cabinet
    from app.fns.client import CabinetError

    if phone == users.DEMO:
        flash(_demo_refusal(), "bad")
        return
    try:
        info = cabinet.connect_with_keys(request.form.get("keys", ""), phone=_own_phone(phone))
    except CabinetError as exc:
        flash(f"Кабинет ответил: {exc}", "bad")
        return
    except Exception as exc:  # noqa: BLE001 — показываем человеку, а не падаем
        log.exception("ключ кабинета ФНС на телефоне не принялся")
        flash(f"Ключ не принялся: {exc}", "bad")
        return
    flash("Кабинет подключён, ключ принят" + (
        ", чеки в нём видны." if info.get("seen") else ", но чеков в нём пока не видно."), "ok")
    _begin_sync(phone)


# ---------- мелкая обслуга ----------
def _own_phone(phone: str | None) -> str | None:
    """Номер, который можно назвать кабинету. У демо своего номера нет."""
    return phone if phone and phone != users.DEMO else None
