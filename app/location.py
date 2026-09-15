"""Адрес клиента и точки магазинов, в которые он разрешается.

Зачем отдельный модуль. Цена и наличие у сетей свои в каждой точке, поэтому
спрашивать цены «вообще» нельзя — только за конкретного человека по его адресу.
До этого адрес лежал строкой в config.yaml, то есть был настройкой установки,
одной на всех. Здесь он становится ответом пользователя: хранится в базе,
переживает перезапуск и правится с экрана.

Две ступени, и разделять их важно.

    АДРЕС — то, что человек знает про себя: «Москва, Ходынский бульвар 4».
    ТОЧКА — то, во что адрес превращается у конкретной сети: у Ленты это
            числовой код магазина, который обслуживает этот адрес.

Точка надёжнее адреса: строку сервер разбирает сам и может выбрать не тот
магазин, а код однозначен. Поэтому адрес разрешается в точку ОДИН РАЗ — когда
человек его ввёл, — и дальше запросы идут по коду. Заодно это экономит по
запросу на каждое сравнение.

Запасной путь сохранён: если адрес в базе не задан, коннектор берёт значение из
config.yaml, как было раньше. Установка «для себя», где адрес и правда один,
продолжает работать без единой правки.
"""
from __future__ import annotations

import logging

from app import repo
from app.models import Location

log = logging.getLogger(__name__)

KEY_ADDRESS = "address"                  # адрес клиента, общий для всех сетей
KEY_POINT = "point.{store}"              # код точки этой сети
KEY_POINT_NAME = "point.{store}.name"    # как эта точка называется у сети — для показа человеку

# сети, которые умеют превращать адрес в точку и считать по ней
RESOLVABLE = ("lenta",)


def address() -> str | None:
    """Адрес клиента или None, если он ещё не указан."""
    return repo.get_setting(KEY_ADDRESS)


def save_address(value: str | None) -> None:
    """Запоминает адрес и сбрасывает все разрешённые точки.

    Сброс обязателен: точка, найденная по старому адресу, к новому отношения не
    имеет. Оставить её — значит считать человеку цены магазина, из которого он
    переехал, и по виду ответа этого не заметить.
    """
    previous = address()
    repo.set_setting(KEY_ADDRESS, value)
    if (value or "").strip() != (previous or "").strip():
        for store in RESOLVABLE:
            forget_point(store)


def point(store_code: str) -> tuple[str | None, str | None]:
    """Код и название точки этой сети: (код, название). Оба None — точка не выбрана."""
    return (repo.get_setting(KEY_POINT.format(store=store_code)),
            repo.get_setting(KEY_POINT_NAME.format(store=store_code)))


def save_point(store_code: str, store_id: str | None, name: str | None = None) -> None:
    repo.set_setting(KEY_POINT.format(store=store_code), store_id)
    repo.set_setting(KEY_POINT_NAME.format(store=store_code), name)


def forget_point(store_code: str) -> None:
    save_point(store_code, None, None)


def nearby(store_code: str, addr: str | None = None) -> list[dict]:
    """Точки сети рядом с адресом. Пустой список — адрес не разобран или сеть не ответила.

    Наружу не бросаем: невозможность разрешить адрес — обычное дело (опечатка,
    сеть не работает в этом городе), и ронять из-за неё экран незачем.
    """
    addr = (addr or address() or "").strip()
    if not addr or store_code not in RESOLVABLE:
        return []
    try:
        from app.connectors.lenta import nearest_stores
        return nearest_stores(addr)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: адрес «%s» разобрать не удалось (%s)", store_code, addr, exc)
        return []


def for_store(store_code: str) -> Location | None:
    """Место клиента для этой сети. None — адреса нет, пусть коннектор берёт config.yaml.

    Код точки и адрес отдаются вместе: код — чтобы спрашивать однозначно, адрес —
    чтобы было чем спросить, если код ещё не подобран.
    """
    addr = address()
    if not addr:
        return None
    store_id, _ = point(store_code)
    return Location(address=addr, store_id=store_id)


def is_set() -> bool:
    return bool(address())
