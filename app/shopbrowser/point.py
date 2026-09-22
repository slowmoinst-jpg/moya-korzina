"""Та же точка, по которой считали, — в окне магазина.

ЗАЧЕМ ЭТО ПОНАДОБИЛОСЬ. Наряд наполнял корзину в том магазине, который витрина
выбрала СЕБЕ САМА. Замер 20.09.2026: чистое окно Магнита приходит с уже
поставленной кукой shopCode=%22992301%22 — это её магазин по умолчанию, и товар
в нём стоит 84,99 ₽, тогда как расчёт показывал человеку 89,99 ₽ по его точке.
Корзина собиралась настоящая, а сумма в ней — не та, которую человек видел. Та
же беда уже случалась со ссылкой Ленты, открывшейся в чужой точке
(app/handover.NOTE_BY_STORE), и стоила она ровно того же: одна сумма на экране,
другая на кассе.

ПОЧЕМУ ЭТО НЕ «ВЫБОР ЗА ЧЕЛОВЕКА». Выбор точки — решение про его деньги, и мы
его не принимаем. Мы ПЕРЕНОСИМ уже принятое: человек указал адрес, приложение
подобрало по нему точку (app/places.py) и показало сумму по ней — значит и
корзина обязана собраться там же. Подставить сюда другую точку было бы враньём;
подставить ту же — единственный способ его избежать.

ПОЧЕМУ КУКАМИ, А НЕ НАЖАТИЯМИ В ОКНЕ ВЫБОРА. Потому что так эта витрина и
устроена — проверено запросами 12.09.2026 и записано в app/connectors/magnit.py:
магазин выбирается НЕ параметром в адресе, а куками shopCode / x_shop_type, а
способ получения — кукой nmg_dt. Замер 20.09.2026 живым браузером подтвердил это
уже на ВИТРИНЕ, а не только на шлюзе: одна и та же карточка молока стоит 84,99 ₽
при shopCode 992301 и 89,99 ₽ при 264856, и меняется она от одной куки.

ФОРМА ЗНАЧЕНИЯ — ЕЁ СОБСТВЕННАЯ. Витрина хранит код в кавычках, закодированных
процентами: %22992301%22. Сырые кавычки она тоже принимает и сама переписывает в
свою форму, но класть сразу её — на одну догадку меньше.

════ ГЛАВНОЕ, ЧТО ДАЛ ЗАМЕР: ТОЧКУ МОЖНО ПЕРЕНЕСТИ НЕ В КАЖДЫЙ МАГАЗИН ════

Карточка товара открывается не во всех форматах Магнита. Замер 20.09.2026 на
одной и той же карточке (Молоко Простоквашино 2,5 % 930 мл):

    ME       992301  Москва       84,99 ₽, кнопка «В корзину»       открылась
    MM       264856  Москва       89,99 ₽, кнопка «В корзину»       открылась
    GM       995402  Новосибирск  84,99 ₽, кнопка «В корзину»       открылась
    DARKSTORE 730884 Москва       89 ₽, «Нет в наличии»             открылась
    MM_MINI  628425  Москва       «Не удалось загрузить»            НЕ ОТКРЫЛАСЬ
    MM_MINI  080870  Новосибирск  «Не удалось загрузить»            НЕ ОТКРЫЛАСЬ

Два разных магазина формата MM_MINI в двух городах дали одно и то же, а нехватка
товара (даркстор) выглядит совсем иначе — страница открывается и честно говорит
«Нет в наличии». Значит дело не в товаре и не в отдельном магазине, а в формате:
у «Магнита у дома мини» интернет-витрины нет, заказать из него нельзя никак.

И ЭТО ВАЖНО ИМЕННО ДЛЯ ЧЕСТНОСТИ, А НЕ ДЛЯ УДОБСТВА. Промолчи мы — вышло бы
худшее из возможного: точка не переносится, витрина берёт свой магазин, корзина
наполняется по ЕГО ценам, и человек получает сумму, которой не видел. Поэтому
форматы, где перенос проверен, перечислены ниже поимённо, а для остальных наряд
останавливается и называет причину. Список — замеренный, а не выдуманный: сети
незамеренного формата здесь нет, и добавлять её без замера нельзя.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Куда ставить куки точки. Сеть попадает сюда только после замера: выдуманное имя
# куки ничего видимого не сломает — оно просто не сработает, а мы будем думать,
# что точка передана.
COOKIE_DOMAIN = {"magnit": ".magnit.ru"}

# Форматы точек, в которых карточка товара ОТКРЫВАЕТСЯ. См. таблицу в шапке.
# MK сюда не вписан нарочно: магазина этого формата рядом с адресами не нашлось,
# и замерить его было не на чем. Незамеренное молчит, а не притворяется рабочим.
SERVED_FORMATS = {"magnit": ("ME", "MM", "GM", "DARKSTORE")}

# Как назвать человеку формат, из которого не заказать.
FORMAT_NAME = {"MM_MINI": "«Магнит у дома мини»", "MK": "«Магнит Косметик»"}


def _magnit(place) -> list[dict]:
    """Три куки Магнита: какой магазин, какого формата и как забирать."""
    code = str(getattr(place, "store_id", "") or "").strip()
    if not code:
        return []
    shop_type = str(getattr(place, "shop_type", None) or "ME")
    delivery = bool(getattr(place, "delivery", True))
    return [
        {"name": "shopCode", "value": f"%22{code}%22"},
        {"name": "x_shop_type", "value": shop_type},
        {"name": "nmg_dt", "value": "DELIVERY_TYPE_DELIVERY" if delivery else "DELIVERY_TYPE_PICKUP"},
    ]


_BUILDERS = {"magnit": _magnit}


def _place_of(chain: str, place):
    if place is not None:
        return place
    from app import location

    return location.for_store(chain)


def served(chain: str, place=None) -> bool:
    """Открывает ли витрина этой сети карточки в ЭТОЙ точке.

    Незнакомая сеть — True: у неё точка и не переносится, и мешать ей нечем.
    Ответ False означает ровно одно: точка известна, но заказать из неё нельзя.
    """
    formats = SERVED_FORMATS.get(chain)
    if not formats:
        return True
    place = _place_of(chain, place)
    if not place or not getattr(place, "store_id", None):
        return True
    return str(getattr(place, "shop_type", None) or "ME") in formats


def cookies_for(chain: str, place=None) -> list[dict]:
    """Куки выбранной точки, готовые к подстановке в браузер.

    Пустой список — нормальный ответ: этой сети точку так не передать, точка
    неизвестна или из неё не заказать. Он ничего не отменяет сам по себе —
    решает вызывающий, посмотрев заодно в trouble().
    """
    build = _BUILDERS.get(chain)
    domain = COOKIE_DOMAIN.get(chain)
    if build is None or not domain:
        return []
    place = _place_of(chain, place)
    if not place or not served(chain, place):
        return []
    return [{**cookie, "domain": domain, "path": "/"} for cookie in build(place)]


def with_point(state: dict | None, chain: str, place=None) -> dict | None:
    """Сохранённый вход плюс выбранная точка. Вход остаётся нетронутым.

    Кука точки из ВХОДА уступает расчёту сознательно: человек мог открыть окно
    магазина неделю назад и с тех пор сменить адрес. Решает расчёт, потому что
    именно его сумму человек видел.

    Возвращает НОВЫЙ словарь: чужое состояние править на месте нельзя — оно
    лежит в рабочем месте человека и переживёт эту передачу.
    """
    fresh = cookies_for(chain, place)
    if not fresh:
        return state
    base = dict(state or {})
    names = {c["name"] for c in fresh}
    kept = [c for c in (base.get("cookies") or [])
            if not (isinstance(c, dict) and c.get("name") in names)]
    base["cookies"] = kept + fresh
    log.info("%s: в окно магазина уходит точка расчёта", chain)
    return base


def instead(chain: str) -> str:
    """Ближайшая точка, из которой заказать МОЖНО. Пусто — не нашли, и не врём."""
    if chain != "magnit":
        return ""
    try:
        from app import geo, location
        from app.connectors.magnit import stores_near

        addr = location.address()
        spot = geo.coords(addr) if addr else None
        if not spot:
            return ""
        for store in stores_near(*spot):
            if store.get("format") in SERVED_FORMATS["magnit"]:
                return (store.get("address") or "").strip()
    except Exception:  # noqa: BLE001 — подсказка не повод ронять передачу
        log.info("%s: замена непригодной точке не подобрана", chain, exc_info=True)
    return ""


def trouble(chain: str, place=None) -> str:
    """Почему в эту точку корзину не собрать. Пусто — собрать можно.

    Вся суть фразы в том, что «позже» тут не поможет: это не сбой сети и не
    перегрузка, а свойство магазина. Человеку нужен не совет подождать, а другая
    точка, и мы её называем.
    """
    place = _place_of(chain, place)
    if not place or not getattr(place, "store_id", None) or served(chain, place):
        return ""
    kind = str(getattr(place, "shop_type", None) or "")
    name = FORMAT_NAME.get(kind) or f"формата {kind}"
    where = instead(chain)
    tail = (f" Ближайший магазин, из которого заказать можно: {where}. "
            "Укажите его адрес в настройках — и расчёт, и корзина пойдут по нему."
            if where else
            " Рядом с вашим адресом магазина с интернет-витриной не нашлось.")
    return (f"Ваш магазин {name} работает только на полке: интернет-витрина не открывает "
            f"в нём ни одной карточки, а значит и корзину туда не собрать. Ждать нечего — "
            f"это не сбой." + tail)


def described(chain: str, place=None) -> str:
    """Как назвать человеку точку, в которую собралась корзина. Пусто — молчим."""
    place = _place_of(chain, place)
    if not cookies_for(chain, place):
        return ""
    try:
        from app import places

        found = places.points(chain)
    except Exception:  # noqa: BLE001 — подпись не повод ронять передачу
        return ""
    for spot in found:
        if str(spot.code) == str(getattr(place, "store_id", "")):
            return spot.label or spot.address or ""
    return ""


__all__ = ["cookies_for", "with_point", "served", "trouble", "instead", "described",
           "COOKIE_DOMAIN", "SERVED_FORMATS"]
