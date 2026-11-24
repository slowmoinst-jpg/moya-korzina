"""Hourly receipt imports for connected workspaces. Manual refresh uses the same runner."""
from __future__ import annotations

import logging
import threading
import time

from app import repo, users
from app.fns import sync as cabinet
from app.fns.client import CaptchaRequired, PermissionDenied, Unauthorized

log = logging.getLogger(__name__)
INTERVAL = 3600
RETRY = 15 * 60
NEXT = "fns.next_check"
ERROR = "fns.sync_error"
PAUSED = "fns.needs_connection"
_lock = threading.Lock()
_running: dict[str, threading.Event] = {}


def running(phone: str) -> bool:
    with _lock:
        return phone in _running


def disconnect(phone: str) -> None:
    with _lock:
        stop = _running.get(phone)
        if stop:
            stop.set()
        cabinet.disconnect()
        for key in (NEXT, ERROR, PAUSED):
            repo.set_setting(key, None)


def connect(phone: str, keys: str, fns_phone: str) -> None:
    with _lock:
        if phone in _running:
            raise cabinet.CabinetError("Идёт загрузка чеков. Дождитесь её завершения.")
        _running[phone] = stop = threading.Event()
    try:
        cabinet.connect_with_keys(keys, phone=fns_phone)
        with _lock:
            if stop.is_set():
                cabinet.disconnect()
                raise cabinet.CabinetError("Подключение отменено.")
            for key in (NEXT, ERROR, PAUSED):
                repo.set_setting(key, None)
    finally:
        with _lock:
            _running.pop(phone, None)


class _Cancelled(Exception):
    pass


def _run(phone: str, stop: threading.Event) -> None:
    def check(*_args):
        if stop.is_set():
            raise _Cancelled
    try:
        users.open_workspace(phone)
        check()
        if not cabinet.is_connected():
            return
        repo.set_setting(NEXT, str(int(time.time()) + RETRY))
        result = cabinet.sync(on_step=check)
        with _lock:
            check()
            repo.set_setting(ERROR, None)
            repo.set_setting(NEXT, str(int(time.time()) + (RETRY if result.get("pending") else INTERVAL)))
    except _Cancelled:
        pass
    except Exception as exc:
        with _lock:
            if not stop.is_set():
                if isinstance(exc, (Unauthorized, PermissionDenied, CaptchaRequired)) or not cabinet.is_connected():
                    repo.set_setting(PAUSED, "1")
                    repo.set_setting(ERROR, "ФНС просит подтвердить подключение. Переподключите автоимпорт.")
                else:
                    repo.set_setting(ERROR, "Не удалось получить чеки. Повторим через 15 минут.")
                    repo.set_setting(NEXT, str(int(time.time()) + RETRY))
    finally:
        users.deactivate()
        with _lock:
            _running.pop(phone, None)


def start(phone: str, *, scheduled: bool = False) -> bool:
    if phone == users.DEMO:
        return False
    with _lock:
        if phone in _running or scheduled and len(_running) >= 2:
            return False
        _running[phone] = stop = threading.Event()
    try:
        threading.Thread(target=_run, args=(phone, stop), name="fns-sync", daemon=True).start()
    except RuntimeError:
        with _lock:
            _running.pop(phone, None)
        raise
    return True


def scan() -> None:
    now = time.time()
    for phone in users.list_workspaces():
        if users.normalize_phone(phone) != phone or phone == users.DEMO:
            continue
        try:
            users.activate(phone)
            if cabinet.is_connected() and not repo.get_setting(PAUSED):
                if float(repo.get_setting(NEXT) or 0) <= now:
                    start(phone, scheduled=True)
        except Exception:
            log.warning("Не удалось проверить расписание загрузки чеков")
        finally:
            users.deactivate()


def serve() -> None:
    log.info("Планировщик чеков запущен")
    while True:
        try:
            scan()
        except Exception:
            log.warning("Не удалось прочитать список подключений ФНС")
        time.sleep(60)
