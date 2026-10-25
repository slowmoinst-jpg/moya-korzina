"""Сборка корзин: одна кнопка — корзина в каждой подключённой сети, по потоку на сеть.

ЗАЧЕМ. Владелец 15.10.2026 поручил класть корзину во все кабинеты, где он вошёл, а
17.10.2026 спросил, можно ли повторять её во всех подключённых сетях параллельно и в
фоне, не уводя человека из приложения, и видеть итог каждой сети. На «Результате» у
каждой сети своя кнопка, и пройти их все — это минуты нажатий и ожидания. Здесь одно
нажатие: каждая сеть идёт своим потоком, а экран «Сборка корзин»
(app/web/screens/carts.py) показывает по строке на сеть: ход, что легло, чего нет в
наличии и итог.

КОРЗИНА ЦЕЛИКОМ, А НЕ ДОЛЯ ВАРИАНТА. Повторить корзину в каждой сети значит положить
в каждую всё, что этой сети знакомо: иначе итоги сетей не сравнить между собой. Какой
сети какую долю брать, решает «Результат»; здесь собираются корзины, по которым это
решение можно проверить итогом самой сети.

ДОРОГИ — ТЕ, ЧТО УЖЕ РАБОТАЮТ. Своих правил здесь нет. Будь они, кнопка «Передать»
на «Результате» и эта сборка разошлись бы при первой же правке одной из них.

    link     Лента и ВкусВилл — ссылка-корзина от самой сети (app/handover.for_store).
             Корзина появится у того, кто откроет ссылку под своим входом; до тех пор
             итог — наш расчёт, и так и подписан.
    browser  Магнит — браузер сервера под сохранённым входом: наряд cartplan.build с
             проверкой цен, пускатель shopbrowser/cart.start, итог со страницы
             корзины (app/carttotal.py).
    api      METRO — собственный метод сети (connectors/metro_cart через cart.start),
             итог из ответа корзины. Пока нет именного токена, сеть не принимает
             запись, и cartplan.why_not её не пускает: строка говорит это словами.
    phone    Пятёрочка, Самокат, Дикси, Перекрёсток — их витрины нашему серверу
             закрыты (store_accounts.ABILITIES). Сервер туда корзину не кладёт, кладёт
             приложение на телефоне (app/phonecart.py): ход оно сообщает (progress),
             итог — отчётом (accept), в ту же форму строки.

СБОРКА С ТЕЛЕФОНА В ФОНЕ (приложение 1.13). Пока человек смотрит корзину на телефоне,
приложение само собирает её в каждой сети, где он вошёл, — скрытым окном на сеть, сети
параллельно. Ход приходит сюда (progress) и держит строку «идёт» по пульсу: телефон
шлёт его на каждой позиции, а замолчал дольше PHONE_STALE — сборка оборвалась. Пока
идёт сборка телефона, сервер в эту сеть не кладёт (start видит живую строку), и
наоборот: телефону, пришедшему к сети, куда кладёт сервер, отвечают отказом.

СТРОКА СЕТИ — ОДНА ФОРМА ДЛЯ ЛЮБОГО ИСПОЛНИТЕЛЯ (Row ниже). Что легло, чего нет в
наличии, что сети незнакомо, итог и кто собирал: сервер или телефон. Отчёт исполнителя
не знает, что его ждёт экран, а экран не знает, кто исполнял: так строка Пятёрочки
примет итог от телефона без единой правки экрана.

ИТОГ — ЧЕЙ ОН, ТАК И ПОДПИСАН (carttotal.Total.source). «Посчитала сеть» — только то,
что сеть написала сама: total_cost METRO, «Итого» на странице корзины Магнита. Не
прочиталось — строка так и говорит, и рядом стоит наш расчёт по легшим позициям,
подписанный нашим. Ничего не досчитываем за сеть.

ВТОРАЯ ПЕРЕДАЧА В СЕТЬ НЕ ПУСКАЕТСЯ. Сеть кладёт поверх лежащего, и повтор удвоил бы
корзину. Решает это пускатель (cart.start, cart.already_sent), здесь лишь не начинаем
сеть, где сборка уже идёт, и не просим у Ленты новую ссылку на ту же корзину. Ещё раз
целиком кладётся только явным «ещё раз» (again), когда человек очистил корзину.

ТЕМП. Сборка добавляет ровно один поток на сеть, и темп внутри сети держит её дорога:
пауза между карточками не чаще connectors.rate_limit_rps (cart._step_pause), одна
ссылка, один запрос записи METRO. Отказ сети — CrawlBlocked и строка «сеть отказала»;
для сетей домашнего выхода отказ ставит паузу на весь адрес владельца
(homeexit.note_refusal), и до её конца сборка туда не идёт.

ЧЕГО ЗДЕСЬ НЕТ. Оформления и оплаты: сборка кончается наполненной корзиной. Уборки
корзин в сетях, которые человек не выбрал, и исполнителя на телефоне — их нет и в коде.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime

from app.carttotal import NETWORK, OURS, Total

log = logging.getLogger(__name__)

KEY = "cartrun.rows"

# Дороги.
LINK, BROWSER, API, PHONE = "link", "browser", "api", "phone"

# Состояния строки.
IDLE = "idle"          # в эту сеть ещё не собирали
READY = "ready"        # можно собирать: так выглядит строка до нажатия
GOING = "going"        # собирается сейчас
DONE = "done"          # собрано: что-то легло или сеть выдала ссылку
FAILED = "failed"      # не легло ничего, и причина названа
REFUSED = "refused"    # сеть отказала (CrawlBlocked)
LOGIN = "login"        # нужен вход: «Подключить» или «Войдите заново»
SENT = "sent"          # корзина сюда уже уехала — второй раз целиком не кладём
CANT = "cant"          # сегодня приложение в эту сеть не кладёт, причина названа
EMPTY = "empty"        # класть нечего: позиции корзины сети незнакомы

FINAL = (DONE, FAILED, REFUSED, LOGIN, SENT, CANT, EMPTY)

# Кто собирал.
SERVER = "server"
DEVICE = "phone"

# Коды остановки передачи (cart._done), которые значат отказ сети: она не пустила
# сервер, встретила проверкой или ответила 401/403/429.
REFUSALS = ("forbidden", "blocked", "check", "refused")
# Коды, при которых передача кончилась до окна магазина: закрывать там нечего.
NO_WINDOW = ("empty", "nologin", "point")

# Причина у позиции, которая значит «нет в наличии», а не «не легло по другой
# причине». Слова — наши же: их пишут cart._put_one, cart._metro и cartplan.verify.
GONE_WHY = re.compile(r"нет в наличии|товара нет|нет в выбранном торговом центре", re.I)

# Причина у позиции, которая означает «выберите магазин/адрес» (Магнит).
STORE_WHY = re.compile(
    r"выберите магазин|выбери магазин|выберите адрес|выбери адрес"
    r"|укажите адрес|выбрать магазин|выбрать адрес|не выбран магазин"
    r"|магазин не выбран|адрес не выбран",
    re.I,
)

# Причина у позиции, которая означает «нужен вход заново» — сеть вернула страницу
# авторизации или явно написала «войдите». Паттерны читаются из why позиций отчёта
# телефона (Магнит, Дикси, Пятёрочка).
LOGIN_WHY = re.compile(
    r"войдите|войти заново|войдите снова|требуется вход|необходим вход"
    r"|сессия истекла|авторизуйтесь|авторизация|вы не авторизованы"
    r"|sign in|please log in|нет входа|нет авторизации",
    re.I,
)

NAME_LIMIT = 200
LIST_LIMIT = 120       # позиций в одном списке строки: наряд короче (cart.LINE_LIMIT)

# Сколько секунд телефон может молчать посреди сборки, прежде чем строка сочтёт её
# оборвавшейся. Пульс приходит на каждой позиции; дольше всего телефон ждёт наряда
# (до минуты: поиск самой сети по незнакомым позициям) и карточки (до 30 с).
PHONE_STALE = 120

_START = threading.Lock()   # «посмотреть, не идёт ли» + «отметить, что пошла»
_WRITE = threading.Lock()   # чтение, слияние и запись строк одной настройкой


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Row:
    """Строка одной сети на экране «Сборка корзин». Секретов в ней нет.

    `missing` — что не легло или чего нет в наличии, у каждой позиции причина и
    признак `gone` («нет в наличии»). `unknown` — позиции, которых сеть не знает:
    они не уезжали вовсе. `total` — итог (carttotal.Total), чей он — в нём же.
    `at` — сколько позиций уже пройдено (ход сборки телефона). `extra` — что лежит в
    корзине сети от прежней сборки, а в корзине человека его нет или меньше: сами мы
    из корзин сетей не убираем, это решает человек.
    """

    code: str
    name: str = ""
    road: str = ""
    state: str = IDLE
    note: str = ""
    planned: int = 0
    landed: list[str] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)
    total: Total | None = None
    link: str | None = None
    by: str = SERVER
    basket_id: int | None = None
    basket_name: str = ""
    stamp: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    beat: float | None = None
    now: str = ""
    login: str = ""        # «connect» — подключить; «again» — войти заново
    at: int = 0
    extra: list[dict] = field(default_factory=list)
    lost: bool = False     # сборку телефона оборвало молчание, а не отказ сети

    def as_dict(self) -> dict:
        data = asdict(self)
        data["total"] = self.total.as_dict() if self.total else None
        return data

    @classmethod
    def of(cls, raw) -> Row | None:
        if not isinstance(raw, dict) or not raw.get("code"):
            return None
        known = {name for name in cls.__dataclass_fields__}
        data = {k: v for k, v in raw.items() if k in known}
        data["total"] = Total.of(raw.get("total"))
        try:
            return cls(**data)
        except TypeError:
            log.warning("строка сборки %s не прочиталась", raw.get("code"))
            return None


# ---------- хранение ----------

def _all() -> dict[str, dict]:
    from app import repo

    raw = repo.get_setting(KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("строки сборки корзин не прочитались — начинаю с пустого")
        return {}
    return data if isinstance(data, dict) else {}


def save(row: Row) -> Row:
    """Записать строку сети целиком. Пульс ставится здесь же: по нему видно живую."""
    from app import repo

    row.beat = time.time()
    with _WRITE:
        data = _all()
        data[row.code] = row.as_dict()
        repo.set_setting(KEY, json.dumps(data, ensure_ascii=False))
    return row


def get(code: str) -> Row | None:
    return Row.of(_all().get(code))


# ---------- что за сеть и какой дорогой ----------

def networks() -> list[str]:
    """Сети, у которых есть строка: все, где вход что-то даёт, в порядке справочника."""
    from app import store_accounts

    return [conn.store_code for conn in store_accounts.connections()]


def road(code: str) -> str:
    """Какой дорогой корзина уходит в эту сеть сегодня."""
    from app import handover, store_accounts

    if code == "lenta":
        return PHONE
    if handover.KIND_BY_STORE.get(code) == handover.LINK:
        return LINK
    if code == "metro":
        return API
    ability = store_accounts.ABILITIES.get(code)
    if ability and store_accounts.CART in ability.gives:
        return BROWSER
    return PHONE


def alive(row: Row | None) -> bool:
    """Идёт ли сборка в этой строке на самом деле — с поправкой на умершую.

    Поток мог умереть вместе с сервером посреди сборки. Живой считаем строку, которую
    трогали не позже cart.STALE_AFTER назад, или ту, чья передача идёт прямо сейчас
    (у неё свой пульс в cart.progress). Сборку телефона держит только его пульс, и
    молчать он может не дольше PHONE_STALE: приложение закрыли — строка это увидит.

    ЕСЛИ ПУЛЬСА ЕЩЁ НЕ БЫЛО, СТРОКА НЕ УМИРАЕТ СРАЗУ. Свежая сборка телефона успевает
    записать started_at и только потом присылает первый пульс; считать её мёртвой
    в эту щель значило бы показать «прервано» через секунду после «начал». Поэтому
    при пустом beat берём время начала попытки, и только если нет и его — «не живая».
    """
    from app.shopbrowser import cart

    if row is None or row.state != GOING:
        return False
    if row.by == DEVICE:
        limit = PHONE_STALE
    elif row.road in (BROWSER, API) and cart.running(row.code):
        return True
    else:
        limit = cart.STALE_AFTER
    beat = _pulse_at(row)
    if beat is None:
        return False
    return (time.time() - beat) < limit


def _pulse_at(row: Row) -> float | None:
    """Когда строку последний раз трогали: пульс, иначе начало попытки. Нет обоих — None."""
    try:
        beat = float(row.beat) if row.beat is not None else 0.0
    except (TypeError, ValueError):
        beat = 0.0
    if beat > 0:
        return beat
    if not row.started_at:
        return None
    try:
        return datetime.fromisoformat(str(row.started_at)).timestamp()
    except (TypeError, ValueError):
        return None


def live(row: Row) -> dict | None:
    """Ход передачи прямо сейчас: «кладу 3 из 12 · Творог». Нет хода — None."""
    from app.shopbrowser import cart

    if row.state != GOING:
        return None
    if row.by == DEVICE:
        return {"at": min(row.at, row.planned) if row.planned else row.at,
                "total": row.planned, "now": row.now}
    if row.road in (BROWSER, API) and cart.running(row.code):
        got = cart.progress(row.code) or {}
        total = int(got.get("total") or 0)
        at = int(got.get("at") or 0)
        return {"at": min(at, total) if total else at, "total": total,
                "now": str(got.get("now") or "")}
    return {"at": 0, "total": row.planned, "now": row.now}


def _stamp(items) -> str:
    """Отпечаток корзины: другой состав — другая ссылка-корзина."""
    pairs = sorted((int(i["product_id"]), round(float(i.get("qty") or 0), 3)) for i in items)
    return hashlib.sha1(repr(pairs).encode("utf-8")).hexdigest()[:12]


def stamp_of(basket_id: int) -> str:
    """Отпечаток корзины по её номеру. По нему телефон видит, менялась ли она с прошлой сборки."""
    from app import repo

    return _stamp(repo.basket_items(int(basket_id)))


def _lines(code: str, basket_id: int):
    """Позиции корзины, оценённые в этой сети, — тем же путём, что у «Кабинетов»."""
    from app import api

    try:
        lines, _note = api._plan_lines({"basket": int(basket_id)}, code)
        return lines
    except Exception:  # noqa: BLE001 — корзины нет или она пуста: класть нечего
        log.info("%s: позиции корзины %s не собрались", code, basket_id, exc_info=True)
        return []


def _login(code: str) -> str:
    """Годится ли сохранённый вход для передачи — насколько видно без сети.

    «connect» — входа не было: строке нужна кнопка «Подключить». «again» — вход
    был, но сохранённого нет или в нём нет куки входа (signals.AUTH_COOKIE): у
    Магнита с 16.10.2026 вход не сохраняется, и строка честно просит войти заново.
    Пусто — вход на месте, а жив ли он, скажет сама сеть при передаче.
    """
    from app import store_accounts
    from app.shopbrowser import signals
    from app.shopbrowser import store as shopstore

    state = shopstore.load(code)
    if state is None:
        return "again" if store_accounts.connection(code).connected_at else "connect"
    if signals.AUTH_COOKIE.get(code) and \
            signals.logged_in_by_cookies(code, state.get("cookies")) is not True:
        return "again"
    return ""


def _home_pause(code: str) -> str:
    """Пауза домашнего выхода после отказа сети. Пусто — паузы нет."""
    from app import homeexit

    if not homeexit.configured(code):
        return ""
    until = homeexit.paused_until(code)
    if not until:
        return ""
    return ("Сеть недавно отказала через домашний выход, и до "
            f"{time.strftime('%H:%M', time.localtime(until))} приложение к ней не ходит: "
            "новый стук продлил бы метку на вашем адресе.")


def _fresh(stamp: str | None, hours: float) -> bool:
    try:
        when = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return False
    return (datetime.now() - when).total_seconds() <= hours * 3600.0


# ---------- до нажатия: что будет с каждой сетью ----------

def prepare(code: str, basket: dict, stamp: str, *, again: bool = False) -> Row:
    """Что случится с этой сетью по нажатию — без единого запроса к сетям.

    Отвечает строкой: READY — сюда пойдёт поток; любое из FINAL — не пойдёт, и
    причина в `note`. Эта же строка рисуется на экране ДО нажатия: человек видит,
    куда корзина уедет, а куда нет и почему.
    """
    from app import cartplan, repo, store_accounts
    from app.shopbrowser import cart

    store = repo.get_store(code)
    row = Row(code=code, name=store.name if store else code, road=road(code),
              basket_id=int(basket["id"]), basket_name=str(basket.get("name") or ""),
              stamp=stamp)
    plan = cartplan.build(code, _lines(code, row.basket_id), force=True)
    row.planned, row.unknown = len(plan.lines), list(plan.unknown)[:LIST_LIMIT]

    ability = store_accounts.ABILITIES.get(code)
    if row.road == PHONE or (row.road != LINK and
                             (not ability or store_accounts.CART not in ability.gives)):
        # Сеть корзину от сервера не принимает вовсе — METRO без токена, закрытые
        # витрины. Причину говорит cartplan, тем же словом, что «Результат».
        row.state, row.note = CANT, cartplan.why_not(code)
        return row
    if not plan.lines:
        row.state = EMPTY
        row.note = ("Ни одна позиция корзины этой сети не знакома — класть нечего. "
                    "Связь товара с его названием в сети ставится на экране «Связи».")
        return row

    if row.road == LINK:
        kept = get(code)
        if (not again and kept and kept.state == DONE and kept.link
                and kept.stamp == stamp and kept.basket_id == row.basket_id
                and _fresh(kept.finished_at, cart.RESEND_HOURS)):
            # Ссылка на эту же корзину уже выдана. Новая — это новая корзина у сети,
            # и открыв обе, человек получил бы её дважды.
            return kept
        row.state = READY
        return row

    login = _login(code)
    if login:
        row.state, row.login = LOGIN, login
        row.note = ("Войдите в эту сеть на «Кабинетах» — тогда корзина ляжет прямо в ваш "
                    "кабинет." if login == "connect" else
                    "Сохранённого входа нет или он устарел — войдите заново на «Кабинетах».")
        return row
    if not store_accounts.can(code, store_accounts.CART):
        row.state, row.login = LOGIN, "again"
        row.note = ("Вход сохранён, но сеть его не подтвердила — откройте её на «Кабинетах» "
                    "и войдите заново.")
        return row
    why = cartplan.why_not(code)
    if why:
        row.state, row.note = CANT, why
        return row
    pause = _home_pause(code)
    if pause:
        row.state, row.note = REFUSED, pause
        return row
    if cart.running(code):
        row.state = SENT
        row.note = ("В эту сеть уже идёт передача — вторую приложение не пустит, иначе всё "
                    "легло бы дважды. Её ход виден на «Кабинетах».")
        return row
    sent = None if again else cart.already_sent(code)
    if sent:
        row.state = SENT
        row.note = (f"Корзина сюда уже передана в {sent['finished_at'][11:16]} "
                    f"({sent['landed']} поз.). Сеть кладёт поверх лежащего, поэтому второй "
                    "раз целиком приложение не кладёт. Очистили корзину в сети — "
                    "нажмите «Ещё раз».")
        return row
    row.state = READY
    return row


def _basket(basket_id: int) -> dict | None:
    from app import repo

    return next((b for b in repo.list_baskets() if int(b["id"]) == int(basket_id)), None)


def preview(basket_id: int) -> list[Row]:
    """Строки до нажатия: что будет с каждой сетью. Корзины нет — пусто."""
    from app import repo

    basket = _basket(basket_id)
    if basket is None:
        return []
    stamp = _stamp(repo.basket_items(int(basket_id)))
    out = []
    for code in networks():
        try:
            out.append(prepare(code, basket, stamp))
        except Exception:  # noqa: BLE001 — одна сеть не повод ронять экран
            log.warning("%s: строка сборки не собралась", code, exc_info=True)
    return out


def rows() -> list[Row]:
    """Последняя сборка: строка на каждую сеть, умершая «идёт» — как оборвавшаяся.

    Оборванная сборка телефона помечается `lost`: это не отказ сети и не «не вышло»,
    а замолчавший телефон, и экран говорит об этом своим состоянием «прервано».
    """
    from app import repo

    stored = _all()
    names = {s.code: s.name for s in repo.list_stores()}
    out = []
    for code in networks():
        row = Row.of(stored.get(code)) or Row(code=code, name=names.get(code, code),
                                              road=road(code))
        if row.state == GOING and not alive(row):
            lost = row.by == DEVICE
            row = replace(row, state=FAILED, lost=lost,
                          finished_at=row.finished_at or _now(), now="",
                          note=PHONE_LOST if lost else
                          "Сборка оборвалась на полуслове — скорее всего, сервер "
                          "перезапустили. Посмотрите, что легло, и соберите ещё раз.")
        out.append(row)
    return out


PHONE_LOST = ("Телефон замолчал посреди сборки: приложение закрыли или пропала связь. "
              "Откройте корзину в приложении — сборка продолжится с того, что уже легло.")


# ---------- нажатие ----------

def start(phone: str, basket_id: int, *, only: str | None = None,
          again: bool = False, wait: bool = False) -> list[Row]:
    """Пустить сборку: по потоку на каждую сеть, где это можно. Ответ — строки.

    Отметка «идёт» ставится здесь, в потоке запроса, до создания потоков: иначе
    между «пустили» и «поток дошёл до записи» второе нажатие успело бы пустить ещё
    один заход в ту же сеть. `only` — одна сеть (кнопка «Ещё раз» у её строки).
    `wait` — дождаться всех потоков: для тестов и командной строки, путь тот же.
    """
    from app import repo

    basket = _basket(basket_id) if phone else None
    if basket is None:
        return []
    stamp = _stamp(repo.basket_items(int(basket_id)))
    codes = [only] if only else networks()
    out: list[Row] = []
    going: list[str] = []
    with _START:
        for code in codes:
            current = get(code)
            if alive(current):
                out.append(current)
                continue
            if (not again and current is not None and current.by == DEVICE
                    and current.state == DONE and current.basket_id == int(basket_id)
                    and current.stamp == stamp):
                # Эту же корзину сюда уже собрал телефон. Сеть кладёт поверх лежащего, и
                # сервер положил бы её второй раз; «Ещё раз» (again) — только после очистки.
                out.append(current)
                continue
            try:
                row = prepare(code, basket, stamp, again=again)
            except Exception as exc:  # noqa: BLE001 — одна сеть не должна ронять остальные
                log.exception("%s: строка сборки не собралась", code)
                row = Row(code=code, name=code, road=road(code), state=FAILED,
                          basket_id=int(basket_id), stamp=stamp,
                          note=f"Сборку в эту сеть не начать: {type(exc).__name__}.")
            if (row.state != READY and current is not None and current.state == DONE
                    and current.basket_id == row.basket_id):
                # Сюда эта корзина уже собрана: ссылка выдана, передача прошла или
                # отчитался телефон. Строку с итогом не затираем словами «уже
                # передано» или «нужен вход» — корзина в сети от этого не исчезла, а
                # повторное нажатие ради новой сети не должно стирать ответ старой.
                out.append(current)
                continue
            if row.state == READY:
                row.state, row.started_at, row.now = GOING, _now(), "начинаю"
                going.append(code)
            else:
                row.finished_at = _now()
            out.append(save(row))

    threads = [threading.Thread(target=_work, args=(phone, code, again),
                                name=f"cartrun-{code}", daemon=True) for code in going]
    for thread in threads:
        thread.start()
    if wait:
        for thread in threads:
            thread.join()
    return out


def _work(phone: str, code: str, again: bool) -> None:
    """Поток одной сети. Сам открывает рабочее место и сам его закрывает.

    Новый поток в Python начинает с чистых contextvars, и путь к базе человека в нём
    пуст (та же ловушка описана в app/web/auth.py): без open_workspace строки и
    отчёты уехали бы в общую базу из config.yaml.
    """
    from app import homeexit, users
    from app.catalog.model import CrawlBlocked

    opened = False
    row = None
    try:
        users.open_workspace(phone)
        opened = True
        row = get(code)
        if row is None or row.state != GOING:
            return
        done = _by_link(row) if row.road == LINK else _by_cart(phone, row, again)
        done.finished_at, done.now = _now(), ""
        save(done)
    except CrawlBlocked as exc:
        log.warning("%s: сеть отказала при сборке корзины: %s", code, exc)
        if homeexit.configured(code):
            homeexit.note_refusal(code)
        if opened:
            latest = get(code) or row or Row(code=code)
            save(replace(latest, state=REFUSED, finished_at=_now(), now="",
                         note=f"Сеть отказала. {exc}".strip()))
    except Exception as exc:  # noqa: BLE001 — одна сеть не должна ронять остальные
        log.exception("%s: сборка корзины оборвалась", code)
        if opened:
            latest = get(code) or row or Row(code=code)
            save(replace(latest, state=FAILED, finished_at=_now(), now="",
                         note=f"Сборка оборвалась: {type(exc).__name__}. Посмотрите, что "
                              "легло, и соберите ещё раз."))
    finally:
        users.deactivate()


def _ours(lines, skus=None) -> tuple[Total | None, int]:
    """Наш расчёт по позициям наряда — и сколько позиций без цены в него не вошли."""
    chosen = [ln for ln in lines if skus is None or str(ln.sku) in skus]
    if not chosen:
        return None, 0
    priced = [ln.price for ln in chosen if ln.price is not None]
    unpriced = len(chosen) - len(priced)
    if not priced:
        return None, unpriced
    return Total(sum=round(sum(priced), 2), source=OURS, at=_now()), unpriced


def _by_link(row: Row) -> Row:
    """Лента и ВкусВилл: корзину собирает сеть по нашей ссылке."""
    from app import cartplan, handover

    lines = _lines(row.code, row.basket_id)
    plan = cartplan.build(row.code, lines, force=True)
    out = replace(row, planned=len(plan.lines), unknown=list(plan.unknown)[:LIST_LIMIT])
    got = handover.for_store(row.code, lines)
    if not got.link:
        out.state = FAILED
        out.note = f"Ссылку сеть не выдала. {got.note}".strip()
        return out
    out.state, out.link = DONE, got.link
    out.total, unpriced = _ours(plan.lines)
    out.note = ("Итог сети появится, когда откроете ссылку под своим входом: корзину по "
                "ней собирает сама сеть. Здесь — наш расчёт. " + (got.note or "") +
                (f" Цены у {unpriced} поз. нет — в сумму они не вошли." if unpriced else "")
                ).strip()
    return out


def _by_cart(phone: str, row: Row, again: bool) -> Row:
    """Магнит и METRO: наряд, пускатель и итог сети — ровно как у кнопки «Передать»."""
    from app import cartplan, carttotal, users
    from app.catalog.model import CrawlBlocked
    from app.shopbrowser import cart, driver

    code = row.code
    lines = _lines(code, row.basket_id)
    # Проверка цен и наличия перед передачей — запрос к сети, поэтому уже в потоке.
    plan = cartplan.build(code, lines, force=True, verify_prices=True)
    out = replace(row, planned=len(plan.lines), unknown=list(plan.unknown)[:LIST_LIMIT],
                  missing=[{"name": name, "why": "нет в наличии", "gone": True}
                           for name in plan.gone][:LIST_LIMIT],
                  changes=list(plan.changes)[:LIST_LIMIT], now="кладу в корзину")
    save(out)
    if not plan.lines:
        out.state = FAILED if plan.gone else EMPTY
        out.note = plan.note or "Класть в корзину нечего."
        return out

    try:
        started = cart.start(code, phone, plan, wait=True, again=again)
    finally:
        # Пускатель с ожиданием закрывает рабочее место за собой, как его фоновый
        # поток, а строке оно нужно дальше.
        users.open_workspace(phone)
    if not started:
        out.state = SENT
        out.note = ("Передача в эту сеть уже шла или только что кончилась — второй раз "
                    "приложение не кладёт, иначе всё легло бы дважды.")
        return out

    got = cart.progress(code) or {}
    stop = str(got.get("stop") or "")
    items = [i for i in (got.get("items") or []) if isinstance(i, dict) and not i.get("earlier")]
    landed = [i for i in items if i.get("ok")]
    out.landed = [str(i.get("name") or i.get("sku") or "—")[:NAME_LIMIT] for i in landed]
    out.missing = (out.missing + [
        {"name": str(i.get("name") or i.get("sku") or "—")[:NAME_LIMIT],
         "why": str(i.get("why") or "не легло")[:NAME_LIMIT],
         "gone": bool(GONE_WHY.search(str(i.get("why") or "")))}
        for i in items if not i.get("ok")])[:LIST_LIMIT]
    out.note = str(got.get("note") or "")

    total, why_not = None, ""
    if landed:
        if out.road == API:
            total = Total.of(got.get("network_total"))
            why_not = "" if total else "сеть не прислала итог корзины"
        else:
            out.now = "читаю итог корзины сети"
            save(out)
            total, why_not = carttotal.read(code, phone)
    if out.road == BROWSER and stop not in NO_WINDOW:
        # Окно передачи больше не нужно — как и у пачки (app/cartfill.py). Где окно
        # не открывалось, не трогаем и браузер: закрытие подняло бы его ради пустоты.
        driver.close(code, phone)
    if total is None and landed:
        # Итог сети не прочитан — так и говорим. Наш расчёт стоит рядом под своей
        # подписью и только по тому, что легло: досчитывать за сеть нечего.
        total, unpriced = _ours(plan.lines, {str(i.get("sku")) for i in landed})
        tail = f" Итог сети не прочитан: {why_not}."
        if total is not None:
            tail += " Сумма в строке — наш расчёт по легшим позициям."
        if unpriced:
            tail += f" Цены у {unpriced} поз. нет — в сумму они не вошли."
        out.note = (out.note + tail).strip()
    out.total = total
    save(out)

    if stop in REFUSALS:
        raise CrawlBlocked(out.note or "сеть не пустила передачу")
    if stop in ("login", "nologin"):
        out.state, out.login = LOGIN, "again"
    elif landed:
        out.state = DONE
    elif stop in ("store", "point", "token"):
        out.state = CANT
    else:
        out.state = FAILED
    return out


# ---------- отчёт исполнителя: телефона или любого другого ----------

def _names(raw, limit: int = LIST_LIMIT) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [" ".join(str(item).split())[:NAME_LIMIT] for item in raw[:limit] if str(item).strip()]


def _int(value, default: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(number, 10_000))


def _basket_of(report: dict, current: Row | None) -> tuple[int | None, str]:
    """Какую корзину собирали: номер из отчёта, иначе тот, что уже стоит в строке."""
    basket_id = report.get("basket")
    try:
        basket_id = int(basket_id) if basket_id is not None else (current.basket_id if current else None)
    except (TypeError, ValueError):
        basket_id = current.basket_id if current else None
    basket = _basket(basket_id) if basket_id is not None else None
    return basket_id, str((basket or {}).get("name") or "")


def _stamp_text(value) -> str:
    """Отпечаток корзины из отчёта: короткая шестнадцатеричная строка и ничего больше."""
    text = str(value or "").strip().lower()
    return text if re.fullmatch(r"[0-9a-f]{6,40}", text) else ""


def progress(report: dict, *, by: str = DEVICE) -> Row:
    """Ход сборки телефона в одной сети: «кладу 3 из 9». Строка держится живой его пульсом.

    Форма: {"store", "basket", "stamp", "planned", "at", "now", "state"}. state —
    "going" (по умолчанию), "empty" (класть нечего) или "failed" с причиной в note:
    так телефон говорит о сборке, которая не дошла до отчёта.

    В сеть, куда прямо сейчас кладёт сервер, телефон не пускаем: две руки в одной
    корзине положили бы всё дважды. Ответ — ValueError, и приложение отступит.
    """
    from app import repo
    from app.shopbrowser import cart

    if not isinstance(report, dict):
        raise ValueError("ход сборки должен быть объектом")
    code = str(report.get("store") or "").strip().lower()
    if code not in networks():
        raise ValueError(f"для сети «{code or '—'}» строки сборки нет")
    state = str(report.get("state") or GOING).strip().lower()
    if state not in (GOING, EMPTY, FAILED):
        state = GOING
    with _START:
        current = get(code)
        if state == GOING and ((current is not None and current.by == SERVER and alive(current))
                               or cart.running(code)):
            raise ValueError("в эту сеть сейчас кладёт сервер — телефон подождёт")
        store = repo.get_store(code)
        basket_id, basket_name = _basket_of(report, current)
        stamp = _stamp_text(report.get("stamp"))
        same = (current is not None and current.by == DEVICE and current.state == GOING
                and current.stamp == stamp and current.basket_id == basket_id
                # Продолжение, а не новая попытка: прошлый пульс ещё жив. Замолчавшую
                # строку телефон начинает заново, и время начала сбрасывается — иначе
                # оценка «осталось» считалась бы от брошенной попытки.
                and alive(current))
        row = Row(code=code, name=store.name if store else code,
                  road=current.road if current else road(code), state=state,
                  by=by if by in (SERVER, DEVICE) else DEVICE,
                  note=" ".join(str(report.get("note") or "").split())[:600],
                  planned=_int(report.get("planned")), at=_int(report.get("at")),
                  now=" ".join(str(report.get("now") or "").split())[:160],
                  basket_id=basket_id, basket_name=basket_name, stamp=stamp,
                  started_at=current.started_at if same else _now(),
                  finished_at=None if state == GOING else _now())
        return save(row)


def accept(report: dict, *, by: str = DEVICE) -> Row:
    """Принять отчёт о сборке в одной сети и положить его в строку этой сети.

    Дверь для исполнителя корзины на телефоне (POST /api/carts/report). Сегодня
    сюда никто не пишет: исполнителя по записям корзины в приложении ещё нет. Форма:

        {"store": "pyaterochka",
         "landed": ["Молоко Простоквашино 930 мл", …],
         "missing": [{"name": "Сыр Российский", "why": "нет в наличии", "gone": true}],
         "unknown": ["…"],
         "total": {"sum": 1234.5, "source": "network", "discount": 120, "delivery": 0},
         "state": "done" | "failed" | "refused" | "login",   — необязательно
         "note": "…", "basket": 7, "stamp": "3fa9c1…",
         "extra": [{"name": "Кефир 1%", "more": 1}]}

    Итог «посчитала сеть» — только с явным "source": "network": объявить итог
    сетевым может лишь тот, кто прочитал его у сети. Поля, которых здесь нет, не
    берутся вовсе: в отчёт из браузера легко попасть чужому, а в строку — нет.
    """
    from app import repo

    if not isinstance(report, dict):
        raise ValueError("отчёт о сборке должен быть объектом")
    code = str(report.get("store") or "").strip().lower()
    if code not in networks():
        raise ValueError(f"для сети «{code or '—'}» строки сборки нет")
    missing: list[dict] = []
    raw_missing = report.get("missing")
    for item in (raw_missing if isinstance(raw_missing, list) else [])[:LIST_LIMIT]:
        if isinstance(item, dict):
            name = " ".join(str(item.get("name") or "").split())[:NAME_LIMIT]
            why = " ".join(str(item.get("why") or "").split())[:NAME_LIMIT]
            if name:
                missing.append({"name": name, "why": why or "не легло",
                                "gone": bool(item.get("gone")) or bool(GONE_WHY.search(why))})
        elif str(item).strip():
            missing.append({"name": " ".join(str(item).split())[:NAME_LIMIT],
                            "why": "не легло", "gone": False})
    landed = _names(report.get("landed"))
    state = str(report.get("state") or "").strip().lower()
    if state not in (DONE, FAILED, REFUSED, LOGIN):
        state = DONE if landed else FAILED

    extra: list[dict] = []
    raw_extra = report.get("extra")
    for item in (raw_extra if isinstance(raw_extra, list) else [])[:LIST_LIMIT]:
        if isinstance(item, dict):
            name = " ".join(str(item.get("name") or "").split())[:NAME_LIMIT]
            if name:
                extra.append({"name": name, "more": _int(item.get("more"), 1) or 1})

    current = get(code)
    store = repo.get_store(code)
    basket_id, basket_name = _basket_of(report, current)
    row = Row(code=code, name=store.name if store else code,
              road=current.road if current else road(code),
              state=state, by=by if by in (SERVER, DEVICE) else DEVICE,
              note=" ".join(str(report.get("note") or "").split())[:600],
              planned=len(landed) + len(missing), landed=landed, missing=missing,
              unknown=_names(report.get("unknown")),
              total=Total.of(report.get("total")),
              basket_id=basket_id, basket_name=basket_name,
              stamp=_stamp_text(report.get("stamp")) or (current.stamp if current else ""),
              started_at=current.started_at if current else None, finished_at=_now(),
              login=(report.get("login") if report.get("login") in ("connect", "again", "store")
                     else ("store" if any(STORE_WHY.search(item["why"]) for item in missing) else "again"))
                     if state == LOGIN else "", extra=extra,
              at=len(landed) + len(missing))
    log.info("%s: отчёт о сборке принят (%s): легло %d, не легло %d", code, row.by,
             len(landed), len(missing))
    return save(row)


__all__ = ["Row", "Total", "NETWORK", "OURS", "KEY", "LINK", "BROWSER", "API", "PHONE",
           "IDLE", "READY", "GOING", "DONE", "FAILED", "REFUSED", "LOGIN", "SENT", "CANT",
           "EMPTY", "SERVER", "DEVICE", "PHONE_STALE", "GONE_WHY", "LOGIN_WHY", "STORE_WHY",
           "networks", "road", "alive", "live",
           "prepare", "preview", "rows", "start", "accept", "progress", "stamp_of", "get", "save"]
