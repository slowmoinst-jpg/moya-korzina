"""Перекрёсток: каталог из карты сайта. Без цен — и это не недоделка.

ЧТО ПРОВЕРЕНО 19.09.2026 С БОЕВОГО СЕРВЕРА, РОССИЙСКИЙ АДРЕС. У сети открыта
карта сайта и закрыто всё остальное:

    sitemap.xml, sitemap-products*.xml   200, одна часть 8,7 МБ, 45 000 адресов
    карточка товара                      200, но 1843 байта — заглушка антибота
    страница акции                       то же, цен в разметке ноль
    api/customer/1.4.1.0/catalog/tree    403
    api/customer/1.4.1.0/catalog/feed    403

То есть **названия и артикулы достижимы, цены нет**. Сборщик берёт первое и не
делает вид, что знает второе: `price` и `in_stock` остаются пустыми, и расчёт
честно видит, что цены у этой сети нет.

ЗАЧЕМ ТОГДА ЭТОТ КАТАЛОГ. Затем, что строку из чека надо опознать. Человек
покупает в Перекрёстке, чек приходит из ФНС, и без каталога сети мы не знаем ни
артикула этой позиции, ни того, что её вообще возит Перекрёсток. С каталогом —
знаем, и цена берётся из его же чека.

ПОЧЕМУ ЭТО ДЁШЕВО. Никаких походов за карточками: имя, артикул и категория лежат
в САМОМ адресе товара —

    /cat/121/p/maslo-slivocnoe-icalki-krestanskoe-72-5-500g-3636734
     категория ^^^        название ^^^^^^^^^^^^^^^^^^^^^^^^  артикул ^^^^^^^

Весь каталог сети — это пять запросов, а не сто тысяч. Разбор 45 000 адресов
одной части дал 100 % разбора и 320 категорий.

ГДЕ ЭТОТ СБОРЩИК РАБОТАЕТ, А ГДЕ НЕТ. Замер 19.09.2026: с боевого сервера карта
отдаётся (200 и 8,7 МБ), с домашней машины владельца та же карта тем же адресом —
403. Сеть смотрит не только на User-Agent (без него 403 и на сервере), но и на
то, откуда пришли. Для дела это ничего не меняет: обход каталога живёт в
контейнере `korzina-jobs` на сервере, там он и проверен. Но локальный прогон
«у себя» может честно упереться в 403, и это не поломка сборщика.

ПРО ЛАТИНИЦУ, И ЭТО ГЛАВНОЕ ОГРАНИЧЕНИЕ. Названия в адресе латинские и без
диакритики: «moloko novaa derevna pasterizovannoe 2 5 1l» — это «Молоко Новая
деревня пастеризованное 2,5 % 1 л». Обратно в кириллицу мы их НЕ переводим, и это
решение, а не лень: обратная транслитерация неоднозначна («c» — это «с» или «ц»?)
и породила бы выдуманные слова. Наш сопоставитель приводит кириллицу к латинице
сам (app/matcher/normalize.py::translit), так что сравнение идёт на одном языке.

Но схемы транслитерации разные: сеть пишет «novaa», наш перевод даёт «novaya»,
«ж» у неё «z», у нас «zh». Значит совпадение будет неточным, и качество
сопоставления по этой сети надо мерить отдельно, а не считать решённым.
"""
from __future__ import annotations

import logging
import re
from typing import Iterator

from app import config
from app.catalog.crawlers import Pace, sitemap_locs
from app.catalog.model import ChainProduct, Crawler, Progress

log = logging.getLogger(__name__)

SITEMAP = "https://www.perekrestok.ru/sitemap.xml"
# Адрес, которым живёт сама витрина. Взят из открытого клиента
# github.com/Open-Inflation/perekrestok_api (manager.py, CATALOG_VERSION 1.4.1.0).
API = "https://www.perekrestok.ru/api/customer/1.4.1.0"
PER_PAGE = 100
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
# Из индекса берём только карты товаров: там же лежат отзывы, бренды и магазины,
# и качать их незачем — это мегабайты мимо цели.
PRODUCT_MAPS = re.compile(r"sitemap-products")
# /cat/<категория>/p/<название-с-фасовкой>-<артикул>
ADDRESS = re.compile(r"/cat/(\d+)/p/(.+?)-(\d+)/?$")


def _price_of(item: dict) -> tuple[str, dict] | None:
    """Артикул и цена из товара описи. None — товар без того или другого.

    Поля описи живьём не видены: проверку сети ещё никто не проходил. Поэтому
    имена берутся с запасом — сеть вправе звать цену и «price», и «priceRegular»,
    — а при любом несовпадении товар молча пропускается. Пустая цена здесь лучше
    выдуманной: приложение обещает считать по настоящим деньгам.
    """
    sku = str(item.get("plu") or item.get("id") or "").strip()
    if not sku:
        return None
    raw = None
    for key in ("priceWithCard", "price", "priceRegular", "cost"):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("value") or value.get("amount")
        if value not in (None, "", 0):
            raw = value
            break
    try:
        price = round(float(str(raw).replace(",", ".")), 2)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    # Копейки целым числом — обычная манера витрин. Отличаем по порядку величины:
    # продуктов дороже двадцати тысяч рублей за штуку у этой сети не бывает.
    if price > 20000:
        price = round(price / 100.0, 2)
    stock = item.get("inStock")
    if stock is None:
        stock = item.get("isAvailable")
    return sku, {"price": price,
                 "in_stock": None if stock is None else bool(stock)}


def parse(url: str) -> ChainProduct | None:
    """Товар из адреса. None — адрес не товарный (раздел, фильтр, что угодно ещё)."""
    match = ADDRESS.search(url)
    if not match:
        return None
    category, slug, article = match.group(1), match.group(2), match.group(3)
    name = slug.replace("-", " ").strip()
    if not name or not article:
        return None
    return ChainProduct(sku=article, name=name, category=category, url=url)


class PerekrestokCrawler(Crawler):
    code = "perekrestok"
    name = "Перекрёсток"

    def __init__(self) -> None:
        self.pace = Pace(chain=self.code)
        # Потолок на случай, если сеть однажды выложит карту на миллион строк:
        # база растёт, а польза от хвоста падает. 0 — без потолка.
        self.cap = int(config.get("catalog.perekrestok.max_products") or 0)
        # Потолок на поход за ценами: страница по сто товаров. Отдельный от потолка
        # названий, потому что названия почти бесплатны (пять запросов на всю сеть),
        # а каждая страница цен — это отдельный запрос под чужой проверкой.
        self.max_price_pages = int(config.get("catalog.perekrestok.max_price_pages") or 200)

    def crawl(self, progress: Progress | None = None) -> Iterator[ChainProduct]:
        say = progress or (lambda msg: None)
        say("карта сайта: беру только части с товарами")
        prices = self._prices(say)
        seen: set[str] = set()
        skipped = 0
        for url in sitemap_locs(SITEMAP, self.pace, PRODUCT_MAPS):
            product = parse(url)
            if not product:
                skipped += 1
                continue
            if product.sku in seen:
                continue
            seen.add(product.sku)
            known = prices.get(product.sku)
            if known:
                product.price = known.get("price")
                product.in_stock = known.get("in_stock")
            yield product
            if self.cap and len(seen) >= self.cap:
                say(f"потолок в {self.cap} товаров — дальше не иду")
                return
            if len(seen) % 20000 == 0:
                say(f"разобрано {len(seen)} товаров")
        say(f"всего товаров {len(seen)}, нетоварных адресов пропущено {skipped}")

    def _prices(self, say) -> dict[str, dict]:
        """Цены под пройденной человеком проверкой. Нет проверки — пустой словарь.

        ОТКУДА БЕРЁТСЯ ПРАВО СЮДА ХОДИТЬ. Карточку эта сеть нашему серверу не
        отдаёт: вместо неё приходит заглушка в 1 837 байт, а на её месте стоит
        головоломка «разверните картинку горизонтально» с нашим адресом на
        странице (замер 20.09.2026, сама за 55 секунд не проходится). Решает её
        ЧЕЛОВЕК — в окне магазина, протяжкой ползунка, — и сеть выдаёт его окну
        куку допуска. Она и лежит в рабочем месте, если он нажал «Запомнить вход».

        Отсюда правило: нет сохранённой проверки — цен нет, и сборщик честно
        отдаёт одни названия, как отдавал раньше. Он НЕ пытается пройти проверку
        сам и не подставляет ничего, кроме того, что сеть выдала окну.

        ЧЕГО ЗДЕСЬ ПОКА НЕТ. Живой проверки не было ни разу: владелец головоломку
        ещё не проходил. Значит этот путь написан, но не измерен, и первое, что он
        обязан делать при любой неожиданности, — молчать и уступать дорогу
        названиям. Поэтому весь он обёрнут в try и любой сбой гасит только цены.
        """
        try:
            from app.shopbrowser import store as shopstore

            state = shopstore.any_saved(self.code)
        except Exception as exc:  # noqa: BLE001 — без цен сборщик работает как раньше
            log.warning("perekrestok: сохранённая проверка не прочиталась (%s)", exc)
            return {}
        if not state:
            say("проверка сети никем не пройдена — беру только названия, без цен")
            return {}

        cookies = {c.get("name"): c.get("value") for c in (state.get("cookies") or [])
                   if c.get("name")}
        if not cookies:
            say("в сохранённой проверке нет ни одной куки — иду без цен")
            return {}
        say(f"иду за ценами под пройденной проверкой ({len(cookies)} кук)")
        try:
            return self._ask_api(cookies, say)
        except Exception as exc:  # noqa: BLE001
            log.warning("perekrestok: цены под проверкой не взялись (%s)", exc)
            say(f"цены под проверкой не взялись ({type(exc).__name__}) — остаются названия")
            return {}

    def _ask_api(self, cookies: dict, say) -> dict[str, dict]:
        """Опись товаров с ценами через тот же адрес, которым живёт сама витрина.

        Адрес взят из открытого клиента github.com/Open-Inflation/perekrestok_api:
        www.perekrestok.ru/api/customer/1.4.1.0, метод /catalog/product/feed. Мы
        берём оттуда ТОЛЬКО перечень адресов, а ходим своим кодом и своим темпом.
        """
        import requests

        out: dict[str, dict] = {}
        session = requests.Session()
        session.cookies.update(cookies)
        session.headers.update({"User-Agent": UA, "Accept": "application/json",
                                "Referer": "https://www.perekrestok.ru/"})
        page = 1
        while page <= self.max_price_pages:
            self.pace.wait()
            answer = session.post(f"{API}/catalog/product/feed", timeout=30,
                                  json={"page": page, "perPage": PER_PAGE})
            if answer.status_code != 200:
                say(f"сеть ответила {answer.status_code} на странице {page} — "
                    "проверка, похоже, уже не действует")
                break
            items = (((answer.json() or {}).get("content") or {}).get("items")) or []
            if not items:
                break
            for item in items:
                got = _price_of(item)
                if got:
                    out[got[0]] = got[1]
            if len(items) < PER_PAGE:
                break
            page += 1
        say(f"цен под проверкой собрано: {len(out)}")
        return out
