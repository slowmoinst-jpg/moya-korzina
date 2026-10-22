"""Запись того, как магазин кладёт товар в корзину: снимает телефон, хранит сервер.

ЗАЧЕМ. Владелец 15.10.2026: «наша задача только, по сути, повторить авторизацию и
наполнение корзины в каждом личном кабинете в той сети, в которой он авторизовался».
Повторять наполнение решено запросами самой сети, а не нажатиями по карточкам
(решение от 01.10.2026: нажатия — минута на сеть). Для этого запросы надо знать, а
под входом их не видели ни у одной сети. Магнит гостю корзину не наполняет вовсе
(замер 20.09.2026). Пятёрочка и Самокат не пускают сервер даже через домашний выход
(403, замер 15.10.2026). Лента, ВкусВилл и Дикси под входом не разведаны. Снять эти
запросы можно только там, где человек вошёл, — на его телефоне.

КАК. Приложение на телефоне (android/app) по кнопке «Записать корзину» включает на
странице магазина запись её собственных запросов к своему же сайту. Человек кладёт
любой товар, убирает его и жмёт «Готово». Приложение шлёт сюда, что сайт при этом
спросил: метод, адрес, имена заголовков, СТРУКТУРУ тела запроса и ответа. По этой
записи потом пишется наполнение корзины каждой сети.

ЧЕГО ЗДЕСЬ НЕТ И БЫТЬ НЕ ДОЛЖНО. Значений заголовков: там ключи входа. Телефонов,
номеров карт, адресов, имён и кодов из СМС. Телефон отправляет уже разобранную
структуру, а здесь она разбирается ещё раз тем же правилом: сервер не полагается на
то, что сборка приложения на чужом телефоне сделала это правильно. Запросы входа
(auth, login, otp, sms…) не записываются вовсе — ни там, ни здесь.

Запись — свойство сети, а не человека: адрес и форма запроса корзины у всех
покупателей одни. Поэтому она лежит в общей папке data/cart_recipes, по файлу на
сеть, а не в базе рабочего места.

    python -m app.cartrecord           что записано по всем сетям
    python -m app.cartrecord magnit    по одной
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from app import config

log = logging.getLogger(__name__)

# Свои адреса каждой сети. Запрос к чужому адресу (счётчики посещений, реклама) в
# запись не попадает: 20.09.2026 пиксель Яндекс.Метрики с «toCart» в параметрах уже
# выдавал себя за запрос корзины Магнита (app/shopbrowser/cart.py, own_cart_calls).
# Хозяин адреса сверяется целиком, по окончанию имени хоста.
CHAIN_HOSTS: dict[str, tuple[str, ...]] = {
    "magnit": ("magnit.ru",),
    "pyaterochka": ("5ka.ru", "x5.ru"),
    "samokat": ("samokat.ru",),
    "lenta": ("lenta.com",),
    "vkusvill": ("vkusvill.ru",),
    "dixy": ("dixy.ru",),
    "metro": ("metro-cc.ru",),
    "perekrestok": ("perekrestok.ru",),
    # Хосты взяты из самих строк базы (замер 19.10.2026): у Vprok витрина на www.vprok.ru,
    # у Монетки — monetka.ru. Без этой записи адрес карточки этих сетей отбрасывался:
    # пустой список хостов читался как «сеть не наша».
    "vprok": ("vprok.ru",),
    "monetka": ("monetka.ru",),
    # Fix Price добавлен 20.10.2026. Сеть обходится сервером и цены у неё есть (6 624
    # строки), но в приложении её не было вовсе — значит собрать её корзину в фоне
    # человек не мог. Домен именно fix-price.com (замер по сборщику и живой карточке
    # 20.10.2026): «fixprice.ru» — не их адрес, и карточка оттуда не опозналась бы.
    "fixprice": ("fix-price.com",),
}

# Запросы входа и счётчиков не пишем вовсе: в теле входа номер телефона и код из СМС,
# а счётчики к корзине отношения не имеют. То же правило стоит в приложении.
SKIP_PATH = re.compile(r"auth|login|logout|otp|sms|verify|token|session|passw|register|"
                       r"sign|captcha|metric|analytic|track|collect|pixel|beacon|event",
                       re.IGNORECASE)

# Слова в имени поля, после которых значение не хранится никогда. Сверяются СЛОВА
# имени, а не подстроки: подстрока «lat» гасила бы calculated_total, а «tel» —
# retailer_id, и запись теряла бы ровно то, ради чего её снимают.
HIDDEN_WORDS = frozenset({
    "token", "auth", "session", "secret", "password", "pass", "passwd", "phone", "tel",
    "telephone", "msisdn", "email", "mail", "card", "pan", "address", "street", "house",
    "flat", "apartment", "entrance", "floor", "lat", "latitude", "lon", "lng", "longitude",
    "geo", "coords", "coordinates", "fio", "name", "firstname", "lastname", "surname",
    "user", "customer", "client", "device", "signature", "sign", "hash", "cookie",
    "birth", "birthday", "inn", "snils", "passport", "comment", "sms", "otp", "captcha",
    "ip",
})
# Длинное число под таким именем — код товара, а не номер телефона или карты.
ID_WORDS = frozenset({"id", "sku", "article", "product", "plu", "good", "goods", "item",
                      "code", "xml"})
# Короткий код без пробелов — «DELIVERY», «784182», «pcs». Только латиница и цифры, как
# и в приложении: одно русское слово без пробела бывает и именем, и городом.
SAFE_TEXT = re.compile(r"^[A-Za-z0-9_\-.:]*$")
LONG_DIGITS = re.compile(r"^\+?\d{11,}$")
# Отметки, которыми телефон уже заменил значение. Второй разбор их не трогает, иначе
# «<строка 12>» превращалась бы в «<строка 11>» — длину уже самой отметки.
MARKER = re.compile(r"^<(скрыто|число|строка \d+|текст \d+|тело|файл|\w{1,12})>$")

MAX_DEPTH = 6
MAX_KEYS = 40
MAX_ENTRIES = 60
# Свёрнутый список: сколько элементов и два для примера. Те же имена — в приложении.
LIST_MARK = "<список>"
LIST_SAMPLE = "пример"


def _words(key: str | None) -> set[str]:
    """«userId» → {user, id}; «retailer_id» → {retailer, id}."""
    if not key:
        return set()
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(key))
    return {w for w in re.split(r"[^a-zA-Zа-яА-Я0-9]+", spaced.lower()) if w}


def shape(value: Any, key: str | None = None, depth: int = 0) -> Any:
    """Структура значения без того, что выдаёт человека. Числа и короткие коды — как есть."""
    if depth > MAX_DEPTH:
        return "…"
    if value is None or isinstance(value, bool):
        return value
    words = _words(key)
    if words & HIDDEN_WORDS:
        return "<скрыто>"
    if isinstance(value, (int, float)):
        if isinstance(value, int) and len(str(abs(value))) >= 11 and not (words & ID_WORDS):
            return "<число>"
        return value
    if isinstance(value, str):
        if MARKER.match(value):
            return value
        if LONG_DIGITS.match(value) and not (words & ID_WORDS):
            return f"<строка {len(value)}>"
        if len(value) <= 24 and SAFE_TEXT.match(value):
            return value
        return f"<строка {len(value)}>"
    if isinstance(value, list):
        return {LIST_MARK: len(value), LIST_SAMPLE: [shape(v, key, depth + 1) for v in value[:2]]}
    if isinstance(value, dict) and LIST_MARK in value:
        # Список, который телефон уже свернул: второй разбор не заворачивает его снова.
        sample = value.get(LIST_SAMPLE)
        count = value[LIST_MARK]
        return {LIST_MARK: count if isinstance(count, int) else 0,
                LIST_SAMPLE: [shape(v, key, depth + 1) for v in (sample or [])[:2]]
                if isinstance(sample, list) else []}
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for n, (k, v) in enumerate(value.items()):
            if n >= MAX_KEYS:
                out["…"] = len(value)
                break
            out[str(k)] = shape(v, str(k), depth + 1)
        return out
    return f"<{type(value).__name__}>"


_ID_SEGMENT = re.compile(r"^(\d{3,}|[0-9a-fA-F-]{16,}|[A-Za-z0-9_\-]{20,})$")


def template_path(path: str) -> str:
    """Путь без идентификаторов: /api/v1/<токен>/10/basket/123 → /api/v1/{id}/10/basket/{id}."""
    return "/".join("{id}" if _ID_SEGMENT.match(seg) else seg for seg in (path or "").split("/"))


def own_host(host: str, chain: str) -> bool:
    host = (host or "").lower().strip(".")
    return any(host == d or host.endswith("." + d) for d in CHAIN_HOSTS.get(chain, ()))


def _body(value: Any) -> Any:
    """Тело запроса или ответа. Приходит уже структурой; строку разбираем здесь сами."""
    if value is None or isinstance(value, (dict, list, bool, int, float)):
        return shape(value) if isinstance(value, (dict, list)) else value
    text = str(value)
    if MARKER.match(text):
        return text                           # «<текст 120>» — отметка, а не данные
    try:
        return shape(json.loads(text))
    except ValueError:
        pass
    if "=" in text and " " not in text:
        return shape(dict(parse_qsl(text, keep_blank_values=True)))
    return f"<текст {len(text)}>"


def normalize(entry: dict, chain: str) -> dict | None:
    """Одна запись с телефона → запись для хранения. None — не корзина или чужой адрес."""
    if not isinstance(entry, dict):
        return None
    method = str(entry.get("method") or "GET").upper()[:10]
    host = str(entry.get("host") or "")
    path = str(entry.get("path") or "")
    if entry.get("url") and not host:
        parts = urlsplit(str(entry["url"]))
        host, path = parts.hostname or "", parts.path
    if not own_host(host, chain) or SKIP_PATH.search(path):
        return None
    query = entry.get("query")
    headers = entry.get("headers") if isinstance(entry.get("headers"), list) else []
    try:
        status = int(entry.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    return {
        "method": method,
        "host": host.lower(),
        "path": template_path(path)[:300],
        "query": shape(query) if isinstance(query, dict) else {},
        # Только имена: значения заголовков — это и есть ключи входа.
        "headers": sorted({str(h).lower()[:60] for h in headers if h})[:30],
        "body": _body(entry.get("body")),
        "status": status,
        "response": _body(entry.get("response")),
        "page": template_path(str(entry.get("page") or ""))[:200],
    }


def _dir() -> str:
    return os.path.join(config.ROOT, "data", "cart_recipes")


def _file(chain: str) -> str:
    return os.path.join(_dir(), f"{chain}.json")


def recipes(chain: str) -> dict:
    """Что записано по сети: {"calls": {"POST host/path": {...}}}. Нет записи — пусто."""
    try:
        with open(_file(chain), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"chain": chain, "calls": {}}
    return data if isinstance(data, dict) else {"chain": chain, "calls": {}}


def accept(body: dict) -> dict:
    """Принять запись с телефона. Ответ — что взято, словами для экрана приложения."""
    chain = str(body.get("store") or "").strip().lower()
    if chain not in CHAIN_HOSTS:
        raise ValueError(f"для сети «{chain or '—'}» запись корзины не предусмотрена")
    entries = body.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("в записи нет ни одного запроса")
    now = datetime.now().isoformat(timespec="seconds")
    data = recipes(chain)
    calls = data.setdefault("calls", {})
    taken: list[str] = []
    skipped = 0
    for entry in entries[:MAX_ENTRIES]:
        row = normalize(entry, chain)
        if row is None:
            skipped += 1
            continue
        key = f"{row['method']} {row['host']}{row['path']}"
        was = calls.get(key) or {}
        calls[key] = {**row, "first_seen": was.get("first_seen") or now, "last_seen": now,
                      "count": int(was.get("count") or 0) + 1}
        taken.append(key)
    data.update({"chain": chain, "updated": now,
                 "app_version": str(body.get("app_version") or "")[:20]})
    os.makedirs(_dir(), exist_ok=True)
    tmp = _file(chain) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, _file(chain))
    log.info("%s: записано запросов корзины %d, пропущено %d", chain, len(taken), skipped)
    return {"ok": True, "store": chain, "taken": len(taken), "skipped": skipped,
            "calls": sorted(set(taken))}


def report(chain: str | None = None) -> str:
    """Записанное словами: по запросу на строку, с формой тела и ответа."""
    out: list[str] = []
    for code in ([chain] if chain else sorted(CHAIN_HOSTS)):
        data = recipes(code)
        calls = data.get("calls") or {}
        if not calls:
            out.append(f"{code}: записи нет")
            continue
        out.append(f"{code}: запросов {len(calls)}, обновлено {data.get('updated')}")
        for key, row in sorted(calls.items()):
            out.append(f"  {key}  → {row.get('status')}  ×{row.get('count')}  "
                       f"(страница {row.get('page')})")
            out.append(f"      заголовки: {', '.join(row.get('headers') or []) or '—'}")
            for part in ("query", "body", "response"):
                value = row.get(part)
                if value not in (None, {}, ""):
                    text = json.dumps(value, ensure_ascii=False)
                    out.append(f"      {part}: {text[:1500]}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Что записано о запросах корзины сетей")
    parser.add_argument("chain", nargs="?", help="одна сеть вместо всех")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    print(report(args.chain))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
