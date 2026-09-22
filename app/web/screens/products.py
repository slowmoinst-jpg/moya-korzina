"""Товары: что покупаем и где это лежит.

ЧТО ЗДЕСЬ ПОЯВИЛОСЬ ОТ ПЕРЕЕЗДА. Поиск по каталогу уехал в адрес: `/products?q=творог`.
В Streamlit он жил в session_state, и это стоило трёх вещей сразу — на отфильтрованный
список нельзя было дать ссылку, кнопка «назад» уводила из приложения целиком, а две
вкладки рядом перетирали фильтр друг другу. Здесь список полностью описан адресом, и
поэтому ссылка на него работает у кого угодно.

Так же устроена и правка: `/products?edit=12` открывает карточку товара, `?edit=new` —
пустую. Вкладок «Список» и «Добавить или изменить» больше нет. Вкладка прятала работу
за переключателем, о котором надо помнить; адрес не прячет ничего и возвращает человека
ровно туда, где он был, — вместе с поиском, потому что `q` из адреса никуда не девается.

ПОЧЕМУ НЕ ВЕРНУЛАСЬ ТАБЛИЦА «ТОВАР × ШЕСТЬ МАГАЗИНОВ». Она отвечала на вопрос
сопоставления («связан ли этот товар с этой сетью»), а не на вопрос покупателя («есть
и почём»), и на телефоне уезжала вбок. Вопрос сопоставления теперь живёт на своём
экране — «Связи». Здесь у товара видно главное: от какой цены он есть и в скольких
сетях; все цены раскрываются по «…».

ПОЧЕМУ «…» — ЭТО <details>, А НЕ ВСПЛЫВАЮЩЕЕ ОКНО. Всплывающее окно требует скрипта и
на телефоне накрывает собой список, из которого его открыли: человек теряет место, куда
смотрел. Раскрывающийся блок остаётся на своём месте в потоке, работает без единой
строки JavaScript и закрывается тем же нажатием.

СЕТЬ ЗДЕСЬ НЕ ТРОГАЕТСЯ ВООБЩЕ. Все цены на экране — последние снимки из базы
(repo.latest_price_for). Спросить магазины можно на «Ценах» и в «Связях», и только
нажатием: иначе открытие каталога означало бы минуту ожидания на каждое обновление
страницы.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from flask import redirect, render_template, request

from app import repo
from app.models import Product
from app.web.views import SCREEN_BY_KEY

log = logging.getLogger(__name__)

UNITS = {"pcs": "шт", "kg": "кг"}

# Неразрывный пробел: «1 234,56 ₽» не должно переноситься по разрыву строки — на
# узком телефоне цена иначе разваливается на «1 234,» и «56 ₽».
NBSP = " "


# ---------- как печатаются числа ----------
# Живёт здесь, а не в отдельном модуле, потому что отдельный модуль — это правка
# общего кода, а переезд идёт по экранам и правит каждый агент только своё.
# «Товары» — первый экран раздела «Каталог», остальные три берут форматирование
# отсюда. Когда переедут все экраны, этому место в одном общем месте.
def rub(value) -> str:
    """1234.56 -> «1 234,56 ₽». Пусто -> «—», а не «0 ₽»: это разные ответы."""
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:,.2f}".replace(",", NBSP).replace(".", ",") + NBSP + "₽"


def num(value, digits: int = 3) -> str:
    """Число без хвостовых нулей и с запятой: 0.8200 -> «0,82», 3.0 -> «3»."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.{digits}f}".rstrip("0").rstrip(".").replace(".", ",")


def unit_label(unit: str | None) -> str:
    return UNITS.get(unit or "pcs", "шт")


def goods(count: int) -> str:
    """«1 товар», «2 товара», «5 товаров» — иначе счётчик выдаёт машину."""
    tail = count % 100
    if 11 <= tail <= 14:
        return f"{count} товаров"
    word = {1: "товар", 2: "товара", 3: "товара", 4: "товара"}.get(count % 10, "товаров")
    return f"{count} {word}"


def nets(count: int) -> str:
    """«в 1 сети», «в 2 сетях» — падеж виден в строке каждого товара."""
    tail = count % 100
    if 11 <= tail <= 14:
        return f"в {count} сетях"
    return f"в {count} сети" if count % 10 == 1 else f"в {count} сетях"


# ---------- данные экрана ----------
def catalog_prices(products, stores) -> dict[int, dict[str, float]]:
    """product_id -> {код сети: цена за единицу}. Последний снимок, без похода в сеть.

    Спрашивается только по показанным товарам, а не по всем: при поиске это разница
    между шестью запросами и шестью сотнями, и на телефоне она видна глазом.
    """
    out: dict[int, dict[str, float]] = {}
    for product in products:
        found: dict[str, float] = {}
        for store in stores:
            snap = repo.latest_price_for(product.id, store.id)
            if not snap:
                continue
            value = snap.get("price_per_kg") if (product.unit == "kg") else snap.get("price")
            value = value if value is not None else snap.get("price")
            if value is not None:
                found[store.code] = float(value)
        out[product.id] = found
    return out


def _stats(products, stores) -> list[tuple[str, str]]:
    """Три цифры, которые стояли в шапке прежнего экрана.

    Пятёрочка из счёта исключена — ровно так считал прежний экран. Менять счёт при
    переезде нельзя: цифра на глазах у человека поехала бы без всякой причины, и
    объяснить это было бы нечем.
    """
    counted = [s for s in stores if s.code != "pyaterochka"]
    matrix = repo.mapping_matrix()
    ready = sum(1 for p in products if any(matrix.get((p.id, s.id)) for s in counted))
    pairs = sum(1 for p in products for s in counted if matrix.get((p.id, s.id)))
    return [("Товаров", str(len(products))),
            ("Готовы к расчёту", f"{ready} из {len(products)}"),
            ("Связок с магазинами", str(pairs))]


def _rows(products, stores, prices) -> list[dict]:
    """Товары, разложенные по категориям, с уже посчитанной строкой «от … за …»."""
    by_category: dict[str, list[dict]] = {}
    for product in products:
        found = prices.get(product.id) or {}
        best = min(found, key=found.get) if found else None
        by_category.setdefault(product.category or "Без категории", []).append({
            "id": product.id,
            "name": product.name,
            "active": bool(product.active),
            "unit": unit_label(product.unit),
            "best_code": best,
            "best_price": found.get(best) if best else None,
            "nets": nets(len(found)) if found else None,
            # Сети без цены перечисляем поимённо: «нет цены у Ленты» — это повод
            # пойти в «Связи», а молчание выглядело бы как «товара там не бывает».
            "prices": [{"code": s.code, "name": s.name, "value": found.get(s.code)}
                       for s in stores if s.code in found],
            "missing": [s.name for s in stores if s.code not in found],
        })
    # Ключ намеренно не «items»: в Jinja `group.items` достаёт метод словаря, а не
    # значение по ключу, и шаблон молча получает функцию вместо списка товаров.
    return [{"name": name, "goods": by_category[name], "count": goods(len(by_category[name]))}
            for name in sorted(by_category)]


def _edited(products, raw: str | None) -> tuple[Product | None, bool]:
    """Какой товар правим. (товар, открыта ли карточка) — «new» открывает пустую."""
    if raw is None:
        return None, False
    if raw == "new":
        return None, True
    try:
        wanted = int(raw)
    except ValueError:
        return None, False
    for product in products:
        if product.id == wanted:
            return product, True
    return None, False


def _save(form) -> tuple[int | None, str | None]:
    """Записать товар. Возвращает (id, ошибка); ошибка — фраза для человека."""
    name = (form.get("name") or "").strip()
    if not name:
        return None, "Напишите название — без него товар не отличить от другого."
    try:
        weight = float((form.get("weight_g") or "0").replace(",", ".") or 0)
    except ValueError:
        return None, "Вес не разобрался. Напишите число, например 200 или 0,5."

    raw_id = (form.get("id") or "").strip()
    unit = form.get("unit") if form.get("unit") in UNITS else "pcs"
    product = Product(
        id=int(raw_id) if raw_id.isdigit() else None,
        name=name,
        # Штрихкод чистим от всего, кроме цифр: из чека он приходит с пробелами и
        # дефисами, а искать по нему магазин будет как по числу.
        barcode="".join(ch for ch in (form.get("barcode") or "") if ch.isdigit()) or None,
        brand=(form.get("brand") or "").strip() or None,
        weight_g=weight or None,
        unit=unit,
        category=(form.get("category") or "").strip() or None,
        active=form.get("active") == "on",
    )
    return repo.upsert_product(product), None


def page():
    products = repo.list_products(active_only=False)
    stores = repo.list_stores()
    query = (request.args.get("q") or "").strip()

    error = None
    if request.method == "POST":
        saved, error = _save(request.form)
        if saved is not None:
            # Перевод после записи, а не страница в ответ на POST: иначе обновление
            # страницы записывает товар второй раз, и человек об этом не узнаёт.
            # Адрес собирается urlencode, а не склейкой: поиск вида «чай&edit=new»
            # иначе дописал бы в адрес чужой параметр.
            params = {k: v for k, v in (("saved", saved), ("q", query)) if v}
            return redirect(f"{SCREEN_BY_KEY['products'].path}?{urlencode(params)}")

    shown = [p for p in products if not query or query.lower() in (p.name or "").lower()]
    prices = catalog_prices(shown, stores)

    edited, editing = _edited(products, request.args.get("edit"))
    if error:                                   # форму не закрываем: ввод остался в ней
        editing = True

    return render_template(
        "products.html",
        screen=SCREEN_BY_KEY["products"],
        rub=rub,
        stats=_stats(products, stores),
        query=query,
        total=len(products),
        shown=len(shown),
        pairs=sum(len(v) for v in prices.values()),
        groups=_rows(shown, stores, prices),
        editing=editing,
        edited=edited,
        # После неудачной записи в поля возвращается введённое, а не сохранённое:
        # иначе человек теряет всё, что набрал, из-за одной незаполненной строки.
        form=request.form if error else None,
        error=error,
        saved=request.args.get("saved"),
        units=UNITS,
    )


# Flask берёт список методов из атрибута самой функции (и проносит его через
# functools.wraps в auth.needs_phone). Так действия экрана живут на его же адресе,
# и карта страниц в app/web/views.py остаётся общей на всех, кто её правит.
page.methods = ("GET", "POST")

__all__ = ["page", "rub", "num", "unit_label", "goods", "nets", "catalog_prices", "UNITS"]
