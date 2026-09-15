"""Сравнение одного товара по всем доставкам сразу.

Оптимизатор отвечает на вопрос «как дешевле собрать корзину». Но прежде него
есть вопрос проще и злее: а этот товар вообще где продаётся и почём? Ответить на
него по ценникам нельзя, потому что ценники несравнимы:

    Молоко 930 мл  —  149 ₽        это 160,22 ₽ за литр
    Молоко 1 л     —  155 ₽        это 155,00 ₽ за литр   <- дешевле, хотя дороже

Поэтому здесь считается ПРИВЕДЁННАЯ цена: сколько стоит килограмм или литр, а не
упаковка. Без неё сравнение доставок превращается в сравнение фасовок.

Второе, что делает эта таблица: показывает, где товара НЕТ. Лента отдаёт остаток
числом, Магнит отвечает 404 на то, чего у него не продают, Дикси — признаком
наличия. Пустая клетка в таблице — это тоже ответ, и для сборки корзины он важнее
цены: нет смысла отправлять человека туда, где он этого не купит.

Наружу ничего не бросаем: магазин, который не ответил, попадает в таблицу строкой
«не ответил», а не роняет сравнение по остальным.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app import repo
from app.matcher.normalize import normalize_name, parse_weight, similarity
from app.models import Location

log = logging.getLogger(__name__)

# Ниже этого порога кандидат уже не про то, что искали. Пустая клетка честнее:
# на «молоко» прилетало детское пюре, на «огурцы» — солёные в банке.
RELEVANT = 0.45

PER_KG = "за кг/л"
PER_PCS = "за штуку"


@dataclass
class StoreOffer:
    """Что нашлось по товару в одной доставке."""
    store_code: str
    store_name: str
    sku: str = ""
    name: str = ""
    price: float | None = None
    unit: str = "pcs"
    weight_g: float | None = None
    per_unit: float | None = None          # приведённая цена
    per_unit_label: str = PER_PCS
    in_stock: bool = True
    url: str | None = None
    score: float = 0.0
    note: str = ""                         # почему пусто, если пусто
    confirmed: bool = False                # это ТОТ товар, а не похожий по названию

    @property
    def found(self) -> bool:
        return bool(self.sku and self.price is not None)


def per_unit_price(price: float | None, unit: str, weight_g: float | None,
                   name: str = "") -> tuple[float | None, str]:
    """Приведённая цена: сколько стоит килограмм, литр или штука.

    Весовой товар уже назван в рублях за килограмм. Штучный приводим по граммовке
    из названия — «930 мл» и «1 л» иначе несравнимы. Если граммовки нет, честно
    оставляем цену за штуку: выдумывать вес нельзя, сравнение станет ложью.
    """
    if price is None:
        return None, PER_PCS
    if unit == "kg":
        return round(float(price), 2), PER_KG
    grams = weight_g if weight_g else parse_weight(name)[0]
    if grams and grams > 0:
        return round(float(price) * 1000.0 / float(grams), 2), PER_KG
    return round(float(price), 2), PER_PCS


def is_relevant(query: str, name: str, score: float | None = None) -> bool:
    """Про то ли это, что искали.

    Одной похожести мало: короткий запрос против длинного названия даёт низкую
    оценку, хотя попадание очевидное — «молоко» и «Молоко пастеризованное 930 мл»
    набирают меньше порога. Поэтому сначала смотрим вхождение: если все слова
    запроса есть в названии, это оно, и длина названия тут ни при чём.
    """
    words = set(normalize_name(query).split())
    if words and words <= set(normalize_name(name).split()):
        return True
    value = score if score else similarity(query, name or "")
    return value >= RELEVANT


def _fill(offer: StoreOffer) -> StoreOffer:
    offer.per_unit, offer.per_unit_label = per_unit_price(
        offer.price, offer.unit, offer.weight_g, offer.name)
    return offer


def _price_of(connector, candidate) -> tuple[float | None, bool]:
    """Цена кандидата. Поиск её отдаёт не у всех — тогда спрашиваем карточку."""
    if candidate.price is not None:
        return candidate.price, True
    snaps = connector.get_prices([candidate.sku])
    if not snaps:
        return None, True
    snap = snaps[0]
    price = snap.price_per_kg if (candidate.unit == "kg" and snap.price_per_kg) else snap.price
    return (price if price and price > 0 else None), bool(snap.in_stock)


def compare_query(query: str, store_codes: list[str] | None = None,
                  per_store: int = 1, location: Location | None = None) -> list[StoreOffer]:
    """Ищет товар во всех доставках и приводит цены к сравнимому виду.

    per_store больше единицы полезен, когда нужно посмотреть, что вообще есть у
    магазина по этому запросу, а не только лучшее совпадение.

    location — где находится клиент. Сравнивать доставки «вообще» нельзя: у Ленты
    и Магнита и цена, и сам ассортимент свои в каждой точке.

    Не передан — место берётся по адресу клиента из app.location, у каждой сети
    своё: код точки Ленты для Магнита ничего не значит. Адреса нет вовсе —
    коннектор возьмёт запасное значение из config.yaml, как было раньше.
    """
    from app import location as client_place
    from app.connectors import get_connector

    stores = repo.list_stores()
    if store_codes:
        stores = [s for s in stores if s.code in store_codes]

    out: list[StoreOffer] = []
    for store in stores:
        try:
            here = location if location is not None else client_place.for_store(store.code)
            connector = get_connector(store.code, here)
        except Exception as exc:  # noqa: BLE001
            out.append(StoreOffer(store.code, store.name, note=f"нет коннектора ({exc})"))
            continue

        try:
            found = connector.search(query, limit=max(1, per_store))
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: поиск по «%s» упал (%s)", store.code, query, exc)
            out.append(StoreOffer(store.code, store.name, note="магазин не ответил"))
            continue

        if not found:
            out.append(StoreOffer(store.code, store.name, note="ничего не нашлось"))
            continue

        fit = [c for c in found if is_relevant(query, c.name or "", c.score)]
        if not fit:
            out.append(StoreOffer(store.code, store.name, note="ничего похожего"))
            continue

        for candidate in fit[:max(1, per_store)]:
            price, in_stock = _price_of(connector, candidate)
            offer = StoreOffer(
                store_code=store.code,
                store_name=store.name,
                sku=candidate.sku,
                name=candidate.name or "",
                price=price,
                unit=candidate.unit or "pcs",
                weight_g=candidate.weight_g,
                in_stock=in_stock,
                url=candidate.url,
                score=round(candidate.score or similarity(query, candidate.name or ""), 3),
                note="" if price is not None else "цены нет",
            )
            out.append(_fill(offer))
    return out


def compare_product(product_id: int) -> list[StoreOffer]:
    """Сравнение по УЖЕ подтверждённым сопоставлениям — без похода в поиск.

    Отвечает на другой вопрос: не «что вообще есть», а «во что мне обойдётся
    именно этот товар там, где я его уже опознал».

    ОГРАНИЧЕНИЕ: снимок цены не помнит, для какой точки он был снят. Пока
    пользователь один, это незаметно. Как только их станет двое с разными
    адресами, здесь понадобится место в самом снимке — иначе один получит цену
    чужого города. Живой путь (compare_query) этим уже не страдает: там место
    входит и в запрос, и в ключ кэша.
    """
    product = repo.get_product(product_id)
    if product is None:
        return []
    out: list[StoreOffer] = []
    for store in repo.list_stores():
        mapping = repo.confirmed_mapping(product_id, store.id)
        if not mapping:
            out.append(StoreOffer(store.code, store.name, note="товар не связан с магазином"))
            continue
        snap = repo.latest_price_for(product_id, store.id)
        if not snap:
            out.append(StoreOffer(store.code, store.name, sku=mapping.get("sku") or "",
                                  name=mapping.get("raw_name") or "", note="цены ещё не брали"))
            continue
        unit = mapping.get("unit") or product.unit or "pcs"
        price = snap.get("price_per_kg") if (unit == "kg" and snap.get("price_per_kg")) else snap.get("price")
        out.append(_fill(StoreOffer(
            store_code=store.code,
            store_name=store.name,
            sku=mapping.get("sku") or "",
            name=mapping.get("raw_name") or "",
            price=price,
            unit=unit,
            weight_g=mapping.get("weight_g") or product.weight_g,
            in_stock=bool(snap.get("in_stock", 1)),
            url=mapping.get("url"),
            note="" if price else "цены нет",
        )))
    return out


def cheapest(offers: list[StoreOffer]) -> StoreOffer | None:
    """Самое выгодное предложение по ПРИВЕДЁННОЙ цене, среди того, что есть в наличии."""
    real = [o for o in offers if o.found and o.in_stock and o.per_unit]
    return min(real, key=lambda o: o.per_unit) if real else None


def spread(offers: list[StoreOffer]) -> dict:
    """Разброс приведённых цен: на сколько дороже самое дорогое предложение.

    Это и есть ответ на вопрос «стоит ли вообще смотреть по сторонам»: если
    разброс три процента, не стоит, а если сорок — очень стоит.
    """
    values = [o.per_unit for o in offers if o.found and o.in_stock and o.per_unit]
    if len(values) < 2:
        return {"min": None, "max": None, "diff": None, "pct": None}
    low, high = min(values), max(values)
    return {
        "min": low,
        "max": high,
        "diff": round(high - low, 2),
        "pct": round((high - low) / low * 100, 1) if low else None,
    }
