"""Приёмная дверь: единственный HTTP-вход приложения, и он сразу под замком.

ЧИТАЯ ЭТОТ ФАЙЛ, ЗНАЙТЕ ГЛАВНОЕ: СВОЕГО КЛИЕНТА У ЭТОЙ ДВЕРИ БОЛЬШЕ НЕТ. Она
писалась для расширения-сборщика, жившего в браузере человека, и всё ниже — про
него: и разрешение на chrome-extension://, и поля saved/skipped, и «наряд для
расширения». 17.09.2026 владелец снял ту дорогу целиком («только работа в нашем
приложении»), вход в сети и наполнение корзины переехали в app/shopbrowser —
браузер на самом сервере. Дверь оставлена: она под замком, обложена тестами и
никому не мешает. Но живой дорогой её считать нельзя, и дописывать в неё новое,
не спросив владельца, не стоит. Разбор решения — docs/design/extension.md.

ЗАЧЕМ ОНА БЫЛА. Цены Пятёрочки и Самоката приложение видит только из браузера
человека — почему именно так, разобрано в app/pricebundle.py. Расширение их
собирало, серверная сторона принимала (app/collector.py), а дороги между ними не
было: у прежнего интерфейса своего маршрута не нашлось, и расширению некуда было
постучаться.

ТРИ МАРШРУТА, И БОЛЬШЕ НИ ОДНОГО

    POST /api/prices     отчёт сборщика → app.collector.accept, в ответ сводка;
    POST /api/cartplan   {store, корзина или позиции} → наряд app.cartplan.build;
    GET  /api/health     жива ли дверь.

Четвёртого маршрута нет намеренно. Каждый новый адрес, смотрящий в интернет, — это
ещё одна поверхность, которую придётся защищать вечно; всё, что можно оставить
внутри приложения, остаётся внутри приложения.

ЗАМОК — ГЛАВНАЯ ЧАСТЬ ЭТОГО ФАЙЛА, А НЕ ДОБАВКА К НЕЙ

Дверь смотрит в интернет, а за ней — база конкретного человека: его покупки, его
корзины, его подключения магазинов. Дверь без замка здесь хуже, чем отсутствие
двери: без неё человек теряет десять минут, с ней — любой, кто знает (или
подберёт) номер телефона, пишет чужие цены в чужую базу, и заметить это
невозможно, потому что выглядит запись совершенно обычно.

Замок устроен так.

СЕКРЕТ РАБОЧЕГО МЕСТА. Случайная строка на 192 бита (secrets.token_urlsafe),
которая живёт в настройках самого рабочего места (ключ SECRET_KEY, та же таблица
settings, где лежат ключи кабинета ФНС и подключения магазинов). Она создаётся
при первом показе человеку и хранится в расширении рядом с адресом приложения.
Подобрать её нельзя: миллион попыток в секунду перебирали бы её дольше, чем
существует Вселенная, — поэтому счётчика неудачных попыток здесь нет, он защищал
бы от того, чего не бывает, ценой ещё одного состояния, которое может сломаться.

СЕКРЕТ ЕДЕТ ТЕЛОМ. Как и номер рабочего места — по той же причине, что описана в
docs/design/extension.md: в адресной строке он осел бы в истории браузера и в
журнале сервера навсегда. Дверь этого не просто ждёт, а следит: секрет или номер,
замеченные в параметрах адреса, — это отказ со словами «смените секрет», потому
что к моменту проверки он УЖЕ записан в чей-нибудь журнал.

ОДИН ОТВЕТ НА ВСЕ ОТКАЗЫ. Неизвестное рабочее место, рабочее место без секрета,
чужой секрет, пустой секрет — всё это 401 с одним и тем же текстом. Разные ответы
превратили бы дверь в справочник: постучавшись с чужим номером, можно было бы
узнать, заведено ли рабочее место, то есть пользуется ли этот человек
приложением. Настоящая причина уходит в журнал сервера, а не в ответ.

НИ ОДНОЙ ЗАПИСИ ДО ОТКРЫТИЯ ЗАМКА. Проверка идёт раньше разбора тела и раньше
любого обращения к базе. Тут есть тихая ловушка: sqlite3.connect создаёт файл
базы сам, поэтому «направить репозиторий в базу и посмотреть, что там» для
несуществующего рабочего места означало бы ЗАВЕСТИ его. Дверь, у которой чужой
стук создаёт рабочие места, наполняет диск чужими папками — и делает это молча.
Поэтому существование проверяется файлом (users.exists) до переключения базы.

СРАВНЕНИЕ ПОСТОЯННОГО ВРЕМЕНИ. hmac.compare_digest, а не ==. Обычное сравнение
строк выходит на первом несовпавшем символе, и разница во времени ответа выдаёт
длину верного начала — по сети это измеримо.

ПОЧЕМУ ДВЕРЬ ТРЕБУЕТ application/json. Не из педантизма. Запрос с
Content-Type: text/plain браузер считает «простым» и отправляет БЕЗ
предварительного спроса — значит любая открытая у человека страница могла бы
вслепую постучаться в дверь на его же localhost. Ответ она не прочитает, но
запись бы произошла. Требование JSON заставляет браузер сначала спросить
разрешения, а разрешение дверь даёт только расширению. Это второй замок, и
работает он ровно там, где первый бесполезен: секрет-то у расширения есть, и
страница, сумевшая его выпросить, прошла бы первый замок.

CORS РОВНО ПОД РАСШИРЕНИЕ. Заголовки выдаются только источникам вида
chrome-extension://…, разрешён один заголовок (Content-Type) и два метода. Ни
звёздочки, ни Access-Control-Allow-Credentials: звёздочка разрешила бы любой
странице в интернете читать ответы двери, а Allow-Credentials означал бы, что к
запросу прикладываются куки, — то есть ту самую попутную власть, которой здесь
нет и не должно быть. Замок у двери один: секрет в теле.

ГДЕ ОНА ЖИВЁТ. Внутри службы korzina-jobs (app/catalog/worker.py): отдельный
контейнер из того же образа, с тем же томом баз, работающий постоянно. Слушатель
занимает свой поток и ночному обходу каталога не мешает: они не делят ни базу, ни
очередь. С 17.09.2026 те же три маршрута отвечают и на порту экрана
(app/web/api.py) — этот порт остался для случая, когда экрана нет вовсе.
"""
from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app import cartplan, collector, config, repo, users
from app.models import BasketLine

log = logging.getLogger(__name__)

# Ключ секрета в настройках рабочего места. Имя с точкой — как у ключей кабинета
# ФНС (app/fns/sync.py): видно, что настройка служебная, а не выбранная человеком.
SECRET_KEY = "api.secret"
SECRET_BYTES = 24                      # 192 бита случайности, 32 символа на экране

DEFAULT_PORT = 8765
DEFAULT_HOST = "0.0.0.0"               # в контейнере иначе не достучаться снаружи

# Пакет цен Пятёрочки — это десятки тысяч позиций, и пятимегабайтное тело здесь
# норма, а не нападение (app/pricebundle.MAX_ITEMS — 50 000). Потолок нужен, чтобы
# одно тело не съело память службы целиком: читать «сколько пришлют» — верный
# способ уронить и обход каталога заодно.
MAX_BODY = 16 * 1024 * 1024
MAX_PLAN_LINES = 2000                  # в корзине человека столько не бывает

# Сколько отвергнутого тела дверь вежливо дочитывает и выбрасывает, см. Door._spill.
SPILL_LIMIT = 1024 * 1024

HEALTH = "/api/health"
PRICES = "/api/prices"
CARTPLAN = "/api/cartplan"

# Поля замка. Они вынимаются из тела до того, как оно уйдёт в app.collector: там
# «secret» честно опознаётся как секрет, выбрасывается и записывается в журнал
# предупреждением — и в журнале осталось бы пугало вместо события.
AUTH_FIELDS = ("workplace", "secret")

# Один ответ на все отказы замка: подробности — в журнал, человеку — что делать.
DENIED = ("Рабочее место или секрет не подошли. Секрет рабочего места лежит в его "
          "настройках (ключ api.secret); своего клиента у этой двери сейчас нет — "
          "см. докстринг файла.")

# Источник расширения: chrome-extension://<32 буквы>. Проверяется целиком, а не
# вхождением подстроки: «origin содержит chrome-extension://» пропустило бы
# https://зло.example/?x=chrome-extension://…, а заодно любой символ из заголовка
# уехал бы в наш же ответ — то есть чужой заголовок писал бы наши.
_EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[a-z0-9._-]{1,64}$")

_JSON_TYPES = ("application/json", "text/json")


class Refused(Exception):
    """Отказ с кодом ответа: что сказать человеку и что записать в журнал.

    Два текста, а не один, ровно из-за замка: человеку уходит общее «не подошли»,
    а в журнал — настоящая причина. Совмещать их нельзя ни в ту, ни в другую
    сторону: общий текст в журнале не даст разобраться в неудачной выкладке, а
    настоящая причина в ответе превратит дверь в справочник рабочих мест.
    """

    def __init__(self, status: int, message: str, reason: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.reason = reason or message


# ---------- секрет рабочего места ----------
def _new_secret() -> str:
    return secrets.token_urlsafe(SECRET_BYTES)


def workplace_secret(*, create: bool = True) -> str | None:
    """Секрет ТЕКУЩЕГО рабочего места — для показа человеку.

    Текущего, а не названного номером: к моменту вызова база уже переключена на
    того, чей идёт прогон (app.users.activate вызывается на каждом прогоне
    интерфейса). Отдельный параметр «чей секрет» здесь был бы приглашением
    показать чужой.

    Создаётся при первом обращении — то есть тогда, когда человек впервые открыл
    экран и посмотрел на него. Раньше создавать незачем: секрет, которого никто не
    видел, ничего не отпирает, а строка в базе уже есть.
    """
    stored = repo.get_setting(SECRET_KEY)
    if stored:
        return stored
    if not create:
        return None
    fresh = _new_secret()
    repo.set_setting(SECRET_KEY, fresh)
    log.info("рабочему месту выдан секрет приёмной двери")
    return fresh


def renew_workplace_secret() -> str:
    """Выдать новый секрет взамен прежнего: старый перестаёт отпирать немедленно.

    Нужно ровно тогда, когда секрет мог утечь — например, уехал в адресной строке
    и осел в чужом журнале. Смена — единственное действие, которое в этом случае
    что-то меняет, поэтому она должна быть в одно нажатие, а не в переписку.
    """
    fresh = _new_secret()
    repo.set_setting(SECRET_KEY, fresh)
    log.info("секрет приёмной двери заменён")
    return fresh


def _unlock(body: dict) -> str:
    """Проверить замок и направить репозиторий в базу этого человека.

    Порядок шагов выбран так, чтобы ни один отказ не оставил следа: номер
    разбирается, существование проверяется файлом, и только потом база
    переключается. Переключить раньше — значит завести рабочее место чужим стуком
    (sqlite3.connect создаёт файл сам).
    """
    phone = users.normalize_phone(body.get("workplace") if isinstance(body, dict) else None)
    given = body.get("secret") if isinstance(body, dict) else None
    given = given if isinstance(given, str) else ""

    if not phone:
        raise Refused(401, DENIED, "номер рабочего места не разобрался")
    if not given.strip():
        raise Refused(401, DENIED, f"{phone}: секрет не прислан")
    if not users.exists(phone):
        raise Refused(401, DENIED, f"{phone}: такого рабочего места нет")

    users.activate(phone)
    stored = repo.get_setting(SECRET_KEY)
    if not stored:
        users.deactivate()
        raise Refused(401, DENIED, f"{phone}: секрет ещё не создан — человек не открывал экран")
    # Сравниваем БАЙТАМИ, а не строками. compare_digest на строках требует, чтобы
    # обе состояли из ASCII, и на кириллице бросает TypeError — то есть подстановка
    # «секрета» из русских букв кончалась бы не отказом, а пятисотым ответом. Это и
    # падение на ровном месте, и разница в ответах, по которой дверь снова
    # становится справочником рабочих мест.
    if not hmac.compare_digest(str(stored).encode("utf-8"), given.encode("utf-8")):
        users.deactivate()
        raise Refused(401, DENIED, f"{phone}: секрет не совпал")
    return phone


# ---------- работы за дверью ----------
def take_prices(body: dict) -> dict:
    """Отчёт сборщика — в базу человека, в ответ сводка для окна расширения.

    Разбора здесь нет ни на строку: он весь в app/collector.py, вместе с
    выбрасыванием полей, похожих на секреты, и правилом «проверить целиком до
    первой записи». Дверь только снимает с тела поля замка и переводит отказ
    приёмника в код ответа.

    Поля «saved» и «skipped» в ответе — зеркало того, что расширение пишет в свой
    журнал (background.js, deliver). Сводка приёмника называет их подробнее
    (prices_saved, prices_skipped), и оба имени едут вместе: расхождение здесь
    стоило бы человеку строки «пакет принят» без единой цифры.
    """
    report = {key: value for key, value in body.items() if key not in AUTH_FIELDS}
    try:
        summary = collector.accept(report)
    except ValueError as err:
        # Приёмник объясняется по-русски и подробно — это и есть лучший ответ
        # расширению: там текст попадёт прямо в окно «Что происходило».
        raise Refused(400, str(err), f"отчёт не принят: {err}") from None
    return {"ok": True, "saved": summary["prices_saved"],
            "skipped": summary["prices_skipped"], **summary}


def _line_of(raw: Any) -> BasketLine | None:
    """Позиция из тела запроса. Без названия позиции нет — её не показать."""
    if not isinstance(raw, dict):
        return None
    name = " ".join(str(raw.get("name") or raw.get("product_name") or "").split())[:300]
    if not name:
        return None
    try:
        product_id = int(raw.get("product_id") or 0)
    except (TypeError, ValueError):
        product_id = 0
    try:
        qty = float(raw.get("qty") or 1)
    except (TypeError, ValueError):
        qty = 1.0
    return BasketLine(product_id=product_id, name=name,
                      unit=str(raw.get("unit") or "pcs"), qty=qty)


def _priced(lines: list[BasketLine], store_code: str) -> list[BasketLine]:
    """Досыпать в позиции последнюю известную цену этой сети.

    Сумма наряда — то, с чем человек сверит наполнившуюся корзину глазами. Без
    цен она равна нулю, а ноль в этом месте не «неизвестно», а «бесплатно»: он
    выглядит как ответ, и сверка по нему пройдёт успешно при любой ошибке.
    """
    store = repo.get_store(store_code)
    if not store:
        return lines
    for line in lines:
        if not line.product_id:
            continue
        price = repo.latest_price_for(line.product_id, store.id)
        if price and price.get("price") is not None:
            line.prices[store_code] = round(float(price["price"]) * line.qty, 2)
    return lines


def _plan_lines(body: dict, store_code: str) -> tuple[list[BasketLine], str]:
    """Что класть в корзину: присланные позиции, названная корзина или последняя.

    Три источника, и порядок между ними не произволен. Присланные позиции —
    самый точный ответ на «положи вот это», он не зависит от того, что человек
    успел поменять в приложении. Номер корзины — обычный путь расширения.
    Последняя корзина — умолчание, и оно совпадает с тем, что человек видит на
    экране «Корзина»: список отсортирован по дате создания, и выбранной по
    умолчанию оказывается та же самая, первая.

    Второе значение — объяснение для пустого наряда. Пустой наряд без слов
    выглядит поломкой приложения, хотя означать может «корзин ещё нет».
    """
    raw_lines = body.get("lines")
    if raw_lines is not None:
        if not isinstance(raw_lines, list):
            raise Refused(400, "«lines» в запросе должен быть списком позиций")
        if len(raw_lines) > MAX_PLAN_LINES:
            raise Refused(400, f"в запросе {len(raw_lines)} позиций — в корзине столько не бывает")
        lines = [line for line in (_line_of(item) for item in raw_lines) if line]
        return _priced(lines, store_code), "В присланном списке нет ни одной позиции с названием."

    baskets = repo.list_baskets()
    raw_id = body.get("basket")
    if raw_id is not None:
        try:
            basket_id = int(raw_id)
        except (TypeError, ValueError):
            raise Refused(400, "«basket» в запросе — это номер корзины") from None
        if not any(int(b["id"]) == basket_id for b in baskets):
            # Отвечаем честно: замок уже открыт, и это СВОЯ корзина человека —
            # скрывать её отсутствие не от кого, а молчаливый пустой наряд
            # заставил бы искать поломку в расширении.
            raise Refused(400, f"Корзины №{basket_id} в этом рабочем месте нет.")
    elif baskets:
        basket_id = int(baskets[0]["id"])
    else:
        return [], "Корзин в рабочем месте пока нет — соберите корзину в приложении."

    items = repo.basket_items(basket_id)
    lines = [BasketLine(product_id=int(item["product_id"]), name=str(item["name"]),
                        unit=str(item.get("unit") or "pcs"), qty=float(item.get("qty") or 1))
             for item in items]
    return _priced(lines, store_code), f"Корзина №{basket_id} пуста."


def take_cartplan(body: dict) -> dict:
    """Наряд на корзину для этой сети — в том же виде, в каком его строит расчёт.

    Сам наряд собирает app/cartplan.py, и все решения про артикулы, неизвестные
    позиции и требование входа остаются там. Здесь — только выбор позиций и
    объяснение пустоты: у cartplan своих объяснений три, и они важнее нашего
    («войдите в магазин» полезнее, чем «корзина пуста»), поэтому своё мы
    дописываем, лишь когда сказать больше нечего.
    """
    store_code = str(body.get("store") or "").strip().lower()
    if not store_code:
        raise Refused(400, "не указана сеть: «store» в запросе обязателен")

    if not body.get("lines") and not body.get("basket"):
        pending = cartplan.get_pending(store_code)
        if pending is not None:
            cartplan.clear_pending(store_code)
            answer = pending.as_dict()
            answer["ok"] = True
            return answer

    # «waiting» — просьба отдать ТОЛЬКО отложенный наряд и ничего не придумывать.
    #
    # Так спрашивает расширение, и спрашивает само: при каждом открытии сайта сети
    # и раз в минуту по будильнику. Ответь мы ему корзиной, собранной из последней
    # покупки, — вход на 5ka.ru молча наполнял бы человеку корзину списком, о
    # котором он сегодня не просил, а отменять это пришлось бы руками в магазине.
    # Наряд появляется здесь только тогда, когда человек сам нажал «передать
    # корзину» (app/cartplan.save_pending), и выдаётся ровно один раз.
    if body.get("waiting"):
        return {"ok": True, "store": store_code, "lines": [], "unknown": [], "total": 0.0,
                "note": "Отложенного наряда нет: нажмите «Передать корзину» в приложении."}

    lines, empty_note = _plan_lines(body, store_code)
    plan = cartplan.build(store_code, lines)
    if not plan.lines and not plan.note:
        plan.note = empty_note
    answer = plan.as_dict()
    answer["ok"] = True
    return answer


# ---------- сама дверь ----------
class Door(BaseHTTPRequestHandler):
    """Обработчик запроса. Всё, что не маршрут и не замок, здесь заканчивается."""

    # Версию Python в заголовке Server знать снаружи незачем: это подсказка тому,
    # кто ищет незакрытую дыру, и никакой пользы тому, кто стучится по делу.
    server_version = "korzina"
    sys_version = ""
    protocol_version = "HTTP/1.1"      # ответы всегда с Content-Length, см. _answer
    _unread = False                    # лежит ли в трубе непрочитанное тело, см. _spill

    # ----- мелочи -----
    def log_message(self, fmt: str, *args) -> None:      # noqa: A003
        log.debug(fmt, *args)

    def log_request(self, code: object = "-", size: object = "-") -> None:
        # Адрес пишем очищенным от параметров: если в них случайно уехал секрет,
        # журнал сервера — последнее место, где он должен осесть.
        cmd = getattr(self, "command", None) or "-"
        log.info("%s %s → %s", cmd, self._path(), code)

    def log_error(self, fmt: str, *args) -> None:
        log.warning(fmt, *args)

    def _path(self) -> str:
        raw = getattr(self, "path", None) or ""
        path = urlsplit(raw).path
        return path[:-1] if len(path) > 1 and path.endswith("/") else path

    def _origin(self) -> str | None:
        origin = (self.headers.get("Origin") or "").strip()
        return origin if _EXTENSION_ORIGIN.match(origin) else None

    def _cors(self) -> None:
        # Vary: Origin — всегда. Без него посредник запомнит ответ, выданный
        # расширению, и отдаст его же обычной странице вместе с разрешением.
        self.send_header("Vary", "Origin")
        origin = self._origin()
        if not origin:
            return
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods",
                         "GET, OPTIONS" if self._path() == HEALTH else "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")

    def _answer(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _spill(self) -> None:
        """Дочитать и выбросить тело, которое разбирать мы не станем.

        Выглядит лишней вежливостью, а на деле от этого зависит, доедет ли отказ.
        Закрытое соединение с непрочитанным телом обрывается жёстко (RST), и
        отправленный ответ пропадает по дороге: расширение увидит не «тело
        слишком большое», а «до приложения не достучаться» — и человек пойдёт
        чинить сеть вместо того, чтобы прочитать объяснение.

        Выбрасываем не всё подряд, а до потолка: тело на гигабайт — это уже не
        ошибка, и тратить на его вычитывание память и время незачем. Такое
        соединение оборвётся, и это единственный правильный исход.
        """
        if not self._unread:
            return
        self._unread = False
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        left = min(max(length, 0), SPILL_LIMIT)
        while left > 0:
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                return
            left -= len(chunk)

    def _refuse(self, err: Refused) -> None:
        self._spill()
        if err.status in (401, 403):
            log.warning("дверь не открыта: %s (%s)", err.reason, self.client_address[0])
        else:
            log.info("отказ %d: %s", err.status, err.reason)
        self._answer(err.status, {"ok": False, "error": err.message})

    # ----- разбор запроса -----
    def _check_query(self) -> None:
        """Секрет и номер рабочего места в адресе — это уже утечка, а не ошибка."""
        query = parse_qs(urlsplit(self.path).query)
        leaked = [name for name in AUTH_FIELDS if name in query]
        if leaked:
            raise Refused(400,
                          "Секрет и номер рабочего места отправляются телом запроса, "
                          "а не адресом: в адресе они попадают в историю браузера и "
                          "в журнал сервера. Этот секрет уже там — смените его в "
                          "приложении.",
                          f"в адресе приехали поля замка: {', '.join(leaked)}")

    def _check_type(self) -> None:
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if kind not in _JSON_TYPES:
            raise Refused(415, "Тело запроса должно быть JSON: Content-Type: application/json.",
                          f"чужой Content-Type: «{kind or '—'}»")

    def _read_body(self) -> dict:
        """Тело запроса как объект JSON — с потолком по длине и без ожидания в пустоту.

        Про Content-Length здесь всерьёз. Без него rfile.read() ждёт, пока
        соединение закроется, — то есть один запрос без длины занимает поток
        двери навсегда. Тело по частям (chunked) http.server не собирает сам,
        поэтому такой запрос — тоже отказ, а не попытка разобрать обрывок.
        """
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            self.close_connection = True
            raise Refused(411, "Тело запроса нужно присылать целиком, с Content-Length.",
                          "тело пришло по частям (chunked)")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self.close_connection = True
            raise Refused(411, "В запросе нет Content-Length — непонятно, сколько читать.",
                          "запрос без Content-Length")
        try:
            length = int(raw_length)
        except ValueError:
            self.close_connection = True
            raise Refused(400, "Content-Length должен быть числом.",
                          "нечисловой Content-Length") from None
        if length < 0:
            self.close_connection = True
            raise Refused(400, "Content-Length отрицательный.", "отрицательный Content-Length")
        if length > MAX_BODY:
            # Соединение закрываем: остаток тела иначе прочитался бы как следующий
            # запрос в том же соединении. Перед закрытием отвергнутое тело
            # дочитывается до потолка (_spill) — иначе отказ не доедет.
            self.close_connection = True
            limit = (f"{MAX_BODY // (1024 * 1024)} МБ" if MAX_BODY >= 1024 * 1024
                     else f"{MAX_BODY} байт")
            raise Refused(413, f"Тело запроса больше {limit} — столько дверь не принимает.",
                          f"тело на {length} байт")
        self._unread = False
        data = self.rfile.read(length) if length else b""
        if len(data) != length:
            self.close_connection = True
            raise Refused(400, "Тело запроса оборвалось на полпути.",
                          f"пришло {len(data)} байт из {length}")
        try:
            body = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as err:
            raise Refused(400, f"Тело запроса не разобралось как JSON: {err}",
                          f"кривой JSON: {err}") from None
        if not isinstance(body, dict):
            raise Refused(400, "Тело запроса должно быть объектом JSON.", "тело не объект")
        return body

    # ----- методы -----
    def do_OPTIONS(self) -> None:      # noqa: N802 — имя задано http.server
        """Предварительный спрос браузера. Разрешение получает только расширение."""
        if self._path() not in (PRICES, CARTPLAN, HEALTH):
            self._answer(404, {"ok": False, "error": "Нет такого адреса."})
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:          # noqa: N802
        path = self._path()
        if path == HEALTH:
            # Жива — и ничего больше. Ни версии, ни числа рабочих мест, ни имени
            # хоста: проверка живости не должна быть источником сведений о том,
            # что за ней стоит. Замка тут нет намеренно — иначе выкладка не смогла
            # бы дождаться двери, не зная ничьих секретов (tools/server-deploy.sh).
            self._answer(200, {"ok": True, "service": "korzina-api"})
            return
        if path in (PRICES, CARTPLAN):
            self._answer(405, {"ok": False, "error": "Этот адрес принимает только POST."})
            return
        self._answer(404, {"ok": False, "error": "Нет такого адреса."})

    def do_POST(self) -> None:         # noqa: N802
        # «Тело ещё лежит в трубе»: пока это так, любой отказ обязан его дочитать
        # (_spill), иначе ответ не доедет до расширения.
        self._unread = True
        path = self._path()
        try:
            try:
                if path == HEALTH:
                    raise Refused(405, "Этот адрес отвечает только на GET.",
                                  "POST на проверку живости")
                if path not in (PRICES, CARTPLAN):
                    raise Refused(404, "Нет такого адреса.", f"неизвестный адрес {path}")
                self._check_query()
                self._check_type()
                body = self._read_body()
                _unlock(body)
                answer = take_prices(body) if path == PRICES else take_cartplan(body)
            finally:
                # База возвращается общей в любом случае. Соединение живёт дольше
                # одного запроса, и следующий по нему запрос с чужим номером писал
                # бы в базу предыдущего, если бы мы этого не сделали.
                users.deactivate()
        except Refused as err:
            self._refuse(err)
            return
        except Exception:                          # noqa: BLE001 — дверь не падает молча
            self._spill()
            log.exception("%s: внутренняя ошибка двери", path)
            self._answer(500, {"ok": False,
                               "error": "Приложение не справилось с запросом — "
                                        "подробности в журнале сервера."})
            return
        self._answer(200, answer)


# ---------- служба ----------
def enabled() -> bool:
    value = config.get("api.enabled", True)
    return value is not False


def port() -> int:
    try:
        return int(config.get("api.port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        log.warning("api.port в config.yaml не число — беру %d", DEFAULT_PORT)
        return DEFAULT_PORT


def host() -> str:
    return str(config.get("api.host") or DEFAULT_HOST)


def make_server(bind: str | None = None, at: int | None = None) -> ThreadingHTTPServer:
    """Слушатель на своём потоке на каждое соединение.

    Поток на соединение, а не один на всех, по одной причине: пакет цен приходит
    мегабайтами и разбирается секундами, и всё это время одиночный слушатель не
    отвечал бы даже на проверку живости — выкладка сочла бы дверь мёртвой.
    Потоки служебные (daemon), поэтому остановка службы их не ждёт.
    """
    server = ThreadingHTTPServer((bind or host(), at if at is not None else port()), Door)
    server.daemon_threads = True
    return server


def serve_in_background() -> ThreadingHTTPServer | None:
    """Открыть дверь рядом с другой работой. Никогда не бросает исключение.

    Возвращает None, если дверь выключена настройкой или порт занят. Именно
    None, а не ошибка: слушателя цепляют к службе обхода каталога, и запрет ей
    родиться из-за занятого порта означал бы, что чужой процесс на 8765 отменяет
    ночной обход сетей. Дверь важна, но не важнее того, к чему её прицепили.
    """
    if not enabled():
        log.info("приёмная дверь выключена настройкой api.enabled")
        return None
    try:
        server = make_server()
    except OSError as err:
        log.error("приёмная дверь не открылась на %s:%d (%s) — расширению некуда "
                  "стучаться, остальная работа службы идёт как обычно", host(), port(), err)
        return None
    threading.Thread(target=server.serve_forever, name="korzina-api", daemon=True).start()
    log.info("приёмная дверь слушает %s:%d — маршруты %s, %s, %s",
             host(), port(), PRICES, CARTPLAN, HEALTH)
    return server


def serve(bind: str | None = None, at: int | None = None) -> None:
    """Дверь как единственная работа процесса: для отладки и запуска вручную."""
    server = make_server(bind, at)
    log.info("приёмная дверь слушает %s:%s — Ctrl+C останавливает",
             server.server_address[0], server.server_address[1])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


__all__ = ["Door", "Refused", "SECRET_KEY", "MAX_BODY", "PRICES", "CARTPLAN", "HEALTH",
           "workplace_secret", "renew_workplace_secret", "take_prices", "take_cartplan",
           "make_server", "serve", "serve_in_background", "enabled", "port", "host"]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    serve()
