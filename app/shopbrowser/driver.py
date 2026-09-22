"""Один браузер, один поток и поручения к нему из любого запроса.

ПОЧЕМУ ПОТОК ВЫДЕЛЕННЫЙ, А НЕ «ГДЕ ПРИДЁТСЯ». Синхронный Playwright привязывает
свои объекты к потоку, в котором создан: позвать страницу из другого потока —
ошибка, а не замедление. При этом waitress держит пул из восьми потоков и раздаёт
запросы по кругу, поэтому два нажатия одного человека почти наверняка придут в
разные потоки. Значит браузер должен жить в своём потоке, а запросы — слать ему
поручения и ждать ответа. Это и делает _Pump ниже.

ПОЧЕМУ ЭТОТ ПОТОК НЕ ХОДИТ В БАЗУ. Путь к базе человека живёт в contextvars, а
новый поток в Python начинает с ЧИСТЫХ contextvars, а не с копии родительских
(та же ловушка описана в app/web/auth.py). Поток браузера, решивший что-нибудь
записать, записал бы это в общую базу из config.yaml — молча и правдоподобно.
Поэтому он НИЧЕГО не хранит: состояние входа он только отдаёт наружу словарём, а
кладёт его в рабочее место тот поток, который пришёл с запросом человека
(app/shopbrowser/store.py).

ЧЕГО ЗДЕСЬ НЕТ. Подмены отпечатка, сокрытия признаков автоматизации и
«невидимок» в модуле не написано. Флаги запуска — только те, без которых Chromium
не живёт в контейнере (`--no-sandbox` под root и `--disable-dev-shm-usage` при
маленьком /dev/shm). Человеческие шаги — капча, код из СМС, кнопка «заказать» —
делает человек.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger(__name__)


class BrowserUnavailable(RuntimeError):
    """Браузера на этой машине нет или он не поднялся.

    Отдельный класс, чтобы экран мог сказать словами, чего не хватает, вместо
    того чтобы показать человеку чужую ошибку про библиотеку libnss3.
    """


class BrowserTimeout(BrowserUnavailable):
    """Поручение не ответило за отведённое время — и оно ПРОДОЛЖАЕТ выполняться.

    ЧЕМ ЭТО ОТЛИЧАЕТСЯ ОТ ВСЕХ ОСТАЛЬНЫХ НЕУДАЧ, И ПОЧЕМУ РАДИ НЕГО ЗАВЕДЁН СВОЙ
    КЛАСС. Future.result(timeout) не отменяет ничего: поручение остаётся в очереди
    потока браузера и спокойно доделывает свои нажатия. Значит, тот, кто получил
    эту ошибку, НЕ ЗНАЕТ, случилось действие или нет, — а закрытый сеанс или
    неподнявшийся Chromium означают твёрдое «не случилось».

    Разница стоит денег. Наполнение корзины (app/shopbrowser/cart.py) объявляет
    непроложенным всё, о чём браузер не отчитался, и предлагает это повторить;
    позиция, которую поручение в это время дощёлкивает, легла бы в корзину второй
    раз. Поэтому таймаут — отдельный ответ, и повтору он не отдаётся.

    Наследуется от BrowserUnavailable нарочно: все, кто ловил его прежде, ловят и
    теперь, а разбираться в разнице обязан только тот, кому она важна.
    """


# КУДА ОТКРЫВАЕТСЯ ОКНО, КОГДА ЧЕЛОВЕК ИДЁТ ВХОДИТЬ.
#
# Здесь у всех шести сетей стоит ГЛАВНАЯ, и это решение, а не недоделка. Своего
# адреса входа — вроде /login — ни у одной из шести не разведано: у них вход
# открывается окошком поверх витрины, а не отдельной страницей. Поставить сюда
# правдоподобный, но непроверенный адрес было бы хуже главной: непроверенный
# адрес — это «страница не найдена» вместо магазина, из которой некуда нажать,
# даже «Назад» (в истории окна она первая). Человек упирается в тупик там, где
# раньше видел хотя бы витрину.
#
# ЧЕМ ЗАМЕНЕНА НЕДОСТАЮЩАЯ ССЫЛКА. Пошаговой подсказкой у самого снимка:
# app/shopbrowser/signals.ENTRANCE знает по каждой сети, где искать «Войти», что
# вводить и по чему видно, что вошёл. Ссылка сэкономила бы одно нажатие, подсказка
# отвечает на весь вопрос «что нажимать» целиком.
#
# КАК ПОДСТАВИТЬ НАСТОЯЩИЙ АДРЕС, КОГДА ЕГО УВИДЯТ ЖИВЬЁМ. Заменить строку сети —
# и только. Но адрес должен остаться НА ТОМ ЖЕ УЗЛЕ, что HOME_URL ниже: сторож в
# tests/test_cabinet_login.py это проверяет, и вот почему (см. HOME_URL).
LOGIN_URL = {
    # Открывается нашему серверу сразу. «Войти» — в НИЖНЕЙ панели, в шапке её нет.
    "magnit": "https://magnit.ru/",
    # Встречает наш сервер своей проверкой: первой может открыться она, а не витрина.
    "pyaterochka": "https://5ka.ru/",
    # Сначала спрашивает адрес доставки — без него не показывает ни товаров, ни цен.
    "samokat": "https://samokat.ru/",
    # Встречает наш сервер проверкой. Гостю пишет «Вход в личный кабинет» — это НЕ
    # признак входа, на нём разбор уже ломался (см. signals.py).
    "lenta": "https://lenta.com/",
    # Открывается нашему серверу сразу.
    "vkusvill": "https://vkusvill.ru/",
    # Встречает наш сервер проверкой. Свой кабинет зовёт «Клуб Друзей».
    "dixy": "https://dixy.ru/",
    # Открывается нашему серверу сразу: замер 19.09.2026 — 200 и почти два мегабайта
    # витрины. Окно здесь нужно не ради нажатий по карточкам, а ради ХЕША КОРЗИНЫ:
    # сеть выдаёт его кукой metro_user_id, и по нему корзина наполняется одним
    # запросом (app/connectors/metro_cart.py), а не шестнадцатью загрузками страниц.
    "metro": "https://online.metro-cc.ru/",
    # Встречает наш сервер не отказом по адресу, а ГОЛОВОЛОМКОЙ: «Разверните
    # картинку горизонтально», наш IP назван, сама за 55 секунд она не проходится
    # (замер 20.09.2026). Окно здесь нужно не ради входа в кабинет, а ради этой
    # проверки: за ней лежат цены к 158 тысячам наших названий. Решается она
    # протяжкой ползунка — для неё в окно и добавлен drag().
    "perekrestok": "https://www.perekrestok.ru/",
}

# ЧЕЙ ЭТО САЙТ — СЧИТАЕТСЯ ОТСЮДА, А НЕ ОТ АДРЕСА ВХОДА.
#
# Граница «окно ходит только по своей сети» (_same_chain ниже) раньше брала узел
# из LOGIN_URL. Пока там стояла главная, разницы не было — но стоит однажды
# вписать в LOGIN_URL вход, живущий на ЧУЖОМ узле (у сетей с общим входом на
# несколько марок так бывает), и граница молча переехала бы туда вместе с ним:
# окно пустили бы гулять по всему чужому узлу и закрыли бы ему сам магазин.
# Переезд такой границы должен быть решением, а не побочным действием правки
# ссылки, поэтому дом сети записан отдельно и своими руками.
HOME_URL = {
    "magnit": "https://magnit.ru/",
    "pyaterochka": "https://5ka.ru/",
    "samokat": "https://samokat.ru/",
    "lenta": "https://lenta.com/",
    "vkusvill": "https://vkusvill.ru/",
    "dixy": "https://dixy.ru/",
    "metro": "https://online.metro-cc.ru/",
    "perekrestok": "https://www.perekrestok.ru/",
}

# Окно узкое нарочно. Сети рисуют по ширине: на 820 точках почти все отдают
# собранную раскладку, которую видно и на телефоне, и на широком экране. Снимок
# шириной в полторы тысячи точек на телефоне нечитаем, а это тот самый экран, с
# которого человек чаще всего и входит.
WIDTH = 820
HEIGHT = 1000

IDLE_LIMIT = 15 * 60       # столько живёт нетронутый сеанс: браузер стоит памяти
MAX_SESSIONS = 4           # больше четырёх Chromium разом сервер не прокормит
CALL_TIMEOUT = 90          # дольше не ждём поручения: человек за это время решит, что всё встало
SHOT_QUALITY = 62          # JPEG: снимок едет по сети на каждое нажатие


@dataclass(frozen=True)
class Shot:
    """Снимок страницы и то, что о ней надо знать экрану."""

    jpeg: bytes
    width: int
    height: int
    url: str
    title: str
    at: float


@dataclass
class _Live:
    """Живой сеанс: контекст браузера, страница и когда его трогали."""

    chain: str
    context: Any
    page: Any
    opened_at: float
    touched_at: float


def _headless() -> bool:
    """Запускать ли браузер БЕЗ экрана.

    ПОЧЕМУ ВООБЩЕ ЕСТЬ ВЫБОР, И ПОЧЕМУ ЭТО НЕ МАСКИРОВКА. Headless — это не
    «незаметный» режим, а урезанный: браузер без окна, без композитора, и он сам
    сообщает о себе, что за ним никто не смотрит. В нашем случае это ПРОСТО
    НЕПРАВДА: за страницей смотрит человек — она показана ему в экране «Кабинет»,
    и нажимает по ней он. Обычный режим с экраном — честное описание того, что тут
    происходит, а не попытка сойти за кого-то другого.

    Отсюда же и остальное: User-Agent не переписывается, отпечаток не
    подменяется. Сеть, которая спросит, кто пришёл, получит честный ответ.

    ГДЕ ЭКРАН БЕРЁТСЯ. На сервере его даёт Xvfb (виртуальный экран, см. Dockerfile),
    и тогда в окружении стоит DISPLAY. На машине разработчика под Windows его нет —
    там остаётся headless, и это правильно: поднимать окно ради теста незачем.
    """
    return not os.environ.get("DISPLAY")


class _Pump:
    """Поток-владелец браузера. Всё, что касается Playwright, происходит в нём."""

    def __init__(self) -> None:
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._broken: str | None = None

    # ----- со стороны запроса -----
    def call(self, job: Callable[[dict], Any], *, timeout: float = CALL_TIMEOUT) -> Any:
        """Отдать поручение потоку браузера и дождаться ответа."""
        if self._broken:
            raise BrowserUnavailable(self._broken)
        self._ensure()
        future: Future = Future()
        self._jobs.put((job, future))
        try:
            return future.result(timeout=timeout)
        except TimeoutError as exc:
            # Поручение не отменяется: оно доживёт в очереди потока и доделает
            # начатое. Кто спрашивал — узнаёт об этом по отдельному классу ошибки.
            raise BrowserTimeout(
                "браузер не ответил за отведённое время — страница сети, похоже, "
                "не догрузилась; попробуйте ещё раз") from exc

    def _ensure(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            ready: Future = Future()
            self._thread = threading.Thread(target=self._run, args=(ready,),
                                            name="shopbrowser", daemon=True)
            self._thread.start()
            ready.result(timeout=120)     # поднятие браузера — единственное долгое место

    # ----- сам поток -----
    def _run(self, ready: Future) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            self._broken = ("браузер на сервере не установлен: нет пакета playwright. "
                            "Поставьте его и Chromium — см. Dockerfile")
            ready.set_exception(BrowserUnavailable(self._broken))
            return

        try:
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(
                        headless=_headless(),
                        args=["--no-sandbox", "--disable-dev-shm-usage"])
                except Exception as exc:  # noqa: BLE001 — причина уедет человеку словами
                    self._broken = f"Chromium не запустился: {exc}"
                    ready.set_exception(BrowserUnavailable(self._broken))
                    return

                state: dict = {"pw": pw, "browser": browser, "live": {}}
                ready.set_result(True)
                self._serve(state)
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            self._broken = f"браузер на сервере не поднялся: {exc}"
            if not ready.done():
                ready.set_exception(BrowserUnavailable(self._broken))

    def _serve(self, state: dict) -> None:
        while True:
            try:
                job, future = self._jobs.get(timeout=60)
            except queue.Empty:
                _sweep(state)          # тишина — повод закрыть забытые сеансы
                continue
            if job is None:
                return
            try:
                future.set_result(job(state))
            except Exception as exc:  # noqa: BLE001 — ошибка едет тому, кто просил
                future.set_exception(exc)
            finally:
                _sweep(state)


_pump = _Pump()


# ---------- работа внутри потока браузера ----------

def _sweep(state: dict) -> None:
    """Закрыть сеансы, которых давно не трогали. Браузер стоит памяти."""
    now = time.time()
    for sid, live in list(state["live"].items()):
        if now - live.touched_at < IDLE_LIMIT:
            continue
        _drop(state, sid)
        log.info("сеанс %s закрыт: его не трогали %d мин", sid, IDLE_LIMIT // 60)


def _drop(state: dict, sid: str) -> None:
    live = state["live"].pop(sid, None)
    if not live:
        return
    try:
        live.context.close()
    except Exception:  # noqa: BLE001 — контекст мог умереть сам
        pass


def _need(state: dict, sid: str) -> _Live:
    live = state["live"].get(sid)
    if not live:
        raise BrowserUnavailable(
            "сеанс магазина закрыт — откройте кабинет заново")
    live.touched_at = time.time()
    return live


# Сколько раз повторить снимок и с какой паузой. Повтор здесь не «на всякий
# случай»: на живом сервере 17.09.2026 ПЕРВЫЙ снимок на только что поднятом экране
# падает с «Page.screenshot: Unable to capture screenshot», а следующие снимки того
# же и даже большего размера снимаются подряд без единой осечки. То есть окно ещё
# не показано, когда мы просим картинку. Проверено отдельно, что дело НЕ в размере:
# на экране 1440×1440 первый снимок падал так же, а полный снимок 820×1000 на
# экране 1280×1024 снимается и ничего не обрезает.
# Без повтора это стоило бы человеку всего экрана «Кабинет»: первое же открытие
# магазина после перезапуска сервера показало бы «страницу не удалось снять».
SHOT_TRIES = 3
SHOT_RETRY_PAUSE = 0.35

# Сколько ждать после набранного, прежде чем снимать. Набранная буква рисуется
# мгновенно; всё, что дольше, человек чувствует как задержку между нажатием и
# появлением знака — а именно она и превращает окно в «постоянную загрузку».
TYPE_PAUSE = 0.08

# Кадр набора ездит чаще всех остальных вместе взятых — по разу на каждую пачку
# знаков, — и едет он по той же связи, по которой человек сидит с телефона.
# Замер 17.09.2026 с рабочей машины: даже пустая страница приложения идёт с
# сервера три четверти секунды, то есть узкое место — связь, а не наша работа.
# Значит единственное, чем можно помочь, — облегчить сам кадр. На 40 он вдвое
# легче обычного, а разглядывать буквы в поле это не мешает: снимок для чтения
# витрины человек берёт кнопкой «Крупнее», и тот идёт обычным качеством.
TYPE_QUALITY = 40

# Пауза между знаками при наборе. Нужна, потому что поля сетей слушают каждое
# нажатие и на мгновенную вставку строки не отзываются. Сорок пять было взято с
# запасом; на длинном коде из СМС этот запас превращался в лишние полсекунды.
TYPE_KEY_DELAY = 25


def _snapshot(live: _Live, quality: int | None = None) -> Shot:
    """Снимок страницы. Не вышел — это ответ, а не повод ронять экран."""
    jpeg = None
    last = None
    for attempt in range(SHOT_TRIES):
        try:
            jpeg = live.page.screenshot(type="jpeg",
                                        quality=quality or SHOT_QUALITY)
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt + 1 < SHOT_TRIES:
                log.debug("снимок не вышел с попытки %d: %s", attempt + 1, exc)
                time.sleep(SHOT_RETRY_PAUSE)
    if jpeg is None:
        raise BrowserUnavailable(f"страницу не удалось снять: {last}") from last
    try:
        title = live.page.title() or ""
    except Exception:  # noqa: BLE001
        title = ""
    return Shot(jpeg=jpeg, width=WIDTH, height=HEIGHT, url=live.page.url,
                title=title[:200], at=time.time())


# Сколько ждём, пока страница НАРИСУЕТСЯ. Не путать с загрузкой: витрины сетей —
# это приложения на JavaScript, у которых domcontentloaded наступает на пустом
# экране, а товар подъезжает через секунды. Первая проверка живьём 17.09.2026 это
# и показала: снимки Пятёрочки и Ленты приходили пустыми (7,5 КБ — это белый лист),
# и человек видел бы белое окно вместо магазина.
DRAW_LIMIT = 12.0          # дольше ждать бессмысленно: значит, показывать нечего
DRAW_STEP = 0.4
DRAW_ENOUGH = 200          # столько знаков видимого текста — уже не пустой лист


def _settle(page, wait: float = 0.8) -> None:
    """Дать странице договорить и НАРИСОВАТЬСЯ.

    Не networkidle: витрины магазинов держат открытыми соединения постоянно, и
    ожидание тишины упёрлось бы в таймаут на каждом шаге. Ждём появления видимого
    текста — это то самое, ради чего человек сюда и смотрит. Не дождались —
    показываем как есть: пустой снимок тоже ответ, и человек нажмёт «Обновить».
    """
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:  # noqa: BLE001 — не дождались, покажем как есть
        pass
    until = time.time() + DRAW_LIMIT
    while time.time() < until:
        try:
            drawn = page.evaluate("() => (document.body && document.body.innerText || '').length")
        except Exception:  # noqa: BLE001 — страница как раз уезжает на другую
            drawn = 0
        if drawn and drawn >= DRAW_ENOUGH:
            break
        time.sleep(DRAW_STEP)
    time.sleep(wait)


# ---------- то, что зовут экраны ----------

def sid_of(chain: str, phone: str) -> str:
    """Один кабинет на человека и сеть. Второй такой же был бы вторым входом."""
    return f"{phone}:{chain}"


def available() -> bool:
    """Есть ли на этой машине браузер вообще. Ходит в импорт, не поднимая его."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def open_store(chain: str, phone: str, *, state: dict | None = None,
               url: str | None = None) -> Shot:
    """Открыть сеть в браузере сервера. Есть сохранённый вход — подставить его.

    `state` — это storage_state сети из рабочего места человека (куки и
    localStorage). Подставляем его ДО первого перехода: подставленный после,
    он не успел бы повлиять на первую же страницу, и человек увидел бы себя
    невошедшим там, где вход есть.
    """
    if chain not in LOGIN_URL:
        raise BrowserUnavailable(f"сеть «{chain}» браузеру незнакома")
    target = url or LOGIN_URL[chain]
    # Адрес приходит формой, а форма приходит откуда угодно. Открыть по просьбе
    # снаружи произвольный сайт значило бы сделать из приложения пересыльщика,
    # которым чужими руками ходят куда угодно с нашего адреса и нашими куками.
    if not _same_chain(chain, target):
        raise BrowserUnavailable("этот адрес не принадлежит открываемой сети")
    sid = sid_of(chain, phone)

    def job(st: dict) -> Shot:
        live = st["live"].get(sid)
        if live is None:
            while len(st["live"]) >= MAX_SESSIONS:
                oldest = min(st["live"].items(), key=lambda kv: kv[1].touched_at)[0]
                _drop(st, oldest)
                log.info("сеанс %s закрыт: больше %d сеансов сервер не держит",
                         oldest, MAX_SESSIONS)
            context = st["browser"].new_context(
                locale="ru-RU", timezone_id="Europe/Moscow",
                viewport={"width": WIDTH, "height": HEIGHT},
                storage_state=state or None)
            page = context.new_page()
            now = time.time()
            live = _Live(chain=chain, context=context, page=page,
                         opened_at=now, touched_at=now)
            st["live"][sid] = live
        live.touched_at = time.time()
        live.page.goto(target, wait_until="domcontentloaded", timeout=60000)
        _settle(live.page)
        return _snapshot(live)

    return _pump.call(job)


def shot(chain: str, phone: str) -> Shot:
    """Снимок того, что сейчас на странице."""
    sid = sid_of(chain, phone)
    return _pump.call(lambda st: _snapshot(_need(st, sid)))


def click(chain: str, phone: str, x: int, y: int) -> Shot:
    """Нажать там, где нажал человек. Координаты — точки снимка."""
    sid = sid_of(chain, phone)

    def job(st: dict) -> Shot:
        live = _need(st, sid)
        live.page.mouse.click(max(0, min(WIDTH, int(x))), max(0, min(HEIGHT, int(y))))
        _settle(live.page, 1.0)
        return _snapshot(live)

    return _pump.call(job)


# Сколько шагов делает протяжка между началом и концом. Один прыжок из точки в
# точку ползунки НЕ ПРИНИМАЮТ: они слушают mousemove и при единственном событии
# считают, что мышь телепортировалась, — так отличают человека от программы.
# Поэтому путь проходится частями, как рукой.
DRAG_STEPS = 24


def drag(chain: str, phone: str, x1: int, y1: int, x2: int, y2: int) -> Shot:
    """Протянуть от точки к точке: нажать, провести, отпустить.

    ЗАЧЕМ ЭТО ОТДЕЛЬНО ОТ НАЖАТИЯ. Окно умело нажимать, печатать и крутить
    страницу — этого хватало на вход по телефону. Но проверку «я не робот» сети
    ставят и такую, которую нажатием не пройти: Перекрёсток 20.09.2026 встречает
    наш сервер головоломкой «Разверните картинку горизонтально» с ПОЛЗУНКОМ под
    ней. Ползунок без протяжки не сдвинуть, и без неё окно приводило бы человека
    к задаче, которую он в нём физически не может решить.

    Решает её человек, своей рукой: приложение только передаёт движение со снимка
    на страницу, как передаёт нажатия и набранный текст.
    """
    sid = sid_of(chain, phone)

    def job(st: dict) -> Shot:
        live = _need(st, sid)
        ax, ay = max(0, min(WIDTH, int(x1))), max(0, min(HEIGHT, int(y1)))
        bx, by = max(0, min(WIDTH, int(x2))), max(0, min(HEIGHT, int(y2)))
        live.page.mouse.move(ax, ay)
        live.page.mouse.down()
        for step in range(1, DRAG_STEPS + 1):
            live.page.mouse.move(ax + (bx - ax) * step / DRAG_STEPS,
                                 ay + (by - ay) * step / DRAG_STEPS)
        live.page.mouse.up()
        _settle(live.page, 1.5)
        return _snapshot(live)

    return _pump.call(job)


# Что сейчас выбрано на чужой странице. Спрашиваем ПЕРЕД набором, и это не
# перестраховка: набор уходит туда, где стоит курсор, а курсор на чужой странице
# живёт своей жизнью. Поле входа Магнита выбирается само, когда окно открылось, —
# но стоит человеку нажать проверку «я не робот», и выбранной становится её
# рамка. Набранное уходит внутрь проверки, поле остаётся пустым, а приложение
# раньше отвечало снимком, как будто всё получилось: человек набирал код из СМС
# по три раза и видел пустую строку. Внутрь чужой рамки нам не заглянуть (она с
# другого сайта), да и не нужно: достаточно знать, что это не поле.
#
# И ЗДЕСЬ ЖЕ БЫЛА ОШИБКА, СТОИВШАЯ ВЛАДЕЛЬЦУ ВХОДА, 20.09.2026. Из «выбрана
# рамка» был сделан вывод «набирать некуда» — и набор запрещался совсем. Но
# рамка бывает двух разных видов, и второй прямо противоположен первому:
#
#   галочка «я не робот» — внутри рамки набирать нечего, поле снаружи;
#   картинка с буквами  — поле ВНУТРИ рамки, и набирать надо ровно туда.
#
# Магнит встретил владельца вторым: Yandex SmartCaptcha, «Введите текст с
# картинки», и поле у неё своё, внутри рамки. Он нажимал в это поле — и получал
# «Набирать некуда», потому что страница видит выбранной рамку, а не то, что
# внутри. Запрет не давал пройти проверку, ради которой открывали окно.
#
# Различить два вида снаружи нельзя: рамка с чужого сайта, и что в ней — нам не
# видно. Значит выбор не между «набирать» и «не набирать», а между запретом и
# честным предупреждением. Запрет ломает рабочий случай молча и наглухо;
# предупреждение оставляет человеку оба пути и говорит правду о том, куда ушли
# буквы. Поэтому в рамку набираем и прямо пишем, что набрали в неё.
ACTIVE_FIELD = """
() => {
  const a = document.activeElement;
  if (!a || a === document.body || a === document.documentElement) return {kind: 'none'};
  if (a.tagName === 'IFRAME')
    return {kind: 'frame', label: (a.getAttribute('title') || '').trim().slice(0, 40)};
  const notText = ['button', 'submit', 'checkbox', 'radio', 'file', 'image', 'reset', 'range'];
  const editable = a.tagName === 'TEXTAREA' || a.isContentEditable
    || (a.tagName === 'INPUT' && !notText.includes((a.type || 'text').toLowerCase()));
  if (!editable)
    return {kind: 'other',
            label: ((a.innerText || a.getAttribute('aria-label') || a.tagName) || '').trim().slice(0, 40)};
  return {kind: 'field',
          label: ((a.placeholder || a.getAttribute('aria-label') || a.name || '')).trim().slice(0, 40)};
}
"""


def type_text(chain: str, phone: str, text: str) -> tuple[Shot, dict]:
    """Набрать текст туда, где сейчас курсор. Отвечает ещё и КУДА попало.

    Набираем по-человечески, с задержкой между знаками: поля ввода у сетей часто
    слушают каждое нажатие и на мгновенную вставку целой строки не отзываются
    вовсе — вход выглядел бы сломанным, хотя дело в скорости.

    НИ В ЧТО — НЕ НАБИРАЕМ ВОВСЕ. Набрать «в никуда» хуже, чем не набрать:
    человек не видит ни текста, ни отказа и повторяет попытку, а код из СМС живёт
    минуту. Это про «не выбрано ничего» и «выбрана кнопка».

    А В РАМКУ — НАБИРАЕМ, И ГОВОРИМ, ЧТО В РАМКУ. У проверки с картинкой поле
    своё, внутри рамки, и запрет не давал её пройти (см. ACTIVE_FIELD выше).
    """
    sid = sid_of(chain, phone)

    def job(st: dict) -> tuple[Shot, dict]:
        live = _need(st, sid)
        try:
            spot = live.page.evaluate(ACTIVE_FIELD) or {}
        except Exception:  # noqa: BLE001 — не спросили, так наберём: страница важнее опроса
            spot = {"kind": "unknown"}
        kind = spot.get("kind") or "none"
        if kind in ("none", "other"):
            return _snapshot(live, TYPE_QUALITY), {"typed": False, "kind": kind,
                                                   "label": spot.get("label") or ""}
        live.page.keyboard.type(str(text)[:200], delay=TYPE_KEY_DELAY)
        # НЕ _settle. Тот ждёт, пока страница НАРИСУЕТСЯ заново: спрашивает
        # состояние загрузки, опрашивает длину текста и спит ещё полсекунды сверху.
        # Для навигации это правильно, для набранной буквы — нет: набор шёл по
        # секунде с лишним, экран всё это время стоял под пеленой «работаю…», и
        # владелец справедливо сказал, что вводить напрямую не получается —
        # «происходит какая-то загрузка постоянная». Букве нужно только дорисоваться.
        time.sleep(TYPE_PAUSE)
        return _snapshot(live, TYPE_QUALITY), {"typed": True, "kind": kind,
                                               "label": spot.get("label") or ""}

    return _pump.call(job)


# Клавиши, которые экран может нажать. Список закрытый: имя клавиши приходит
# формой, а форма приходит откуда угодно.
KEYS = {"enter": "Enter", "backspace": "Backspace", "tab": "Tab",
        "escape": "Escape", "delete": "Delete"}


def key(chain: str, phone: str, name: str) -> Shot:
    sid = sid_of(chain, phone)
    pressed = KEYS.get(str(name).lower())
    if not pressed:
        raise BrowserUnavailable(f"клавиша «{name}» экрану не разрешена")

    def job(st: dict) -> Shot:
        live = _need(st, sid)
        live.page.keyboard.press(pressed)
        _settle(live.page, 0.8)
        return _snapshot(live)

    return _pump.call(job)


def scroll(chain: str, phone: str, dy: int) -> Shot:
    sid = sid_of(chain, phone)

    def job(st: dict) -> Shot:
        live = _need(st, sid)
        live.page.mouse.wheel(0, int(dy))
        _settle(live.page, 0.5)
        return _snapshot(live)

    return _pump.call(job)


def navigate(chain: str, phone: str, where: str) -> Shot:
    """Перейти: «back», «forward», «reload» или адрес внутри той же сети.

    Чужой адрес не пускаем: экран показывает страницу магазина, и увести его на
    произвольный сайт значило бы превратить приложение в открытый пересыльщик,
    которым чужими руками можно ходить куда угодно с нашего адреса.
    """
    sid = sid_of(chain, phone)
    move = str(where or "").strip()

    def job(st: dict) -> Shot:
        live = _need(st, sid)
        if move == "back":
            live.page.go_back(wait_until="domcontentloaded", timeout=45000)
        elif move == "forward":
            live.page.go_forward(wait_until="domcontentloaded", timeout=45000)
        elif move in ("reload", ""):
            live.page.reload(wait_until="domcontentloaded", timeout=45000)
        else:
            if not _same_chain(live.chain, move):
                raise BrowserUnavailable("этот адрес не принадлежит открытой сети")
            live.page.goto(move, wait_until="domcontentloaded", timeout=60000)
        _settle(live.page)
        return _snapshot(live)

    return _pump.call(job)


def _same_chain(chain: str, url: str) -> bool:
    """Тот же ли это сайт. Сверяем имя узла, а не подстроку.

    «5ka.ru.zlo.example» содержит «5ka.ru» и нашим быть не должен — то же правило,
    по которому живёт match() у парсеров сетей.
    """
    from urllib.parse import urlsplit

    try:
        host = (urlsplit(url).hostname or "").lower()
        home = (urlsplit(HOME_URL[chain]).hostname or "").lower()
    except (ValueError, KeyError):
        return False
    return bool(host) and (host == home or host.endswith("." + home))


def see(chain: str, text: str, cookies=()) -> dict:
    """Вердикт по тексту страницы и кукам — БЕЗ браузера и без сети.

    ПОЧЕМУ ЭТО ОТДЕЛЬНО ОТ look. Здесь живёт весь вердикт «вошли / не вошли / не
    разобрал», и ошибается он тихо: «не разобрал», превратившееся в «вы не вошли»,
    гасит человеку живое подключение вместе с персональными ценами, а «вошли» у
    гостя предлагает запомнить вход, которого нет, — обе поломки здесь уже
    случались. Пока разбор сидел внутри поручения браузеру, проверить его можно
    было только живой сетью, то есть почти никогда. Отдельной функции хватает
    поддельной страницы: tests/test_cabinet_login.py проходит так все шесть сетей.

    look ниже добывает те же две вещи из настоящей страницы и зовёт сюда.
    """
    from app.shopbrowser import signals

    # Кука сильнее текста и потому спрашивается первой: она не зависит от
    # вёрстки вообще, а текст сети переделывают несколько раз в год.
    by_cookie = signals.logged_in_by_cookies(chain, cookies)
    by_text = signals.logged_in(chain, text)
    entered = by_cookie if by_cookie is not None else by_text
    # ПОДПИСЬ И БАЛЛЫ СПРАШИВАЮТСЯ ТОЛЬКО У ТОГО, ПРО КОГО УЖЕ ДОКАЗАН ВХОД.
    #
    # Раньше их читали всегда, и «не разобрал» приезжало на экран с номером карты:
    # приложение честно писало «не знаю, вошли вы или нет» — и тут же подписывало
    # кабинет «Карта Лента •••1234», вычитанной образцом, который живой страницей
    # не подтверждён (провенанс — в signals.ACCOUNT). Дальше эта подпись ложилась в
    # рабочее место и в store_accounts, человек верил, что подключение живое, и
    # получал пустой перенос корзины. Поправить это потом нечем: в базе лежит
    # правдоподобная подпись, снятая с чужой витрины.
    name = None
    if entered is True:
        # Общее имя карты — украшение для ВОШЕДШЕГО, и подставляет его тот, кто
        # знает, что человек вошёл, а не разбор текста.
        name = signals.account(chain, text) or signals.CARD_NAME.get(chain)
    guard = signals.guard_kind(text)
    says, tone = signals.verdict_words(entered, guard=guard, account=name)
    return {
        "logged_in": entered,
        "by_cookie": by_cookie,
        "guarded": bool(guard),
        # Чем именно встретила сеть: проверкой, которую проходят, или отказом
        # отдать страницу. Экрану этого мало, а вот наполнению корзины нужно:
        # человеку надо сказать, идти ли ему проходить проверку.
        "guard": guard,
        "account": name,
        "points": signals.points(text) if entered is True else None,
        "says": says,
        "says_kind": tone,
    }


def look(chain: str, phone: str) -> dict:
    """Что видно на странице: вошёл ли человек, как подписан, не встретила ли защита.

    Читаем ВИДИМЫЙ текст, а не разметку: то же правило, по которому жил разбор
    страниц с самого начала. Скрытая в закрытом меню кнопка «Войти» в видимый
    текст не попадает — и правильно, человек её тоже не видит.
    """
    sid = sid_of(chain, phone)

    def job(st: dict) -> dict:
        live = _need(st, sid)
        try:
            text = live.page.inner_text("body")[:40000]
        except Exception:  # noqa: BLE001 — страница могла уехать прямо сейчас
            text = ""
        try:
            jar = live.context.cookies()
        except Exception:  # noqa: BLE001
            jar = []
        return {"url": live.page.url, **see(chain, text, jar)}

    return _pump.call(job, timeout=40)


def run(chain: str, phone: str, job, *, timeout: float = CALL_TIMEOUT):
    """Выполнить своё дело над страницей — внутри потока браузера.

    Дверь для тех, кто знает, ЧТО делать со страницей, но не должен знать, как
    устроен поток: наполнение корзины (app/shopbrowser/cart.py) живёт именно так.
    `job` получает страницу и возвращает что угодно; всё, что он делает, делается
    в том единственном потоке, которому Playwright принадлежит.
    """
    sid = sid_of(chain, phone)
    return _pump.call(lambda st: job(_need(st, sid).page), timeout=timeout)


def remember(chain: str, phone: str) -> dict:
    """Забрать состояние входа (куки и localStorage) — чтобы записать его в базу.

    Записывает не этот поток: у него нет и не должно быть базы человека. Он
    только отдаёт словарь тому, кто пришёл с запросом.
    """
    sid = sid_of(chain, phone)
    return _pump.call(lambda st: _need(st, sid).context.storage_state())


def close(chain: str, phone: str) -> None:
    sid = sid_of(chain, phone)
    try:
        _pump.call(lambda st: _drop(st, sid))
    except BrowserUnavailable:
        pass          # закрывать нечего — это и был нужный итог


def session(chain: str, phone: str) -> dict | None:
    """Что известно про открытый сеанс. Нет сеанса — None, а не пустой словарь."""
    sid = sid_of(chain, phone)

    def job(st: dict) -> dict | None:
        live = st["live"].get(sid)
        if not live:
            return None
        return {"chain": live.chain, "url": live.page.url,
                "opened_at": live.opened_at, "touched_at": live.touched_at}

    try:
        return _pump.call(job, timeout=20)
    except BrowserUnavailable:
        return None


def sessions() -> list[dict]:
    """Все открытые сеансы — для присмотра за памятью сервера."""
    def job(st: dict) -> list[dict]:
        return [{"sid": sid, "chain": live.chain, "opened_at": live.opened_at,
                 "touched_at": live.touched_at} for sid, live in st["live"].items()]

    try:
        return _pump.call(job, timeout=20)
    except BrowserUnavailable:
        return []


__all__ = ["BrowserUnavailable", "BrowserTimeout", "Shot", "LOGIN_URL", "HOME_URL", "WIDTH", "HEIGHT",
           "KEYS", "available", "open_store", "shot", "click", "type_text", "key",
           "scroll", "navigate", "see", "look", "remember", "close", "session",
           "sessions", "sid_of"]
