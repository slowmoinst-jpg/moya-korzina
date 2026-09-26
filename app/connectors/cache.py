"""Файловый кэш ответов коннекторов и троттлинг запросов (раздел 5.4 спецификации).

Кэш — обычные JSON-файлы в data/cache/, TTL из config `connectors.cache_ttl_hours`.
Протухшие файлы убираются сами (не чаще раза в час), иначе папка росла без предела.

Троттлинг — не чаще `connectors.rate_limit_rps` запроса в секунду НА МАГАЗИН. Счётчик
общий для потоков И ПРОЦЕССОВ: экран приложения и ночной обход — разные процессы, и
раньше каждый держал свой темп, так что вместе они ходили в одну сеть вдвое чаще
разрешённого. Слот сети записан в файле data/pace/<сеть> под замком ядра; ждут —
уже отпустив замок, поэтому пауза одной сети не держит запросы к остальным.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Callable

from app import config

log = logging.getLogger(__name__)

CACHE_DIR = os.path.join(config.ROOT, "data", "cache")
PACE_DIR = os.path.join(config.ROOT, "data", "pace")
PRUNE_EVERY_SEC = 3600.0

_lock = threading.Lock()
_last_request: dict[str, float] = {}          # store_code -> time.time() занятого слота
_last_prune = 0.0


# ---------- троттлинг ----------
def _ahead_limit(interval: float) -> float:
    """Насколько вперёд слот сети может честно уйти: очередь из полусотни запросов.

    Дальше — не очередь, а сбитые часы (перевод времени, NTP): чужая или своя
    метка из «будущего» иначе усыпила бы запрос на час.
    """
    return max(60.0, 50.0 * interval)


def _shared_slot(store_code: str, slot: float, interval: float) -> float:
    """Слот с учётом других процессов: не раньше, чем через interval после чужого.

    Замок ядра (flock) держится ровно на чтение и запись метки, без сна. На Windows
    fcntl нет — там темп держится внутри процесса, как и раньше.
    """
    try:
        import fcntl
    except ImportError:
        return slot
    try:
        os.makedirs(PACE_DIR, exist_ok=True)
        with open(os.path.join(PACE_DIR, store_code), "a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                raw = handle.read().strip()
                try:
                    other = float(raw) if raw else None
                except ValueError:
                    other = None
                if other is not None and other - time.time() <= _ahead_limit(interval):
                    slot = max(slot, other + interval)
                handle.seek(0)
                handle.truncate()
                handle.write(repr(slot))
                handle.flush()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        log.debug("темп %s: общий слот не записан (%s)", store_code, exc)
    return slot


def reserve(store_code: str, interval: float) -> float:
    """Занять следующий слот сети. Возвращает, сколько секунд ждать до него."""
    if interval <= 0:
        return 0.0
    with _lock:
        now = time.time()
        last = _last_request.get(store_code)
        if last is not None and last - now > _ahead_limit(interval):
            last = None                       # часы сдвинулись назад — метка не в счёт
        slot = now if last is None else max(now, last + interval)
        slot = _shared_slot(store_code, slot, interval)
        _last_request[store_code] = slot
    return max(0.0, slot - time.time())


def throttle(store_code: str) -> None:
    """Блокирующая пауза, чтобы к одному магазину не ходить чаще rate_limit_rps в секунду.

    Спим ВНЕ общего замка: раньше time.sleep стоял под ним, и ожидание Магнита
    держало живой поиск во ВкусВилле и Ленте, хотя им ждать было нечего.
    """
    rps = float(config.get("connectors.rate_limit_rps", 1.0) or 1.0)
    interval = 1.0 / rps if rps > 0 else 0.0
    wait = reserve(store_code, interval)
    if wait > 0:
        time.sleep(wait)


def reset_throttle(store_code: str | None = None) -> None:
    """Сброс счётчиков (нужен тестам)."""
    with _lock:
        if store_code is None:
            _last_request.clear()
        else:
            _last_request.pop(store_code, None)
        if not os.path.isdir(PACE_DIR):
            return
        for name in os.listdir(PACE_DIR):
            if store_code is None or name == store_code:
                try:
                    os.remove(os.path.join(PACE_DIR, name))
                except OSError:
                    pass


# ---------- файловый кэш ----------
def _ttl_seconds() -> float:
    return float(config.get("connectors.cache_ttl_hours", 6) or 0) * 3600.0


def _path(store_code: str, key: str) -> str:
    digest = hashlib.md5(f"{store_code}|{key}".encode("utf-8")).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"{store_code}_{digest}.json")


def cache_get(store_code: str, key: str) -> Any | None:
    """Значение из кэша или None, если его нет / протухло / файл битый."""
    path = _path(store_code, key)
    try:
        if not os.path.exists(path):
            return None
        ttl = _ttl_seconds()
        if ttl > 0 and time.time() - os.path.getmtime(path) > ttl:
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("value")
    except Exception as exc:                                  # битый файл кэшем не считаем
        log.warning("кэш %s: не прочитан (%s)", path, exc)
        return None


def cache_set(store_code: str, key: str, value: Any) -> None:
    if value is None:                                         # неудачный ответ не кэшируем
        return
    path = _path(store_code, key)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"key": key, "saved_at": time.time(), "value": value}, fh, ensure_ascii=False)
    except Exception as exc:
        log.warning("кэш %s: не записан (%s)", path, exc)
    _maybe_prune()


def prune(older_than_sec: float | None = None) -> int:
    """Удалить протухшие файлы кэша. Возвращает, сколько удалено.

    Протухший файл cache_get всё равно не читает — он только занимает место на
    томе сервера, а место там конечное.
    """
    limit = _ttl_seconds() if older_than_sec is None else float(older_than_sec)
    if limit <= 0 or not os.path.isdir(CACHE_DIR):
        return 0
    border = time.time() - limit
    removed = 0
    for name in os.listdir(CACHE_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(CACHE_DIR, name)
        try:
            if os.path.getmtime(path) < border:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    return removed


def _maybe_prune() -> None:
    global _last_prune
    now = time.time()
    if now - _last_prune < PRUNE_EVERY_SEC:
        return
    _last_prune = now
    try:
        removed = prune()
        if removed:
            log.info("кэш: убрано протухших файлов %d", removed)
    except Exception:  # noqa: BLE001 — уборка не повод ронять запрос
        log.debug("кэш: уборка не удалась", exc_info=True)


def cache_clear(store_code: str | None = None) -> None:
    """Удаляет файлы кэша (все или одного магазина). Нужно тестам и отладке."""
    if not os.path.isdir(CACHE_DIR):
        return
    prefix = f"{store_code}_" if store_code else ""
    for name in os.listdir(CACHE_DIR):
        if name.endswith(".json") and name.startswith(prefix):
            try:
                os.remove(os.path.join(CACHE_DIR, name))
            except OSError:
                pass


def cached_call(store_code: str, key: str, fn: Callable[[], Any]) -> Any:
    """Кэш + троттлинг вокруг сетевого вызова.

    Попадание в кэш — сеть не трогаем вообще (и паузу не держим).
    Промах — ждём свой слот по rate_limit_rps, зовём fn(), кладём непустой результат в кэш.
    """
    hit = cache_get(store_code, key)
    if hit is not None:
        return hit
    throttle(store_code)
    value = fn()
    cache_set(store_code, key, value)
    return value
