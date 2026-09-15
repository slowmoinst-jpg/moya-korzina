"""Клиент MCP — способ, которым сети сами предлагают ходить за их данными.

ВкусВилл и Лента подняли серверы Model Context Protocol и открыли их без ключей.
Это первый случай в проекте, когда магазин не «разбирается», а отвечает по
собственному опубликованному контракту: у ВкусВилла есть инструмент, создающий
ссылку на корзину, у Ленты карточка товара отдаёт остаток числом.

Транспорт — streamable HTTP: обычный POST с телом JSON-RPC. Ответ приходит либо
чистым JSON, либо кадрами SSE («data: {...}»), поэтому разбираем оба вида.

Правило раздела 9 в силе и здесь: наружу исключений не выпускаем. Не ответил
сервер — вернули None, а коннектор сам решит, брать ли справочные цены.

ВАЖНО ПРО ЗАГОЛОВКИ: оба сервера стоят за фильтром, который смотрит на
User-Agent. Запрос без него получает 403 «Access Blocked», тот же запрос с
браузерным — 200. Проверено 13.09.2026.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app import config
from app.connectors.base import USER_AGENT, api_disabled, note_failure, note_success
from app.connectors.cache import cached_call

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-03-26"
_SSE_DATA = re.compile(r"^data:\s*(.+)$", re.M)

# url -> заголовок сессии, если сервер его выдал. Оба известных сервера сессию не держат,
# но протокол это допускает, и держать заголовок дешевле, чем однажды на нём споткнуться.
_SESSIONS: dict[str, str | None] = {}


def _headers(url: str) -> dict[str, str]:
    head = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": USER_AGENT,
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    session = _SESSIONS.get(url)
    if session:
        head["Mcp-Session-Id"] = session
    return head


def _decode(body: str) -> dict | None:
    """Тело ответа -> объект JSON-RPC. Понимает и голый JSON, и кадры SSE."""
    frames = _SSE_DATA.findall(body or "")
    for chunk in reversed(frames or [body or ""]):
        try:
            data = json.loads(chunk)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _post(url: str, store_code: str, payload: dict) -> dict | None:
    if api_disabled(store_code):
        return None
    try:
        import requests
    except ImportError:  # pragma: no cover
        return None
    timeout = float(config.get("connectors.timeout_sec", 10) or 10)
    try:
        resp = requests.post(url, headers=_headers(url), json=payload, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: MCP %s недоступен (%s)", store_code, url, exc)
        note_failure(store_code)
        return None
    if resp.status_code != 200:
        log.warning("%s: MCP %s ответил %s", store_code, url, resp.status_code)
        note_failure(store_code)
        return None
    session = resp.headers.get("Mcp-Session-Id")
    if session:
        _SESSIONS[url] = session
    note_success(store_code)
    return _decode(resp.text)


def handshake(url: str, store_code: str) -> dict | None:
    """initialize: проверяет, что сервер жив, и забирает его представление о себе."""
    data = _post(url, store_code, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "moya-korzina", "version": "0.1"},
        },
    })
    return (data or {}).get("result")


def list_tools(url: str, store_code: str) -> list[dict]:
    """Какие инструменты сервер объявляет сейчас. Нужно для диагностики, не для расчёта."""
    data = _post(url, store_code, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    return ((data or {}).get("result") or {}).get("tools") or []


def call_tool(url: str, store_code: str, tool: str, arguments: dict,
              cache_key: str | None = None) -> Any | None:
    """Вызов инструмента. Возвращает разобранный ответ инструмента либо None.

    Инструменты MCP отдают результат текстом внутри content[]; у обоих серверов это
    текст с JSON, поэтому пробуем его разобрать, а если внутри не JSON — отдаём строку.

    cache_key включает файловый кэш и троттлинг на 1 запрос в секунду к магазину.
    Без него запрос уходит сразу: так зовут то, что кэшировать нельзя, — создание
    ссылки на корзину.
    """
    payload = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
               "params": {"name": tool, "arguments": arguments}}

    def run() -> Any | None:
        data = _post(url, store_code, payload)
        if not data:
            return None
        if data.get("error"):
            log.warning("%s: инструмент %s ответил ошибкой: %s", store_code, tool, data["error"])
            return None
        if _rate_limited(data):
            # 429 от магазина — это «подожди», а не «сломалось». Отличать важно:
            # иначе предохранитель гасит магазин до конца сеанса из-за пары лишних
            # запросов, а расчёт молча уезжает на справочные цены.
            log.warning("%s: магазин просит сбавить темп (лимит запросов). "
                        "Ответа сейчас не будет, справочные цены тут не помогут.", store_code)
            return None
        result = data.get("result") or {}
        text = "".join(part.get("text", "") for part in (result.get("content") or [])
                       if isinstance(part, dict))
        if not text:
            return result or None
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return text

    if cache_key is None:
        return run()
    return cached_call(store_code, cache_key, run)


def _rate_limited(data: dict) -> bool:
    """Ответ вида {"ok": false, "code": "rate_limited"} — у ВкусВилла именно такой."""
    result = data.get("result") or {}
    text = "".join(part.get("text", "") for part in (result.get("content") or [])
                   if isinstance(part, dict))
    return "rate_limited" in text or "Превышен лимит запросов" in text


def ok_payload(answer: Any) -> dict | None:
    """Оба сервера отвечают {"ok": true, "data": {...}}. Достаёт data, если всё хорошо."""
    if not isinstance(answer, dict):
        return None
    if answer.get("ok") is False:
        return None
    data = answer.get("data")
    return data if isinstance(data, dict) else (answer if "ok" not in answer else None)
