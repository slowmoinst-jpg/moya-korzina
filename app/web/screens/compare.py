"""Сравнение: один товар во всех доставках сразу.

Корзину считает оптимизатор, но перед этим у человека есть вопрос проще: а где это
вообще есть и почём? Ответ по ценникам не собирается — 930 мл за 149 ₽ и 1 л за 155 ₽
не сравнить на глаз. Поэтому главная колонка здесь не цена, а приведённая: сколько
стоит килограмм или литр. Вторая важная колонка — наличие: пустая клетка значит «здесь
не купишь», и для сборки корзины это важнее цены.

ЗАПРОС ЖИВЁТ В АДРЕСЕ: `/compare?q=молоко&per=2`. Это не украшение. Сравнение — та самая
вещь, которую пересылают: «смотри, в Ленте дешевле на сорок процентов». В Streamlit
переслать было нечего, потому что запрос лежал в session_state одной вкладки одного
человека.

ПОЧЕМУ ХОЖДЕНИЕ В СЕТЬ ЗДЕСЬ ВСЁ-ТАКИ НА ОТКРЫТИИ СТРАНИЦЫ. Правило «не дёргать сети
без нужды» тут выполнено не отсутствием запроса, а тем, что нужда записана в самом
адресе: `q` появляется в нём только потому, что человек нажал «Сравнить». Пустой
`/compare` не спрашивает никого. А повтор того же адреса почти ничего не стоит: ответы
магазинов кэшируются на шесть часов (app/connectors/cache.py), поэтому обновление
страницы и возврат «назад» идут из кэша, а не по новой в шесть сетей.

Форма поиска — method="get" именно за этим: POST дал бы тот же результат, но без
адреса, который можно переслать, и с переспросом при каждом обновлении.
"""
from __future__ import annotations

import logging

from flask import render_template, request

from app.web.screens.products import num, rub
from app.web.views import SCREEN_BY_KEY

log = logging.getLogger(__name__)

PER_STORE = (1, 2, 3)

# Ниже этого разброса ходить по сторонам не стоит: разница меньше похода. Порог тот
# же, что стоял на прежнем экране, — менять его при переезде значило бы менять ответ.
WORTH_LOOKING = 10


def _rows(compare, offers) -> list[dict]:
    """Найденное, от дешёвого к дорогому; чего нет в наличии — в конец.

    Отсутствие товара опускает предложение вниз независимо от цены: самая дешёвая
    строка, которую нельзя купить, наверху списка читается как ответ, а она — нет.
    """
    best = compare.cheapest(offers)
    found = sorted([o for o in offers if o.found],
                   key=lambda o: (not o.in_stock, o.per_unit or 1e9))
    return [{
        "code": offer.store_code,
        "store": offer.store_name,
        "name": offer.name,
        "price": offer.price,
        "per_unit": offer.per_unit,
        "per_unit_label": offer.per_unit_label,
        "in_stock": offer.in_stock,
        "url": offer.url,
        "score": offer.score,
        "win": best is not None and offer.sku == best.sku and offer.store_code == best.store_code,
    } for offer in found]


def _misses(offers) -> list[dict]:
    """Кто ничего не показал и почему. Молчание сети — тоже ответ, и его надо назвать."""
    return [{"store": o.store_name, "code": o.store_code, "note": o.note or "пусто"}
            for o in offers if not o.found]


def page():
    query = (request.args.get("q") or "").strip()
    try:
        per_store = int(request.args.get("per") or 1)
    except ValueError:
        per_store = 1
    per_store = per_store if per_store in PER_STORE else 1

    context = {
        "screen": SCREEN_BY_KEY["compare"],
        "rub": rub, "num": num,
        "query": query,
        "per_store": per_store,
        "per_options": PER_STORE,
        "rows": [], "misses": [], "summary": None, "error": None,
        "worth_looking": WORTH_LOOKING,
    }
    if not query:
        return render_template("compare.html", **context)

    from app import compare

    try:
        offers = compare.compare_query(query, per_store=per_store)
    except Exception as exc:  # noqa: BLE001 — упавшая сеть не повод отдать пятисотую
        log.exception("сравнение по «%s» не получилось", query)
        context["error"] = f"Сравнение не получилось: {exc}. Остальное на экране работает."
        return render_template("compare.html", **context)

    best = compare.cheapest(offers)
    gap = compare.spread(offers)
    context["rows"] = _rows(compare, offers)
    context["misses"] = _misses(offers)
    if best:
        context["summary"] = {
            "code": best.store_code, "store": best.store_name,
            "per_unit": best.per_unit, "per_unit_label": best.per_unit_label,
            "diff": gap["diff"], "pct": gap["pct"],
        }
    return render_template("compare.html", **context)


__all__ = ["page"]
