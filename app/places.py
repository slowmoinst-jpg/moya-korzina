"""Точки, по которым приложение вообще ходит за данными.

Решение владельца 16.09.2026: «если мы будем по всем магазинам всех сетей это
делать, это большая нагрузка. Давай ограничимся теми магазинами, адреса которых
пользователь указал». То есть загрузка идёт не от сети («обойдём весь Магнит»),
а от человека: указал адрес — подобрали рядом с ним по точке в каждой сети, и
дальше ходим только туда.

Почему это не только про нагрузку. Каталог, собранный по случайному магазину,
описывает не тот мир, в котором живёт человек: у Магнита в Краснодаре 22,5 тыс.
позиций, в Новосибирске 10,8 тыс., и половина «известных» товаров человеку
недоступна. Точка из его адреса убирает и лишнюю работу, и это враньё разом.

Что здесь есть:

    addresses()      — адреса всех рабочих мест, без повторов;
    points(chain)    — точки этой сети, подобранные по тем адресам;
    all_points()     — то же по всем сетям сразу, для экранов и журналов.

ТОЧКА ЕСТЬ НЕ У ВСЕХ СЕТЕЙ, и это не недоделка. Каталог и цены зависят от точки
только у Магнита и Ленты (BY_POINT). ВкусВилл отвечает одинаково по всей стране —
в его MCP нет ни города, ни магазина. У Дикси поисковый движок вообще без
географии, а цен он не отдаёт. Спрашивать у них «ближайшую точку» бессмысленно,
поэтому points() для них пуст, и читать это надо как «делить нечего», а не как
«ничего не нашли».

ПОТОЛОК. Число точек растёт с числом людей, и обход Магнита — это десять минут на
каждую. Поэтому берём не больше catalog.max_points_per_chain точек: сначала
рабочие места живых людей, демо — последним. Отрезанные точки не пропадают молча,
про них пишется в журнал.

ЗАПАСНОЙ ПУТЬ. Никто ещё не указал адреса (свежий сервер, установка «для себя») —
берём адрес из config.yaml, а не бросаем каталог пустым.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app import config

log = logging.getLogger(__name__)

# Сети, у которых каталог и цены зависят от точки. Остальные отвечают одинаково
# по всей стране, и «ближайшая точка» для них ничего не значит.
BY_POINT = ("magnit", "lenta", "metro", "pyaterochka", "samokat")

DEFAULT_MAX_POINTS = 3


@dataclass(frozen=True)
class Point:
    """Точка сети, попавшая в загрузку, и адрес человека, из-за которого она там."""

    chain: str
    code: str            # код точки в её сети: storeCode Магнита, aliasId хаба Ленты
    label: str           # человеку: адрес самой точки
    address: str         # адрес рабочего места, по которому точка подобрана

    def __str__(self) -> str:
        return f"{self.chain}:{self.code} ({self.label or self.address})"


def addresses() -> list[str]:
    """Адреса всех рабочих мест, без повторов. Пусто нигде — адрес из config.yaml.

    Демо идёт последним: это витрина, и вытеснять ею точку живого человека из-под
    потолка нельзя. Сломанная или пустая база одного места не мешает остальным —
    рабочее место без адреса это норма, а не ошибка.
    """
    from app import repo, users
    from app.location import KEY_ADDRESS

    found: list[str] = []
    keep = config.db_override()
    try:
        names = [p for p in users.list_workspaces() if p != users.DEMO]
        names += [p for p in users.list_workspaces() if p == users.DEMO]
        for phone in names:
            try:
                config.set_db_override(users.db_path_for(phone))
                value = (repo.get_setting(KEY_ADDRESS) or "").strip()
            except Exception as exc:  # noqa: BLE001
                log.warning("адрес рабочего места %s не прочитан (%s)", phone, exc)
                continue
            if value and value not in found:
                found.append(value)
    finally:
        config.set_db_override(keep)

    if not found:
        spare = (config.get("connectors.lenta_address") or "").strip()
        if spare:
            log.info("адресов рабочих мест нет — беру запасной из config.yaml: %s", spare)
            found.append(spare)
    return found


def max_points() -> int:
    try:
        value = int(config.get("catalog.max_points_per_chain") or DEFAULT_MAX_POINTS)
    except (TypeError, ValueError):
        return DEFAULT_MAX_POINTS
    return max(1, value)


def points(chain: str) -> list[Point]:
    """Точки этой сети по адресам рабочих мест. Пусто — точек у сети не бывает.

    Одна точка на адрес и ни одной лишней: два человека из соседних домов дают
    один магазин, и обойти его надо один раз. Сеть, которой рядом с адресом не
    нашлось, просто не даёт точки — обходить в этом городе нечего.
    """
    if chain not in BY_POINT:
        return []
    resolve = _RESOLVERS.get(chain)
    if resolve is None:                                    # pragma: no cover
        return []

    out: list[Point] = []
    seen: set[str] = set()
    cap = max_points()
    for addr in addresses():
        try:
            point = resolve(addr)
        except Exception as exc:  # noqa: BLE001 — сеть молчит, остальные адреса живут дальше
            log.warning("%s: точка для адреса «%s» не подобрана (%s)", chain, addr, exc)
            continue
        if not point or point.code in seen:
            continue
        if len(out) >= cap:
            log.info("%s: точка «%s» за потолком в %d — в этот обход не пойдёт",
                     chain, point.label or addr, cap)
            continue
        seen.add(point.code)
        out.append(point)
    return out


def points_for(address: str, chain: str) -> list[Point]:
    """Точка этой сети для ОДНОГО адреса. Пусто — сети рядом нет или точек у неё не бывает.

    Отдельно от points() затем, что у загрузки по требованию другой вопрос. points()
    отвечает «куда ходить вообще» — по всем рабочим местам сразу. Здесь вопрос уже
    ответа: человек только что указал адрес, и грузить надо ЕГО цены, не дожидаясь
    ночного обхода и не трогая чужие города.
    """
    if chain not in BY_POINT:
        return []
    resolve = _RESOLVERS.get(chain)
    address = " ".join((address or "").split())
    if resolve is None or not address:
        return []
    try:
        point = resolve(address)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: точка для адреса «%s» не подобрана (%s)", chain, address, exc)
        return []
    return [point] if point else []


def all_points() -> dict[str, list[Point]]:
    """Точки по всем сетям, которые вообще их имеют. Для экранов и журналов."""
    return {chain: points(chain) for chain in BY_POINT}


# ---------- как точка подбирается в каждой сети ----------
def _magnit(address: str) -> Point | None:
    """Ближайший продуктовый магазин Магнита: у него и цены, и ассортимент свои."""
    from app.connectors.magnit import nearest_store

    store = nearest_store(address)
    if not store:
        return None
    return Point(chain="magnit", code=str(store["code"]),
                 label=store.get("address") or "", address=address)


def _lenta(address: str) -> Point | None:
    """Хаб доставки Ленты — единственный код, который её витрина считает своим.

    Ближайший магазин тут не годится: Лента сама называет хаб для адреса, и он
    бывает в десяти километрах, тогда как магазин стоит в двухстах метрах и на
    доставке пуст (app/connectors/lenta.py, _where).
    """
    from app.connectors.lenta import delivery_hub, hub_info

    hub = delivery_hub(address)
    if not hub:
        return None
    info = hub_info(address)
    name = ""
    if isinstance(info, dict):
        name = info.get("address") or info.get("name") or ""
    return Point(chain="lenta", code=str(hub), label=name, address=address)


def _metro(address: str) -> Point | None:
    """Ближайший торговый центр METRO.

    Здесь «ближайший» честно значит ближайший, в отличие от Ленты с её хабом
    доставки: центров у сети меньше сотни на страну и единицы на город (в
    Петербурге три), и каждый обслуживает себя сам.
    """
    from app.connectors.metro import nearest_store

    store = nearest_store(address)
    if not store or not store.get("code"):
        return None
    return Point(chain="metro", code=str(store["code"]),
                 label=store.get("address") or "", address=address)


def _pyaterochka(address: str) -> Point | None:
    """Ближайший магазин Пятёрочки: цены и наличие на витрине зависят от точки."""
    from app.connectors.pyaterochka import nearest_store

    store = nearest_store(address)
    if not store or not store.get("code"):
        return None
    return Point(chain="pyaterochka", code=str(store["code"]),
                 label=store.get("address") or "", address=address)


def _samokat(address: str) -> Point | None:
    """Ближайший даркстор (витрина) Самоката: ассортимент и цены привязаны к точке."""
    from app.connectors.samokat import nearest_store

    store = nearest_store(address)
    if not store or not store.get("code"):
        return None
    return Point(chain="samokat", code=str(store["code"]),
                 label=store.get("address") or "", address=address)


_RESOLVERS = {
    "magnit": _magnit,
    "lenta": _lenta,
    "metro": _metro,
    "pyaterochka": _pyaterochka,
    "samokat": _samokat,
}
