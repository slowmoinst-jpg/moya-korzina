"""Отчёт сборщика: одна дверь для всего, что человек собрал в своём браузере.

Сборщик — расширение, живущее во вкладке человека. Почему именно там, разобрано
в app/pricebundle.py и app/store_accounts.py: серверного входа не существует ни у
одной сети, капчу за человека не пройти, а из обычной вкладки обычного покупателя
магазин открывается как для любого покупателя — потому что он и есть покупатель.

С ОДНОЙ СТРАНИЦЫ СБОРЩИК ВИДИТ СРАЗУ НЕСКОЛЬКО ВЕЩЕЙ. Цены витрины, личные купоны,
вошёл человек в аккаунт или нет, и чем кончился наряд на корзину (app/cartplan.py).
Всё это — об одном мгновении и об одном аккаунте, поэтому и принимается одним
вызовом accept().

ПОЧЕМУ ДВЕРЬ ОДНА, А НЕ ТРИ. Прими части отчёта тремя отдельными вызовами — и
появятся состояния, которых в жизни не бывает: цены по карте уже записаны, а
подключение ещё не отмечено, и расчёт считает по личным ценам, будучи уверенным,
что человек не входил. Или наоборот: купоны легли, цены потерялись на полпути, а
человек об этом не узнал, потому что первый вызов успел ответить «принято».

ПРОВЕРКА ЦЕЛИКОМ ДО ПЕРВОЙ ЗАПИСИ. Отчёт сначала разбирается весь (parse) и только
потом пишется (accept). Поэтому кривой отчёт не оставляет следов: «половина
принята» здесь не бывает. Это и есть та атомарность, которая тут достижима, —
одной транзакцией три разных хранилища (цены в таблицах, подключение и купоны в
настройках) всё равно не накрыть, а «не начинать, пока не проверено» — накрывает.

ЧТО ЛЕЖИТ В ОТЧЁТЕ

    {"store": "pyaterochka",           обязательное: код сети из store_accounts.ABILITIES
     "address": "Москва, …",           адрес, по которому сеть считала цены
     "store_code": "…",                код точки в сети, если он виден
     "collected_at": "2026-09-16T23:40:00",
     "logged_in": true,                вошёл ли человек в аккаунт ПРЯМО СЕЙЧАС
     "account": "Карта X5 •••1234",    как магазин называет человека
     "gives": ["prices", "coupons", "cart"],   что сборщик реально принёс
     "items": [ … как в pricebundle … ],
     "coupons": [{"id": "…", "title": "-30% на молоко",
                  "ends_at": "2026-09-30", "value": "-30%"}],
     "cart_result": {"ok": ["sku1"], "failed": [{"sku": "sku2", "why": "нет в наличии"}]}}

Любой раздел может отсутствовать: со страницы каталога придут одни цены, из
личного кабинета — купоны и признак входа, после исполнения наряда — cart_result.
Отсутствие раздела и пустой раздел — РАЗНЫЕ ответы, и различать их приходится
всерьёз. «logged_in нет вовсе» значит «сборщик не смотрел», и подключение трогать
нельзя: гасить его от каждого отчёта со страницы каталога — значит выключать
человеку личные цены на ровном месте. «logged_in: false» значит «смотрел, человек
вышел», и подключение обязано погаснуть: врать, что он подключён, нельзя.

ГДЕ ЧТО ОКАЗЫВАЕТСЯ

    цены      → app/pricebundle.py, оттуда в store_prices; своей логики разбора
                цен здесь нет и быть не должно — два приёмника цен разошлись бы;
    вход      → store_accounts.mark_connected с ТЕМИ умениями, что реально пришли,
                а не с обещанными справочником: обещание из ABILITIES — это то,
                что сеть умеет вообще, а gives — то, что доехало сегодня;
    купоны    → настройки рабочего места (COUPONS_KEY), читаются coupons();
    корзина   → в сводку и в отчёт о ПЕРЕДАЧЕ (HANDOVER_KEY), но не в состояние
                корзины. Хранить «в корзине лежит вот это» нельзя: человек мог
                поменять её руками у себя в магазине, и мы бы с ней разошлись, не
                узнав. А «тогда-то мы положили 14 позиций, две не легли — почему»
                верно навсегда: это про наше действие, а не про его корзину.

НИ ОДНОГО СЕКРЕТА. Отчёт приходит из браузера, где рядом с ценами лежат и куки, и
токены, и код из СМС. Сборщик их не собирает, но приёмник не может на это
надеяться: достаточно одной строки в расширении, чтобы чужой секрет поехал к нам и
остался в базе навсегда. Поэтому всё, что похоже на секрет по имени поля,
выбрасывается ДО первой записи (_scrub), на любой глубине, и об этом остаётся
запись в журнале — с именами полей, но без значений: писать секрет в лог — то же
самое, что записать его в базу, только незаметнее. Сюда же ссылки: адрес вида
…?token=… чистится от таких параметров, а не отбрасывается целиком, потому что
карточка товара человеку нужна.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app import pricebundle, repo, store_accounts

log = logging.getLogger(__name__)

# Купоны лежат в настройках рабочего места, рядом с подключениями (store_accounts.KEY).
# Не таблица, потому что это не история и не справочник, а короткий снимок «что у
# человека на руках сейчас»: он целиком заменяется следующим отчётом.
COUPONS_KEY = "store_coupons"

MAX_COUPONS = 500          # больше — это выгрузка витрины, а не личные предложения
MAX_CART_LINES = 5000      # наряд на корзину столько не содержит ни при каком походе
TITLE_LIMIT = 200
VALUE_LIMIT = 100
WHY_LIMIT = 200
ID_LIMIT = 80
ACCOUNT_LIMIT = 120
STAMP_LIMIT = 64

# Части имён, по которым поле выбрасывается на входе. Подстрокой, потому что
# сочинить можно что угодно: authToken, x-csrf-token, magnitAccessToken.
SECRET_PARTS = ("token", "password", "passwd", "secret", "cookie", "jwt", "bearer",
                "apikey", "credential", "captcha", "session", "authorization",
                "signature", "парол", "токен", "куки", "секрет", "капча")

# А эти — только целиком. «code» подстрокой искать нельзя: под неё попадёт
# store_code, честный код точки магазина, без которого цены не к чему привязать.
SECRET_NAMES = frozenset({"code", "otp", "sms", "smscode", "pin", "auth", "key",
                          "csrf", "pwd", "cvv", "cvc", "hash", "код"})

# Поля, в которых лежит адрес: их чистим от параметров с секретами, а не выбрасываем.
URL_NAMES = frozenset({"url", "link", "href", "image", "img", "photo", "picture", "icon"})

_NOT_LETTERS = re.compile(r"[^a-zа-я0-9]+")
_LONG_DIGITS = re.compile(r"\d{5,}")
_ISO_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_RU_DAY = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})")


# ---------- мелкая работа с полями ----------
def _plain(name: Any) -> str:
    """Имя поля без украшений: X-CSRF-Token и csrfToken должны сойтись в одно."""
    return _NOT_LETTERS.sub("", str(name).lower().replace("ё", "е"))


def _is_secret(name: Any) -> bool:
    plain = _plain(name)
    if not plain:
        return False
    return plain in SECRET_NAMES or any(part in plain for part in SECRET_PARTS)


def _text(value: Any, limit: int) -> str:
    """Строка без лишних пробелов и не длиннее разумного.

    Длина ограничивается не из экономии: настройки рабочего места — это одна
    строка в SQLite, и мегабайт описания акции, случайно попавший в название
    купона, сделал бы её нечитаемой для всех остальных.
    """
    return " ".join(str(value if value is not None else "").split())[:limit]


def _kept_pairs(chunk: str, dropped: list[str], where: str) -> str:
    kept = []
    for pair in chunk.split("&"):
        name = pair.split("=", 1)[0]
        if _is_secret(name):
            dropped.append(f"{where}?{name}")
            continue
        kept.append(pair)
    return "&".join(kept)


def _safe_url(raw: Any, dropped: list[str], where: str) -> str:
    """Адрес без параметров, похожих на ключ доступа.

    Ссылка на карточку товара человеку нужна, поэтому целиком её не выбрасываем.
    А вот …/product/123?access_token=… в базе остался бы навсегда и утёк бы вместе
    с любой выгрузкой цен — этого допускать нельзя.
    """
    url = _text(raw, 2000)
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:            # адрес, который не разбирается, нам всё равно не нужен
        return ""
    query = _kept_pairs(parts.query, dropped, where) if parts.query else parts.query
    fragment = (_kept_pairs(parts.fragment, dropped, where)
                if "=" in parts.fragment else parts.fragment)
    if query == parts.query and fragment == parts.fragment:
        return url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, fragment))


def _scrub(value: Any, dropped: list[str], path: str = "") -> Any:
    """Отчёт без полей, похожих на секреты, — на любой глубине.

    Обход рекурсивный нарочно: секрет приезжает не только верхним полем, он
    оказывается внутри позиции, внутри купона, внутри вложенного «meta». Имена
    выброшенных полей копятся в dropped — чтобы в журнал попало, ЧТО пришло, и не
    попало, ЧЕМУ оно было равно.
    """
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            where = f"{path}.{name}" if path else name
            if _is_secret(name):
                dropped.append(where)
                continue
            if isinstance(item, str) and _plain(name) in URL_NAMES:
                clean[name] = _safe_url(item, dropped, where)
                continue
            clean[name] = _scrub(item, dropped, where)
        return clean
    if isinstance(value, list):
        return [_scrub(item, dropped, f"{path}[]") for item in value]
    return value


def _as_data(payload: str | bytes | dict) -> Any:
    if isinstance(payload, (str, bytes)):
        try:
            return json.loads(payload)
        except ValueError as err:
            raise ValueError(f"отчёт сборщика не разобрался как JSON: {err}") from None
    return payload


def _account(raw: Any) -> str | None:
    """Как магазин называет человека — но без номера карты целиком.

    Магазин подписывает вошедшего по-разному: «Иван», «+7 999 …», «Карта X5
    •••1234». Всё это можно и нужно показать на экране, чтобы человек видел, ЧЕЙ
    аккаунт подключён. А вот полный номер карты — уже не подпись, а платёжный
    реквизит, и хранить его мы не станем даже по чужой неосторожности: длинная
    цепочка цифр сворачивается в последние четыре.
    """
    text = _text(raw, ACCOUNT_LIMIT)
    if not text:
        return None
    return _LONG_DIGITS.sub(lambda m: "•••" + m.group(0)[-4:], text)


def _stamp(raw: Any) -> str | None:
    """Время сборки в том виде, в каком его понимает вся остальная база.

    Две ловушки, обе тихие. Первая: браузер отдаёт время в UTC с буквой Z
    (toISOString), а база живёт по местному времени, и сложенные вместе они дают
    цены «из будущего» на три часа — снимок, собранный минуту назад, оказывается
    свежее сегодняшнего. Поэтому время с зоной переводится в местное. Вторая:
    неразобранная строка ушла бы в fetched_at как есть и перемешала бы историю
    цен, которая сортируется строкой, — поэтому непонятное время это отказ, а не
    «ну, положим как пришло».
    """
    text = _text(raw, STAMP_LIMIT)
    if not text:
        return None
    candidate = text.replace(" ", "T")
    if candidate[-1:] in ("z", "Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(candidate)
    except ValueError:
        raise ValueError(f"«collected_at» не похоже на время сборки: «{text}»") from None
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment.isoformat(timespec="seconds")


def _day(raw: Any) -> str | None:
    """День в виде ГГГГ-ММ-ДД. Непонятная дата — это «без срока», а не отказ.

    Купон без срока — обычное дело («действует, пока магазин не передумает»), и
    ронять из-за него весь отчёт незачем. Сравнивать дни строками можно ровно
    потому, что вид у них один.
    """
    text = _text(raw, 32)
    iso = _ISO_DAY.match(text)
    if iso:
        return f"{iso.group(1)}-{iso.group(2)}-{iso.group(3)}"
    ru = _RU_DAY.match(text)
    if ru:
        return f"{ru.group(3)}-{ru.group(2)}-{ru.group(1)}"
    return None


def _today() -> str:
    return date.today().isoformat()


# ---------- купоны ----------
def _coupon(raw: Any) -> dict | None:
    """Один купон или None, если показывать его человеку нечестно.

    Обязательно только название: именно оно попадёт на экран. Купон без названия
    показать нельзя — получится строка «-30%» неизвестно на что, и человек либо
    не поймёт её, либо поймёт неправильно.
    """
    if not isinstance(raw, dict):
        return None
    title = _text(raw.get("title") or raw.get("name"), TITLE_LIMIT)
    if not title:
        return None
    value = _text(raw.get("value") or raw.get("discount"), VALUE_LIMIT) or None
    coupon = {
        "id": _text(raw.get("id"), ID_LIMIT) or None,
        "title": title,
        "ends_at": _day(raw.get("ends_at") or raw.get("expires_at") or raw.get("end")),
        "value": value,
    }
    # Цифры купона — то, что делает его частью расчёта (app/personal.py), а не только
    # строкой на экране. Всё необязательное: купон без цифр по-прежнему показывается.
    coupon.update(_coupon_terms(raw, value))
    skus = raw.get("skus") if isinstance(raw.get("skus"), list) else [raw.get("sku")]
    skus = [_text(s, ID_LIMIT) for s in skus if s not in (None, "")]
    if skus:
        coupon["skus"] = [s for s in skus if s][:50]
    target = _text(raw.get("product") or raw.get("target"), TITLE_LIMIT)
    if target:
        coupon["product"] = target
    if raw.get("activated") is not None or raw.get("active") is not None:
        coupon["activated"] = bool(raw.get("activated", raw.get("active")))
    return coupon


_PERCENT = re.compile(r"(\d{1,2}(?:[.,]\d+)?)\s*%")
_RUB = re.compile(r"(\d[\d\s]*(?:[.,]\d{1,2})?)\s*(?:₽|руб|р\.)", re.I)


def _money(raw: Any) -> float | None:
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    try:
        number = float(str(raw).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    return number if 0 < number < 1_000_000 else None


def _coupon_terms(raw: dict, value: str | None) -> dict:
    """Что купон делает с ценой: личная цена, процент или рубли скидки.

    Явные поля сборщика (price, percent, off_rub) главнее разбора подписи: подпись
    «-30%» читается однозначно, а «до 500 ₽» — уже нет. Непонятное не угадывается:
    купон без цифр остаётся на экране, но в расчёт не идёт.
    """
    terms: dict = {}
    price = _money(raw.get("price") or raw.get("personal_price"))
    percent = _money(raw.get("percent"))
    off = _money(raw.get("off_rub"))
    text = value or ""
    if percent is None and price is None and off is None and text:
        found = _PERCENT.search(text)
        if found:
            percent = _money(found.group(1))
        else:
            rub = _RUB.search(text)
            if rub:
                # «-50 ₽» — скидка рублями; «149 ₽» — личная цена
                off, price = ((_money(rub.group(1)), None) if text.strip().startswith(("-", "−", "–"))
                              else (None, _money(rub.group(1))))
    if price is not None:
        terms["price"] = round(price, 2)
    if percent is not None and percent < 100:
        terms["percent"] = round(percent, 2)
    if off is not None:
        terms["off_rub"] = round(off, 2)
    return terms


def _coupon_key(coupon: dict) -> str:
    """Чем купон отличается от соседа: своим кодом, а если его нет — названием."""
    return str(coupon.get("id") or "").strip().lower() or str(coupon.get("title") or "").strip().lower()


def _expired(coupon: Any, today: str) -> bool:
    if not isinstance(coupon, dict):
        return True
    ends_at = coupon.get("ends_at")
    return bool(ends_at) and str(ends_at) < today


def _stored() -> dict[str, dict]:
    raw = repo.get_setting(COUPONS_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("купоны магазинов не прочитались — начинаю с пустого")
        return {}
    return data if isinstance(data, dict) else {}


def _merge_coupons(previous: list, arrived: list[dict], today: str) -> list[dict]:
    """Купоны после нового отчёта: пришедшие плюс истёкшие из прежних.

    Отчёт — снимок того, что магазин показывает человеку сейчас, поэтому
    действующие купоны он заменяет целиком: потраченный купон исчез из аккаунта,
    и показывать его дальше значило бы обещать скидку, которой уже нет.

    А вот истёкшие остаются лежать. Они никому не показываются (их отсеивает
    coupons), но и стереться молча не должны: «в сентябре у вас было -30% на
    молоко» — это знание о том, что сеть вообще даёт такому покупателю, и стоит
    оно ровно столько же, сколько прошлогодняя цена в истории.
    """
    fresh = {_coupon_key(c) for c in arrived}
    kept = [c for c in previous
            if isinstance(c, dict) and _coupon_key(c) not in fresh and _expired(c, today)]
    return arrived + kept


def _remember_coupons(store_code: str, arrived: list[dict], seen_at: str) -> None:
    data = _stored()
    previous = (data.get(store_code) or {}).get("items") or []
    data[store_code] = {
        "updated_at": seen_at,
        "items": _merge_coupons(previous, arrived, _today()),
    }
    repo.set_setting(COUPONS_KEY, json.dumps(data, ensure_ascii=False))


def coupons(store_code: str, *, today: str | None = None) -> list[dict]:
    """Действующие купоны сети: ближайшие к концу — первыми.

    Просроченные не отдаются, но и не стираются — они остаются в хранилище как
    история (см. _merge_coupons). Отсев делается при чтении, а не при записи,
    ровно по этой причине: запись, которая чистит за собой, необратима, а чтение
    можно повторить с другим днём.

    День передаётся явно там, где он должен быть один на весь экран или на весь
    тест. По умолчанию — сегодняшний.
    """
    day = today or _today()
    items = (_stored().get(store_code) or {}).get("items") or []
    live = [c for c in items if isinstance(c, dict) and not _expired(c, day)]
    live.sort(key=lambda c: (str(c.get("ends_at") or "9999-12-31"), str(c.get("title") or "")))
    return live


def coupons_updated_at(store_code: str) -> str | None:
    """Когда сборщик в последний раз приносил купоны этой сети.

    Без этой отметки список купонов нечестен: человек не отличит «купонов нет» от
    «мы их не видели с прошлого месяца», а это разные ответы.
    """
    return (_stored().get(store_code) or {}).get("updated_at")


# Чем кончилась последняя передача корзины. Это СОБЫТИЕ, а не состояние корзины,
# и разница здесь принципиальна. «В корзине лежит вот это» хранить нельзя: человек
# мог поменять её руками у себя в магазине, и мы бы с ней разошлись, не узнав об
# этом. А «17.09 в 18:42 мы положили 14 позиций, две не легли — творога не было в
# наличии» верно навсегда, потому что говорит о нашем действии, а не о его корзине.
# Без этой записи человек нажимает «Передать корзину» и не узнаёт в приложении
# ничего: отчёт уходил в журнал сервера, которого он не читает.
HANDOVER_KEY = "store_handover"


def _remember_handover(store_code: str, cart: dict, at: str) -> None:
    raw = repo.get_setting(HANDOVER_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        log.warning("отчёты о передаче не прочитались — начинаю с пустого")
        data = {}
    if not isinstance(data, dict):
        data = {}
    data[store_code] = {"at": at, "ok": len(cart["ok"]), "failed": cart["failed"]}
    repo.set_setting(HANDOVER_KEY, json.dumps(data, ensure_ascii=False))


def handover_report(store_code: str) -> dict | None:
    """Чем кончилась последняя передача корзины в эту сеть. Не было — None."""
    raw = repo.get_setting(HANDOVER_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    got = data.get(store_code) if isinstance(data, dict) else None
    return got if isinstance(got, dict) else None


# ---------- корзина ----------
def _cart(raw: Any) -> dict | None:
    """Чем кончился наряд: что легло в корзину и что не легло — с причиной.

    Причина обязана доехать до человека целиком. «Положено 12 из 14» без слов
    «творога не было в наличии» заставит его пересобирать корзину вслепую.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("«cart_result» в отчёте должен быть объектом с «ok» и «failed»")
    ok_raw = raw.get("ok") or []
    failed_raw = raw.get("failed") or []
    if not isinstance(ok_raw, list) or not isinstance(failed_raw, list):
        raise ValueError("в «cart_result» и «ok», и «failed» должны быть списками")
    if len(ok_raw) + len(failed_raw) > MAX_CART_LINES:
        raise ValueError("в «cart_result» больше позиций, чем бывает в наряде на корзину")

    ok = [sku for sku in (_text(item, ID_LIMIT) for item in ok_raw) if sku]
    failed: list[dict] = []
    for item in failed_raw:
        if isinstance(item, dict):
            sku = _text(item.get("sku") or item.get("id"), ID_LIMIT)
            why = _text(item.get("why") or item.get("reason"), WHY_LIMIT)
        else:
            # Голая строка вместо пары — беднее, но это всё ещё факт «не легло»,
            # и терять его из-за формы записи нельзя.
            sku, why = _text(item, ID_LIMIT), ""
        if not sku and not why:
            continue
        failed.append({"sku": sku or None, "why": why or None})
    return {"ok": ok, "failed": failed}


# ---------- разбор отчёта ----------
def _gives(data: dict, evidence: tuple[str, ...], store: str) -> list[str]:
    """Что подключение реально даёт после этого отчёта.

    Складывается из двух вещей, и обе нужны. Названное сборщиком — это его ответ
    «я умею в этой сети вот это». Принесённое (evidence) — доказательство: если в
    отчёте лежат купоны, значит купоны эта сеть вошедшему отдаёт, что бы ни
    говорил справочник ABILITIES и что бы сборщик ни забыл перечислить.

    Чего здесь нет — обещаний справочника. Записать их значило бы разрешить
    наряд на корзину сети, которая корзину сегодня не приняла, и человек узнал бы
    об этом, только не найдя товары у себя в магазине.
    """
    raw = data.get("gives")
    if raw is None:
        declared = []
    elif isinstance(raw, (list, tuple)):
        declared = [_plain(item) for item in raw]
    else:
        raise ValueError("«gives» в отчёте должен быть списком: что именно сборщик принёс")

    unknown = sorted({g for g in declared if g and g not in store_accounts.ABILITY_NAMES})
    if unknown:
        log.warning("%s: сборщик назвал незнакомые умения (%s) — они не записываются",
                    store, ", ".join(unknown))

    gives: list[str] = []
    for ability in [g for g in declared if g in store_accounts.ABILITY_NAMES] + list(evidence):
        if ability not in gives:
            gives.append(ability)
    return gives


def parse(payload: str | bytes | dict) -> dict:
    """Разобрать и проверить отчёт целиком, ничего не записывая.

    Отдельно от записи — чтобы половина отчёта никогда не оказалась принятой:
    любой отказ случается здесь, до первого обращения к базе. Заодно разбор можно
    показать человеку до того, как что-то произойдёт, — тем же способом, каким
    показывается наряд на корзину.
    """
    data = _as_data(payload)
    if not isinstance(data, dict):
        raise ValueError("отчёт сборщика должен быть объектом JSON")

    dropped: list[str] = []
    data = _scrub(data, dropped)
    if dropped:
        # Имена — да, значения — никогда: секрет в журнале хранится ровно так же
        # долго, как в базе, только найти его там труднее.
        log.warning("в отчёте сборщика пришли поля, похожие на секреты, — "
                    "выброшены до записи: %s", ", ".join(sorted(set(dropped))))

    store = _text(data.get("store"), 40).lower()
    if store not in store_accounts.ABILITIES:
        raise ValueError(f"сеть «{store or '—'}» сборщик не обслуживает; "
                         f"ожидались: {', '.join(sorted(store_accounts.ABILITIES))}")

    logged_in = data.get("logged_in")
    if logged_in is not None and not isinstance(logged_in, bool):
        # Строка «false» в Python истинна, и подключение зажглось бы как раз тогда,
        # когда человек из магазина вышел. Такой отчёт лучше не принимать вовсе.
        raise ValueError("«logged_in» в отчёте должен быть true или false")

    collected_at = _stamp(data.get("collected_at"))
    address = _text(data.get("address"), 300) or None
    store_code = _text(data.get("store_code"), ID_LIMIT) or None

    raw_items = data.get("items")
    if raw_items is not None and not isinstance(raw_items, list):
        raise ValueError("«items» в отчёте должен быть списком позиций")
    prices = None
    if raw_items:
        # Разбор цен целиком чужой: у пакета цен своя дверь, свои правила про ноль
        # и акцию и свой счёт непонятых позиций. Второй такой разбор здесь
        # разошёлся бы с первым в первый же день.
        prices = pricebundle.parse({"store": store, "address": address,
                                    "store_code": store_code,
                                    "collected_at": collected_at, "items": raw_items})

    raw_coupons = data.get("coupons")
    if raw_coupons is not None and not isinstance(raw_coupons, list):
        raise ValueError("«coupons» в отчёте должен быть списком купонов")
    if raw_coupons is not None and len(raw_coupons) > MAX_COUPONS:
        raise ValueError(f"в отчёте {len(raw_coupons)} купонов — это выгрузка витрины, "
                         "а не личные предложения человека")
    arrived: list[dict] = []
    coupons_skipped = 0
    seen: set[str] = set()
    for raw in raw_coupons or []:
        coupon = _coupon(raw)
        if not coupon:
            coupons_skipped += 1
            continue
        if _coupon_key(coupon) in seen:      # один купон показан и в списке, и в баннере
            continue
        seen.add(_coupon_key(coupon))
        arrived.append(coupon)

    cart = _cart(data.get("cart_result"))

    evidence: list[str] = []
    if prices and prices["items"]:
        evidence.append(store_accounts.PRICES)
    if arrived:
        evidence.append(store_accounts.COUPONS)
    if cart is not None:
        evidence.append(store_accounts.CART)
    gives = _gives(data, tuple(evidence), store)

    coupons_collected = bool(arrived) or store_accounts.COUPONS in gives

    points = data.get("points")
    try:
        points = float(points) if points is not None else None
    except (ValueError, TypeError):
        points = None

    return {
        "store": store,
        "address": address,
        "store_code": store_code,
        "collected_at": collected_at,
        "logged_in": logged_in,
        "account": _account(data.get("account")),
        "gives": gives,
        "points": points,
        "prices": prices,
        "coupons": arrived,
        "coupons_skipped": coupons_skipped,
        "coupons_collected": coupons_collected,
        "cart": cart,
        "dropped": sorted(set(dropped)),
    }


def accept(payload: str | bytes | dict) -> dict:
    """Принять отчёт сборщика целиком и вернуть сводку для экрана.

    Сводка нужна не для красоты. «Взято 1240 цен, не разобрано 3» — единственный
    способ заметить, что сеть сменила вёрстку, до того как расчёт начнёт врать;
    «подключение есть, купонов 4, в корзину легло 12 из 14» — единственный способ
    для человека понять, что вообще сейчас произошло в его браузере.

    Порядок записи — цены, купоны, подключение — выбран так, чтобы последним
    менялось то, что человек видит на экране первым: если бы подключение
    зажигалось раньше цен, экран успел бы сказать «личные цены получены» до того,
    как они действительно легли.
    """
    report = parse(payload)
    store = report["store"]
    stamp = report["collected_at"] or datetime.now().isoformat(timespec="seconds")

    saved = 0
    if report["prices"]:
        # Разобранный пакет остаётся пакетом: import_prices пропустит его через ту
        # же дверь parse ещё раз и получит то же самое. Зато до этой строки не
        # происходит ни одной записи, даже если дальше в отчёте окажется мусор.
        saved = pricebundle.import_prices(report["prices"])["saved"]

    if report["coupons_collected"]:
        _remember_coupons(store, report["coupons"], seen_at=stamp)

    if report["logged_in"] is True:
        store_accounts.mark_connected(store, account=report["account"],
                                      gives=report["gives"],
                                      points=report.get("points"))
    elif report["logged_in"] is False:
        store_accounts.forget(store)
    state = store_accounts.connection(store)

    cart = report["cart"]
    if cart is not None:
        # Событие, а не состояние корзины: см. HANDOVER_KEY. Человек нажал
        # «Передать корзину» в приложении — там же он и должен узнать, чем это
        # кончилось, а не в журнале сервера, которого он не читает.
        _remember_handover(store, cart, stamp)
    if cart and cart["failed"]:
        log.warning("%s: в корзину не легло %d позиц.: %s", store, len(cart["failed"]),
                    "; ".join(f"{f['sku'] or '—'} — {f['why'] or 'без причины'}"
                              for f in cart["failed"]))

    known = repo.get_store(store)
    summary = {
        "store": store,
        "store_name": known.name if known else store,
        "address": report["address"],
        "collected_at": stamp,
        "prices_saved": saved,
        "prices_skipped": report["prices"]["skipped"] if report["prices"] else 0,
        "connected": state.connected,
        "account": state.account,
        "points": state.points,
        "gives": list(report["gives"]),
        "coupons_saved": len(report["coupons"]) if report["coupons_collected"] else 0,
        "coupons_skipped": report["coupons_skipped"],
        "coupons_active": len(coupons(store)),
        "cart_ok": len(cart["ok"]) if cart else 0,
        "cart_failed": cart["failed"] if cart else [],
        "dropped": report["dropped"],
    }
    log.info("%s: отчёт сборщика — цен %d (не разобрано %d), купонов %d, "
             "в корзину легло %d, подключение %s",
             store, summary["prices_saved"], summary["prices_skipped"],
             summary["coupons_saved"], summary["cart_ok"],
             "есть" if summary["connected"] else "нет")
    return summary


__all__ = ["accept", "parse", "coupons", "coupons_updated_at", "COUPONS_KEY",
           "handover_report", "HANDOVER_KEY"]
