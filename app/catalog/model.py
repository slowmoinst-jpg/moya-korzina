"""Товар сети как его видит сборщик, и контракт самого сборщика."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Iterator


@dataclass
class ChainProduct:
    """Одна строка каталога одной сети. Всё, кроме sku и name, может отсутствовать.

    weight_g — фасовка в граммах или миллилитрах, если сеть её отдаёт числом; если нет,
    сопоставление достанет её из названия само (см. match.py). unit — «pcs» для штучных,
    «kg» для весовых; None — сеть не сказала.
    """
    sku: str
    name: str
    brand: str | None = None
    weight_g: float | None = None
    unit: str | None = None
    category: str | None = None
    url: str | None = None
    image: str | None = None
    barcode: str | None = None
    price: float | None = None
    in_stock: bool | None = None
    # «Товар в каталоге сети есть, подробности не спрашивал». Так сборщик отмечает
    # живыми тысячи уже известных позиций из sitemap, не тратя по запросу на каждую:
    # база продлевает им last_seen, а незнакомые с такой отметкой просто ждут очереди.
    seen_only: bool = False
    # Точка, в которой увидены цена и наличие (storeCode Магнита, код центра METRO).
    # Цена и остаток у сетей свои в каждой точке, и без неё цена второго города
    # затирала цену первого: человек видел чужие цены и чужое наличие.
    point: str | None = None


Progress = Callable[[str], None]

# Артикулы-заглушки: строка чека (app/connectors/history.PREFIX), строка прайса
# (app/pricelist.SKU_PREFIX) и строка справочника data/fallback_prices.csv — она
# пишется как «<код сети>-…» (так сопоставлено демо). Это источники ЦЕНЫ, а не коды
# товара в сети: по такому «артикулу» сеть свой товар не узнает, и класть его в
# корзину или мостить через него единую базу нельзя. Одно правило на всё приложение:
# наряд, карточка покрытия, дозор цен и единая база спрашивают его здесь.
PLACEHOLDER_PREFIXES = ("hist-", "list-")


def placeholder_sku(sku: str | None, store_code: str | None = None) -> bool:
    """Заглушка, а не настоящий артикул сети. store_code — узнать и «<сеть>-…»."""
    text = str(sku or "")
    if text.startswith(PLACEHOLDER_PREFIXES):
        return True
    return bool(store_code) and text.startswith(f"{store_code}-")


class Crawler(ABC):
    """Сборщик каталога одной сети: перечисляет товары, ничего не записывая.

    Правила для всех сборщиков, и они про то, чтобы источник нас пережил: не чаще
    одного запроса в секунду (rate_limit_rps в config.yaml), один поток на сеть,
    пачки и sitemap вместо запроса на каждую карточку. Сборщик каталога читает
    витрину анонимно; вход под учёткой владельца и его корзина живут в его
    браузере (app/shopbrowser, app/collector.py) — это другая дорога к тем же
    данным, а не запрет. Упёрлись в защиту — сборщик поднимает CrawlBlocked с
    объяснением, сеть в этот раз пропускается, а дорогу к ней меняем: браузер
    владельца, его учётка, sitemap, чеки. Остальные сети от этого не страдают.
    """

    code: str = ""
    name: str = ""

    @abstractmethod
    def crawl(self, progress: Progress | None = None) -> Iterator[ChainProduct]:
        """Перечислить каталог. Порядок любой; дубли по sku допустимы — база их сложит."""


class CrawlBlocked(Exception):
    """Сеть закрыла дорогу: капча, антибот, 403. Ломиться тем же темпом в ту же дверь
    бессмысленно и только злит источник — фиксируем состояние и меняем маршрут: браузер
    владельца, его учётка, sitemap, MCP, чеки."""
