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


Progress = Callable[[str], None]


class Crawler(ABC):
    """Сборщик каталога одной сети: перечисляет товары, ничего не записывая.

    Правила для всех сборщиков, и они про то, чтобы источник нас пережил: не чаще
    одного запроса в секунду (rate_limit_rps в config.yaml), только чтение каталога,
    без входов и корзин — вход живёт там, где живёт учётка владельца, в его браузере
    (app/collector.py). Упёрлись в защиту — сборщик поднимает CrawlBlocked с
    объяснением, и сеть в этот раз пропускается, а дорогу к ней ищем отдельно;
    остальные сети от этого не страдают.
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
