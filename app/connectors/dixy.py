"""Коннектор Дикси. Каталог — через их поисковый движок, цена — со страницы товара.

Сам dixy.ru встречает незнакомый адрес страницей «Поставь галочку в поле „Я не
робот"». Ломиться в неё незачем — она рассчитана на человека. Но каталог Дикси живёт
не только там: поиск на их сайте работает на стороннем движке Diginetica, и он
отвечает с собственных хостов, куда защита Дикси не распространяется.

Отсюда разделение труда:

  ПОИСК — autocomplete.diginetica.net. Отдаёт название, бренд, категорию,
  признак available и идентификатор товара — тот самый, что стоит в адресе
  карточки на dixy.ru. Цену Дикси в этот движок не отдаёт вовсе: там всегда
  «0.0», потому что цена у них своя в каждом магазине («Цена в конкретном
  магазине» написано прямо на карточке).

  ЦЕНА — страница товара dixy.ru/catalog/<категория>/<id>/. Разметка разобрана по
  настоящему слепку страницы, он лежит в tests/fixtures/dixy_product.html и на нём
  же проверяется разбор:

      <div class="card-line detail" data-bsk_data="242556 1 1 шт 0" ...>
        <div class="card__price-num">420.<span>90</span><span class='rub'>руб.</span></div>

  Рубли и копейки разнесены по узлам, единица измерения спрятана в data-bsk_data.

ПРО ДОСТУП К ЦЕНАМ. Защита Дикси (Qrator) смотрит на адрес. Серверу в дата-центре
она отвечает 403 «Поставь галочку в поле „Я не робот“» или заглушкой «Не удалось
загрузить сайт», а через домашний интернет владельца (app/homeexit.py) отдаёт
карточку целиком. Поэтому карточка идёт через домашний выход, как листинг у
сборщика каталога. Поиск в Diginetica через дом не идёт: движок отвечает серверу
сам, и гонять его через адрес владельца незачем.

ЧЕРЕЗ ДОМ — НАСТОЯЩИМ БРАУЗЕРОМ, А НЕ ПРОСТЫМ ЗАПРОСОМ. Замер 27.09.2026: простые
запросы карточек через выход Qrator пропустил двенадцать раз подряд, по одному в две
секунды, а на тринадцатом показал «Поставь галочку в поле „Я не робот“». Проверку он
показал не нам одним, а всему адресу владельца, настоящему браузеру тоже. Сборщик
каталога тем временем листает витрину браузером, выполнившим скрипты Qrator, по
запросу в секунду: 25.09.2026 он прошёл так 500 страниц без единого отказа. Поэтому
через дом заход открывает Chromium (_Browser): главная страница без картинок, видео и
шрифтов, потом карточки её же запросом изнутри страницы, с её куками. Выхода нет —
простой запрос напрямую, как было до выхода. Сервер Дикси не пускает, но ставить сюда
браузер ради заведомого отказа незачем.

ЦЕНА — ИЗ JSON КАРТОЧКИ. Замер 27.09.2026 через туннель: карточка несёт тот же
JSON товара, что витрина отдаёт листингом (initElementData({...}): priceSimple,
oldPriceSimple, canBuy, hidePrice, symbol). Разбирает его тот же shelf_product, что у
сборщика каталога (app/catalog/crawlers/dixy.py), так что правка схемы Дикси чинится в
одном месте. Заодно пришло наличие: canBuy=false вместе с bskState «not» и
quantity 0 — «сейчас не купить». Прежний разбор вёрстки (card__price-num) остался
запасным.

ПОЧЕМУ КАРТОЧКА, А НЕ ЛИСТИНГ. Листингу (ajax/listing-json.php) браузер нужен так же:
простому запросу он отвечает 403 даже с куками сессии. Разница в объёме. Товары
корзины разбросаны по разделам, где до 43 страниц по 30 товаров, а раздела и страницы
товара мы не знаем, так что за дюжиной позиций пришлось бы листать сотни страниц.
Карточка — один запрос на товар, около 21 КБ сжатого HTML. Для всего каталога
(14 тыс. позиций) дешевле листинг, и его листает ночной обход.

Отказ, проверка или заглушка вместо карточки — сигнал для всего захода. Остальные
позиции берутся из прайса и чеков (app/connectors/history.py), а сайт в этот раз больше
не спрашиваем: следующая карточка получила бы тот же ответ. Отказ через дом ставит
сеть на паузу для всех процессов (homeexit.note_refusal): стучать в помеченный адрес
значит продлевать метку. Отказ в кэш не кладётся: пройдёт пауза или поднимется туннель —
первый же запрос возьмёт карточку, без шести часов ожидания.
"""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime
from typing import Any

from app import config, homeexit, pricelist
from app.connectors.base import (
    USER_AGENT,
    api_disabled,
    note_failure,
    note_success,
    register,
    similarity,
)
from app.connectors import smart_extract
from app.connectors.cache import cache_get, cached_call
from app.connectors.history import HistoryConnector
from app.models import Candidate, PriceSnapshot

log = logging.getLogger(__name__)

SITE_URL = "https://dixy.ru"
SEARCH_URL = "https://autocomplete.diginetica.net/autocomplete"
# Ключ и номер площадки открыто лежат в cdn.diginetica.net/2164/client.js,
# который сайт подключает каждому посетителю. Секретом они не являются.
DEFAULT_API_KEY = "97PGH0KXFA"
DEFAULT_SITE_ID = "2164"

# рубли и копейки разнесены: «420.<span>90</span>»
_PRICE = re.compile(r'card__price-num"[^>]*>\s*([\d\s]+)[.,]?\s*(?:<span[^>]*>(\d{1,2})</span>)?', re.S)
# «242556 1 1 шт 0» — четвёртым идёт единица измерения
_BSK = re.compile(r'data-bsk_data="[^"]*?\s(шт|кг|л|г)\s', re.I)
_NO_PRICE = re.compile(r"(нет в наличии|товар закончил|не продаётся)", re.I)

# JSON товара в карточке: module.initElementData({"id":242556,"xml_id":"2000329470",…}).
_CARD_JSON = "initElementData("
# Признаки настоящей карточки. У отказа Qrator и у заглушки «Не удалось загрузить сайт»
# нет ни одного из них: там одна фраза и картинка.
_CARD_MARKS = (_CARD_JSON, "data-bsk_data", "card__price-num")


def _settings() -> dict[str, Any]:
    return {
        "apiKey": str(config.get("connectors.dixy_api_key", DEFAULT_API_KEY) or DEFAULT_API_KEY),
        "sid": str(config.get("connectors.dixy_site_id", DEFAULT_SITE_ID) or DEFAULT_SITE_ID),
    }


def _unit(name: str, page: str = "") -> str:
    match = _BSK.search(page or "")
    if match:
        return "kg" if match.group(1).lower() == "кг" else "pcs"
    return "kg" if re.search(r"\bвесов\w*|/\s*кг\b", name or "", re.I) else "pcs"


def product_url(item: dict) -> str | None:
    """Адрес карточки: категория из поискового движка плюс идентификатор товара.

    Именно так устроены адреса каталога Дикси — проверено по слепкам:
    dixy.ru/catalog/alkogolnye-napitki/belye-vina/2000175281/
    """
    sku = str(item.get("id") or "").strip()
    if not sku:
        return None
    cats = [c for c in (item.get("categories") or []) if c.get("link_url")]
    if not cats:
        return None
    link = str(cats[0]["link_url"]).strip("/")
    return f"{SITE_URL}/{link}/{sku}/"


def parse_price(page: str) -> float | None:
    """Цена со страницы товара. None — если цены на странице нет."""
    if not page or _NO_PRICE.search(page):
        return None
    match = _PRICE.search(page)
    if not match:
        return None
    rubles = re.sub(r"\s", "", match.group(1) or "")
    kopecks = match.group(2) or "0"
    try:
        return round(float(f"{rubles}.{kopecks:0<2.2s}"), 2)
    except ValueError:
        return None


def is_card(page: str | None) -> bool:
    """Пришла ли карточка товара, а не отказ, проверка или заглушка."""
    return bool(page) and any(mark in page for mark in _CARD_MARKS)


def card_json(page: str | None) -> dict | None:
    """JSON товара из карточки — тот же, что витрина отдаёт листингом. None — его нет."""
    start = (page or "").find(_CARD_JSON)
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(page[start + len(_CARD_JSON):].lstrip())
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def read_card(page: str) -> dict | None:
    """Цена, наличие, единица и название с карточки. None — не разобрано ничем.

    Цена None при разобранном JSON — это ответ витрины (hidePrice, нулевая цена), а не
    поломка разбора: модель здесь спрашивать не о чем. Наличие None — страница о нём
    молчит (так бывает у прежнего разбора вёрстки): это «не знаю», а не «нет».
    """
    data = card_json(page)
    if data is not None:
        from app.catalog.crawlers.dixy import shelf_product

        product = shelf_product(data)
        if product is not None:
            return {"price": product.price, "in_stock": product.in_stock,
                    "unit": product.unit or _unit(product.name, page), "name": product.name}
    price = parse_price(page)
    if price is None:
        return None
    return {"price": price, "in_stock": None, "unit": _unit("", page), "name": None}


def is_product_url(url: str | None, sku: str) -> bool:
    """Рабочий адрес карточки: /product/<название>-<артикул>/."""
    return bool(url) and bool(re.search(rf"/product/[^/?#]+-{re.escape(str(sku))}/?$", url))


def is_bare_url(url: str | None, sku: str) -> bool:
    """/product/<артикул>/ — так адрес собирает поисковый движок каталога, а сайт отвечает 404."""
    return bool(url) and bool(re.search(rf"/product/{re.escape(str(sku))}/?$", url))


# Столько ждать после главной: скрипты Qrator ставят куки, витрина дорисовывается.
SETTLE_MS = 3000
# Карточка изнутри страницы — с пределом по времени. У page.evaluate своего предела нет:
# запрос, повисший в туннеле, держал бы поток дозора и открытый браузер вечно.
CARD_TIMEOUT_MS = 30000
FETCH_CARD_JS = """async ([u, ms]) => { const c = new AbortController();
    const t = setTimeout(() => c.abort(), ms);
    try { const r = await fetch(u, {credentials: 'include', signal: c.signal});
          return [r.status, await r.text()]; }
    finally { clearTimeout(t); } }"""


class _Browser:
    """Вкладка Chromium через домашний выход — на один заход за ценами.

    Открывает главную: скрипты Qrator выполняются и ставят куки, как у человека. Потом
    спрашивает карточки её же запросом изнутри страницы, как сборщик листает витрину:
    с её куками и адресом. Картинки, видео и шрифты через дом не качаются (_no_media
    сборщика). guard — что пришло вместо главной: «check», «blocked» или пусто.
    """

    def __init__(self, proxy_url: str) -> None:
        from playwright.sync_api import sync_playwright

        from app.catalog.crawlers.dixy import _no_media
        from app.shopbrowser import signals

        self._pw = sync_playwright().start()
        self._browser = None
        try:
            self._browser = self._pw.chromium.launch(headless=True, args=["--no-sandbox"],
                                                     proxy={"server": proxy_url})
            context = self._browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow",
                                                viewport={"width": 1366, "height": 900})
            context.route("**/*", _no_media)
            self._page = context.new_page()
            self._page.goto(f"{SITE_URL}/", wait_until="domcontentloaded", timeout=60000)
            self._page.wait_for_timeout(SETTLE_MS)
            self.guard = signals.guard_kind(self._page.inner_text("body")[:6000])
        except Exception:
            self.close()
            raise

    def get(self, url: str) -> tuple[str | None, int]:
        """Страница и код — как у DixyConnector._get: не 200 — страницы нет.

        Не уложился запрос в CARD_TIMEOUT_MS — evaluate бросает, и заход кончается как
        сбой связи: без паузы, остальное из прайса и чеков.
        """
        status, body = self._page.evaluate(FETCH_CARD_JS, [url, CARD_TIMEOUT_MS])
        status = int(status or 0)
        return (body if status == 200 else None), status

    def close(self) -> None:
        for step in (getattr(self._browser, "close", None), self._pw.stop):
            try:
                if step:
                    step()
            except Exception:  # noqa: BLE001 — закрыть не вышло: процесс браузера умрёт с заходом
                pass


class _Visit:
    """Один заход за ценами: какой дорогой идём к dixy.ru и не отказал ли он уже.

    Дорога выбирается раз на заход, а не на товар. Домашний выход жив — браузер через
    него (_Browser), и открывается он лениво: все карточки в кэше — браузер не нужен.
    Выхода нет — простой запрос напрямую, как было до выхода. Отказ, проверка или
    заглушка вместо карточки — ответ сразу на весь заход: сеть смотрит на адрес, и
    следующая карточка получила бы то же самое. Отказ через дом ставит сеть на паузу для
    всех процессов (homeexit.note_refusal).
    """

    def __init__(self, code: str, home: str | None, refused: bool = False) -> None:
        self.code = code
        self.home = home
        self.refused = refused
        self._browser: _Browser | None = None

    def get(self, connector: "DixyConnector", url: str) -> tuple[Any | None, int]:
        if self.home is None:
            return connector._get(url)          # напрямую, как до домашнего выхода
        try:
            if self._browser is None:
                self._browser = _Browser(self.home)
            if self._browser.guard:
                return None, 403                # проверка вместо главной — дальше не идём
            page, status = self._browser.get(url)
        except Exception as exc:  # noqa: BLE001 — браузер не открылся: цены из прайса и чеков
            log.warning("%s: браузер через домашний выход не отдал %s (%s: %s)", self.code, url,
                        type(exc).__name__, str(exc)[:160])
            note_failure(self.code)
            return None, 0
        if status in (200, 404):
            note_success(self.code)
        else:
            note_failure(self.code)
        return page, status

    def refuse(self, status: int) -> None:
        self.refused = True
        way = "через домашний выход" if self.home else "напрямую, без домашнего выхода"
        log.warning("%s: dixy.ru не отдал карточку %s (ответ %s) — остальные позиции этого "
                    "захода берутся из прайса и чеков, сайт в этот раз больше не спрашиваем",
                    self.code, way, status or "не пришёл")
        # Проверка или отказ через дом — пауза для всех. Сбой связи (ответа нет) паузы не
        # стоит: это не метка на адресе, и повторит его предохранитель.
        if self.home and status:
            homeexit.note_refusal(self.code)

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None


@register("dixy")
class DixyConnector(HistoryConnector):
    """Дикси: каталог из Diginetica, цена с карточки через домашний выход, запас — прайс и чеки."""

    code = "dixy"
    site_url = SITE_URL

    # --- сеть ---
    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": f"{SITE_URL}/",
        }

    def _get(self, url: str, params: dict | None = None) -> tuple[Any | None, int]:
        """Ответ и код простым запросом. Наружу не бросает — это штатный сценарий раздела 9.

        Им идут поиск в Diginetica и карточка без домашнего выхода; через дом карточку
        берёт браузер (_Browser). 404 — ответ «такой карточки нет», а не обрыв связи:
        предохранитель на него не реагирует, как и у Магнита.
        """
        if api_disabled(self.code):
            return None, 0
        try:
            import requests
        except ImportError:  # pragma: no cover
            return None, 0
        timeout = float(config.get("connectors.timeout_sec", 10) or 10)
        try:
            resp = requests.get(url, params=params, headers=self._headers(), timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: запрос к %s не удался (%s)", self.code, url, exc)
            note_failure(self.code)
            return None, 0
        if resp.status_code == 404:
            note_success(self.code)          # сайт ответил, просто по этому адресу ничего нет
            return None, 404
        if resp.status_code != 200:
            log.warning("%s: %s ответил %s", self.code, url, resp.status_code)
            note_failure(self.code)
            return None, resp.status_code
        note_success(self.code)
        if "json" in (resp.headers.get("content-type") or ""):
            try:
                return resp.json(), 200
            except ValueError:
                return None, 200
        return resp.text, 200

    # --- поиск ---
    def _to_candidate(self, item: dict) -> Candidate | None:
        sku = str(item.get("id") or "").strip()
        name = html.unescape(str(item.get("name") or "")).strip()
        if not sku or not name:
            return None
        return Candidate(
            store_code=self.code,
            sku=sku,
            name=name,
            price=None,                     # цену поисковый движок не знает, она в карточке
            unit=_unit(name),
            url=product_url(item),
        )

    def _search(self, query: str, limit: int) -> list[Candidate]:
        # В кэш идёт только ответ движка. Пара (None, 403), которая лежала там раньше,
        # не None — и шесть часов выдавала неудачу за «ничего не нашлось».
        answer = cached_call(self.code, f"find:{query}:{limit}", lambda: self._get(
            SEARCH_URL, {"st": query, "num_products": max(limit, 5), "num_suggestions": 0,
                         **_settings()})[0])
        items = (answer or {}).get("products") if isinstance(answer, dict) else None
        found = [c for c in (self._to_candidate(i) for i in items or []) if c]
        if not found:
            log.info("%s: поиск ничего не дал по «%s» — беру прайс и чеки", self.code, query)
            return super()._search(query, limit)
        for candidate in found:
            candidate.score = similarity(query, candidate.name)
        found.sort(key=lambda c: c.score, reverse=True)
        return found[:limit]

    # --- цены ---
    def _card_price(self, sku: str, visit: _Visit) -> dict | None:
        """Цена с карточки: {'price', 'in_stock', 'unit', 'name', 'at'} или None.

        'at' — когда карточку сняли с сайта. Из кэша она приходит и часом старше, и
        снимок цены несёт это время, а не «сейчас»: иначе цена из кэша прожила бы в
        расчёте лишние часы (app/freshness.py). В кэш ложится разобранный ответ, а не
        страница в сотню килобайт, и никогда — отказ: поднявшийся туннель должен
        сработать на следующем же запросе, а не через срок кэша.

        После отказа в этом заходе сайт не спрашиваем, но снятое раньше из кэша берём.
        """
        key = f"card:{sku}"
        if visit.refused:
            return cache_get(self.code, key)
        url = self._card_url(sku)
        if not url:
            return None

        def ask() -> dict | None:
            page, status = visit.get(self, url)
            at = datetime.now().isoformat(timespec="seconds")
            if status == 404:
                log.info("%s: карточки %s по адресу %s нет — цены с неё не будет", self.code, sku, url)
                return {"price": None, "in_stock": None, "unit": None, "name": None, "at": at}
            if not isinstance(page, str) or not is_card(page):
                visit.refuse(status)
                return None
            got = read_card(page)
            if got is None:
                # карточка пришла, а цены в ней не видно — вёрстка могла смениться
                guess = smart_extract.price_from_html(self.code, page, f"артикул {sku}")
                got = {"price": guess["price"] if guess else None,
                       "in_stock": guess["in_stock"] if guess else None,
                       "unit": guess["unit"] if guess else None, "name": None}
            return {**got, "at": at}

        return cached_call(self.code, key, ask)

    def _card_url(self, sku: str) -> str | None:
        """Адрес, по которому сайт ответит карточкой, а не 404 и не переадресацией.

        Сопоставление хранит адрес таким, каким его дал источник: карта сайта и витрина —
        рабочий /product/<название>-<артикул>/, прежний поиск — /catalog/<раздел>/<артикул>/
        (сайт переадресует на рабочий: лишний запрос через дом), движок каталога —
        /product/<артикул>/ (404). Рабочий адрес бывает и в общем каталоге: обход витрины
        дописывает его к строке. Голый адрес не спрашиваем вовсе — ответ известен.
        """
        stored = self._known_url(sku)
        if is_product_url(stored, sku):
            return stored
        from app.catalog import store as catalog

        known = catalog.product_url(self.code, sku)
        if is_product_url(known, sku):
            return known
        if stored and not is_bare_url(stored, sku):
            return stored
        return None

    def _known_url(self, sku: str) -> str | None:
        """Адрес карточки, сохранённый матчером при сопоставлении."""
        from app import repo
        from app.db import get_conn

        store = repo.get_store(self.code)
        if not store:
            return None
        with get_conn() as conn:
            row = conn.execute("SELECT url FROM store_products WHERE store_id=? AND sku=?",
                               (store.id, str(sku))).fetchone()
        return (row["url"] if row else None) or None

    def _visit(self) -> _Visit:
        """Дорога этого захода. Выхода нет — for_chain отдаёт None, и карточка идёт напрямую.

        Сайт не спрашиваем вовсе, если предохранитель на паузе или сеть через дом недавно
        показала проверку: снятое раньше берётся из кэша, остальное — из прайса и чеков.
        """
        home = homeexit.for_chain(self.code)
        paused = api_disabled(self.code) or (home is not None
                                             and homeexit.paused_until(self.code) is not None)
        return _Visit(self.code, home, refused=paused)

    def _get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        out: list[PriceSnapshot] = []
        rest: list[str] = []
        listed = {row["sku"] for row in pricelist.load(self.code)}
        visit: _Visit | None = None
        try:
            for sku in skus:
                if sku in listed or str(sku).startswith(("hist-", self.fallback_sku_prefix)):
                    rest.append(sku)             # прайс и чеки разберёт родитель
                    continue
                if visit is None:
                    visit = self._visit()
                got = self._card_price(str(sku), visit)
                if not got or got.get("price") is None:
                    rest.append(sku)
                    continue
                price = float(got["price"])
                stock = got.get("in_stock")
                out.append(PriceSnapshot(
                    store_code=self.code,
                    sku=str(sku),
                    price=price,
                    price_per_kg=price if got.get("unit") == "kg" else None,
                    # Промолчала страница о наличии — это «не знаю», а не «нет».
                    in_stock=True if stock is None else bool(stock),
                    fetched_at=got.get("at") or datetime.now().isoformat(timespec="seconds"),
                    name=got.get("name"),
                ))
        finally:
            if visit is not None:
                visit.close()
        if rest:
            out.extend(super()._get_prices(rest))
        return out
