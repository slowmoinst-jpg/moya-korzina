"""Файловый кэш ответов коннекторов и троттлинг запросов (раздел 5.4 спецификации).

Кэш — обычные JSON-файлы в data/cache/, TTL из config `connectors.cache_ttl_hours`.
Троттлинг — блокирующая пауза, не чаще `connectors.rate_limit_rps` запроса в секунду
НА МАГАЗИН (отдельный счётчик времени на каждый store_code).
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

_lock = threading.Lock()
_last_request: dict[str, float] = {}          # store_code -> time.monotonic() последнего запроса


# ---------- троттлинг ----------
def throttle(store_code: str) -> None:
    """Блокирующая пауза, чтобы к одному магазину не ходить чаще rate_limit_rps в секунду."""
    rps = float(config.get("connectors.rate_limit_rps", 1.0) or 1.0)
    interval = 1.0 / rps if rps > 0 else 0.0
    if interval <= 0:
        return
    with _lock:
        last = _last_request.get(store_code)
        now = time.monotonic()
        if last is not None:
            wait = interval - (now - last)
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
        _last_request[store_code] = now


def reset_throttle(store_code: str | None = None) -> None:
    """Сброс счётчиков (нужен тестам)."""
    with _lock:
        if store_code is None:
            _last_request.clear()
        else:
            _last_request.pop(store_code, None)


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
