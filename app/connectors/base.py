"""Единый интерфейс коннектора цен + реестр магазинов (разделы 3.1 и 5.4 спецификации).

Правило раздела 9: внутренние API магазинов не документированы и могут отвалиться,
падение одного коннектора не должно блокировать расчёт. Поэтому публичные search()/get_prices()
никогда не выбрасывают наружу: ловят всё, пишут logging.warning и отдают fallback либо [].
"""
from __future__ import annotations

import difflib
import logging
import re
import time
from abc import ABC, abstractmethod

from app import config
from app.models import Candidate, Location, PriceSnapshot

log = logging.getLogger(__name__)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_JUNK = re.compile(r"[^0-9a-zа-я]+")


class ConnectorError(Exception):
    """Ошибка слоя коннекторов (неизвестный магазин и т.п.)."""


# ---------- похожесть названий ----------
def normalize(text: str | None) -> str:
    """«ВК/ПОЛ.Сыр СТРАЧАТЕЛЛА мяг.200г» -> «вк пол сыр страчателла мяг 200г»."""
    return _JUNK.sub(" ", (text or "").lower().replace("ё", "е")).strip()


def digits(value: str | None) -> str:
    """Только цифры: штрихкод могут записать с пробелами, дефисами или как число."""
    return re.sub(r"\D", "", str(value or ""))


def similarity(query: str, name: str) -> float:
    """Грубая похожесть 0..1: SequenceMatcher по нормализованным строкам + бонус за вхождение."""
    q, n = normalize(query), normalize(name)
    if not q or not n:
        return 0.0
    ratio = difflib.SequenceMatcher(None, q, n).ratio()
    if q in n or n in q:
        ratio = max(ratio, 0.9)
    return round(min(1.0, ratio), 3)


# ---------- место клиента ----------
def configured_location(store_code: str) -> Location:
    """Запасное место из config.yaml — то, чем продукт жил, пока адрес был один на всех.

    Нужно двум сценариям: одиночной установке «для себя», где адрес и правда один,
    и разработке, где удобно прописать точку в файл. Как только адрес приходит от
    клиента, он побеждает: config — именно запасное значение, а не настройка поверх.
    """
    if store_code == "lenta":
        return Location(
            address=config.get("connectors.lenta_address") or None,
            store_id=config.get("connectors.lenta_store_id") or None,
        )
    if store_code == "magnit":
        return Location(
            store_id=config.get("connectors.magnit_shop_code") or None,
            shop_type=str(config.get("connectors.magnit_shop_type", "ME") or "ME"),
            delivery=bool(config.get("connectors.magnit_delivery", True)),
        )
    return Location()


# ---------- абстракция ----------
class Connector(ABC):
    """Контракт: code, search(query, limit) -> [Candidate], get_prices(skus) -> [PriceSnapshot].

    Место клиента (`location`) коннектор получает при создании и несёт в каждый запрос.
    Не задано — берётся запасное значение из config.yaml, как было до появления адресов.
    """

    code: str = ""

    def __init__(self, code: str | None = None, location: Location | None = None) -> None:
        if code:
            self.code = code
        self.location = location or configured_location(self.code)

    # --- публичные методы: наружу исключений не выпускают ---
    def search(self, query: str, limit: int = 3) -> list[Candidate]:
        try:
            return self._search(query, limit)[:limit]
        except Exception as exc:
            log.warning("%s: search(%r) упал (%s) — пробую fallback", self.code, query, exc)
            try:
                return self._fallback_search(query, limit)
            except Exception as exc2:
                log.warning("%s: fallback тоже не сработал (%s)", self.code, exc2)
                return []

    def search_barcode(self, barcode: str) -> Candidate | None:
        """Товар по штрихкоду — самый надёжный ключ сопоставления, какой бывает.

        Название можно понять двояко («Страчателла» — сыр или мороженое?), штрихкод
        нельзя. Поэтому если магазин умеет искать по нему, этот путь идёт первым.
        None означает «не умеет или не нашёл» и это нормальный ответ.
        """
        code = digits(barcode)
        if not code:
            return None
        try:
            return self._search_barcode(code)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: поиск по штрихкоду %s упал (%s)", self.code, code, exc)
            return None

    def _search_barcode(self, barcode: str) -> Candidate | None:
        """Большинство магазинов по штрихкоду искать не дают. Врать об этом не надо."""
        return None

    def get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        if not skus:
            return []
        try:
            return self._get_prices(list(skus))
        except Exception as exc:
            log.warning("%s: get_prices упал (%s) — пробую fallback", self.code, exc)
            try:
                return self._fallback_prices(list(skus))
            except Exception as exc2:
                log.warning("%s: fallback тоже не сработал (%s)", self.code, exc2)
                return []

    # --- реализуют наследники ---
    @abstractmethod
    def _search(self, query: str, limit: int) -> list[Candidate]:
        ...

    @abstractmethod
    def _get_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        ...

    # --- общий резерв: CSV data/fallback_prices.csv ---
    def _fallback_search(self, query: str, limit: int) -> list[Candidate]:
        from app.connectors.stub import fallback_prices, fallback_search  # noqa: F401  (ленивый импорт)
        return fallback_search(self.code, query, limit)

    def _fallback_prices(self, skus: list[str]) -> list[PriceSnapshot]:
        from app.connectors.stub import fallback_prices
        return fallback_prices(self.code, skus)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} code={self.code!r}>"


# ---------- предохранитель: не ждём таймаут на каждом SKU, если API магазина мёртв ----------
#
# ПАУЗА, А НЕ «ДО КОНЦА СЕАНСА». Пока приложение жило в Streamlit, сеанс был сеансом
# человека. Теперь экран и служба korzina-jobs — долгие процессы от выкладки до
# выкладки, и сеть, выключенная тремя неудачами, оставалась выключенной сутками. А
# неудачи бывают временными: ноутбук владельца уснул, и домашний выход (app/homeexit.py)
# пропал на ночь. Поэтому сеть выключается на connectors.failure_cooldown_min минут, а
# потом получает одну попытку: удалась — снова в строю, нет — ещё одна пауза.
_FAILS: dict[str, int] = {}
_FAILED_AT: dict[str, float] = {}
FAIL_LIMIT_DEFAULT = 3
COOLDOWN_MIN_DEFAULT = 15.0


def _fail_limit() -> int:
    return int(config.get("connectors.failure_threshold", FAIL_LIMIT_DEFAULT) or FAIL_LIMIT_DEFAULT)


def _cooldown_sec() -> float:
    try:
        return float(config.get("connectors.failure_cooldown_min", COOLDOWN_MIN_DEFAULT)
                     or COOLDOWN_MIN_DEFAULT) * 60.0
    except (TypeError, ValueError):
        return COOLDOWN_MIN_DEFAULT * 60.0


def api_disabled(store_code: str) -> bool:
    """API магазина выключен на паузу после серии подряд идущих неудач."""
    if _FAILS.get(store_code, 0) < _fail_limit():
        return False
    if time.time() - _FAILED_AT.get(store_code, 0.0) < _cooldown_sec():
        return True
    # Пауза вышла — одна попытка. Не удастся — note_failure сразу выключит сеть снова.
    _FAILS[store_code] = _fail_limit() - 1
    return False


def note_failure(store_code: str) -> None:
    _FAILS[store_code] = _FAILS.get(store_code, 0) + 1
    _FAILED_AT[store_code] = time.time()
    if _FAILS[store_code] == _fail_limit():
        log.warning("%s: API не ответил %d раза подряд — %.0f мин беру цены без него",
                    store_code, _fail_limit(), _cooldown_sec() / 60.0)


def note_success(store_code: str) -> None:
    _FAILS.pop(store_code, None)
    _FAILED_AT.pop(store_code, None)


def reset_failures() -> None:
    _FAILS.clear()
    _FAILED_AT.clear()


# ---------- коннектор поверх сайта магазина ----------
class HttpCatalogConnector(Connector):
    """Коннектор, который ходит на сайт магазина сам (сегодня — Магнит).

    Здесь общее только то, что действительно общее: браузерные заголовки и
    отметка синтетических артикулов резервного CSV. Поиск и цены у каждой сети
    свои — у Магнита это JSON-шлюз и разбор вёрстки.

    До 26.09.2026 тут жил ещё «универсальный» разбор любого JSON с угадыванием
    копеек по порогу (цена больше 100 000 — значит копейки). Им не пользовался
    никто: Магнит переопределял и поиск, и цены. Угадывание было и неверным —
    12 990 копеек проходили как 12 990 ₽, — так что код удалён целиком, а не
    исправлен: единица цены у каждой сети задаётся явно в её коннекторе.
    """

    api_url: str = ""
    site_url: str = ""
    fallback_sku_prefix: str = ""          # синтетические SKU из CSV, их в API искать бессмысленно

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": self.site_url or "https://ya.ru/",
        }


# ---------- реестр ----------
_REGISTRY: dict[str, type[Connector]] = {}


def register(*codes: str):
    """Декоратор: @register('magnit') над классом коннектора."""
    def wrap(cls: type[Connector]) -> type[Connector]:
        for code in codes or (cls.code,):
            _REGISTRY[code] = cls
        return cls
    return wrap


def _ensure_loaded() -> None:
    """Регистрация коннекторов — импортом их модулей. Все сети, а не часть.

    Здесь не хватало METRO: без импорта всего пакета get_connector("metro")
    отвечал «нет коннектора», хотя он есть. Пятёрочка и Самокат живут в history.
    """
    if not _REGISTRY or "metro" not in _REGISTRY:
        from app.connectors import (  # noqa: F401  (регистрация при импорте)
            dixy, history, lenta, magnit, metro, stub, vkusvill)


def available_codes() -> list[str]:
    _ensure_loaded()
    return sorted(_REGISTRY)


def get_connector(store_code: str, location: Location | None = None) -> Connector:
    """'magnit' | 'vkusvill' | 'lenta' | 'pyaterochka' | 'dixy' | 'stub' -> экземпляр коннектора.

    `location` — где находится клиент. Без него берётся запасное место из config.yaml.
    """
    _ensure_loaded()
    code = (store_code or "").strip().lower()
    cls = _REGISTRY.get(code)
    if cls is None:
        raise ConnectorError(f"нет коннектора для магазина {store_code!r}; известны: {available_codes()}")
    return cls(code, location)
