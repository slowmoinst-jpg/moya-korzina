"""Цены: как менялась цена товара по магазинам во времени.

Снимки цен пишутся с первого расчёта и не перезаписываются (repo.save_price) — из них
и строится график. Пока обновление было одно, показывать нечего: об этом здесь сказано
словами, а не нарисовано прямой линией, которая выглядела бы как «цена не менялась».

СЕТЬ ДЁРГАЕТСЯ ТОЛЬКО НАЖАТИЕМ. Это главное, что легко потерять при переезде. В
Streamlit «Обновить цены» была кнопкой, и опрос магазинов шёл только после неё; на
обычном сайте так же легко написать опрос прямо в открытии страницы — и тогда каждое
обновление, каждый возврат «назад» и каждая вторая вкладка означают минуту ожидания и
шесть походов в сети. Поэтому опрос живёт в POST на этот же адрес, а открытие страницы
читает только базу.

ГРАФИК РИСУЕТСЯ SVG, А НЕ БИБЛИОТЕКОЙ. Прежний экран звал st.line_chart, за которым
стоят pandas и altair. Ради одной линии тянуть на страницу мегабайт скриптов незачем,
тем более что рисуем мы её на сервере: точки уже посчитаны, остаётся соединить. Заодно
линия получает цвет своей сети — то самое, ради чего выбрано направление «Цвет магазина».

ТОВАР ВЫБИРАЕТСЯ АДРЕСОМ: `/prices?product=12`. Не выпадающим списком, помнящим себя, —
на цену конкретного товара теперь можно дать ссылку и вернуться к ней «назад».
"""
from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import urlencode

from flask import flash, redirect, render_template, request

from app import repo
from app.web.screens.products import num, rub, unit_label
from app.web.views import SCREEN_BY_KEY

log = logging.getLogger(__name__)

# Размер картинки графика в её собственных единицах. Наружу она отдаётся шириной в
# 100% и высотой auto, поэтому на телефоне просто уменьшается целиком — без
# горизонтальной прокрутки и без второго набора размеров под узкий экран.
CHART_W, CHART_H, PAD = 600, 200, 26


def _back(product_id: str | None, pricelist_code: str | None) -> str:
    """Адрес возврата после действия. Собирается заново, а не берётся из формы.

    Скрытое поле с готовым адресом было бы короче и опаснее: подставленный кем угодно
    чужой адрес увёл бы человека с формы загрузки прайса на подделку, и выглядело бы
    это как продолжение работы.
    """
    params = {k: v for k, v in (("product", product_id), ("pl", pricelist_code)) if v}
    path = SCREEN_BY_KEY["prices"].path
    return f"{path}?{urlencode(params)}" if params else path


def _when(raw) -> datetime | None:
    """Отметка времени снимка или None. Неразобранный снимок пропускаем, а не падаем."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        for shape in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(raw), shape)
            except ValueError:
                continue
    return None


def _collect(product, stores) -> list[dict]:
    """Снимки цен по магазинам: [{code, name, points: [(время, цена, в наличии)]}].

    Весовой товар считается по цене за килограмм, штучный — по цене за штуку: иначе
    в одном ряду оказались бы рубли за упаковку и рубли за килограмм, и график
    показал бы скачок там, где сменилась не цена, а единица.
    """
    is_kg = (product.unit or "pcs") == "kg"
    out: list[dict] = []
    for store in stores:
        points = []
        for snap in repo.price_history(product.id, store.id):
            price = (snap.get("price_per_kg") or snap.get("price")) if is_kg else snap.get("price")
            stamp = _when(snap.get("fetched_at"))
            if price is None or stamp is None:
                continue
            points.append({"when": stamp, "price": float(price),
                           "in_stock": bool(snap.get("in_stock", 1))})
        if points:
            points.sort(key=lambda p: p["when"])
            out.append({"code": store.code, "name": store.name, "points": points})
    return out


def _cards(series: list[dict], unit: str) -> list[dict]:
    """Текущая цена по магазинам и насколько она сдвинулась с первого снимка.

    Красным растущая цена, зелёным упавшая: зелёный в этом интерфейсе означает
    выгоду, и подешевевший товар — единственное, что здесь ей является.
    """
    cards = []
    for row in series:
        points = row["points"]
        first, last = points[0]["price"], points[-1]["price"]
        delta = round(last - first, 2)
        cards.append({
            "code": row["code"], "name": row["name"], "unit": unit,
            "last": last, "count": len(points),
            "delta": None if (len(points) < 2 or abs(delta) < 0.005) else delta,
            "in_stock": points[-1]["in_stock"],
        })
    cards.sort(key=lambda c: c["last"])
    return cards


def _chart(series: list[dict]) -> dict | None:
    """Точки графика в координатах картинки. None — рисовать нечего.

    Шкала одна на все сети нарочно: разные шкалы на одном поле показали бы две
    дорожки рядом там, где одна сеть вдвое дороже другой, — и человек прочитал бы
    это как «цены одинаковые».
    """
    # Линия нужна хотя бы одной сети. Считать «две точки всего» достаточным нельзя:
    # три сети по одному снимку дают три точки и ни одной линии — на экране выходит
    # пустая рамка с тремя кружками, и человек читает её как сломанный график.
    if not any(len(row["points"]) >= 2 for row in series):
        return None

    prices = [p["price"] for row in series for p in row["points"]]
    stamps = [p["when"] for row in series for p in row["points"]]

    low, high = min(prices), max(prices)
    span = (high - low) or max(high * 0.1, 1.0)     # плоская линия не должна делить на ноль
    start, end = min(stamps), max(stamps)
    seconds = (end - start).total_seconds() or 1.0

    lines = []
    for row in series:
        points = [(
            PAD + (p["when"] - start).total_seconds() / seconds * (CHART_W - 2 * PAD),
            CHART_H - PAD - (p["price"] - low) / span * (CHART_H - 2 * PAD),
        ) for p in row["points"]]
        lines.append({
            "code": row["code"], "name": row["name"],
            "points": " ".join(f"{x:.1f},{y:.1f}" for x, y in points),
            "dots": [{"x": round(x, 1), "y": round(y, 1)} for x, y in points],
        })
    return {"w": CHART_W, "h": CHART_H, "pad": PAD, "lines": lines,
            "low": low, "high": high,
            "from": start.strftime("%d.%m.%Y"), "to": end.strftime("%d.%m.%Y")}


def _snapshots(series: list[dict]) -> list[dict]:
    """Все снимки одним списком, новые сверху."""
    flat = [{"code": row["code"], "name": row["name"], **point}
            for row in series for point in row["points"]]
    flat.sort(key=lambda r: r["when"], reverse=True)
    for row in flat:
        row["label"] = row["when"].strftime("%d.%m.%Y %H:%M")
    return flat


def _totals() -> list[tuple[str, str]]:
    """Сколько всего снимков накоплено — цифры из шапки прежнего экрана."""
    try:
        with repo.get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM store_prices").fetchone()["n"]
            days = conn.execute("SELECT COUNT(DISTINCT substr(fetched_at, 1, 10)) AS n "
                                "FROM store_prices").fetchone()["n"]
    except Exception as exc:  # noqa: BLE001 — счётчик в шапке не повод ронять экран
        log.warning("счётчики снимков не собрались (%s)", exc)
        return []
    return [("Снимков цен", str(total)), ("Дней наблюдений", str(days))]


def _safety_net() -> str:
    """Что будет, если магазин сменит вёрстку.

    Без этой строки запасной разбор — невидимая настройка в конфиге, о которой
    человек узнаёт только когда уже поздно: цены поехали, а почему — неизвестно.
    """
    try:
        from app.connectors import smart_extract
        ok, why = smart_extract.available()
    except Exception as exc:  # noqa: BLE001
        return f"Состояние запасного разбора вёрстки неизвестно ({exc})."
    if ok:
        return ("Если магазин сменит вёрстку, цену попробует прочитать языковая модель — "
                "и в журнал уйдёт предупреждение, что разбор пора чинить.")
    return (f"Запасной разбор вёрстки языковой моделью не включён ({why}). "
            "Если магазин сменит разметку, цена возьмётся из справочника — она может "
            "быть неточной.")


def _manual_stores(stores) -> list:
    """Сети, у которых цены неоткуда взять, кроме прайса и чеков."""
    try:
        from app.connectors.history import MANUAL_STORES
    except Exception:  # noqa: BLE001
        return []
    return [s for s in stores if s.code in MANUAL_STORES]


def _pricelist(manual, chosen_code: str | None) -> dict | None:
    """Что показать в блоке прайсов: выбранная сеть, её строки и у кого прайс уже есть."""
    if not manual:
        return None
    from app import pricelist

    chosen = next((s for s in manual if s.code == chosen_code), manual[0])
    rows = pricelist.load(chosen.code)
    return {
        "stores": [{"code": s.code, "name": s.name,
                    "updated": pricelist.updated_at(s.code)} for s in manual],
        "chosen": chosen,
        "rows": rows[:200],                     # весь прайс в страницу не вываливаем
        "count": len(rows),
        "updated": pricelist.updated_at(chosen.code),
    }


# ---------- действия ----------
def _do_refresh(products, stores) -> None:
    """Спросить цены у магазинов. Единственное место экрана, которое ходит в сеть."""
    from app import matcher

    try:
        result = matcher.refresh_prices([p.id for p in products], [s.code for s in stores])
    except Exception as exc:  # noqa: BLE001 — упавший магазин не повод отдать пятисотую
        log.exception("обновление цен не удалось")
        flash(f"Не удалось обновить цены: {exc}. Остальное на экране работает.", "bad")
        return
    flash(f"Готово: обновлено цен — {result.get('updated', 0)}.", "ok")
    for error in (result.get("errors") or []):
        flash(str(error), "warn")


def _do_pricelist_save(code: str, uploaded) -> None:
    """Положить присланный файл на место прайса сети.

    Код сети сюда приходит уже проверенным по справочнику — см. page(). Без проверки
    он бы дошёл до pricelist.path_for, который склеивает из него имя файла: значение
    вида «../../что-нибудь» писало бы куда угодно, а «Удалить прайс» — удаляло бы.
    """
    from app import pricelist

    if uploaded is None or not uploaded.filename:
        flash("Файл не выбран.", "warn")
        return
    raw = uploaded.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Прайсы выгружают из Excel, а он на русской Windows пишет cp1251. Упасть на
        # этом значило бы отказать человеку в самом частом файле, который он принесёт.
        text = raw.decode("cp1251", errors="replace")
    count = pricelist.save(code, text)
    if count:
        flash(f"Загружено {count} позиций. Нажмите «Обновить цены», чтобы они попали "
              "в расчёт.", "ok")
    else:
        flash("В файле не нашлось ни одной строки с названием и ценой. Проверьте, что "
              "колонки называются «Название» и «Цена».", "bad")


def page():
    products = repo.list_products()
    stores = repo.list_stores()
    manual = _manual_stores(stores)

    if request.method == "POST":
        action = request.form.get("action")
        # Сеть берём из справочника, а не из формы: её код уходит в имя файла прайса,
        # и присланное значение записало бы файл (или удалило его) где угодно.
        chosen = next((s for s in manual if s.code == request.form.get("store")), None)
        if action == "refresh":
            if products:
                _do_refresh(products, stores)
            else:
                flash("Список товаров пуст — спрашивать цены не о чем.", "info")
        elif chosen is None and action in ("pricelist_save", "pricelist_drop"):
            flash("Такой сети нет в списке тех, у кого прайс заводят вручную.", "warn")
        elif action == "pricelist_save":
            _do_pricelist_save(chosen.code, request.files.get("file"))
        elif action == "pricelist_drop":
            from app import pricelist
            flash("Прайс удалён." if pricelist.remove(chosen.code) else "Прайса и не было.",
                  "info")
        # Перевод обратно на тот же адрес: обновление страницы после действия не
        # должно повторять его молча — ни опроса магазинов, ни загрузки прайса.
        return redirect(_back(request.form.get("product"), request.form.get("pl")))

    chosen_id = request.args.get("product")
    product = next((p for p in products if str(p.id) == chosen_id), None)
    if product is None and products and chosen_id is None:
        product = products[0]

    series = _collect(product, stores) if product else []
    unit = unit_label(product.unit) if product else "шт"

    return render_template(
        "prices.html",
        screen=SCREEN_BY_KEY["prices"],
        rub=rub, num=num,
        totals=_totals(),
        products=products,
        product=product,
        unit=unit,
        cards=_cards(series, unit),
        chart=_chart(series),
        snapshots=_snapshots(series),
        # «Один снимок» — это про каждую сеть отдельно, а не про их сумму: пока ни у
        # одной нет второй точки, менялась не цена, а только состав снятого.
        single=bool(series) and not any(len(r["points"]) >= 2 for r in series),
        safety=_safety_net(),
        pricelist=_pricelist(manual, request.args.get("pl")),
        pl=request.args.get("pl") or "",
    )


# Действия экрана живут на его же адресе — см. пояснение в app/web/screens/products.py.
page.methods = ("GET", "POST")

__all__ = ["page"]
