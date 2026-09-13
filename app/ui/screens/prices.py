"""Экран «Цены»: как менялась цена товара по магазинам во времени.

Снимки цен пишутся с первого расчёта и не перезаписываются — из них и строится график.
Пока обновление было одно, показывать нечего: об этом честно говорим, а не рисуем прямую.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app import repo
from app.ui import theme
from app.ui.helpers import load, module_warning, rub, show_exception, unit_label

esc = theme.esc


def render() -> None:
    products = repo.list_products()
    if not products:
        st.info("Список товаров пуст. Заведите товары или загрузите чек на экране «История».")
        return

    stores = [s for s in repo.list_stores() if repo.confirmed_mapping(products[0].id, s.id) or True]
    _refresh_bar(products, stores)
    _pricelist_box(stores)

    product = st.selectbox("Товар", products, format_func=lambda p: p.name, key="prices_product")
    if not product:
        return

    series, rows = _collect(product, stores)
    if not series:
        st.info("По этому товару цен ещё нет. Свяжите его с магазином на экране «Товары» "
                "и нажмите «Обновить цены».")
        return

    _summary(product, rows)
    frame = pd.DataFrame(series)
    if len(frame) < 2:
        st.info("Пока это один снимок цены. График появится после второго обновления — "
                "снимки не перезаписываются, история набирается сама.")
    else:
        st.line_chart(frame, height=320)
    _table(rows)


def _pricelist_box(stores) -> None:
    """Загрузка прайса для магазинов, у которых нельзя спросить цены.

    У Пятёрочки и Дикси каталог закрыт, а MCP нет. Единственный способ узнать цену
    на то, чего человек ещё не покупал, — принести её самому. Пусть приносит файлом.
    """
    from app import pricelist
    from app.connectors.history import MANUAL_STORES

    manual = [s for s in stores if s.code in MANUAL_STORES]
    if not manual:
        return

    loaded = [s for s in manual if pricelist.load(s.code)]
    title = "Прайс-листы вручную"
    if loaded:
        title += " — есть у: " + ", ".join(f"{s.name} ({pricelist.updated_at(s.code)})" for s in loaded)

    with st.expander(title):
        st.caption("У этих магазинов закрытый каталог: цены им неоткуда взять, кроме ваших чеков. "
                   "Файл с ценниками закрывает и то, чего вы ещё не покупали.")
        store = st.selectbox("Магазин", manual, format_func=lambda s: s.name, key="pl_store")
        rows = pricelist.load(store.code)
        if rows:
            st.markdown(f"Сейчас загружено: **{len(rows)}** позиций от {pricelist.updated_at(store.code)}.")
            st.dataframe(pd.DataFrame(rows)[["name", "price", "unit"]]
                         .rename(columns={"name": "Название", "price": "Цена", "unit": "Ед."}),
                         hide_index=True, use_container_width=True)

        st.markdown("Формат простой: название и цена. Единицу и артикул можно не указывать.")
        st.code("Название;Цена;Единица\nМолоко 1 л;79,90;шт\nЯблоки;149;кг", language=None)

        uploaded = st.file_uploader("Файл с ценами", type=["csv", "txt"], key=f"pl_file_{store.code}")
        if uploaded is not None and st.button("Загрузить прайс", key=f"pl_save_{store.code}"):
            try:
                text = uploaded.getvalue().decode("utf-8-sig")
            except UnicodeDecodeError:
                text = uploaded.getvalue().decode("cp1251", errors="replace")
            count = pricelist.save(store.code, text)
            if count:
                st.success(f"Загружено {count} позиций. Нажмите «Обновить цены», чтобы они попали в расчёт.")
                st.rerun()
            else:
                st.error("В файле не нашлось ни одной строки с названием и ценой. "
                         "Проверьте, что колонки называются «Название» и «Цена».")

        if rows and st.button("Удалить прайс", key=f"pl_del_{store.code}"):
            pricelist.remove(store.code)
            st.rerun()


def _refresh_bar(products, stores) -> None:
    left, right = st.columns([3, 1])
    with left:
        st.caption("Цены сохраняются снимками с отметкой времени. Чем чаще обновляете — "
                   "тем подробнее история.")
    with right:
        if st.button("Обновить цены", key="prices_refresh_btn", width="stretch"):
            fn, err = load("app.matcher", "refresh_prices")
            if err:
                module_warning(err)
                return
            try:
                with st.spinner("Спрашиваем цены у магазинов…"):
                    result = fn([p.id for p in products], [s.code for s in stores])
            except Exception as exc:  # noqa: BLE001
                show_exception(exc, "Не удалось обновить цены")
                return
            st.success(f"Готово: обновлено {result.get('updated', 0)}.")
            st.rerun()


def _collect(product, stores) -> tuple[dict, list[dict]]:
    """Ряды для графика по магазинам и плоский список снимков для таблицы."""
    series: dict[str, pd.Series] = {}
    rows: list[dict] = []
    is_kg = (product.unit or "pcs") == "kg"

    for store in stores:
        history = repo.price_history(product.id, store.id)
        if not history:
            continue
        points, index = [], []
        for snap in history:
            price = (snap.get("price_per_kg") or snap.get("price")) if is_kg else snap.get("price")
            if price is None:
                continue
            stamp = pd.to_datetime(snap.get("fetched_at"), errors="coerce")
            if pd.isna(stamp):
                continue
            points.append(float(price))
            index.append(stamp)
            rows.append({
                "store": store.name,
                "store_code": store.code,
                "when": stamp,
                "price": float(price),
                "in_stock": bool(snap.get("in_stock", 1)),
            })
        if points:
            series[store.name] = pd.Series(points, index=index)
    return series, rows


def _summary(product, rows: list[dict]) -> None:
    """Текущая цена по магазинам и насколько она сдвинулась с первого снимка."""
    by_store: dict[str, list[dict]] = {}
    for row in rows:
        by_store.setdefault(row["store"], []).append(row)

    unit = unit_label(product.unit)
    cards = []
    for name, points in by_store.items():
        points.sort(key=lambda r: r["when"])
        first, last = points[0]["price"], points[-1]["price"]
        delta = round(last - first, 2)
        if len(points) < 2 or abs(delta) < 0.005:
            change = '<span style="font-size:12px;color:var(--ink3);">без изменений</span>'
        else:
            color = "var(--red)" if delta > 0 else "var(--green)"
            sign = "+" if delta > 0 else "−"
            change = (f'<span style="font-size:12px;font-weight:600;color:{color};">'
                      f'{sign}{rub(abs(delta))} с первого снимка</span>')
        cards.append(
            '<div class="mk-card" style="padding:15px 18px;display:flex;flex-direction:column;gap:6px;">'
            f'<span style="display:inline-flex;align-items:center;gap:9px;font-weight:600;">'
            f'{theme.dot(points[0]["store_code"])}{esc(name)}</span>'
            f'<span class="mk-serif" style="font-size:21px;">{rub(last)}'
            f'<span style="font-size:12px;font-weight:400;color:var(--ink3);"> за {esc(unit)}</span></span>'
            f'{change}'
            f'<span style="font-size:12px;color:var(--ink3);">снимков: {len(points)}</span></div>'
        )
    if cards:
        theme.block('<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));'
                    'gap:16px;margin:10px 0 18px;">' + "".join(cards) + "</div>")


def _table(rows: list[dict]) -> None:
    with st.expander(f"Все снимки ({len(rows)})"):
        frame = pd.DataFrame([
            {
                "Когда": r["when"].strftime("%d.%m.%Y %H:%M"),
                "Магазин": r["store"],
                "Цена": rub(r["price"]),
                "В наличии": "да" if r["in_stock"] else "нет",
            }
            for r in sorted(rows, key=lambda r: r["when"], reverse=True)
        ])
        st.dataframe(frame, hide_index=True, width="stretch")


def header_stats() -> str:
    """Сколько всего снимков цен накоплено."""
    with repo.get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM store_prices").fetchone()["n"]
        days = conn.execute("SELECT COUNT(DISTINCT substr(fetched_at, 1, 10)) AS n "
                            "FROM store_prices").fetchone()["n"]
    return theme.stat_chips([
        ("Снимков цен", str(total)),
        ("Дней наблюдений", str(days)),
    ])
