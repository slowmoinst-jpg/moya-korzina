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

from app import freshness, location, repo
from app import config
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
    # Обычная цена своего магазина за 30 дней и насколько нынешняя от неё отличается, %.
    # Пусто — истории нет, и тогда о «дорого/дешево» не говорим вовсе.
    usual: float | None = None
    vs_usual: float | None = None

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


def weights_agree(query: str, name: str) -> bool:
    """Совпадает ли фасовка. Ложь — это НЕ тот товар, как бы ни было похоже название.

    Самая дорогая ошибка подбора выглядела так: «Страчателла 200 г» сопоставилась
    с мороженым «Maxibuo брикет страчателла 92 г». По названию похоже до
    неразличимости — слово «страчателла» есть и там и там, — а по фасовке видно
    сразу: двести грамм и девяносто два это разные товары.

    Граммовку берём из НАЗВАНИЙ обеих сторон, поэтому ключ ничего не стоит и
    работает во всех сетях: производителя в чеке указывают не все, штрихкод
    каталоги не отдают вовсе (проверено 15.09.2026 у Ленты и Магнита), а вес
    пишут в названии почти всегда.

    Оба веса неизвестны — не возражаем: у весовых товаров («Слива») граммовки нет
    ни у кого, и отсекать по ней значит выбросить их целиком. Известен только
    один — тоже не возражаем: молчание не улика.
    """
    ожидаемый, _ = parse_weight(query or "")
    найденный, _ = parse_weight(name or "")
    if not ожидаемый or not найденный:
        return True
    допуск = float(config.get("weight_tolerance_pct", 20) or 20) / 100.0
    отклонение = abs(найденный - ожидаемый) / ожидаемый
    return отклонение <= допуск


def is_relevant(query: str, name: str, score: float | None = None) -> bool:
    """Про то ли это, что искали.

    Одной похожести мало: короткий запрос против длинного названия даёт низкую
    оценку, хотя попадание очевидное — «молоко» и «Молоко пастеризованное 930 мл»
    набирают меньше порога. Поэтому сначала смотрим вхождение: если все слова
    запроса есть в названии, это оно, и длина названия тут ни при чём.

    Но ВХОЖДЕНИЕ НЕ ПЕРЕБИВАЕТ ФАСОВКУ. Иначе «Страчателла 200 г» принимает
    мороженое «страчателла 92 г»: слово вошло, значит «это оно». Вес проверяется
    первым и снимает вопрос без всякой похожести.
    """
    if not weights_agree(query, name):
        return False
    if is_broader(query, name):
        return False
    words = set(normalize_name(query).split())
    if words and words <= set(normalize_name(name).split()):
        return True
    value = score if score else similarity(query, name or "")
    return value >= RELEVANT


def is_broader(query: str, name: str) -> bool:
    """Кандидат ШИРЕ запроса — значит это другой товар, а не тот же.

    Найденная ошибка: на «Персики консервированные 500 г» Магнит отдал просто
    «Персики» — свежие, 107,99 ₽ против 418 у консервированных. Похожесть при
    этом подскакивала до 0,9, потому что короткое название целиком входит в
    длинный запрос, и вхождение засчитывалось за попадание.

    Направление вхождения решает всё, и путать его нельзя:

        запрос «молоко» -> «Молоко пастеризованное 930 мл»  — ЭТО ОНО,
            мы искали общее и нашли частное, уточнения только добавились;
        запрос «персики консервированные» -> «Персики»      — НЕ ОНО,
            мы искали частное и нашли общее, уточнение ПОТЕРЯЛОСЬ.

    Цифры и единицы из сравнения выброшены: фасовку проверяет weights_agree,
    и «500» в запросе не должно считаться потерянным уточнением.
    """
    из_запроса = _meaningful(query)
    из_названия = _meaningful(name)
    if not из_запроса or not из_названия:
        return False
    потеряно = из_запроса - из_названия
    return bool(потеряно) and из_названия < из_запроса


def _meaningful(text: str) -> set[str]:
    """Слова названия без цифр и единиц измерения — только смысловые уточнения."""
    единицы = {"г", "гр", "кг", "мл", "л", "шт", "мг"}
    return {w for w in normalize_name(text or "").split()
            if not w.isdigit() and w not in единицы and not w[:1].isdigit()}


def _fill(offer: StoreOffer) -> StoreOffer:
    offer.per_unit, offer.per_unit_label = per_unit_price(
        offer.price, offer.unit, offer.weight_g, offer.name)
    # «Похоже, но не то» — говорим словами: иначе человек видит в списке напиток рядом с
    # сырами и не понимает, почему он дешевле всех. В выбор такое предложение не идёт
    # (`_worth`), но и прятать его незачем: пусть видно, что нашлось.
    if offer.found and not offer.note and not _worth(offer):
        offer.note = "похоже, но это другой товар — в выбор не берём"
    _note_usual(offer)
    return offer


# Насколько цена должна отличаться от обычной, чтобы об этом стоило сказать словами.
# Десяток процентов — уже повод подождать, мелочь в два процента человек не заметит.
# «Хватает ли истории» решает store.usual_trusted: трёх изменений или недели наблюдений —
# это правило живёт в одном месте, а не копией здесь.
USUAL_NOTE_PCT = 10.0


def _note_usual(offer: StoreOffer) -> None:
    """Сказать словами, дорого ли брать сейчас: цена против обычной за 30 дней.

    ЗАЧЕМ. История цены появилась 19.10.2026 (store.price_history): без неё база
    помнила только последнюю цену. Теперь есть с чем сравнить, и в предложении видно
    «дороже обычного на 15 %» — это и есть ответ на «а не переплачиваю ли я».

    Обычная цена — медиана изменений своего магазина (store.usual_price). Нет истории
    или изменений меньше трёх — молчим: придуманное «обычно дешевле» хуже молчания.
    Чужая пометка (почему пусто) не перебивается: она важнее.
    """
    if not offer.found or not offer.price or offer.price <= 0:
        return
    try:
        from app.catalog import store as catalog_store

        # Хватает ли истории — решает store.usual_trusted: история пишется только при
        # СМЕНЕ цены, поэтому у стабильного товара в ней одна строка, и «не меньше трёх»
        # не сработало бы никогда (замер 20.10.2026). Неделя наблюдений — тоже довод.
        if not catalog_store.usual_trusted(offer.store_code, offer.sku, days=30):
            return
        usual = catalog_store.usual_price(offer.store_code, offer.sku, days=30)
    except Exception:  # noqa: BLE001 — история не должна ломать сравнение
        return
    if not usual or usual <= 0:
        return
    offer.usual = usual
    offer.vs_usual = round((float(offer.price) - usual) / usual * 100, 1)
    if offer.note:
        return
    if offer.vs_usual >= USUAL_NOTE_PCT:
        offer.note = f"дороже обычного на {offer.vs_usual:.0f} %"
    elif offer.vs_usual <= -USUAL_NOTE_PCT:
        offer.note = f"дешевле обычного на {abs(offer.vs_usual):.0f} %"


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

    МЕСТО. Снимок цены помнит точку (store_prices.point_key), и цена берётся у
    точки, которую человек поставил на карте (location.point_key), а не у чужого
    города. Точки нет — читается общий снимок сети, как было раньше. Живой путь
    (compare_query) устроен так же: место входит в запрос и в ключ кэша.
    """
    product = repo.get_product(product_id)
    if product is None:
        return []
    # Цена — у точки человека на карте, если она есть: у Ленты, Магнита и METRO
    # один и тот же товар в разных магазинах стоит по-разному. Точки нет — None,
    # и freshness.price_for читает общий снимок, как раньше.
    point_key = location.point_key()
    out: list[StoreOffer] = []
    for store in repo.list_stores():
        mapping = repo.confirmed_mapping(product_id, store.id)
        if not mapping:
            out.append(StoreOffer(store.code, store.name, note="товар не связан с магазином"))
            continue
        snap = freshness.price_for(product_id, store.id, point_key=point_key)
        if not snap:
            out.append(StoreOffer(store.code, store.name, sku=mapping.get("sku") or "",
                                  name=mapping.get("raw_name") or "", note="свежей цены нет"))
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


# Во сколько раз цена может отличаться от соседей, чтобы ей верить при выборе магазина.
# ЗАМЕР 19.10.2026. У Дикси в базе 469 строк с ценой 1.99 ₽ — старый разбор карточки
# (крем NIVEA, лейка садовая, мыльный пистолет). Из-за них Дикси выглядел дешевле всех
# в 50–500 раз, и «где дешевле» отправляло человека в магазин за кремом по 1.99.
# Правило простое: если одна цена ниже медианы остальных в пять раз, это не выгода,
# а мусор в данных, и решать по ней нельзя. Считаем по СЕРЕДИНЕ, а не по средней:
# одна ошибка не должна двигать ориентир.
OUTLIER_LOW = 0.2
OUTLIER_HIGH = 5.0
# Меньше трёх предложений — судить не о чем: оставляем как есть, пусть решает человек.
OUTLIERS_MIN_OFFERS = 3
# Насколько название должно быть похоже, чтобы предложение считалось ТЕМ ЖЕ товаром,
# а не похожим. ЗАМЕР 20.10.2026 на живом прогоне: на «сыр страчателла» самым дешёвым
# оказывался «Напиток кисломолочный Exponenta Bio-Skyr Страчателла» за 149.99 — то есть
# напиток выигрывал у сыров и отправлял человека за «сыром», который пить надо. Оценка
# у него была 0.35, а у настоящих совпадений — 0.55…0.9, так что порог их разделяет.
CONFIDENT_SCORE = 0.5


def _worth(offer: StoreOffer) -> bool:
    """Можно ли считать это предложение тем же товаром, а не «похожим».

    Неизвестная оценка (0) и подтверждённая связка человека не отбрасываются: мы не знаем
    хуже человека, а наказывать за отсутствие оценки нельзя.
    """
    if offer.confirmed or offer.score <= 0:
        return True
    return offer.score >= CONFIDENT_SCORE


def _sane(offers: list[StoreOffer]) -> list[StoreOffer]:
    """Предложения, чьей приведённой цене можно верить: без одиноких выбросов.

    Выброс — цена, отличающаяся от медианы остальных больше чем в пять раз в любую
    сторону. Сравнивать по такой цене нельзя: она либо разобрана неверно, либо снята с
    другой единицы товара. Предложения без цены и без наличия сюда не попадают вовсе.
    """
    real = [o for o in offers if o.found and o.in_stock and o.per_unit and _worth(o)]
    if len(real) < OUTLIERS_MIN_OFFERS:
        return real
    values = sorted(o.per_unit for o in real)
    middle = values[len(values) // 2]
    if middle <= 0:
        return real
    low, high = middle * OUTLIER_LOW, middle * OUTLIER_HIGH
    kept = [o for o in real if low <= o.per_unit <= high]
    # Если правило выбросило всё (цены разошлись слишком сильно), честнее показать как
    # есть: пусть человек видит разброс, а не пустоту вместо ответа.
    return kept or real


def cheapest(offers: list[StoreOffer]) -> StoreOffer | None:
    """Самое выгодное предложение по ПРИВЕДЁННОЙ цене, среди того, что есть в наличии.

    Одинокие выбросы (цена в разы ниже соседей) в выбор не идут: см. `_sane`.
    """
    real = _sane(offers)
    return min(real, key=lambda o: o.per_unit) if real else None


def spread(offers: list[StoreOffer]) -> dict:
    """Разброс приведённых цен: на сколько дороже самое дорогое предложение.

    Это и есть ответ на вопрос «стоит ли вообще смотреть по сторонам»: если
    разброс три процента, не стоит, а если сорок — очень стоит.
    """
    values = [o.per_unit for o in _sane(offers)]
    if len(values) < 2:
        return {"min": None, "max": None, "diff": None, "pct": None}
    low, high = min(values), max(values)
    return {
        "min": low,
        "max": high,
        "diff": round(high - low, 2),
        "pct": round((high - low) / low * 100, 1) if low else None,
    }
