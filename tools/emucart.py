"""Живой прогон сборки корзины Магнита на эмуляторе: наряд, отчёт, снимок, вердикт.

ЗАЧЕМ. Корзину наполняет телефон касаниями в окне сайта (app/phonecart.py), и до
18.10.2026 «легло» означало ровно одно: кнопка нашлась и нажалась. Такая проверка
уже один раз соврала — на METRO нажатие прошло, витрина его приняла своим пикселем,
а корзина осталась пустой (tools/cartcheck.py). Поэтому прогон обязан кончаться не
словами приложения, а чужим свидетелем: ответом самого Магнита
`GET /webgate/v1/checkout/preview`, который 17.10.2026 сошёлся со страницей корзины
до копейки (app/phonecart.py:89–101).

ЧЕМ РАБОТАЕТ. Три канала и ни одного сверх: HTTP к локальному серверу «Моей корзины»
(наряд и отчёт), CDP к окну WebView на эмуляторе (пассивный перехват ответа сайта) и
adb — только подсказки человеку. Внутренности приложения инструмент не трогает: из
базы читается ровно одна настройка, `phonecart.last.<сеть>`, — последний отчёт
телефона. Так вердикт не может опереться на то, что инструмент сам себе записал.

ЧЕГО НЕ ДЕЛАЕТ. Своих запросов к Магниту нет: снимок — это ответ на запрос, который
сайт отправил сам, пока открывалась его корзина. Капчу инструмент не обходит, номер
и код за владельца не вводит, «Оформить», «Оплатить» и «Удалить» не нажимает. Куки
читаются ТОЛЬКО именами: значение `mg_at` не показывается и не сохраняется.

ЛИЧНОЕ НЕ УЕЗЖАЕТ В РЕПОЗИТОРИЙ. Сырой ответ preview остаётся вне репозитория
(`%LOCALAPPDATA%\\korzina-emu`), а в дело идёт только `sanitize_preview`: числа из
summary и у позиций — артикул, название, количество, цены и наличие. Адрес доставки,
телефон, ФИО, токены и координаты не «замазываются», а не попадают в файл вовсе:
замазку потом кто-нибудь снимет.

ПОЧЕМУ СВОЙ КЛИЕНТ CDP, А НЕ PLAYWRIGHT. Это знание стоило живого прогона
18.10.2026, и оно дороже кода: Playwright с Android WebView НЕ работает. Его
`chromium.connect_over_cdp` первым же делом зовёт методы УРОВНЯ БРАУЗЕРА —
`Browser.setDownloadBehavior`, `Target.setAutoAttach`, — а WebView такого уровня не
отдаёт вовсе: у него нет ни браузера, ни вкладок, только одна страница. Ответ выходит
такой: `Protocol error (Browser.setDownloadBehavior): Browser context management is
not supported`, и выглядит он как поломка стенда, хотя эмулятор здоров, а приложение
на экране. Проверено вживую: websocket САМОЙ СТРАНИЦЫ (`webSocketDebuggerUrl` из
`GET /json`) принимает `Runtime.evaluate`, `Network.enable` и `Page.navigate`. Поэтому
ниже — маленький клиент на `websockets` (16-я версия уже стоит в проекте), который
говорит только то, что страница умеет. Playwright из проекта не выброшен: он нужен
app/shopbrowser, здесь он просто не годится.

КОГДА CDP НЕ ДАЁТСЯ. Если проброшенного порта нет или страниц в нём не видно,
инструмент говорит об этом прямо (`adb forward`, `/proc/net/unix`) и называет запасной
путь из плана (раздел 5): итог из `api_totals` отчёта — это перехват самого
приложения, доказательство слабее, — плюс `uiautomator dump` страницы корзины и
снимки экрана. Молчаливый «снимок» был бы хуже отсутствия снимка.

ЕЩЁ ОДНА ОСОБЕННОСТЬ ЭТОГО WEBVIEW (замер 18.10.2026). `requestId` у него НЕ уникален:
три разных запроса страницы пришли с одним и тем же id, а `Network.getResponseBody`
по нему вернул тело последнего завершённого запроса. Поэтому пойманному по URL ответу
верить на слово нельзя: тело всё равно проходит через `sanitize_preview` (не ответ
корзины — не снимок), а в протокол печатается URL, у которого тело взято. Иначе под
видом корзины в доказательства могло бы уехать чужое тело, и вердикт был бы вынесен по
данным другого экрана.

КОМАНДЫ (`python -m tools.emucart <команда>`):
    plan            наряд Магнита по HTTP и проверка порога «настоящих ссылок»
    wait-report     ждать новый отчёт приложения, читая phonecart.last.magnit
    check-login     видно ли ИМЯ куки входа (значение не читается) — шаг 11 стенда
    snap-preview    пассивно поймать checkout/preview через CDP и очистить от личного
    verdict         свести «до», «после» и отчёт в вердикт (JSON)
    export          положить в репозиторий только очищенное

ПРИЗНАК ВХОДА — ЭТО СЕССИЯ, А НЕ ИМЯ КУКИ. Живой прогон 18.10.2026 показал, чего
стоит проверка по одному имени. Вход в Магнит в WebView был настоящий: на
`magnit.ru/profile` имя, бонусы, QR и «Выйти из учётной записи», `/ecom/cart` и
`checkout/preview` отвечали 200. А куки `mg_at` не было ни в `document.cookie`, ни в
базе кук WebView: сайт держит сессию в `localStorage` — ключи `au_atid`, `au_expd`,
`au_phnu` (имена замерены, значения не читались). Проверка «есть ли имя mg_at»
выдавала ложное «человек не вошёл» и звала владельца зря. Поэтому `check-login`
смотрит три признака и решает по живому, сильнейший первым: слова самой страницы —
те же `STRONG_IN`/`ANONYMOUS`, что у приложения (app/shopbrowser/signals.py), — потом
ключи сессии в localStorage (ТОЛЬКО ИМЕНА), потом имя куки как старая подсказка. Ни
один не сработал — вердикт «не знаю», а не «не вошёл»: замер 17.09.2026 про `mg_at`
устарел, и соврать в эту сторону дороже, чем признаться.

КОДЫ ВЫХОДА те же, что у стенда (tools/emu-stand.ps1): 0 — готово, 10 — стенд или
канал доказательств не поднялся, 20 — нужен человек, 30 — прогон не закрылся
(корзина не наполнилась либо наряд беднее порога).
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Сеть прогона и адреса. Порт 8611 — стенд `web-emu` (план, шаг 5); 9222 — то, что
# проброшен из WebView эмулятора командой `adb forward` (шаг 10).
STORE = "magnit"
BASE_DEFAULT = "http://127.0.0.1:8611"
CDP_DEFAULT = "http://127.0.0.1:9222"
CART_URL = "https://magnit.ru/ecom/cart"
PREVIEW_PATH = "/webgate/v1/checkout/preview"
# Имя куки входа Магнита, снятое 17.09.2026 (android/probe). С 18.10.2026 это лишь
# ПОДСКАЗКА: вход живёт в localStorage, а куки в WebView нет вовсе. Опираться на неё
# одну нельзя — так уже вышло ложное «человек не вошёл» при живом входе.
COOKIE_NAME = "mg_at"
# Семейство ключей сессии в localStorage Магнита: au_atid, au_expd, au_phnu замерены
# 18.10.2026. Правило «семейство au_» — вывод из этих трёх имён, и оно осторожное:
# лишний ключ в счёт не идёт (значения ключей не читаются вовсе), а пропажа семейства
# даёт «не знаю», не «не вошёл».
SESSION_PREFIX = "au_"
LOGIN_STATES = ("yes", "no", "unknown")

# Рабочее место прогона. demo — единственное, у которого есть подтверждённые связи
# Магнита с настоящими ссылками (поправка 2 плана), и единственный вход без кода
# (`POST /demo`, поправка 4). Имя то же, что в app/users.py: DEMO.
DEMO_WORKPLACE = "demo"

# Порог наряда — 4 строки с настоящими ссылками: у рабочего места demo ровно четыре
# подтверждённые связи Магнита, остальные шестнадцать синтетические (поправка 2 плана).
MIN_ROWS = 4
WAIT_TIMEOUT = 900.0

EXIT_DONE, EXIT_STAND, EXIT_HUMAN, EXIT_NOT_FILLED = 0, 10, 20, 30

# Поля позиции, которые остаются в снимке. Перечислены ИМЕНАМИ, а не «что-нибудь со
# словом name»: иначе `userName` получателя уехал бы в репозиторий вместе с товаром.
ARTICLE_KEYS = ("goodId", "offerId", "productId", "id")
NAME_KEYS = ("name", "goodName", "productName", "title")
QTY_KEYS = ("qnty", "quantity", "qty", "count")
AVAILABLE_KEYS = ("available", "availability", "inStock", "isAvailable", "stock")

# Деньги и количество позиции. Слова, а не подстроки: «total» нужен, а «retailer»
# под него не подходит (та же беда, что у HIDDEN_WORDS в app/cartrecord.py).
MONEY_WORDS = frozenset({"price", "cost", "sum", "amount", "total", "discount", "sale",
                         "promo", "pay", "fee"})
ITEM_LIST_WORDS = frozenset({"item", "items", "line", "lines", "good", "goods",
                             "position", "positions"})

# Числа из summary, которые остаются в снимке. ЭТО БЕЛЫЙ СПИСОК, А НЕ «ВСЁ, ЧТО ЧИСЛО».
# Ревью 18.10.2026: без него в публичный репозиторий уезжало любое число под любым
# ключом, кроме закрытых слов, — а `latitude`, `zip`, `flat` и `bonusBalance` под
# закрытые слова не попадают. Имена ниже — замеренные: ровно они лежали в summary
# ответа `checkout/preview` живого прогона (itemsCount=1, addedItemsCount=4,
# giftsCount=0, itemsTotalSalePrice, totalBasePrice, totalFinalPrice); остальные
# подобраны по смыслу итога корзины и без личных слов. Чего в списке нет — не
# сохраняется вовсе, только считается в `dropped`.
SUMMARY_KEYS = frozenset({
    "itemsCount", "addedItemsCount", "giftsCount", "giftItemsCount",
    "totalBasePrice", "totalFinalPrice", "itemsTotalBasePrice", "itemsTotalSalePrice",
    "totalSalePrice", "totalDiscount", "totalDeliveryPrice", "deliveryPrice",
    "totalServicePrice", "servicePrice", "minOrderPrice", "freeDeliveryFrom",
})

# Привязка снимка к прогону. Без неё снимок месячной давности с тем же товаром
# сходил бы за сегодняшний: вердикт сверял бы товар, а не прогон. Пишется в снимок
# командой snap-preview, проверяется вердиктом.
BOUND_FIELDS = ("captured_at", "url", "request_id", "status")

# Виды дефектов из раздела 6 плана. Строки вынесены сюда, чтобы тест и протокол
# называли одну и ту же беду одними словами.
DEFECT_MISSING = "легло вместо не легло"
DEFECT_QTY = "количество разошлось"
DEFECT_EXTRA = "не легло, а в корзине есть"

# Исходы прогона. «Частично» заведено ревью 18.10.2026: прогон «1 из 4» печатал
# «наполнилась» и ok=true, как «4 из 4», и для владельца это была одна и та же строка
# там, где разница — между «собралось» и «собралась четверть».
OUTCOMES = ("наполнилась", "частично", "не наполнилась", "не доказано")

# Слова в `why` отчёта, после которых прогон обязан позвать человека (п. 2а плана).
# Ищем по границам слов, а не подстрокой: «вход» находился и в «входная дверь», и в
# «входимость», и прогон звал человека по чужой беде.
HUMAN_WORDS = ("вход", "вош", "проверк", "капч", "робот", "подтверд", "выберите адрес",
               "выберите магазин", "номер", "код из смс")
HUMAN_RE = re.compile(r"\b(" + "|".join(map(re.escape, HUMAN_WORDS)) + ")", re.IGNORECASE)

# Что можно положить в репозиторий из каталога прогона. Список — белый, как и у
# снимка: сырое (`raw-*.json`, окна сайта, куки) в репозиторий не попадает никогда.
CLEAN_FILES = ("plan.json", "verdict.json", "preview-before.json", "preview-after.json",
               "report.json")
LOG_FILES = ("logcat-korzina.txt", "server-phonecart.log")
# Что в журнале вообще нужно: строки про корзину. Всё остальное — чужая жизнь телефона.
LOG_KEEP = re.compile(r"Korzina|phonecart|cartrun", re.IGNORECASE)
# Строки журнала, которые не кладём даже если тег наш: в них бывает вход владельца.
LOG_DROP = re.compile(r"(?:\d{11,})|Bearer|Authoriz|mg_at|(?:^|\W)cookie|token|session|"
                      r"[\w.+-]+@[\w-]+\.[\w.]+|passw", re.IGNORECASE)

__all__ = ["STORE", "PREVIEW_PATH", "COOKIE_NAME", "SESSION_PREFIX", "SUMMARY_KEYS",
           "BOUND_FIELDS", "OUTCOMES", "DEFECT_MISSING", "DEFECT_QTY", "DEFECT_EXTRA",
           "EmucartError", "CdpError", "CdpSocket", "sanitize_preview", "sanitize_report",
           "sanitize_plan", "verdict", "main"]


# ---------- слова и числа ----------
# Правило чистки берётся у записи корзины (app/cartrecord.py), а не заводится своё:
# третий список «личных» слов в репозитории разошёлся бы с серверным при первой же
# правке, и снимок начал бы хранить то, что сервер уже не хранит.
@lru_cache(maxsize=1)
def _record_rules():
    from app import cartrecord

    return (cartrecord._words, frozenset(cartrecord.HIDDEN_WORDS),
            frozenset(cartrecord.ID_WORDS))


def _words(key: str) -> set[str]:
    """«userId» → {user, id}; «qntyAvailable» → {qnty, available}."""
    return _record_rules()[0](key)


def _personal(key: str) -> bool:
    """Имя поля выдаёт человека: адрес, телефон, ФИО, токен, координаты, карта."""
    return bool(_words(key) & _record_rules()[1])


def _id_words() -> frozenset[str]:
    """Имена-коды: число под ними — артикул или номер, а не сумма (ID_WORDS)."""
    return _record_rules()[2]


def _number(value: Any) -> float | None:
    """Число из ответа сайта. Строку «1 234,56» тоже читаем: сайт шлёт их строками."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or abs(number) > 1e9:      # NaN и мусорные величины
        return None
    return round(number, 2)


def _int(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number >= 0 else None


def _text(value: Any, limit: int) -> str:
    """Значение одной строкой: переводы строк и двойные пробелы в снимке не нужны."""
    return re.sub(r"\s+", " ", "" if value is None else str(value)).strip()[:limit]


def _safe_url(value: Any) -> str:
    """Адрес без query и якоря: «/profile?uid=123#bonus» → «/profile».

    Ревью 18.10.2026: инструмент печатал полный `location.href`. В параметрах бывает и
    номер заказа, и метка человека, а печатается это в отчёт прогона, который читает
    владелец и который потом кладут в репозиторий. Путь и хост нужны для доказательства,
    параметры — нет.
    """
    text = _text(value, 400)
    if not text:
        return ""
    parts = urlsplit(text)
    if not parts.scheme and not parts.netloc:
        return text.split("?", 1)[0].split("#", 1)[0][:200]
    path = parts.path or "/"
    return f"{parts.scheme}://{parts.netloc}{path}"[:200]


def _own_link(url: Any, host: str = "magnit.ru") -> bool:
    """Ведёт ли адрес на сам Магнит. Проверяем ХОСТ, а не подстроку.

    Ревью 18.10.2026: «настоящая ссылка» искалась подстрокой `magnit.ru`, и под неё
    подошёл бы `https://example.com/?x=magnit.ru/product/1`. В наряде это значило бы
    «есть настоящие ссылки», которых нет.
    """
    parts = urlsplit(_text(url, 400))
    name = (parts.hostname or "").lower().strip(".")
    return name == host or name.endswith("." + host)


def _http():
    """Сессия HTTP без прокси из окружения: стенд и канал отладки живут на 127.0.0.1.

    Ревью 18.10.2026: `requests` по умолчанию берёт HTTP_PROXY из окружения, а websocket
    рядом идёт с `proxy=None`. С корпоративным прокси половина разговора шла бы через
    него, половина — напрямую, и канал назывался бы «не поднят» там, где он есть.
    """
    import requests

    session = requests.Session()
    session.trust_env = False
    return session


def _repo_root() -> Path:
    """Корень репозитория: каталог, в котором лежит `tools/`."""
    return Path(__file__).resolve().parents[1]


def _in_repo(path: Any) -> bool:
    """Лежит ли путь внутри репозитория. Сырое и снимки экрана туда не пишем."""
    try:
        target = Path(path).resolve()
    except (OSError, ValueError):
        return True
    root = _repo_root()
    return target == root or root in target.parents


def _no_raw_in_repo(path: Any, what: str) -> None:
    """Отказ, если сырое просят положить в репозиторий."""
    if _in_repo(path):
        raise EmucartError(f"{what} нельзя писать в репозиторий: {path} — сырое живёт вне "
                           "него (%LOCALAPPDATA%\\korzina-emu), а в репозиторий его "
                           "кладут только через export и только очищенным")


# ---------- снимок ответа Магнита ----------
def sanitize_preview(raw: Any, meta: dict | None = None) -> dict:
    """Ответ `checkout/preview` без личного: белый список полей, прочее — в счётчик.

    ПОЧЕМУ БЕЛЫЙ СПИСОК, А НЕ «ВЫЧЕРКНУТЬ ИЗВЕСТНОЕ ПЛОХОЕ». В ответе рядом с товарами
    лежат адрес доставки, получатель, телефон, координаты магазина и токены. Чёрный
    список пропустил бы любое поле, которого мы ещё не видели, и первая же новая
    ветка унесла бы адрес владельца в репозиторий. Белый список ошибается в другую
    сторону: потерять поле в снимке не страшно, сохранить чужое — страшно.

    ЧТО ОСТАЁТСЯ. Числа из `summary` — только те, что перечислены в SUMMARY_KEYS
    (itemsCount, addedItemsCount, giftsCount, itemsTotalSalePrice, totalBasePrice,
    totalFinalPrice и подобные замеренные итоги); у каждой позиции — артикул
    (goodId/offerId/productId/id), название, количество, цены и признак наличия.
    Ревью 18.10.2026 нашло две дыры, и обе закрыты: числа брались под ЛЮБЫМ ключом
    (числовые `latitude`, `zip`, `flat`, `bonusBalance` уехали бы в публичный
    репозиторий), а у цен позиции имя ключа копировалось без проверки на личные слова
    (`payCardLast4`). Название — товара: ключи позиции перечислены именами, а не «всё
    со словом name».

    ГДЕ ИЩУТСЯ ПОЗИЦИИ. Сначала по замеренному пути Магнита `suborders[].items[]` — там
    строка корзины как её отдаёт сайт. Только если его нет — обходом по ключам вроде
    `items`, и в этом обходе правила строже: контейнер с личным словом в имени не
    раскрывается вовсе (иначе `addressLines: [{id, name: «г. Москва…»}]` положил бы
    адрес в `name`), а позицией считается лишь элемент, у которого есть И артикул, И
    количество. Обход нужен: на живом «до» замеренный путь пуст, и без обхода снимок
    терял бы ветку, если сеть переименует её.

    ДЕНЬГИ. У Магнита они в копейках (app/phonecart.py, множитель 0.01). Числа не
    пересчитываем: иначе вердикт сверял бы копейки с рублями. О множителе говорит
    `money_scale`, а пересчитывает тот, кто сравнивает.

    ЧЕМ СНИМОК ПРИВЯЗАН К ПРОГОНУ. `meta` — это `captured_at`, `url`, `request_id`,
    `status`, снятые в момент поимки ответа (их даёт snap-preview). Без них снимок
    месячной давности с тем же товаром сходил бы за сегодняшний: вердикт сверял бы
    товар, а не прогон. `meta=None` оставляет поля пустыми — так выглядят файлы,
    снятые старым инструментом.
    """
    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError as err:
            raise ValueError(f"ответ preview не разобрался как JSON: {err}") from err
    if not isinstance(data, dict):
        raise ValueError("ответ preview должен быть объектом JSON, "
                         f"а пришёл {type(data).__name__}")

    # Итог берём у КОРНЕВОГО summary, а во вложенные заглядываем только если корневого
    # нет: у позиции бывает свой маленький summary, и его число, попав в корзину
    # наряду, выдало бы чужой итог за итог всей корзины.
    summary = _clean_summary(data.get("summary"))
    if not summary:
        for node in _find_dicts(data, "summary"):
            summary = _clean_summary(node)
            if summary:
                break

    items: list[dict] = []
    where: list[str] = []
    dropped_fields = 0
    for path, rows in _item_lists(data):
        where.append(path)
        for row in rows:
            if isinstance(row, dict):
                clean, dropped = _clean_item(row)
                dropped_fields += dropped
                if clean:
                    items.append(clean)

    if not summary and not items:
        raise ValueError("в ответе нет ни summary, ни позиций — это не ответ корзины: "
                         "скорее всего страница проверки или пустой ответ")
    meta = meta if isinstance(meta, dict) else {}
    return {
        "summary": summary,
        "items": items,
        "items_found": len(items),
        "where": where,
        "money_scale": 0.01,
        "dropped": {"personal_fields": _count_personal(data), "item_fields": dropped_fields},
        # Привязка к прогону: время поимки, адрес ответа, его id в отладке и код ответа.
        "captured_at": _text(meta.get("captured_at"), 30),
        "url": _safe_url(meta.get("url")),
        "request_id": _text(meta.get("request_id"), 40),
        "status": _int(meta.get("status")),
    }


def _clean_summary(node: Any) -> dict[str, float]:
    """Числа из summary — только из SUMMARY_KEYS. Строки и вложенные блоки не берём.

    Ревью 18.10.2026: здесь стояло «любой ключ, кроме закрытых слов», и числовые
    координаты или почтовый индекс уехали бы в репозиторий как «итог корзины».
    """
    out: dict[str, float] = {}
    if not isinstance(node, dict):
        return out
    for key, value in node.items():
        name = _text(key, 60)
        if name not in SUMMARY_KEYS or _personal(name):
            continue
        number = _number(value)
        if number is not None:
            out[name] = number
    return out


def _find_dicts(node: Any, name: str, depth: int = 0) -> list[dict]:
    """Все словари под ключом `name` на любой глубине: `summary` у сетей бывает в data."""
    if depth > 8:
        return []
    out: list[dict] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == name and isinstance(value, dict):
                out.append(value)
            elif isinstance(value, (dict, list)):
                out.extend(_find_dicts(value, name, depth + 1))
    elif isinstance(node, list):
        for value in node:
            out.extend(_find_dicts(value, name, depth + 1))
    return out


def _item_lists(data: dict) -> list[tuple[str, list]]:
    """Списки позиций корзины: замеренный путь Магнита, иначе — осторожный поиск.

    Запасной поиск строже замеренного пути нарочно. Он идёт по незнакомой разметке, и
    ревью 18.10.2026 показало, чего это стоит: список `addressLines` с элементами
    `{id, name: «г. Москва, ул. …»}` проходил как позиции корзины и клал адрес в `name`
    снимка. Поэтому: в личные ветки не заходим вовсе, а позицией считаем только элемент
    с артикулом И количеством. Замеренный путь нужен, и если сеть переименует ветку,
    обход её найдёт — но найдёт по делу.
    """
    found: list[tuple[str, list]] = []
    suborders = data.get("suborders")
    if isinstance(suborders, list):
        for index, sub in enumerate(suborders):
            if isinstance(sub, dict) and isinstance(sub.get("items"), list):
                found.append((f"suborders[{index}].items", sub["items"]))
    if found:
        return found

    walked: list[tuple[str, list]] = []
    stack: list[tuple[str, Any]] = [("", data)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if _personal(str(key)):
                    continue                     # адрес, получатель, куки: не наш список
                child = f"{path}.{key}" if path else str(key)
                if isinstance(value, list) and _words(str(key)) & ITEM_LIST_WORDS:
                    walked.append((child, value))
                elif isinstance(value, (dict, list)):
                    stack.append((child, value))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                stack.append((f"{path}[{index}]", value))
    return [(path, rows) for path, rows in walked if _looks_like_items(rows)]


def _looks_like_items(rows: list) -> bool:
    """Похож ли список на позиции корзины: у элемента есть И артикул, И количество.

    Одного артикула мало: `id` есть у любой строки чужого списка. Именно пара
    «артикул + количество» отличает позицию корзины от адреса, баннера и подсказки.
    """
    for row in rows:
        if not isinstance(row, dict):
            continue
        if set(QTY_KEYS) & set(row) and set(ARTICLE_KEYS) & set(row):
            return True
    return False


def _clean_item(raw: dict) -> tuple[dict, int]:
    """Одна позиция снимка: только артикул, название, количество, цены и наличие."""
    out: dict[str, Any] = {}
    kept: set[str] = set()

    for key in ARTICLE_KEYS:
        value = raw.get(key)
        if value not in (None, ""):
            out["article"] = _text(value, 40)
            out["article_from"] = key
            kept.add(key)
            break
    for key in NAME_KEYS:
        if raw.get(key):
            out["name"] = _text(raw[key], 120)
            kept.add(key)
            break
    for key in QTY_KEYS:
        number = _number(raw.get(key))
        if number is not None:
            out["qnty"] = number
            kept.add(key)
            break

    prices: dict[str, float] = {}
    for key, value in raw.items():
        name = str(key)
        words = _words(name)
        # Число под именем с id-словом — код, а не сумма: то же правило, что у записи
        # корзины (ID_WORDS). Без него `promoId: 123456` лежал бы в ценах позиции, и
        # протокол показывал бы номер акции как сумму.
        if not words & MONEY_WORDS or words & _id_words():
            continue
        # Личные слова проверяются и здесь: ревью 18.10.2026 нашло, что имя ключа
        # копировалось в файл без проверки, и `payCardLast4` уезжал в репозиторий.
        if _personal(name):
            continue
        number = _number(value)
        if number is not None:
            prices[_text(name, 40)] = number
            kept.add(key)
    if prices:
        out["prices"] = prices

    for key in AVAILABLE_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, bool):
            out["available"] = value
        elif isinstance(value, str):
            out["available"] = value.strip().lower() in ("true", "1", "yes", "да", "есть")
        else:
            number = _number(value)
            if number is not None:
                out["available"] = number > 0
        kept.add(key)
        break
    return out, len([key for key in raw if key not in kept])


def _count_personal(node: Any, depth: int = 0) -> int:
    """Сколько полей с личными словами было в сыром ответе — без значений, только число."""
    if depth > 8:
        return 0
    if isinstance(node, dict):
        return sum((1 if _personal(str(key)) else 0) + _count_personal(value, depth + 1)
                   for key, value in node.items())
    if isinstance(node, list):
        return sum(_count_personal(value, depth + 1) for value in node)
    return 0


# ---------- вердикт ----------
def verdict(before: Any, after: Any, report: Any, plan: Any = None,
            manual: bool = True, allow_unbound: bool = False) -> dict:
    """Свести «до», «после», отчёт и наряд в один ответ: что доказано, а что нет.

    Условия доказательства — из раздела 5 плана: снято «до», каждая позиция с `ok=true`
    нашлась в снимке «после» по артикулу наряда с нужным числом, итог Магнита сошёлся с
    итогом отчёта до копейки и больше нуля. Снимок экрана корзины машина проверить не
    может — он остаётся человеку и назван в `manual`.

    ЧЕТЫРЕ ИСХОДА, А НЕ ДВА (ревью 18.10.2026). «Частично» заведено потому, что прогон
    «1 из 4» печатал «наполнилась» и ok=true, как «4 из 4»: для владельца это разница
    между «собралось» и «собралась четверть». Исходы: «наполнилась» (всё легло и всё
    сверено), «частично» (легло не всё), «не наполнилась» (доказано, что отчёт соврал
    или не легло ничего), «не доказано» (сверить нечем: нет снимка, снимок не привязан
    к прогону, нет чисел). «Не доказано» — не приговор корзине, а отказ выносить
    вердикт: путать его с «не наполнилась» значит объявлять провал там, где исправная
    корзина, — ровно та ошибка, ради которой инструмент и написан.

    ЧИСЛО НАЖАТИЙ БЕРЁТСЯ ИЗ ОТЧЁТА: `times` — это clicks наряда (android/app
    MainActivity:1661, lineDone). Если «до» не пусто, ждём прибавку к лежавшему:
    повторный прогон кладёт поверх, а не удваивает. Позиция с `how=kept` уже лежала в
    корзине сети — прибавки от неё не ждём, но и «уже лежит» проверяем по «до»: если до
    наряда её там не было, приложение сказало неправду.

    СВЕРКА ИДЁТ ПО АРТИКУЛУ, А НЕ ПО СТРОКЕ ОТЧЁТА. Два товара корзины могут сойтись на
    одном артикуле сети (дубль из чека и каталога — app/phonecart.plan их складывает), и
    в отчёте тогда две строки с одним sku. Сверка по строкам дала бы «легло 2» там, где
    легла одна позиция с числом 2.

    ПРИВЯЗКА К ПРОГОНУ. Снимок несёт `captured_at`, `url`, `request_id` и `status`
    (пишет snap-preview). Без них снимок месячной давности с тем же товаром сходил бы за
    сегодняшний. Снимок без привязки — «не доказано»; `allow_unbound` (ключ
    `--allow-unbound`) считает вердикт по старым файлам прогона, но прямо говорит в
    тексте и в JSON, что доказательство слабее. Молчаливого послабления нет.

    ДВЕ ОШИБКИ ЛОВЯТСЯ ОСОБО (раздел 6 плана): «легло», а позиции в снимке нет или число
    другое (DEFECT_MISSING, DEFECT_QTY) и обратная — «не легло», а в корзине прибавилось
    (DEFECT_EXTRA). Это дефекты, а не «не сошлось»: в `defects` сказано, где искать причину.

    Итог отчёта читается серверным правилом (app/phonecart.network_total): свой разбор
    полей разошёлся бы с приёмом отчёта при первой правке API_TOTALS.
    """
    report = report if isinstance(report, dict) else {}
    plan = plan if isinstance(plan, dict) else {}
    items = [item for item in (report.get("items") or []) if isinstance(item, dict)]
    b, a = _as_preview(before), _as_preview(after)
    before_taken, after_taken = bool(b), bool(a)
    was_qty, now_qty = _quantities(b), _quantities(a)
    now_avail = {sku: row.get("available") for sku, row in now_qty.items()}

    planned = _int(report.get("planned"))
    landed = _int(report.get("landed"))
    planned = len(items) if planned is None else planned
    landed = sum(1 for item in items if item.get("ok")) if landed is None else landed
    share = f"{landed} из {planned}"

    notes: list[str] = []
    unproven: list[str] = []
    defects: list[dict] = []

    # --- привязка доказательства к прогону ---
    binding = _binding(b, a, report, plan, manual)
    if binding["problems"]:
        unproven.extend(binding["problems"])
    elif binding["missing"]:
        if allow_unbound:
            unproven.append("доказательство слабее: снимки без времени и источника "
                            f"({', '.join(binding['missing'])}) — привязка не подтверждена "
                            "(--allow-unbound)")
        else:
            unproven.append("снимки без привязки к прогону: нет "
                            f"{', '.join(binding['missing'])} — старый снимок с тем же "
                            "товаром сойдёт за сегодняшний (--allow-unbound считает вердикт "
                            "с этой оговоркой)")
    for line in binding["unproven"]:
        unproven.append(line)

    # --- сверка позиций по артикулу ---
    claims = _claims(items)
    articles: dict[str, dict] = {}
    for sku, claim in claims.items():
        articles[sku] = _check_article(sku, claim, was_qty, now_qty, before_taken,
                                       after_taken, defects)
    positions = _positions(items, claims, articles)

    landed_rows = [row for row in positions if row["ok"]]
    unverifiable_landed = sum(1 for row in landed_rows if row["match"] is None)
    unverifiable = sum(1 for row in positions if row["match"] is None)
    if not after_taken:
        positions_done, positions_why = False, "снимка «после» нет — позиции не сверить"
    elif not landed_rows:
        positions_done, positions_why = False, "отчёт не назвал ни одной легшей позиции"
    elif unverifiable_landed:
        positions_done, positions_why = False, "часть позиций сверить не с чем"
    else:
        positions_done = all(row["match"] for row in landed_rows)
        positions_why = ("все позиции отчёта нашлись с нужным числом" if positions_done
                         else "позиции отчёта разошлись со снимком корзины")

    # --- итог и счётчики сайта ---
    totals = _totals(a, report)
    counters = _counters(a, report, claims)
    if counters["ok"] is False:
        unproven.extend(counters["problems"])
    elif counters["ok"] is None:
        unproven.extend(counters["problems"])

    not_landed = [{"sku": _text(item.get("sku"), 40), "name": _text(item.get("name"), 120),
                   "why": _text(item.get("why"), 200) or "не легло",
                   "available": now_avail.get(_text(item.get("sku"), 40))}
                  for item in items if not item.get("ok")]

    proof = [
        {"id": "before", "name": "снимок «до» снят", "required": True, "done": before_taken,
         "detail": f"itemsCount={_items_count(b)}" if before_taken else "снимка нет"},
        {"id": "after", "name": "снимок «после» снят", "required": True, "done": after_taken,
         "detail": f"itemsCount={_items_count(a)}" if after_taken else "снимка нет"},
        {"id": "positions", "name": "позиции отчёта нашлись в корзине", "required": True,
         "done": positions_done, "detail": positions_why},
        {"id": "total", "name": "итог Магнита сошёлся с отчётом", "required": True,
         "done": bool(totals["match"]), "detail": totals["why"]},
        {"id": "binding", "name": "снимки привязаны к этому прогону", "required": False,
         "done": binding["bound"] if not binding["missing"] or allow_unbound else None,
         "detail": binding["why"]},
        {"id": "counters", "name": "счётчики сайта сошлись", "required": False,
         "done": counters["ok"], "detail": counters["why"]},
    ]

    # --- исход ---
    if not after_taken:
        outcome = "не доказано"
    elif binding["problems"]:
        outcome = "не доказано"
    elif binding["missing"] and not allow_unbound:
        outcome = "не доказано"
    elif defects:
        outcome = "не наполнилась"
    elif not planned:
        outcome = "не доказано"
    elif positions and unverifiable == len(positions):
        # Ни одну строку сверить не с чем: «не наполнилась» здесь было бы приговором
        # корзине, о которой мы ничего не знаем (ревью 18.10.2026, п. 6).
        outcome = "не доказано"
    elif landed == 0:
        outcome = "не наполнилась"
    elif landed < planned:
        outcome = "частично"
    elif (positions_done and totals["match"] and counters["ok"] is not False
          and not unverifiable_landed):
        outcome = "наполнилась"
    else:
        outcome = "не доказано"
    overall = outcome == "наполнилась"
    if outcome == "частично":
        notes.append(f"наполнилась доля корзины: {share}; не принято "
                     f"{len(not_landed)}: " + "; ".join(
                         f"{row['name'] or row['sku']} — {row['why']}" for row in not_landed))
    if not after_taken:
        notes.append("снимка «после» нет: запасной путь из раздела 5 плана — итог из "
                     f"api_totals['{PREVIEW_PATH}'] отчёта (это перехват самого "
                     "приложения, доказательство слабее), uiautomator dump страницы "
                     "корзины и снимки экрана")
    if not before_taken:
        notes.append("«до» не снято: без него не отличить «легло сейчас» от «лежало раньше» — "
                     "приёмка не закрывается, даже если корзина полна")
    if not totals["match"]:
        notes.append(f"итог: в снимке {totals['preview']}, в отчёте {totals['report']} "
                     f"(поле {totals['field']}, множитель {totals['scale']})")
    if unverifiable_landed:
        notes.append("часть легших позиций сверить не удалось: снимок корзины без "
                     "количества или отчёт без числа нажатий")
    for row in articles.values():
        if row["match"] is None and row["why"] not in unproven:
            unproven.append(f"позиция {row['sku']}: {row['why']}")

    return {
        "store": _text(report.get("store"), 20) or STORE,
        "at": datetime.now().isoformat(timespec="seconds"),
        "report_at": report.get("at"),
        "ok": overall,
        "verdict": outcome,
        "share": share,
        "planned": planned,
        "landed": landed,
        "bound": binding["bound"],
        "binding_note": ("" if binding["bound"] else
                         "доказательство слабее: снимки без времени и источника"),
        "binding": binding,
        "before": {"taken": before_taken, "items_count": _items_count(b),
                   "captured_at": b.get("captured_at"), "status": b.get("status")},
        "after": {"taken": after_taken, "items_count": _items_count(a),
                  "captured_at": a.get("captured_at"), "status": a.get("status")},
        "totals": totals,
        "counters": counters,
        "articles": articles,
        "positions": positions,
        "not_landed": not_landed,
        "proof": proof,
        "defects": defects,
        "unproven": unproven,
        "notes": notes,
        "manual": ["снимок экрана /ecom/cart в WebView: те же позиции и «Итого» — "
                   "сверяет человек, машина этого не видит"],
    }


def _binding(before: dict, after: dict, report: dict, plan: dict, manual: bool) -> dict:
    """Привязано ли доказательство к ЭТОМУ прогону, а не к какому-то другому.

    Три вещи, которых у старого инструмента не было и из-за которых снимок месячной
    давности сходил за свежий: время поимки ответа, порядок времён вокруг отчёта и
    отпечаток наряда. Плюс код ответа: снимок ошибки — не снимок корзины.

    «Нет привязки» (missing) и «привязка чужая» (problems) — разные вещи, и путать их
    нельзя: первое лечится ключом `--allow-unbound` и честной оговоркой, второе — нет,
    потому что метаданные есть и говорят, что снимок снят не вокруг этого отчёта.
    """
    out: dict[str, Any] = {"bound": False, "missing": [], "problems": [], "unproven": [],
                           "checked": {}, "why": ""}
    for name, preview, title in (("before", before, "«до»"), ("after", after, "«после»")):
        if not preview:
            continue
        for field in BOUND_FIELDS:
            if preview.get(field) in (None, "", []):
                out["missing"].append(f"{name}.{field}")
        status = _int(preview.get("status"))
        if status is not None and not 200 <= status < 300:
            out["problems"].append(f"снимок {title} снят с кодом {status}")
        url = str(preview.get("url") or "")
        if url and not _own_link(url):
            out["problems"].append(f"снимок {title} снят не с magnit.ru, а с {url}")
        if preview.get("captured_at") and _moment(preview.get("captured_at")) is None:
            out["problems"].append(f"время снимка {title} не читается "
                                   f"({preview.get('captured_at')})")

    before_at = _moment(before.get("captured_at"))
    after_at = _moment(after.get("captured_at"))
    report_at = _moment(report.get("at"))
    if before_at and report_at and after_at:
        if before_at < report_at < after_at:
            out["checked"]["order"] = True
        else:
            out["problems"].append(
                "порядок времён нарушен: «до» "
                f"{before_at.isoformat(timespec='seconds')}, отчёт "
                f"{report_at.isoformat(timespec='seconds')}, «после» "
                f"{after_at.isoformat(timespec='seconds')} — снимки сняты не вокруг "
                "этого отчёта")
    else:
        out["unproven"].append("время отчёта или снимков не читается — порядок "
                               "«до < отчёт < после» не проверен")

    stamp_plan = _text(plan.get("stamp"), 40)
    stamp_report = _text(report.get("stamp"), 40)
    if stamp_plan and stamp_report:
        if stamp_plan == stamp_report:
            out["checked"]["stamp"] = True
        else:
            out["problems"].append(f"отпечаток наряда {stamp_plan} не тот, что в отчёте "
                                   f"{stamp_report}: отчёт от другой сборки корзины")
    elif not plan:
        out["unproven"].append("наряд не передан (--plan): отпечаток наряда с отчётом "
                               "не сверен")
    else:
        out["unproven"].append("в наряде или отчёте нет отпечатка — сверять нечего")

    background = report.get("background")
    if isinstance(background, bool):
        if background == manual:
            out["problems"].append(
                "отчёт от фоновой сборки 1.13, а сверяется ручная «Собрать» — это разные "
                "прогоны" if background else
                "отчёт от ручной сборки, а сверяется фоновая — это разные прогоны")
        else:
            out["checked"]["background"] = True
    else:
        out["unproven"].append("в отчёте не сказано, ручная это сборка или фоновая")

    out["bound"] = not out["missing"] and not out["problems"]
    if out["problems"]:
        out["why"] = "; ".join(out["problems"])
    elif out["missing"]:
        out["why"] = "нет " + ", ".join(out["missing"])
    else:
        out["why"] = "время, адрес, id ответа и код на месте, порядок и отпечаток сошлись"
    return out


def _claims(items: list[dict]) -> dict[str, dict]:
    """Что отчёт обещал по каждому артикулу: прибавку, «уже лежало» или отказ.

    Строки одного артикула складываются: два товара корзины могут сойтись на одном
    артикуле сети, и в отчёте тогда две строки с одним sku (app/phonecart.plan их
    складывает ещё в наряде, но отчёт приходит от приложения и не обязан).
    """
    out: dict[str, dict] = {}
    for item in items:
        sku = _text(item.get("sku"), 40)
        row = out.setdefault(sku, {"sku": sku, "add": 0, "kept": 0, "rows": 0,
                                   "names": [], "unknown_times": 0, "ok_rows": 0})
        how = _text(item.get("how"), 10) or "tap"
        times = _int(item.get("times"))
        if times is None:
            row["unknown_times"] += 1
            times = 0
        if item.get("ok"):
            # ok_rows нужен отдельно от суммы: строка «легло» без числа нажатий даёт
            # нулевую прибавку, и без этого счётчика она читалась бы как отказ.
            row["ok_rows"] += 1
            row["kept" if how == "kept" else "add"] += times
        row["rows"] += 1
        if item.get("name") and _text(item.get("name"), 120) not in row["names"]:
            row["names"].append(_text(item.get("name"), 120))
    return out


def _check_article(sku: str, claim: dict, was_qty: dict, now_qty: dict, before_taken: bool,
                   after_taken: bool, defects: list[dict]) -> dict:
    """Одна позиция наряда против снимка корзины: что ждали и что нашли."""
    name = "; ".join(claim["names"])[:120]
    was = (was_qty.get(sku) or {}).get("qty")
    seen = sku in now_qty
    now_row = now_qty.get(sku) or {}
    now = now_row.get("qty") if now_row.get("qty_known") else None
    want_add, want_kept = claim["add"], claim["kept"]
    expected = (want_add if want_add else None)
    match: bool | None
    why: str

    def defect(kind: str, text: str, where: str) -> None:
        defects.append({"kind": kind, "sku": sku, "name": name, "why": text, "where": where})

    if not sku:
        match, why = None, "в отчёте нет артикула — сверять нечем"
    elif not after_taken:
        match, why = None, "снимка «после» нет"
    elif claim["unknown_times"] and claim["ok_rows"]:
        match, why = None, "отчёт не назвал число нажатий — сверить не с чем"
    elif want_add:
        expected = (was or 0) + want_add
        if not seen:
            match = False
            why = f"позиции нет в корзине Магнита, ждали {expected}"
            defect(DEFECT_MISSING, why,
                   "android/app MainActivity.lineDone (касание отчиталось «легло»), "
                   "app/phonecart.py (приём отчёта)")
        elif now is None:
            match, why = None, "позиция в корзине есть, но количества в снимке нет"
        else:
            match = now == expected
            why = f"в корзине {now}, ждали {expected} (было {was or 0}, нажатий {want_add})"
            if not match:
                defect(DEFECT_QTY, why, "app/phonecart.py (наряд), "
                                        "android/app MainActivity.lineDone")
    elif want_kept:
        expected = want_kept
        if not before_taken:
            match, why = None, "«до» не снято: не видно, лежала ли позиция до наряда"
        elif (was or 0) < want_kept:
            match = False
            why = (f"приложение сказало «уже лежит {want_kept}», а до наряда в корзине "
                   f"было {was or 0}")
            defect(DEFECT_MISSING, why, "app/phonecart.py (наряд), "
                                        "android/app MainActivity.keptLine")
        elif not seen:
            match = False
            why = f"позиции нет в корзине Магнита, а приложение назвало её лежащей"
            defect(DEFECT_MISSING, why, "android/app MainActivity.keptLine")
        elif now is None:
            match, why = None, "позиция в корзине есть, но количества в снимке нет"
        else:
            match = now >= want_kept
            why = f"в корзине {now}, до наряда {was or 0}, «уже лежит» — не меньше {want_kept}"
            if not match:
                defect(DEFECT_MISSING, why, "android/app MainActivity.keptLine")
    else:
        # Только отказы: прибавки быть не должно. Позиция, которая в снимке есть без
        # числа, не «прошла» — по ней просто нечего сказать (ревью 18.10.2026).
        if not before_taken:
            match, why = None, "«до» не снято: не отличить прибавку от прежней позиции"
        elif seen and now is None:
            match, why = None, "позиция в корзине есть, а количества в снимке нет"
        elif (now or 0) > (was or 0):
            match = False
            why = f"в корзине {now}, до наряда было {was or 0}"
            defect(DEFECT_EXTRA, why, "app/phonecart.py (приём отчёта), "
                                      "android/app MainActivity.lineDone")
        else:
            match = True
            why = f"в корзине {now or 0}, до наряда {was or 0} — прибавки нет, как и сказал отчёт"
    return {"sku": sku, "name": name, "add": want_add, "kept": want_kept,
            "rows": claim["rows"], "before": was, "after": now, "found": seen,
            "expected": expected, "match": match, "why": why}


def _positions(items: list[dict], claims: dict, articles: dict) -> list[dict]:
    """Строки отчёта с итогом сверки их артикула — для таблицы «позиция → наряд → снимок»."""
    out = []
    for item in items:
        sku = _text(item.get("sku"), 40)
        article = articles.get(sku) or {}
        out.append({"sku": sku, "name": _text(item.get("name"), 120),
                    "ok": bool(item.get("ok")), "how": _text(item.get("how"), 10) or "tap",
                    "times": _int(item.get("times")),
                    "before": article.get("before"), "after": article.get("after"),
                    "found": article.get("found", False),
                    "expected": article.get("expected"), "match": article.get("match"),
                    "why": article.get("why", "в отчёте нет артикула — сверять нечем"),
                    "rows_on_article": (claims.get(sku) or {}).get("rows", 1)})
    return out


def _counters(after: dict, report: dict, claims: dict) -> dict:
    """Сходятся ли счётчики, которыми сайт и отчёт описывают одну и ту же корзину.

    Ревью 18.10.2026: у Магнита недоступные строки в `suborders[].items[]` не попадают,
    и «всё сошлось» выходило там, где сайт называл четыре добавленных позиции при одной
    в корзине. Считать это доказанным нельзя — но и провалом тоже: разницу объясняют
    позиции «нет в наличии». Поэтому расхождение идёт в «не доказано», а не в приговор.

    Три проверки, и каждая — только если обе стороны известны:
      itemsCount == сумма количеств позиций снимка (снимок сам себе не противоречит);
      addedItemsCount == сумма нажатий по позициям, названным легшими;
      число позиций в отчёте == числу позиций в снимке.
    """
    summary = after.get("summary") or {}
    counts = {"checks": [], "ok": None, "problems": [], "why": ""}
    items = [item for item in (after.get("items") or []) if isinstance(item, dict)]
    count = _number(summary.get("itemsCount"))
    qty_sum = sum(_number(item.get("qnty")) or 0 for item in items) if items else 0.0
    added = _number(summary.get("addedItemsCount"))
    clicks = sum(row["add"] for row in claims.values())
    report_count = None
    try:
        from app import phonecart

        network = phonecart.network_total(report)
        report_count = network["count"] if network else None
    except Exception:  # noqa: BLE001 — правило сервера недоступно: проверку пропускаем
        report_count = None

    def check(name: str, left: Any, right: Any, words: str) -> None:
        if left is None or right is None:
            counts["checks"].append({"name": name, "done": None, "detail": "сверить нечем"})
            return
        done = float(left) == float(right)
        counts["checks"].append({"name": name, "done": done, "detail": words})
        if not done:
            counts["problems"].append(words)

    check("itemsCount = позиций или штук в снимке",
          count, count if count is None else _counts_match(count, len(items), qty_sum),
          f"снимок: itemsCount={_show(count)}, позиций найдено {len(items)}, "
          f"сумма количеств {_show(qty_sum)}")
    check("addedItemsCount = нажатия легших",
          added, clicks if added is not None else None,
          f"сайт: addedItemsCount={_show(added)}, отчёт: нажатий по легшим={clicks}")
    check("позиций в отчёте и в снимке",
          report_count, count if report_count is not None else None,
          f"отчёт: позиций {_show(report_count)}, снимок: itemsCount={_show(count)}")

    done_flags = [row["done"] for row in counts["checks"] if row["done"] is not None]
    if any(flag is False for flag in done_flags):
        counts["ok"] = False
        counts["why"] = "; ".join(counts["problems"])
    elif not done_flags:
        counts["why"] = "счётчиков нет ни в снимке, ни в отчёте — сверять нечего"
    else:
        counts["ok"] = True
        counts["why"] = "; ".join(row["detail"] for row in counts["checks"])
    return counts


def _counts_match(count: float, found: int, qty_sum: float) -> float:
    """Сходится ли `itemsCount` с тем, что видно в снимке — по любой из двух трактовок.

    Ревью 18.10.2026 предлагало сверять `itemsCount` с суммой количеств. На живых
    замерах этого не отличить: в корзине была одна позиция с одним количеством, и оба
    числа совпадали. Для позиции на 3 штуки трактовки расходятся, и жёсткая сверка с
    суммой количеств объявила бы «не доказано» исправной корзине. Поэтому принимаем
    любую из двух: сайт считает либо позиции, либо штуки. Не подходит ни одна — вот это
    уже расхождение.
    """
    return count if count in (float(found), qty_sum) else -1


def _show(value: Any) -> str:
    """Число для протокола: «3», а не «3.0» — так его читает человек."""
    if value is None:
        return "—"
    number = _number(value)
    if number is None:
        return str(value)
    return str(int(number)) if number == int(number) else str(number)


def _as_preview(value: Any) -> dict:
    """Снимок к сверке: очищенный берём как есть, сырой чистим тем же правилом.

    Разбирать сырой ответ здесь второй раз нельзя — правило чистки одно, и живёт оно
    в `sanitize_preview`. Нечитаемый снимок возвращается пустым: «снимка нет» — честный
    ответ, а падение вердикта на середине прогона ничего не доказывает.
    """
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    if not isinstance(value, dict):
        return {}
    if "money_scale" in value and "items_found" in value:      # уже очищен
        return value
    try:
        return sanitize_preview(value)
    except ValueError:
        return {}


def _quantities(preview: dict) -> dict[str, dict]:
    """Сколько чего лежит в снимке: {артикул: {qty, qty_known, available}}.

    Сумма, а не первая строка: один артикул может прийти двумя позициями (дубль из
    чека и каталога — app/phonecart.plan их складывает), и снимок тоже может.
    `available` собирается затем, чтобы вердикт называл непринятые позиции не только
    словами отчёта, но и тем, что о них сказала сама витрина.
    """
    out: dict[str, dict] = {}
    for item in preview.get("items") or []:
        if not isinstance(item, dict):
            continue
        sku = _text(item.get("article"), 40)
        if not sku:
            continue
        row = out.setdefault(sku, {"qty": 0.0, "qty_known": False, "available": None})
        qty = _number(item.get("qnty"))
        if qty is not None:
            row["qty"] += qty
            row["qty_known"] = True
        if isinstance(item.get("available"), bool):
            row["available"] = item["available"]
    return out


def _items_count(preview: dict) -> int | None:
    number = _number((preview.get("summary") or {}).get("itemsCount"))
    return int(number) if number is not None else None


@lru_cache(maxsize=1)
def _preview_rule() -> dict:
    """Правило итога Магнита из app/phonecart.py: путь, поле и множитель копеек."""
    from app import phonecart

    for rule in phonecart.API_TOTALS.get(STORE, ()):
        if rule.get("path") == PREVIEW_PATH:
            return dict(rule)
    return {"path": PREVIEW_PATH, "total": "summary.totalFinalPrice", "scale": 0.01}


def _totals(after: dict, report: dict) -> dict:
    """Итог: число из снимка против итога отчёта — до копейки и больше нуля.

    Заодно сверяется число позиций: если отчёт и снимок называют разное, значит между
    ними корзина успела измениться (или снимок чужой), и «сошлось» говорить нельзя.
    """
    from app import phonecart

    rule = _preview_rule()
    field = str(rule.get("total") or "summary.totalFinalPrice").rsplit(".", 1)[-1]
    scale = float(rule.get("scale") or 0.01)
    kop = _number((after.get("summary") or {}).get(field))
    preview_total = round(kop * scale, 2) if kop is not None else None
    network = phonecart.network_total(report)
    report_total = network["total"] if network else None
    report_count = network["count"] if network else None
    preview_count = _items_count(after)
    match = (preview_total is not None and report_total is not None and report_total > 0
             and abs(report_total - preview_total) < 0.005)
    counts_match: bool | None = None
    if report_count is not None and preview_count is not None:
        counts_match = report_count == preview_count
        if match and not counts_match:
            match = False
    if preview_total is None:
        why = "в снимке «после» нет итога"
    elif report_total is None:
        why = "в отчёте нет сверенного итога сети"
    elif counts_match is False:
        why = (f"итог сошёлся ({preview_total}), а число позиций нет: отчёт "
               f"{report_count}, снимок {preview_count}")
    else:
        why = f"снимок {preview_total}, отчёт {report_total}"
    return {"match": match, "preview": preview_total, "report": report_total,
            "items_count": preview_count, "report_items_count": report_count,
            "counts_match": counts_match, "field": field, "scale": scale, "why": why}


# ---------- общие мелочи ----------
def _console() -> None:
    """Печать в консоль Windows: в cp866 нет знака «₽», и один товар с ценой ронял бы отчёт."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_json(path: Any, data: Any) -> None:
    """Записать JSON, читаемый человеком: файлы доказательств открывают глазами.

    Каталог здесь НЕ создаём: опечатка в пути иначе насыпала бы в репозитории пустых
    папок, а заметить её было бы нечем. Пусть прогон остановится и скажет, что не так.
    """
    target = Path(path)
    if not target.parent.exists():
        raise EmucartError(f"каталога для записи нет: {target.parent} — создайте его "
                           "или укажите существующий (сырое живёт вне репозитория)")
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")


def _read_json(path: Any) -> Any:
    """Прочитать файл доказательства. Нет файла — None, битый — понятная ошибка."""
    if not path:
        return None
    source = Path(path)
    if not source.exists():
        print(f"ФАЙЛ НЕ НАЙДЕН: {source}")
        return None
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except ValueError as err:
        print(f"ФАЙЛ НЕ ЧИТАЕТСЯ КАК JSON: {source}: {err}")
        return None


def _moment(value: Any) -> datetime | None:
    """Время из ISO. Зону снимаем: отчёт пишется локальным временем (app/phonecart.py)."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


# ---------- команды ----------
def cmd_plan(args: argparse.Namespace) -> int:
    """Шаг 6: пробный наряд по HTTP. Вход в «Мою корзину» — без кода (`POST /demo`).

    До эмулятора этот шаг отвечает на вопрос, что вообще ляжет. Пустой наряд кнопкой
    «Положить этот» не подменяется (раздел 4 плана): она годится дымовой проверкой, а
    приёмку не закрывает.
    """
    base = args.base.rstrip("/")
    session = _http()
    try:
        gate = session.get(base + "/login", timeout=10)
    except Exception as err:  # noqa: BLE001 — стенд не отвечает: причина в тексте, не в стеке
        print(f"СТЕНД НЕ ПОДНЯТ: {base}/login не ответил ({err}).")
        print("Поднимите локальный сервер (tools/emu-stand.ps1, шаг 5) и повторите.")
        return EXIT_STAND
    if gate.status_code != 200:
        print(f"СТЕНД НЕ ПОДНЯТ: {base}/login ответил {gate.status_code}.")
        return EXIT_STAND

    body: dict[str, Any] = {"store": args.store}
    if args.basket is not None:
        body["basket"] = int(args.basket)
    try:
        # Витрина demo заводится и наполняется при первом открытии; вход без кода —
        # подтверждено app/web/login.py (поправка 4 плана).
        session.post(base + "/demo", timeout=args.timeout)
        answer = session.post(base + "/api/device/cartplan", json=body, timeout=args.timeout)
    except Exception as err:  # noqa: BLE001 — сервер не ответил: причина словами
        print(f"НАРЯД НЕ ОТДАН: {err}")
        return EXIT_STAND
    if answer.status_code != 200:
        print(f"НАРЯД НЕ ОТДАН: сервер ответил {answer.status_code}: {answer.text[:300]}")
        return EXIT_STAND
    data = answer.json()
    if not isinstance(data, dict):
        print(f"НАРЯД НЕ РАЗОБРАЛСЯ: {str(data)[:300]}")
        return EXIT_STAND

    rows = [row for row in (data.get("lines") or []) if isinstance(row, dict)]
    # «Настоящая ссылка» — по ХОСТУ, а не подстрокой: `https://example.com/?x=magnit.ru`
    # подстрокой проходил, и наряд объявлялся годным без единой карточки Магнита.
    real = [row for row in rows if _own_link(row.get("url"))]
    print(f"НАРЯД {args.store}: строк {len(rows)}, из них с настоящими ссылками {len(real)}")
    for index, row in enumerate(rows, 1):
        print(f"  {index:>3}. {row.get('sku')}  нажатий {row.get('clicks')}  "
              f"{row.get('name')}  {row.get('url') or '(без адреса карточки)'}")
    unknown = data.get("unknown") or []
    if unknown:
        listed = ", ".join(map(str, unknown))[:200]
        print(f"  незнакомых этой сети позиций: {len(unknown)} — {listed}")
    print(f"  корзина: {data.get('basket')} (отпечаток {data.get('stamp')})")
    print(f"  note: {data.get('note') or '—'}")
    print(f"  порог приёмки: {args.min_rows} строк с ссылкой на magnit.ru (поправка 2 плана)")
    thin = len(real) < args.min_rows
    if thin:
        print(f"НАРЯД БЕДНЕЕ ПОРОГА ({len(real)} < {args.min_rows}) — так приёмка не закроется.")
        print("Кнопкой «Положить этот» наряд не подменяем (раздел 4 плана): записываем note")
        print("и спрашиваем оркестратора.")
    if args.out:
        _write_json(args.out, {
            "store": args.store, "at": _now(), "basket": data.get("basket"),
            "stamp": data.get("stamp"), "note": data.get("note"),
            "unknown": unknown, "rows": rows, "real_rows": len(real),
            "min_rows": args.min_rows,
        })
        print(f"  наряд записан: {args.out}")
    return EXIT_NOT_FILLED if thin else EXIT_DONE


def _last_report(store: str, workplace: str) -> dict | None:
    """Последний отчёт телефона по сети — единственное, что читается из приложения.

    Читаем не по сети, а тем же кодом, что сервер: отчёт уже вычищен при приёме
    (app/phonecart.accept_report), и второй разбор здесь был бы вторым правилом.
    Рабочее место называет человек: наряд шага 6 выдаётся витрине demo (`POST /demo` —
    единственный вход без кода), но наряд мог уйти и другому рабочему месту, если
    человек вошёл в него сам.
    """
    from app import phonecart, users

    if not os.path.exists(users.db_path_for(workplace)):
        return None
    users.activate(workplace)
    try:
        return phonecart.last(store)
    except Exception:  # noqa: BLE001 — базы нет или она не открылась: «отчёта нет»
        return None
    finally:
        users.deactivate()


def _report_matches(report: dict, args: argparse.Namespace) -> str:
    """Годится ли отчёт для ЭТОГО прогона: пусто — годится, иначе почему нет.

    Ревью 18.10.2026: ждали просто «новее прошлого», и отчёт фоновой сборки 1.13,
    случившейся между шагами, сходил за отчёт ручного «Собрать». Отпечаток наряда и
    признак «фоновая» — то, чем один прогон отличается от другого.
    """
    stamp = _text(args.stamp, 40)
    if not stamp and args.plan:
        plan = _read_json(args.plan) or {}
        stamp = _text((plan or {}).get("stamp"), 40)
    if stamp and _text(report.get("stamp"), 40) != stamp:
        return (f"отпечаток {report.get('stamp')} — это отчёт другой сборки корзины "
                f"(ждём {stamp})")
    background = report.get("background")
    if args.background == "no" and background is True:
        return "отчёт от фоновой сборки 1.13, а ждём ручную «Собрать»"
    if args.background == "yes" and background is False:
        return "отчёт от ручной сборки, а ждём фоновую"
    return ""


def cmd_wait_report(args: argparse.Namespace) -> int:
    """Шаг 13: ждать, пока приложение положит НОВЫЙ отчёт о сборке Магнита.

    Порог «новее начала» — не придирка: без него старый отчёт с прошлого прогона
    сошёл бы за свежий, и вердикт был бы вынесен по чужой корзине. Но и «новее» мало:
    между шагами 12 и 13 приложение 1.13 само собирает корзины в фоне, и её отчёт
    новее — а к нашему прогону отношения не имеет. Поэтому сверяются ещё отпечаток
    наряда и признак «фоновая сборка» (ревью 18.10.2026).
    """
    since = _moment(args.since)
    if since is None:
        current = _last_report(args.store, args.workplace)
        since = _moment((current or {}).get("at"))
        if since is not None:
            print(f"В базе уже лежит отчёт от {since.isoformat(timespec='seconds')} — "
                  "ждём новее него.")
    stamp = _text(args.stamp, 40)
    if not stamp and args.plan:
        plan = _read_json(args.plan) or {}
        stamp = _text((plan or {}).get("stamp"), 40)
    which = {"no": "ручная", "yes": "фоновая"}.get(args.background, "любая")
    print(f"ЖДУ отчёт «{args.store}» (срок {args.timeout:.0f} с; начало сборки "
          f"{since.isoformat(timespec='seconds') if since else '—'}; "
          f"отпечаток {stamp or 'любой'}; сборка {which})…")
    deadline = time.monotonic() + args.timeout
    seen: dict[str, str] = {}
    while True:
        report = _last_report(args.store, args.workplace)
        at = _moment((report or {}).get("at"))
        if report and at is not None and (since is None or at > since):
            reason = _report_matches(report, args)
            if not reason:
                return _show_report(report, args)
            key = _text(report.get("at"), 30)
            if key not in seen:
                seen[key] = reason
                print(f"  отчёт от {key} не наш: {reason} — жду свой")
        if time.monotonic() >= deadline:
            print("ОТЧЁТ НЕ ПРИШЁЛ за отведённое время.")
            if seen:
                for key, reason in list(seen.items())[-3:]:
                    print(f"  видел чужой отчёт от {key}: {reason}")
            print("Смотреть: панель на телефоне (ход сборки), `adb logcat -s Korzina:*`,")
            print("повторить с шага 13 (tools/emu-stand.ps1 -From 13).")
            return EXIT_NOT_FILLED
        time.sleep(max(0.5, args.interval))


def _show_report(report: dict, args: argparse.Namespace) -> int:
    """Показать отчёт и решить, звать ли человека. Отчёт пришёл — прогон идёт дальше."""
    items = [item for item in (report.get("items") or []) if isinstance(item, dict)]
    print(f"ОТЧЁТ {report.get('store')} от {report.get('at')}: "
          f"легло {report.get('landed')} из {report.get('planned')}, "
          f"счётчик корзины {report.get('cart_count')}")
    human: list[str] = []
    for item in items:
        mark = "легло" if item.get("ok") else "НЕ легло"
        how = item.get("how") or "tap"
        why = str(item.get("why") or "")
        print(f"  [{mark}] {item.get('sku')}  {item.get('name')}  "
              f"нажатий {item.get('times')} / положено {item.get('put')} ({how})"
              + (f" — {why}" if why else ""))
        if not item.get("ok") and HUMAN_RE.search(why):
            human.append(f"{item.get('name')}: {why}")
    if args.out:
        _write_json(args.out, report)
        print(f"  отчёт записан: {args.out}")
    if human:
        print("НУЖЕН ЧЕЛОВЕК: отчёт говорит про вход или проверку —")
        for line in human[:5]:
            print(f"  — {line}")
        print("Снимите экран и передайте владельцу: "
              "`adb exec-out screencap -p > screens/step13.png` (код 20).")
        return EXIT_HUMAN
    return EXIT_DONE


def _adb_targets() -> list[str]:
    """Живые цели отладки WebView из `/proc/net/unix` — подсказка человеку, не проверка.

    За один живой прогон 18.10.2026 приложение на эмуляторе перезапускалось трижды, и
    `adb forward` каждый раз оставался указывать на сокет УЖЕ УБИТОГО процесса:
    соединение принималось и тут же рвалось, а выглядело это как «WebView не отвечает»,
    хотя эмулятор здоров. Живой pid из этой подсказки и есть та единственная строка,
    которой чинится шаг 10. adb — разрешённый канал инструмента, и здесь он только
    читает список сокетов.
    """
    adb = shutil.which("adb") or os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk", "platform-tools", "adb.exe")
    if not adb or not os.path.exists(adb):
        return []
    try:
        done = subprocess.run([adb, "shell", "cat /proc/net/unix"], capture_output=True,
                              text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted(set(re.findall(r"webview_devtools_remote_(\d+)", done.stdout or "")))


def _cdp_hints() -> None:
    """Как починить отладку. Без живого pid подсказка бесполезна: проброс смотрят не туда."""
    print("Шаг 10 плана: убедиться, что отладка WebView включена, и пробросить порт:")
    print("  adb shell cat /proc/net/unix | findstr webview_devtools_remote")
    print("  adb forward tcp:9222 localabstract:webview_devtools_remote_<pid>")
    for pid in _adb_targets():
        print(f"  живая цель сейчас: webview_devtools_remote_{pid}")
        print(f"    adb forward tcp:9222 localabstract:webview_devtools_remote_{pid}")
    print("Приложение перезапускалось? Тогда старый проброс смотрит на мёртвый сокет:")
    print("соединение принимается и сразу рвётся — это и значит «WebView не отвечает».")
    print("Запасной путь доказательств (раздел 5 плана): api_totals отчёта,")
    print("uiautomator dump страницы корзины и снимки экрана.")


def _cdp_targets(cdp: str) -> list[dict]:
    """Цели отладки из `GET /json`. Пусто — канал не поднят, объяснение уже напечатано.

    Идём за СПИСКОМ СТРАНИЦ, а не за `/json/version`: у WebView уровень браузера не
    поддерживается, и браузерный websocket оттуда нам не годится — работать можно
    только с websocket самой страницы (`webSocketDebuggerUrl` в этом списке).
    """
    try:
        answer = _http().get(cdp.rstrip("/") + "/json", timeout=5)
        answer.raise_for_status()
        raw = answer.json()
    except Exception as err:  # noqa: BLE001 — любая беда здесь значит «канала нет»
        print(f"КАНАЛ CDP НЕ ПОДНЯТ: {cdp}/json не ответил ({err}).")
        _cdp_hints()
        return []
    if not isinstance(raw, list):
        print(f"КАНАЛ CDP ОТВЕТИЛ НЕ СПИСКОМ ЦЕЛЕЙ: {str(raw)[:200]}")
        return []
    return [row for row in raw if isinstance(row, dict)]


def _pages(targets: list[dict]) -> list[dict]:
    """Только те цели, с которыми можно говорить: страницы со своим websocket.

    У WebView в списке бывают и служебные цели (worker, iframe) — им наши методы не
    нужны и они бы только сбивали выбор.
    """
    return [row for row in targets
            if str(row.get("type") or "page") == "page" and row.get("webSocketDebuggerUrl")]


def _pick_page(pages: list[dict], prefer: str = "") -> dict | None:
    """Страница для работы: сначала нужного сайта (magnit.ru), иначе — первая."""
    if not pages:
        return None
    if prefer:
        for row in pages:
            if prefer in str(row.get("url") or ""):
                return row
    return pages[0]


# Выражение страницы для имён кук — и НИЧЕГО БОЛЬШЕ: часть до первого «=». Значение
# куки входа это и есть вход владельца, и в инструменте ему не место ни в памяти, ни в
# файле. Признаки входа целиком собирает `_login_js`.
COOKIE_NAMES_JS = ("(() => document.cookie.split(';')"
                   ".map(c => c.split('=')[0].trim()).filter(Boolean))()")
# Сколько ждать ответа окна на один вызов и сколько держать тело ответа страницы.
CDP_TIMEOUT = 30.0
CDP_MAX_FRAME = 64 * 1024 * 1024


class EmucartError(RuntimeError):
    """Понятная ошибка инструмента: причина словами, а не трассировкой.

    Трассировка в отчёте прогона читается как поломка инструмента и уводит разбор не
    туда: у нас уже был случай, когда забытый каталог для `--out` выглядел падением.
    """


class CdpError(EmucartError):
    """Разговор с окном отладки не состоялся: причина словами, а не трассировкой."""


def _frame(call_id: int, method: str, params: dict | None = None) -> str:
    """Кадр запроса CDP: id, method, params — ровно в таком виде его ждёт страница."""
    return json.dumps({"id": int(call_id), "method": str(method),
                       "params": dict(params or {})}, ensure_ascii=False)


def _answer_of(raw: Any, call_id: int, method: str = "") -> dict:
    """Ответ на НАШ вызов по id. Событие ответом не считается — у него нет id.

    Разбирать ответ по «первому пришедшему кадру» нельзя: страница шлёт события
    вперемешку с ответами, и Network.responseReceived легко принять за ответ на
    Network.enable. Поэтому id сверяется, а не угадывается.
    """
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as err:
        raise CdpError(f"кадр CDP не разобрался как JSON: {str(raw)[:120]}") from err
    if not isinstance(data, dict):
        raise CdpError(f"кадр CDP не объект: {str(raw)[:120]}")
    if data.get("id") is None or int(data.get("id")) != int(call_id):
        raise CdpError(f"ответ не на наш вызов: id={data.get('id')}, ждали {call_id}")
    error = data.get("error")
    if error:
        reason = error.get("message") if isinstance(error, dict) else error
        raise CdpError(f"страница отказала в {method or 'методе'}: {reason}")
    result = data.get("result")
    return result if isinstance(result, dict) else {}


def _event_of(raw: Any) -> tuple[str, dict] | None:
    """Событие CDP: («Network.responseReceived», params). Не событие — None."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("id") is not None:
        return None
    method = data.get("method")
    if not isinstance(method, str):
        return None
    params = data.get("params")
    return method, params if isinstance(params, dict) else {}


def _preview_hit(events: list[tuple[str, dict]], path: str = PREVIEW_PATH) -> dict | None:
    """Ответ сайта о корзине среди событий: последний по времени (свежий) и его requestId."""
    hit: dict | None = None
    for method, params in events:
        if method != "Network.responseReceived":
            continue
        response = params.get("response")
        url = str((response or {}).get("url") or "") if isinstance(response, dict) else ""
        if path in url:
            hit = {"requestId": str(params.get("requestId") or ""), "url": url,
                   "status": (response or {}).get("status") if isinstance(response, dict) else None}
    return hit


def _body_loaded(events: list[tuple[str, dict]], request_id: str) -> bool:
    """Дошла ли загрузка ответа до конца: ждём `Network.loadingFinished` по его id.

    Ревью 18.10.2026: `getResponseBody` звался сразу после `responseReceived`, и тело
    могло быть ещё не готово — тогда снимок выходил пустым или чужим.
    """
    return any(method == "Network.loadingFinished"
               and str(params.get("requestId") or "") == request_id
               for method, params in events)


def _cart_body(body: str) -> bool:
    """Похоже ли тело на ответ `checkout/preview`: в нём есть итог корзины.

    Ревью 18.10.2026: пропускной фильтр был слабый — подходил любой ответ по тому же
    пути, и чужое тело (`carts/lite` с нулём) могло сойти за снимок корзины. Требуем
    поле итога, которым вердикт и сверяет: без него это не доказательство.
    """
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return False
    rule = _preview_rule()
    field = str(rule.get("total") or "summary.totalFinalPrice").rsplit(".", 1)[-1]
    summary = data.get("summary") if isinstance(data, dict) else None
    return bool(isinstance(summary, dict) and _number(summary.get(field)) is not None)


async def _preview_from_page(socket: str, page_url: str, wait: float,
                             timeout: float) -> dict:
    """Открыть корзину и пассивно поймать ответ сайта `checkout/preview`.

    Своих запросов к Магниту нет: Network.enable включается ДО перехода, дальше ждём,
    пока сайт сам сходит за своей корзиной, и читаем тело уже пойманного ответа — но
    только после `Network.loadingFinished`, и только если в теле есть итог корзины.

    Возвращаем и URL со статусом, у которых взято тело: у этого WebView requestId не
    уникален (замер 18.10.2026), и «взяли у того, у кого просили» — утверждение, которое
    надо называть вслух, а не подразумевать.
    """
    async with CdpSocket(socket, timeout=timeout) as cdp:
        await cdp.call("Network.enable")
        await cdp.call("Page.enable")
        await cdp.call("Page.navigate", {"url": page_url})
        deadline = time.monotonic() + wait
        hit = None
        while time.monotonic() < deadline:
            await cdp.collect(min(0.5, max(0.05, deadline - time.monotonic())))
            hit = _preview_hit(cdp.events)
            if hit and _body_loaded(cdp.events, hit["requestId"]):
                break
        if hit is None or not hit.get("requestId"):
            return {"body": None, "url": "", "request_id": "", "status": None,
                    "why": f"за {wait:.0f} с сайт не запросил {PREVIEW_PATH}"}
        if not _body_loaded(cdp.events, hit["requestId"]):
            return {"body": None, "url": hit.get("url") or "", "request_id": hit["requestId"],
                    "status": hit.get("status"),
                    "why": "ответ корзины начался, но загрузка не завершилась"}
        answer = await cdp.call("Network.getResponseBody",
                                {"requestId": hit["requestId"]})
        body = answer.get("body")
        if answer.get("base64Encoded") and isinstance(body, str):
            body = base64.b64decode(body).decode("utf-8", "replace")
        result = {"body": body if isinstance(body, str) else None,
                  "url": hit.get("url") or "", "request_id": hit["requestId"],
                  "status": hit.get("status"), "why": ""}
        if isinstance(body, str) and not _cart_body(body):
            result["body"] = None
            result["why"] = ("тело не похоже на ответ корзины (нет итога "
                             f"{_preview_rule().get('total')})")
        return result


class CdpSocket:
    """Разговор с ОДНОЙ страницей WebView по CDP: уровень страницы, не браузера.

    ПОЧЕМУ ТАК, А НЕ PLAYWRIGHT — в докстринге модуля: у WebView нет уровня браузера,
    и Playwright падает на первом же его методе. Здесь только те методы, что страница
    принимает вживую: Runtime.evaluate, Network.enable, Page.navigate,
    Page.captureScreenshot, Network.getResponseBody.

    Соединение закрывается выходом из контекста и НЕ закрывает окно владельца: у
    страницы нет метода «закрой браузер», а `Browser.close` ей и не пришёл бы.
    """

    def __init__(self, socket: str, timeout: float = CDP_TIMEOUT):
        self.socket = socket
        self.timeout = timeout
        self.events: list[tuple[str, dict]] = []
        self._ws: Any = None
        self._last_id = 0
        self._closed: type = Exception

    async def __aenter__(self) -> "CdpSocket":
        try:
            from websockets.asyncio.client import connect
            from websockets.exceptions import ConnectionClosed
        except ImportError as err:  # noqa: BLE001 — библиотеки нет: узел стены, не поломка окна
            raise CdpError("нет библиотеки websockets — без неё окно отладки не спросить "
                           "(поставьте: pip install -r requirements.txt)") from err
        # Класс обрыва держим при себе: по нему отличаем «срок вышел» (нормально) от
        # «страница пропала» (это сбой канала, а не повод звать человека).
        self._closed = ConnectionClosed

        try:
            # proxy=None: до 127.0.0.1 прокси не нужен, а переменная окружения с
            # корпоративным прокси иначе увела бы разговор с отладкой в сторону.
            self._ws = await connect(self.socket, open_timeout=self.timeout,
                                     max_size=CDP_MAX_FRAME, proxy=None)
        except Exception as err:  # noqa: BLE001
            raise CdpError(f"websocket страницы не открылся: {type(err).__name__}: "
                           f"{str(err)[:200]}") from err
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001 — закрытие не важнее уже собранных данных
                pass
            self._ws = None

    async def call(self, method: str, params: dict | None = None) -> dict:
        """Вызвать метод страницы и вернуть его result. События по дороге копятся.

        Срок — на ВЕСЬ вызов, а не на каждый кадр (ревью 18.10.2026): пока страница
        шлёт события, ожидание кадра сбрасывалось, и вызов не истекал никогда. Счёт
        идёт от отправки запроса.

        `errorText` из ответа — это отказ страницы сделать переход (её собственная
        беда, а не наша опечатка): молча его пропустить значило бы снимать корзину,
        которая не открылась.
        """
        self._last_id += 1
        call_id = self._last_id
        try:
            await self._ws.send(_frame(call_id, method, params))
        except Exception as err:  # noqa: BLE001
            raise CdpError(f"кадр {method} не ушёл: {type(err).__name__}") from err
        deadline = time.monotonic() + self.timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise CdpError(f"страница не ответила на {method} за {self.timeout:.0f} с")
            try:
                raw = await asyncio.wait_for(self._ws.recv(), left)
            except asyncio.TimeoutError as err:
                raise CdpError(f"страница не ответила на {method} за {self.timeout:.0f} с") from err
            except Exception as err:  # noqa: BLE001
                raise CdpError(f"связь со страницей оборвалась на {method}: "
                               f"{type(err).__name__}") from err
            event = _event_of(raw)
            if event is not None:
                self.events.append(event)
                continue
            answer = _answer_of(raw, call_id, method)
            error = answer.get("errorText")
            if error:
                raise CdpError(f"{method} не удался: {error}")
            return answer

    async def collect(self, seconds: float) -> None:
        """Слушать события указанное время: ответ сайта приходит сам, мы его не зовём.

        Истёк срок — это нормальный конец слушания. Оборвалась связь — нет: ревью
        18.10.2026 нашло, что здесь глоталась любая ошибка, и потеря страницы
        превращалась в «ответ не пойман, нужен человек» вместо «канал оборвался».
        """
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return
            try:
                raw = await asyncio.wait_for(self._ws.recv(), left)
            except asyncio.TimeoutError:
                return
            except self._closed as err:
                raise CdpError(f"страница закрыла отладку: {type(err).__name__}") from err
            except Exception as err:  # noqa: BLE001
                raise CdpError(f"связь со страницей оборвалась: {type(err).__name__}") from err
            event = _event_of(raw)
            if event is not None:
                self.events.append(event)

    async def evaluate(self, expression: str) -> Any:
        """Значение выражения на странице (Runtime.evaluate, returnByValue).

        Пустота здесь — ошибка, а не ответ. Функция, которую забыли позвать, и
        `undefined` (ревью 18.10.2026) читались как «кук нет» и звали человека зря:
        `Runtime.evaluate` выполняет код и на `() => …` вернул бы саму функцию.
        """
        answer = await self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True})
        details = answer.get("exceptionDetails")
        if details:
            text = (details.get("exception") or {}).get("description", "")
            raise CdpError(f"выражение на странице упало: {text or details}")
        result = answer.get("result")
        if not isinstance(result, dict):
            raise CdpError("страница ответила на выражение без результата")
        if result.get("type") == "function":
            raise CdpError("выражение вернуло функцию, а не её вызов: оберните в «(…)()»")
        if result.get("type") == "undefined":
            raise CdpError("выражение на странице вернуло undefined — читать нечего")
        return result.get("value")


@lru_cache(maxsize=1)
def _signals() -> dict:
    """Слова входа — из app/shopbrowser/signals.py, а не свои.

    Правило входа одно на весь репозиторий: приложение судит о нём теми же словами
    (STRONG_IN, ANONYMOUS) и по тому же куску страницы — началу и концу видимого
    текста. Свой список здесь разошёлся бы с приложением при первой правке вёрстки, и
    инструмент сказал бы «не вошёл» там, где приложение уже показало человеку кабинет.
    """
    from app.shopbrowser import signals

    return {"strong": signals.STRONG_IN, "anon": signals.ANONYMOUS, "guard": signals.GUARD,
            "head": int(signals.HEAD_CHARS), "tail": int(signals.TAIL_CHARS)}


def _login_js() -> str:
    """Выражение страницы: признаки входа, наружу — только да/нет и имена.

    Слова страницы проверяются ЗДЕСЬ ЖЕ, в браузере, а не в Python: в видимом тексте
    стоит «Здравствуйте, <имя>», и выпустить этот текст из страницы значило бы увезти
    имя владельца в протокол. Наружу уходят три boolean, число ключей и ИМЕНА ключей
    семейства сессии; значения кук и localStorage не читаются ни здесь, ни там.

    Выражение ЗОВЁТСЯ на месте, `(…)()`: Runtime.evaluate выполняет код, а не вызывает
    функцию-значение. С `() => …` без вызова страница вернула бы саму функцию, и
    проверка молча показала бы «кук нет» — на живом прогоне 18.10.2026 так и вышло.
    """
    rules = _signals()
    return ("(() => {\n"
            f"  const head = {rules['head']}, tail = {rules['tail']};\n"
            "  const body = ((document.body && document.body.innerText) || '')"
            ".toLowerCase().replace(/ё/g, 'е');\n"
            "  const text = body.length <= head + tail ? body\n"
            "    : body.slice(0, head) + ' … ' + body.slice(-tail);\n"
            f"  const strong = new RegExp({json.dumps(rules['strong'])}, 'i');\n"
            f"  const anon = new RegExp({json.dumps(rules['anon'])}, 'i');\n"
            f"  const guard = new RegExp({json.dumps(rules['guard'])}, 'i');\n"
            "  const keys = [];\n"
            "  try { for (let i = 0; i < localStorage.length; i++)"
            " keys.push(String(localStorage.key(i))); } catch (e) {}\n"
            "  return {\n"
            "    url: location.href,\n"
            "    cookies: document.cookie.split(';')"
            ".map(c => c.split('=')[0].trim()).filter(Boolean),\n"
            "    storage_count: keys.length,\n"
            f"    session_keys: keys.filter(k => k.indexOf({json.dumps(SESSION_PREFIX)}) === 0),\n"
            "    strong: strong.test(text), anon: anon.test(text), guard: guard.test(text),\n"
            "  };\n"
            "})()")


async def _page_facts(socket: str, timeout: float = CDP_TIMEOUT) -> dict:
    """Что за страница перед нами: адрес без параметров и признаки входа — именами и да/нет.

    Ни значений кук, ни значений localStorage, ни текста страницы наружу не выходит.
    Адрес отдаётся без query и якоря, а `document.title` не отдаётся вовсе: ревью
    18.10.2026 нашло, что полный `location.href` и заголовок уезжали в протокол, а в
    заголовке личного кабинета бывает имя владельца.
    """
    async with CdpSocket(socket, timeout=timeout) as cdp:
        raw = await cdp.evaluate(_login_js())
    facts = raw if isinstance(raw, dict) else {}
    return {
        "url": _safe_url(facts.get("url")),
        "cookies": [str(name).strip() for name in (facts.get("cookies") or [])
                    if str(name).strip()],
        "session_keys": _session_keys(facts.get("session_keys") or []),
        "storage_count": int(facts.get("storage_count") or 0),
        "strong": bool(facts.get("strong")),
        "anon": bool(facts.get("anon")),
        "guard": bool(facts.get("guard")),
    }


def _login_visible(names: list, cookie: str = COOKIE_NAME) -> bool:
    """Видно ли ИМЯ куки входа. Значения здесь нет и быть не может — только имена."""
    return cookie in {str(name).strip() for name in names or []}


def _session_keys(keys: list) -> list[str]:
    """Имена ключей сессии в localStorage — семейство `au_`. Значения не читаются."""
    return sorted({str(key).strip() for key in keys or []
                   if str(key).strip().startswith(SESSION_PREFIX)})


def _login_state(payload: dict, prefer: str = "") -> tuple[str, str]:
    """«вошёл / не вошёл / не знаю» и на чём решено — по сессии, а не по имени куки.

    Порядок признаков тот же, что у приложения (app/shopbrowser/signals.logged_in):
    слова вошедшего → слова гостя → «страница не сказала». Это не случайность: два
    ответа про один вход, разойдясь, заставят человека гадать, кому верить. Дальше идут
    признаки, которых у приложения нет, — ключи сессии в localStorage (так Магнит держит
    вход с 18.10.2026) и имя куки как старая подсказка.

    ОТСУТСТВИЕ ВСЕГО — ЭТО «НЕ ЗНАЮ», А НЕ «НЕ ВОШЁЛ». 18.10.2026 живой вход был, а
    имени `mg_at` не было ни в куках страницы, ни в базе кук WebView: проверка по куке
    объявила владельца гостем и позвала его входить заново. Врать в эту сторону дороже,
    чем признаться: стенд зовёт человека по вердикту.
    """
    url = str(payload.get("url") or "")
    if prefer and prefer not in url:
        return "unknown", f"открыта не страница {prefer}, а {url or '—'}: про вход она не говорит"
    if payload.get("guard"):
        return "unknown", "страница показала проверку — это не витрина"
    if payload.get("strong"):
        return "yes", "страница говорит словами вошедшего («выйти», «мой профиль» и подобные)"
    session = _session_keys(payload.get("session_keys") or [])
    if payload.get("anon"):
        if session:
            return "unknown", ("признаки расходятся: страница зовёт войти, а ключи сессии "
                               f"{', '.join(session)} на месте")
        return "no", "страница зовёт войти"
    if session:
        return "yes", f"в localStorage лежат ключи сессии {', '.join(session)} (имена, не значения)"
    if _login_visible(payload.get("cookies") or []):
        return "yes", f"видно имя куки {COOKIE_NAME} (старый признак, замер 17.09.2026)"
    return "unknown", ("ни слов входа на странице, ни ключей сессии, ни имени куки — "
                       "определённый ответ даёт страница magnit.ru/profile")


def _human(payload: dict, prefer: str = "") -> str:
    """Строка вердикта про вход: «вошёл», «не вошёл» или «не знаю» — и на чём решено.

    Молчание здесь дороже слов: стенд по этой строке либо зовёт владельца, либо идёт
    дальше по прогону. Поэтому «не знаю» — полноправный ответ, а не сбой.
    """
    state, why = _login_state(payload, prefer)
    words = {"yes": "человек вошёл", "no": "человек не вошёл",
             "unknown": "про вход не знаю"}[state]
    return f"{words}: {why}"


def cmd_check_login(args: argparse.Namespace) -> int:
    """Шаг 11 стенда: вошёл ли человек — по сессии, а не по имени куки.

    Нужна стенду, чтобы решить «звать владельца или шаг пройден», и стоит эта ошибка
    дорого в обе стороны: ложное «не вошёл» гоняет человека к телефону зря, ложное
    «вошёл» ведёт прогон в гостя. 18.10.2026 вышло первое: вход был живой, а куки
    `mg_at` не было — сайт держит сессию в `localStorage`. Поэтому здесь три признака
    (слова страницы теми же регулярками, что у приложения, ключи сессии в localStorage
    и имя куки как старая подсказка), и «не знаю» вместо «не вошёл», когда не видно
    ничего. Значения кук и localStorage не читаются и не сохраняются: это вход
    владельца. Печатаются и машинные строки `login=…` и `mg_at=…` — стенду нужно и то,
    и другое.
    """
    targets = _cdp_targets(args.cdp)
    if not targets:
        return EXIT_STAND
    pages = _pages(targets)
    if not pages:
        print("КАНАЛ CDP ОТКРЫТ, НО СТРАНИЦ В НЁМ НЕТ: приложение на экране эмулятора?")
        print("Откройте окно «Моей корзины» и повторите.")
        return EXIT_STAND
    page = _pick_page(pages, args.prefer)
    if page is None:
        return EXIT_STAND
    try:
        facts = asyncio.run(_page_facts(str(page["webSocketDebuggerUrl"]), args.timeout))
    except CdpError as err:
        print(f"ОКНО WEBVIEW НЕ ОТВЕТИЛО: {err}")
        print("Запасной путь доказательств (раздел 5 плана): снимки экрана и "
              "api_totals отчёта.")
        return EXIT_STAND
    session = _session_keys(facts["session_keys"])
    state, _ = _login_state(facts, args.prefer)
    print(f"СТРАНИЦА ОТЛАДКИ: {facts['url'] or '—'}")
    print(f"  имён кук видно: {len(facts['cookies'])} (значения не читаем и не храним); "
          f"mg_at={'visible' if _login_visible(facts['cookies']) else 'not-visible'}")
    print(f"  ключей localStorage видно: {facts['storage_count']} (значения не читаем и не "
          f"храним); семейство {SESSION_PREFIX}*: {len(session)}"
          + (f" ({', '.join(session)})" if session else ""))
    print(f"  слова страницы: вошедшего — {'да' if facts['strong'] else 'нет'}; "
          f"зовущие войти — {'да' if facts['anon'] else 'нет'}; "
          f"проверка браузера — {'да' if facts['guard'] else 'нет'}")
    print(f"  login={state}")
    print(f"ВЕРДИКТ: {_human(facts, args.prefer)}")
    if state != "yes":
        print(f"  имя куки {COOKIE_NAME} признаком входа больше не считается (замер "
              "18.10.2026): вход Магнита лежит в localStorage")
    if state == "unknown" and args.prefer and args.prefer not in facts["url"]:
        print(f"  откройте {args.prefer} в WebView (шаг 11) и повторите")
    if state != "yes" and args.require:
        print("НУЖЕН ЧЕЛОВЕК: вход в magnit.ru внутри WebView (код 20).")
        return EXIT_HUMAN
    return EXIT_DONE


async def _capture_png(socket: str, path: str, timeout: float) -> None:
    """Снимок страницы средствами самой страницы (Page.captureScreenshot)."""
    async with CdpSocket(socket, timeout=timeout) as cdp:
        answer = await cdp.call("Page.captureScreenshot", {"format": "png"})
    data = answer.get("data")
    if not isinstance(data, str):
        raise CdpError("страница не отдала снимок")
    Path(path).write_bytes(base64.b64decode(data))


def cmd_snap_preview(args: argparse.Namespace) -> int:
    """Шаги 12 и 14: пассивно поймать ответ сайта о корзине и очистить его.

    Своих запросов к Магниту нет: включаем Network, открываем `/ecom/cart` и ждём,
    пока САМ сайт сходит за `checkout/preview`. Тело читается у пойманного ответа, а не
    запрашивается заново, и только после `Network.loadingFinished`.

    ОКНО САМОГО ПРИЛОЖЕНИЯ НЕ УВОДИМ. Ревью 18.10.2026: если страницы Магнита в WebView
    нет, бралась первая попавшаяся — окно «Моей корзины», — и переход уносил его на
    корзину Магнита, обрывая идущую фоновую сборку. Теперь без страницы Магнита шаг
    останавливается и зовёт человека: открыть Магнит в окне приложения — это его шаг.

    Куки читаются только именами. Значение `mg_at` — ключ входа владельца: показать
    или сохранить его значило бы унести вход в репозиторий. Снимок несёт привязку к
    прогону (время, адрес без параметров, id ответа, код), по ней вердикт и отличает
    сегодняшний снимок от прошлогоднего.
    """
    if args.raw_out:
        _no_raw_in_repo(args.raw_out, "сырой ответ (--raw-out)")
    if args.shot:
        _no_raw_in_repo(args.shot, "снимок экрана (--shot)")
    targets = _cdp_targets(args.cdp)
    if not targets:
        return EXIT_STAND
    pages = _pages(targets)
    page = _pick_page(pages, args.prefer)
    if page is None:
        print("КАНАЛ CDP ОТКРЫТ, НО СТРАНИЦ В НЁМ НЕТ: приложение на экране эмулятора?")
        print("Откройте окно «Моей корзины» и повторите (код 10).")
        return EXIT_STAND
    if args.prefer and args.prefer not in str(page.get("url") or ""):
        print(f"НУЖЕН ЧЕЛОВЕК: в WebView нет страницы {args.prefer} "
              f"(открыто: {_safe_url(page.get('url'))}).")
        print("Откройте Магнит в окне приложения — это человеческий шаг (плана, шаг 11) —")
        print("и повторите. Окно самого приложения инструмент не уводит: иначе он оборвал бы")
        print("идущую в нём сборку корзины (код 20).")
        return EXIT_HUMAN

    socket = str(page["webSocketDebuggerUrl"])
    print(f"ОКНО ОТЛАДКИ: {_safe_url(page.get('url')) or '—'}")
    try:
        caught_answer = asyncio.run(_preview_from_page(socket, args.page, args.wait,
                                                       args.page_timeout))
    except CdpError as err:
        print(f"ОКНО WEBVIEW НЕ ОТВЕТИЛО: {err}")
        print("Android WebView принимает не всё: уровнем браузера он не владеет. "
              "Запасной путь доказательств (раздел 5 плана):")
        print(f"  1) итог — api_totals['{PREVIEW_PATH}'] в report.json "
              "(перехват самого приложения, доказательство слабее);")
        print("  2) позиции — uiautomator dump страницы корзины;")
        print("  3) снимки экрана. В протоколе сказать, что CDP не дался.")
        return EXIT_STAND
    result_text = caught_answer.get("body")
    caught_at = _now()

    if args.shot:
        try:
            asyncio.run(_capture_png(socket, args.shot, args.page_timeout))
            print(f"  снимок страницы: {args.shot} — перед коммитом его смотрит владелец")
        except (CdpError, ValueError) as err:  # noqa: BLE001 — снимок не важнее ответа
            print(f"  снимок экрана не сделался: {err}")
    try:
        facts = asyncio.run(_page_facts(socket, args.page_timeout))
        print(f"  {_human(facts, args.prefer)}")
    except CdpError as err:  # noqa: BLE001 — имена кук не прочитались: снимок уже есть
        print(f"  про вход не спросилось: {err}")

    if not result_text:
        why = caught_answer.get("why") or "причина неизвестна"
        print(f"ОТВЕТ checkout/preview НЕ ПОЙМАН: {why}.")
        print("Причины по плану: корзина Магнита не открылась сама (вход, капча, выбор")
        print("адреса) — тогда нужен человек; либо сайт не запрашивал preview, пока")
        print("страница грузилась, — откройте корзину ещё раз.")
        return EXIT_HUMAN
    try:
        clean = sanitize_preview(result_text, {
            "captured_at": caught_at, "url": caught_answer.get("url"),
            "request_id": caught_answer.get("request_id"),
            "status": caught_answer.get("status")})
    except ValueError as err:
        print(f"ОТВЕТ PREVIEW НЕ РАЗОБРАЛСЯ: {err}")
        print("Либо пришла страница проверки, а не корзина (нужен человек, код 20), либо")
        print("тело взято не у того запроса: у этого WebView requestId не уникален. Верный")
        print("ответ лучше не записывать вовсе — снимок не сохранён.")
        return EXIT_HUMAN

    _write_json(args.out, clean)
    summary = clean["summary"]
    print(f"СНИМОК КОРЗИНЫ записан: {args.out}")
    print(f"  пойман ответ: {_safe_url(caught_answer.get('url'))} "
          f"(код {caught_answer.get('status')}, requestId {caught_answer.get('request_id')})")
    print(f"  привязка к прогону: снят в {caught_at}; порядок времён проверит вердикт")
    print(f"  позиций: {clean['items_found']} (где: {', '.join(clean['where']) or '—'})")
    print(f"  itemsCount: {summary.get('itemsCount')}, "
          f"totalFinalPrice: {summary.get('totalFinalPrice')} "
          f"(копейки: множитель {clean['money_scale']})")
    print(f"  выброшено: личных полей {clean['dropped']['personal_fields']}, "
          f"полей позиций {clean['dropped']['item_fields']}")
    if args.raw_out:
        Path(args.raw_out).write_text(result_text, encoding="utf-8")
        print(f"  СЫРОЙ ответ: {args.raw_out} — вне репозитория, в доказательства его не класть")
    return EXIT_DONE


def cmd_verdict(args: argparse.Namespace) -> int:
    """Шаг 14: вердикт по снимкам и отчёту. JSON — в файл, короткий итог — на экран."""
    before = _read_json(args.before) if args.before else None
    after = _read_json(args.after) if args.after else None
    report = _read_json(args.report)
    if report is None:
        return EXIT_STAND
    plan = _read_json(args.plan) if args.plan else None
    result = verdict(before, after, report, plan=plan, manual=not args.background,
                     allow_unbound=args.allow_unbound)
    print(f"ВЕРДИКТ: {result['verdict']} — {result['share']}")
    if not result["bound"]:
        print(f"  {result['binding_note'] or 'привязка не подтверждена'}"
              + ("" if args.allow_unbound else " (--allow-unbound посчитает с этой оговоркой)"))
    for row in result["proof"]:
        mark = "да" if row["done"] else ("НЕТ" if row["done"] is False else "не с чем")
        print(f"  [{mark}] {row['name']}: {row['detail']}")
    for row in result["not_landed"]:
        taken = "" if row["available"] is None else (
            " (в корзине Магнита её нет — «нет в наличии»)" if not row["available"] else "")
        print(f"  НЕ ПРИНЯТО: {row['sku']} {row['name']} — {row['why']}{taken}")
    for defect in result["defects"]:
        print(f"  ДЕФЕКТ: {defect['kind']} — {defect['sku']} {defect['name']}: {defect['why']}")
        print(f"          смотреть: {defect['where']}")
    for line in result["unproven"]:
        print(f"  не доказано: {line}")
    for note in result["notes"]:
        print(f"  к сведению: {note}")
    for line in result["manual"]:
        print(f"  человеку: {line}")
    if args.out:
        _write_json(args.out, result)
        print(f"  вердикт записан: {args.out}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_DONE if result["ok"] else EXIT_NOT_FILLED


def _check_clean(path: Path) -> None:
    """Отказ, если «очищенный» снимок на деле сырой: в репозиторий его пускать нельзя."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "items_found" not in data or "money_scale" not in data:
        raise ValueError(f"{path.name}: это не вывод sanitize_preview, а сырой ответ")


# Поля отчёта телефона — те же, что оставляет приём (app/phonecart.accept_report), и
# поля наряда — те же, что пишет plan. Второй белый список здесь стоит не потому, что
# серверу нет веры, а потому, что export берёт файл ПО ИМЕНИ: под именем report.json
# мог оказаться и сырой ответ, и чужая выгрузка (ревью 18.10.2026).
REPORT_FIELDS = ("store", "at", "app_version", "planned", "landed", "cart_count",
                 "cart_page", "our_total", "background", "basket", "stamp", "seconds")
REPORT_ITEM_FIELDS = ("name", "sku", "ok", "why", "put", "times", "how", "calls")
PLAN_FIELDS = ("store", "at", "basket", "stamp", "note", "real_rows", "min_rows")
PLAN_ROW_FIELDS = ("sku", "name", "url", "clicks", "qty", "price", "unit")


def sanitize_report(raw: Any) -> dict:
    """Отчёт телефона по белому списку приёма: имена, артикулы, суммы — и ничего сверх.

    Отчёт уже вычищен сервером, но export берёт файл по имени. Здесь второй проход
    оставляет только те поля, которые приём и так пишет, и выбрасывает всё прочее —
    включая `calls`, если строка в них не похожа на «PUT /path код».
    """
    data = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for key in REPORT_FIELDS:
        if key not in data:
            continue
        value = data[key]
        if key in ("planned", "landed", "cart_count", "basket", "seconds"):
            number = _int(value)
            if number is not None:
                out[key] = number
        elif key == "our_total":
            number = _number(value)
            if number is not None:
                out[key] = number
        elif key == "background":
            out[key] = bool(value)
        else:
            out[key] = _text(value, 200)
    items = []
    for item in (data.get("items") or [])[:200]:
        if not isinstance(item, dict):
            continue
        clean = {"name": _text(item.get("name"), 120), "sku": _text(item.get("sku"), 40),
                 "ok": bool(item.get("ok")), "why": _text(item.get("why"), 200),
                 "put": _int(item.get("put")) or 0, "times": _int(item.get("times")) or 0,
                 "how": _text(item.get("how"), 10) or "tap"}
        calls = [_text(call, 120) for call in (item.get("calls") or [])[:6]
                 if isinstance(call, str) and re.match(r"^[A-Z]{3,7} \S+", call.strip())]
        clean["calls"] = calls
        items.append(clean)
    if items or "items" in data:
        out["items"] = items
    totals = []
    for row in (data.get("page_totals") or [])[:20]:
        if isinstance(row, dict) and _number(row.get("amount")) is not None:
            totals.append({"kind": _text(row.get("kind"), 20), "label": _text(row.get("label"), 30),
                           "amount": _number(row.get("amount"))})
    if totals or "page_totals" in data:
        out["page_totals"] = totals
    api: dict[str, dict] = {}
    for path, fields in list((data.get("api_totals") or {}).items())[:6]:
        if not isinstance(fields, dict):
            continue
        clean = {}
        for key, value in list(fields.items())[:40]:
            name = _text(key, 80)
            number = _number(value)
            if number is not None and not _personal(name):
                clean[name] = number
        if clean:
            api[_text(path, 200)] = clean
    if api or "api_totals" in data:
        out["api_totals"] = api
    return out


def sanitize_plan(raw: Any) -> dict:
    """Наряд по белому списку plan: артикул, название, ссылка, нажатия — и ничего сверх."""
    data = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for key in PLAN_FIELDS:
        if key not in data:
            continue
        if key in ("basket", "real_rows", "min_rows"):
            number = _int(data[key])
            if number is not None:
                out[key] = number
        else:
            out[key] = _text(data[key], 400)
    unknown = [_text(name, 120) for name in (data.get("unknown") or [])[:200]
               if _text(name, 120)]
    if unknown:
        out["unknown"] = unknown
    rows = []
    for row in (data.get("rows") or [])[:200]:
        if not isinstance(row, dict):
            continue
        clean: dict[str, Any] = {}
        for key in PLAN_ROW_FIELDS:
            if key not in row:
                continue
            if key in ("clicks", "qty", "price"):
                number = _number(row[key])
                if number is not None:
                    clean[key] = number
            elif key == "url":
                clean[key] = _safe_url(row[key])
            else:
                clean[key] = _text(row[key], 160)
        rows.append(clean)
    if rows or "rows" in data:
        out["rows"] = rows
    return out


def clean_log(text: str) -> tuple[str, int]:
    """Оставить в журнале только строки про корзину и выбросить строки с признаками личного.

    Ревью 18.10.2026: `--logs` клал в репозиторий файл как есть. Даже обрезанный по тегу
    журнал телефона может нести вход владельца — длинные числа, токены, куки. Сколько
    строк выброшено, печатается: молчаливая чистка выглядела бы как «в журнале этого не
    было».
    """
    kept, dropped = [], 0
    for line in (text or "").splitlines():
        if not LOG_KEEP.search(line) or LOG_DROP.search(line):
            dropped += 1
            continue
        kept.append(line[:400])
    return "\n".join(kept) + ("\n" if kept else ""), dropped


def cmd_export(args: argparse.Namespace) -> int:
    """Шаг 15 плана: сложить доказательства в репозиторий — только очищенное.

    ЧТО ИМЕННО ДЕЛАЕТ (и чего не обещает). `plan.json` и `report.json` проходят белый
    список полей и переписываются очищенными; снимки берутся только те, что сделаны
    `sanitize_preview` (иначе отказ); снимки экрана и журналы копируются отдельными
    ключами и не по умолчанию — снимок корзины видит владелец, прежде чем он попадёт в
    репозиторий (раздел 5 плана), а журналы перед копированием чистятся: остаются строки
    про корзину и выбрасываются строки с признаками входа. `verdict.json` копируется как
    есть: он целиком сделан этим инструментом и личного не несёт.
    """
    run, target = Path(args.run), Path(args.to)
    if not run.is_dir():
        print(f"КАТАЛОГ ПРОГОНА НЕ НАЙДЕН: {run}")
        return EXIT_STAND
    # Сначала проверяем ВСЁ, потом копируем: иначе отказ на середине оставил бы в
    # репозитории половину доказательств — и непонятно, полон ли набор.
    for name in CLEAN_FILES:
        if not name.startswith("preview-"):
            continue
        source = run / name
        if not source.exists():
            continue
        try:
            _check_clean(source)
        except ValueError as err:
            print(f"ОТКАЗ: {err}")
            print("В репозиторий идёт только снимок после sanitize_preview; сырой "
                  "остаётся в каталоге прогона вне репозитория.")
            return EXIT_STAND

    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    missing: list[str] = []
    for name in CLEAN_FILES:
        source = run / name
        if not source.exists():
            missing.append(name)
            continue
        if name in ("plan.json", "report.json"):
            # Переписываем очищенным, а не копируем как есть: файл с таким именем мог
            # прийти откуда угодно.
            raw = json.loads(source.read_text(encoding="utf-8"))
            clean = sanitize_plan(raw) if name == "plan.json" else sanitize_report(raw)
            if clean == raw:
                shutil.copy2(source, target / name)
                note = ""
            else:
                _write_json(target / name, clean)
                note = " (переписан по белому списку)"
            copied.append(name + note)
            continue
        shutil.copy2(source, target / name)
        copied.append(name)
    if args.screens:
        shots = sorted((run / "screens").glob("*.png")) if (run / "screens").is_dir() else []
        for shot in shots:
            (target / "screens").mkdir(exist_ok=True)
            shutil.copy2(shot, target / "screens" / shot.name)
            copied.append(f"screens/{shot.name}")
    if args.logs:
        for name in LOG_FILES:
            source = run / name
            if not source.exists():
                continue
            text = source.read_text(encoding="utf-8", errors="replace")
            cleaned, dropped = clean_log(text)
            if not cleaned.strip():
                print(f"  журнал {name}: после чистки не осталось строк про корзину — "
                      f"в репозиторий не кладу (выброшено {dropped})")
                continue
            (target / name).write_text(cleaned, encoding="utf-8")
            copied.append(f"{name} (выброшено строк: {dropped})")
    for name in copied:
        print(f"  положено: {name}")
    if missing:
        print(f"  нет в прогоне: {', '.join(missing)}")
    if not copied:
        print("НЕЧЕГО КЛАСТЬ: в каталоге прогона нет ни одного доказательства.")
        return EXIT_STAND
    print("Перед коммитом снимки смотрит владелец: шапку с адресом доставки и номером "
          "обрезать, снимок экрана входа в Магнит в репозиторий не класть (раздел 5 плана).")
    return EXIT_DONE


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.emucart",
        description="Живой прогон сборки корзины Магнита на эмуляторе: наряд, отчёт, "
                    "снимок корзины и вердикт.",
        epilog="Коды выхода: 0 — готово, 10 — стенд или канал доказательств не поднялся, "
               "20 — нужен человек, 30 — прогон не закрылся (корзина не наполнилась "
               "либо наряд беднее порога).")
    sub = parser.add_subparsers(dest="command", metavar="команда")

    plan = sub.add_parser("plan", help="наряд Магнита по HTTP (шаг 6)")
    plan.add_argument("--base", default=BASE_DEFAULT,
                      help=f"адрес локального сервера (по умолчанию {BASE_DEFAULT})")
    plan.add_argument("--store", default=STORE, help="сеть наряда (по умолчанию magnit)")
    plan.add_argument("--basket", type=int, default=None,
                      help="номер корзины; без него — самая свежая")
    plan.add_argument("--min-rows", type=int, default=MIN_ROWS,
                      help=f"порог настоящих ссылок (по умолчанию {MIN_ROWS}, поправка 2)")
    plan.add_argument("--timeout", type=float, default=300.0,
                      help="сколько ждать ответ сервера, с")
    plan.add_argument("--out", default=None, help="куда записать plan.json")
    plan.set_defaults(func=cmd_plan)

    wait = sub.add_parser("wait-report", help="ждать новый отчёт приложения (шаг 13)")
    wait.add_argument("--store", default=STORE, help="сеть отчёта (по умолчанию magnit)")
    wait.add_argument("--workplace", default=DEMO_WORKPLACE,
                      help=f"чьё рабочее место читать (по умолчанию {DEMO_WORKPLACE})")
    wait.add_argument("--since", default=None,
                      help="ISO-время начала сборки; без него — время последнего отчёта")
    wait.add_argument("--stamp", default=None,
                      help="отпечаток наряда: чужой отчёт (другая сборка корзины) не берём")
    wait.add_argument("--plan", default=None,
                      help="plan.json: взять отпечаток наряда оттуда")
    wait.add_argument("--background", choices=("no", "yes", "any"), default="no",
                      help="какую сборку ждать: ручную «Собрать» (no, по умолчанию), "
                           "фоновую 1.13 (yes) или любую (any)")
    wait.add_argument("--timeout", type=float, default=WAIT_TIMEOUT,
                      help=f"срок ожидания, с (по умолчанию {WAIT_TIMEOUT:.0f})")
    wait.add_argument("--interval", type=float, default=5.0, help="пауза между опросами, с")
    wait.add_argument("--out", default=None, help="куда записать report.json")
    wait.set_defaults(func=cmd_wait_report)

    check = sub.add_parser("check-login",
                           help="вошёл ли человек — по сессии, а не по имени куки (шаг 11)")
    check.add_argument("--cdp", default=CDP_DEFAULT,
                       help=f"адрес отладки WebView (по умолчанию {CDP_DEFAULT})")
    check.add_argument("--prefer", default="magnit.ru",
                       help="какой сайт предпочесть при выборе страницы (по умолчанию magnit.ru)")
    check.add_argument("--timeout", type=float, default=CDP_TIMEOUT,
                       help=f"сколько ждать ответа окна, с (по умолчанию {CDP_TIMEOUT:.0f})")
    check.add_argument("--require", action="store_true",
                       help="считать неподтверждённый вход поводом позвать человека (код 20); "
                            "по умолчанию код 0 — проверка сделана, решает стенд")
    check.set_defaults(func=cmd_check_login)

    snap = sub.add_parser("snap-preview",
                          help="поймать checkout/preview через CDP (шаги 12, 14)")
    snap.add_argument("--out", required=True,
                      help="куда записать очищенный снимок (preview-*.json)")
    snap.add_argument("--cdp", default=CDP_DEFAULT,
                      help=f"адрес отладки WebView (по умолчанию {CDP_DEFAULT})")
    snap.add_argument("--prefer", default="magnit.ru",
                      help="какой сайт предпочесть при выборе страницы (по умолчанию magnit.ru)")
    snap.add_argument("--page", default=CART_URL,
                      help=f"страница корзины (по умолчанию {CART_URL})")
    snap.add_argument("--wait", type=float, default=25.0, help="сколько ждать ответ сайта, с")
    snap.add_argument("--page-timeout", type=float, default=CDP_TIMEOUT,
                      help=f"сколько ждать ответа окна, с (по умолчанию {CDP_TIMEOUT:.0f})")
    snap.add_argument("--raw-out", default=None,
                      help="куда записать СЫРОЙ ответ — только вне репозитория")
    snap.add_argument("--shot", default=None, help="файл снимка экрана страницы (PNG)")
    snap.set_defaults(func=cmd_snap_preview)

    verdict_cmd = sub.add_parser("verdict", help="вердикт по снимкам «до»/«после» и отчёту")
    verdict_cmd.add_argument("--before", default=None, help="preview-before.json")
    verdict_cmd.add_argument("--after", default=None, help="preview-after.json")
    verdict_cmd.add_argument("--report", required=True, help="report.json (phonecart.last.magnit)")
    verdict_cmd.add_argument("--plan", default=None,
                             help="plan.json: сверить отпечаток наряда с отчётом")
    verdict_cmd.add_argument("--background", action="store_true",
                             help="сверяется фоновая сборка 1.13, а не ручная «Собрать»")
    verdict_cmd.add_argument("--allow-unbound", action="store_true",
                             help="считать вердикт по снимкам без привязки (старый формат): "
                                  "в выводе и JSON будет сказано, что доказательство слабее")
    verdict_cmd.add_argument("--out", default=None, help="куда записать verdict.json")
    verdict_cmd.set_defaults(func=cmd_verdict)

    export = sub.add_parser("export", help="положить доказательства в репозиторий (шаг 15)")
    export.add_argument("--run", required=True, help="каталог прогона (вне репозитория)")
    export.add_argument("--to", required=True, help="каталог доказательств в репозитории")
    export.add_argument("--screens", action="store_true", help="взять и снимки экрана")
    export.add_argument("--logs", action="store_true", help="взять logcat и журнал сервера")
    export.set_defaults(func=cmd_export)
    return parser


def main(argv: list[str]) -> int:
    _console()
    parser = _parser()
    args = parser.parse_args(argv)
    # Разбор команды держим в `func`, а не в `run`: у команды export есть свой ключ
    # --run (каталог прогона), и он затирал бы имя обработчика — так это и вылезло.
    if getattr(args, "func", None) is None:
        parser.print_help()
        return EXIT_DONE
    try:
        return int(args.func(args))
    except EmucartError as err:
        # Своя ошибка инструмента — это «стенд или канал не поднялся»: прогон стоит,
        # причина названа, и стек читать не нужно.
        print(f"НЕ ВЫШЛО: {err}")
        return EXIT_STAND


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
