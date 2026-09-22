"""Пакет цен, собранный в браузере человека, — в его базу.

Зачем он есть. Пятёрочку и Самокат приложение с сервера не видит: их защита
отвечает отказом датацентру и подозрением — любому автоматическому браузеру.
Проверено 16.09.2026 с трёх сторон: с боевого сервера, из отдельного браузера и
из браузера владельца с подключённым отладчиком. А из обычной вкладки обычного
человека обе сети открываются как для любого покупателя — потому что он и есть
покупатель. Значит собирать цены надо там, и единственное, чего не хватает, —
дороги от его вкладки до его базы. Эта дорога здесь.

Приёмник ничего не знает о том, как пакет собран. Ему всё равно, пришёл файл от
закладки, от расширения или из чужого прайса, лишь бы поля были на месте. Так же
устроен приём чеков (app/importers/bundle.py), и по той же причине: способ
доставки меняется чаще, чем смысл данных.

ЧТО ЛЕЖИТ В ПАКЕТЕ

    {"store": "pyaterochka",              обязательное: код сети из справочника
     "address": "Столярный переулок, 2",  адрес, по которому сеть считала цены
     "store_code": "...",                 код точки в сети, если он виден
     "collected_at": "2026-09-16T22:10",  когда собрано
     "items": [
        {"sku": "4306830",                обязательное: артикул в сети
         "name": "Азу из курицы …",       обязательное
         "price": 176.99,                 обязательное: цена, по которой продают СЕЙЧАС
         "base_price": 294.99,            цена без акции, если она показана
         "in_stock": true,
         "unit": "pcs", "weight_g": 300,
         "url": "https://5ka.ru/product/…-4306830/"}]}

ДВЕ ЦЕНЫ И КАКАЯ ИЗ НИХ НАША. В расчёт идёт `price` — та, по которой человек
заплатит сегодня. `base_price` сохраняется рядом, но только как справка: считать
по ней выгоду значило бы обещать скидку, которой к моменту заказа может не быть.

ЧЕГО ПРИЁМНИК НЕ ДЕЛАЕТ. Не сопоставляет товары с эталонами — это работа
app/matcher и она идёт отдельно, по подтверждению человека. Не чистит старые
цены: снимок цены только вставляется, история не переписывается (repo.save_price).
И не верит пакету на слово — всё, что не разобралось, возвращается списком, а не
теряется молча.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from app import repo

log = logging.getLogger(__name__)

# Сети, для которых пакет вообще имеет смысл. Остальные приложение спрашивает само,
# и принимать по ним пакет — значит молча смешивать два источника с разной свежестью.
BROWSER_STORES = ("pyaterochka", "samokat")

MAX_ITEMS = 50000          # больше одного магазина в пакет не кладут
NAME_LIMIT = 300


def _as_data(payload: str | bytes | dict) -> Any:
    if isinstance(payload, (str, bytes)):
        return json.loads(payload)
    return payload


def _number(value: Any) -> float | None:
    """Число из цены. Строки с запятой и пробелами — обычное дело в вёрстке."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    text = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    try:
        return round(float(text), 2)
    except ValueError:
        return None


def _weight(value: Any) -> float | None:
    number = _number(value)
    return number if number and number > 0 else None


def _item(raw: Any) -> dict | None:
    """Одна позиция пакета или None, если её нельзя занести честно.

    Требуем три вещи: артикул, название и цену больше нуля. Ноль ценой не
    считается — у витрин это обычно значит «здесь не продаётся», и принять его
    за цену значит положить человеку в корзину бесплатный товар.
    """
    if not isinstance(raw, dict):
        return None
    sku = str(raw.get("sku") or raw.get("id") or "").strip()
    name = " ".join(str(raw.get("name") or "").split())[:NAME_LIMIT]
    price = _number(raw.get("price"))
    if not sku or not name or not price or price <= 0:
        return None
    unit = str(raw.get("unit") or "pcs").strip().lower()
    return {
        "sku": sku,
        "name": name,
        "price": price,
        "base_price": _number(raw.get("base_price")),
        "in_stock": bool(raw.get("in_stock", True)),
        "unit": "kg" if unit.startswith("kg") or unit.startswith("кг") else "pcs",
        "weight_g": _weight(raw.get("weight_g")),
        "url": (str(raw.get("url") or "").strip() or None),
    }


def parse(payload: str | bytes | dict) -> dict:
    """Разбирает пакет, ничего не записывая.

    Возвращает сеть, адрес, разобранные позиции и число непонятых. Непонятые
    считаются, а не выбрасываются: человек должен видеть, что часть страницы
    прочитать не удалось, — это первый признак сменившейся вёрстки.
    """
    data = _as_data(payload)
    if not isinstance(data, dict):
        raise ValueError("пакет цен должен быть объектом JSON")

    store = str(data.get("store") or "").strip().lower()
    if store not in BROWSER_STORES:
        raise ValueError(f"сеть «{store or '—'}» не принимает пакеты цен; "
                         f"ожидались: {', '.join(BROWSER_STORES)}")

    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("в пакете нет списка items")
    if len(raw_items) > MAX_ITEMS:
        raise ValueError(f"в пакете {len(raw_items)} позиций — это больше одного магазина")

    items: list[dict] = []
    seen: set[str] = set()
    skipped = 0
    for raw in raw_items:
        item = _item(raw)
        if not item:
            skipped += 1
            continue
        if item["sku"] in seen:          # одна позиция попадает в несколько категорий
            continue
        seen.add(item["sku"])
        items.append(item)

    return {
        "store": store,
        "address": " ".join(str(data.get("address") or "").split()) or None,
        "store_code": (str(data.get("store_code") or "").strip() or None),
        "collected_at": str(data.get("collected_at") or "").strip() or None,
        "items": items,
        "skipped": skipped,
    }


def import_prices(payload: str | bytes | dict) -> dict:
    """Пакет цен в базу человека. Возвращает сводку для экрана.

    Сводка нужна не для красоты: «взято 1240, не разобрано 3» — это единственный
    способ заметить, что сеть сменила вёрстку, до того как расчёт начнёт врать.
    """
    bundle = parse(payload)
    store = repo.get_store(bundle["store"])
    if not store:
        raise ValueError(f"сети «{bundle['store']}» нет в справочнике магазинов")

    stamp = bundle["collected_at"] or datetime.now().isoformat(timespec="seconds")
    saved = 0
    for item in bundle["items"]:
        store_product_id = repo.upsert_store_product(
            store.id, item["sku"], item["name"],
            weight_g=item["weight_g"], unit=item["unit"], url=item["url"])
        repo.save_price(store_product_id, item["price"],
                        in_stock=item["in_stock"], fetched_at=stamp)
        saved += 1

    log.info("%s: принято %d цен из браузера (адрес %s)", bundle["store"], saved, bundle["address"])
    return {
        "store": bundle["store"],
        "store_name": store.name,
        "address": bundle["address"],
        "collected_at": stamp,
        "saved": saved,
        "skipped": bundle["skipped"],
    }


__all__ = ["parse", "import_prices", "BROWSER_STORES"]
