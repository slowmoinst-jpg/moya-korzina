"""Сборщики каталогов по сетям и общие для них мелочи: темп, sitemap, HTTP.

Что известно о каждой сети (разведка 16.09.2026 с сервера в России, подробности —
в docs/design/catalog-2026-09-16.md):

  Магнит     — JSON-шлюз витрины magnit.ru/webgate: дерево категорий, товары
               категории страницами по 50; каталог зависит от магазина (storeCode).
  Лента      — полный перечень из sitemap (~80 тыс. id), подробности — карточкой
               через MCP по одной; сайт за Qrator, но sitemap отдаётся свободно.
  ВкусВилл   — перечень из sitemap (~13,6 тыс.), подробности — карточкой через MCP.
  Дикси      — поисковый движок Diginetica, которым живёт сайт: страницы по 400,
               перечисление по единицам измерения и брендам; цен и наличия там нет.
  Fix Price  — только через НАШ браузер: простому запросу витрина отдаёт заглушку
               в 1,8 КБ, окну — страницу с ценами. Товары лежат в состоянии Nuxt,
               по 24 на страницу раздела (замер 20.09.2026).
  Пятёрочка  — программного канала нет: сайт и API закрыты антиботом ServicePipe
               даже с российского адреса. Каталог только из чеков и прайса.
  Самокат    — то же: ServicePipe на сайте и на API. Только чеки.
"""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Iterator

import requests

from app import config
from app.catalog.model import CrawlBlocked, Crawler

log = logging.getLogger(__name__)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


class Pace:
    """Не чаще N запросов в секунду. Одна на сборщик, ждёт перед каждым запросом."""

    def __init__(self, rps: float | None = None) -> None:
        rate = float(rps or config.get("catalog.rate_limit_rps") or 1.0)
        self.gap = 1.0 / max(rate, 0.05)
        self.last = 0.0

    def wait(self) -> None:
        delta = time.time() - self.last
        if delta < self.gap:
            time.sleep(self.gap - delta)
        self.last = time.time()


# Сколько раз пробовать и с какой паузой, когда сеть отвечает отказом.
#
# ПОЧЕМУ ЭТО ВООБЩЕ ЕСТЬ — измерено 19.09.2026 на Дикси, и это стоило целого
# неверного вывода. Его карта товаров сперва отдалась (200, 1,35 МБ), а через
# полтора десятка быстрых запросов подряд начала отвечать 403 НА ВСЁ, включая ту
# самую карту. Мы записали это как «сеть закрылась». Повторный замер с паузой в
# четыре секунды: пять запросов из пяти — 200, по 1,35 МБ, за десятую долю секунды.
#
# То есть 403 бывает не запретом, а просьбой сбавить темп, и отличить одно от
# другого можно единственным способом — подождать и попробовать ещё раз. Один
# отказ больше не считается приговором: приговор выносится после RETRIES попыток.
RETRIES = 4
BACKOFF_SEC = (2.0, 5.0, 12.0)      # паузы между попытками, дальше — последняя
THROTTLED = (403, 429, 500, 502, 503, 504)


def _retry_after(response: requests.Response, default: float) -> float:
    """Сеть может сама сказать, сколько ждать. Сказала — слушаемся её, а не себя."""
    raw = (response.headers.get("Retry-After") or "").strip()
    try:
        asked = float(raw)
    except ValueError:
        return default
    # Потолок в минуту: заголовок бывает и на час, а обход столько не ждёт.
    return max(default, min(asked, 60.0))


def http_get(url: str, pace: Pace | None = None, timeout: float = 60.0,
             retries: int = RETRIES, **kwargs) -> requests.Response:
    """GET с браузерным UA и повтором при отказе.

    401 — это «нужен ключ», ждать бессмысленно, отказ сразу. Остальные отказы из
    THROTTLED пробуются заново с растущей паузой: чаще всего это темп, а не запрет.
    Кончились попытки — наружу CrawlBlocked, и сеть в этот обход пропускается.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": kwargs.pop("accept", "*/*")}
    last = ""
    for attempt in range(max(1, retries)):
        if pace:
            pace.wait()
        try:
            response = requests.get(url, headers=headers, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code == 401:
                raise CrawlBlocked(f"{url}: ответ 401 — нужен ключ, ждать нечего")
            if response.status_code not in THROTTLED:
                response.raise_for_status()
                return response
            last = f"ответ {response.status_code}"
            if attempt + 1 < retries:
                pause = _retry_after(response, BACKOFF_SEC[min(attempt, len(BACKOFF_SEC) - 1)])
                log.info("%s: %s — жду %.0f с и пробую снова (%d из %d)",
                         url, last, pause, attempt + 2, retries)
                time.sleep(pause)
                continue
        if attempt + 1 < retries:
            pause = BACKOFF_SEC[min(attempt, len(BACKOFF_SEC) - 1)]
            log.info("%s: %s — жду %.0f с и пробую снова (%d из %d)",
                     url, last, pause, attempt + 2, retries)
            time.sleep(pause)
    raise CrawlBlocked(f"{url}: {last} после {retries} попыток с паузами")


def sitemap_locs(index_url: str, pace: Pace | None = None, pick: re.Pattern | None = None
                 ) -> Iterator[str]:
    """Адреса из sitemap: индекс → вложенные карты → loc. pick отбирает вложенные карты."""
    root = ET.fromstring(http_get(index_url, pace).content)
    children = [el.text.strip() for el in root.iter() if el.tag.endswith("}loc") and el.text]
    if root.tag.endswith("}urlset"):
        yield from children
        return
    for child in children:
        if pick and not pick.search(child):
            continue
        sub = ET.fromstring(http_get(child, pace).content)
        for el in sub.iter():
            if el.tag.endswith("}loc") and el.text:
                yield el.text.strip()


def registry() -> dict[str, type[Crawler]]:
    """Код сети → класс сборщика. Импорт ленивый, чтобы один сломанный модуль не ронял все."""
    from app.catalog.crawlers.dixy import DixyCrawler
    from app.catalog.crawlers.fixprice import FixPriceCrawler
    from app.catalog.crawlers.lenta import LentaCrawler
    from app.catalog.crawlers.magnit import MagnitCrawler
    from app.catalog.crawlers.metro import MetroCrawler
    from app.catalog.crawlers.monetka import MonetkaCrawler
    from app.catalog.crawlers.perekrestok import PerekrestokCrawler
    from app.catalog.crawlers.vkusvill import VkusvillCrawler
    from app.catalog.crawlers.vprok import VprokCrawler

    return {c.code: c for c in (MagnitCrawler, DixyCrawler, VkusvillCrawler, LentaCrawler,
                                MetroCrawler, PerekrestokCrawler, VprokCrawler, MonetkaCrawler,
                                FixPriceCrawler)}


def make(code: str, points: list | None = None) -> Crawler:
    """Сборщик сети, настроенный на точки людей.

    Точки приходят из app/places.py — их подбирают к адресам рабочих мест. Сеть,
    у которой точек не бывает (ВкусВилл, Дикси — один ответ на всю страну),
    получает сборщик как раньше, без аргументов.

    Магниту точек можно много: ассортимент у него в каждом магазине свой, и обойти
    надо каждую. Ленте — одну: перечень товаров она отдаёт общий на страну в
    sitemap, а от хаба зависят только карточки, и второй хаб удвоил бы запросы, не
    добавив каталогу ни строки.
    """
    cls = registry().get(code)
    if not cls:
        raise KeyError(f"для сети «{code}» сборщика нет")
    codes = [p.code for p in (points or [])]
    if not codes:
        return cls()
    if code in ("magnit", "metro"):
        # У обоих ассортимент и остаток свои в каждой точке, значит обойти надо каждую.
        return cls(codes)
    if code == "lenta":
        return cls(codes[0])
    return cls()
