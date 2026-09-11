"""Экран «История»: таблица покупок с фильтрами и загрузка чека."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app import repo
from app.ui import theme
from app.ui.helpers import (
    drop_file,
    load,
    module_warning,
    num,
    rub,
    save_upload,
    show_exception,
    store_selectbox,
    unit_label,
)


def render() -> None:
    stores = repo.list_stores()
    _table(stores)
    st.divider()
    _import(stores)


def _table(stores) -> None:
    theme.heading("Покупки")
    c1, c2, c3 = st.columns(3)
    with c1:
        date_from = st.date_input("Дата с", value=None, format="DD.MM.YYYY", key="hist_from")
    with c2:
        date_to = st.date_input("Дата по", value=None, format="DD.MM.YYYY", key="hist_to")
    with c3:
        store = store_selectbox(stores, "Магазин", key="hist_store", with_all=True)

    rows = repo.list_history(
        date_from.isoformat() if date_from else None,
        date_to.isoformat() if date_to else None,
        store.id if store else None,
    )
    if not rows:
        st.info("Покупок пока нет. Загрузите чек ниже — позиции появятся в этой таблице.")
        return

    esc = theme.esc
    body = []
    for r in rows:
        name = r.get("product_name") or r.get("raw_name") or "—"
        raw = r.get("raw_name") or ""
        hint = f' <span style="color:var(--ink3);font-size:12px;">{esc(raw)}</span>' if raw and raw != name else ""
        body.append([
            f'<span style="color:var(--ink2);">{esc(r.get("date") or "")}</span>',
            f'<span style="display:inline-flex;align-items:center;gap:7px;">'
            f'{theme.dot(r.get("store_code"))}{esc(r.get("store_name") or "—")}</span>',
            f'{esc(name)}{hint}',
            f'<span style="color:var(--ink2);">{num(r.get("qty"))} {unit_label(r.get("unit"))}</span>',
            f'<span style="color:var(--ink2);">{rub(r.get("unit_price"))}</span>',
            f'<span style="font-weight:500;">{rub(r.get("total"))}</span>',
        ])

    total = sum(float(r.get("total") or 0) for r in rows)
    theme.block(theme.table(
        grid="92px 132px minmax(0,1fr) 96px 116px 116px",
        header=["Дата", "Магазин", "Позиция", "Кол-во", "Цена", "Сумма"],
        rows=body,
        foot=[
            f'<span style="font-weight:600;">Строк: {len(rows)}</span>', "", "", "",
            '<span class="mk-eyebrow">Итого</span>',
            f'<span class="mk-serif" style="font-size:19px;">{rub(total)}</span>',
        ],
        aligns=["left", "left", "left", "right", "right", "right"],
    ))


def _import(stores) -> None:
    theme.heading("Загрузить покупки")
    theme.block(
        '<div class="mk-card" style="border-style:dashed;border-color:var(--line2);padding:18px 20px;'
        'display:flex;align-items:center;gap:14px;margin-bottom:10px;">'
        '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round" style="color:var(--warm);flex:none;">'
        '<path d="M12 16V4.5M7.5 9 12 4.5 16.5 9"></path>'
        '<path d="M4.5 15.5v2.8a1.7 1.7 0 0 0 1.7 1.7h11.6a1.7 1.7 0 0 0 1.7-1.7v-2.8"></path></svg>'
        '<span style="font-size:13px;color:var(--ink2);line-height:1.45;">'
        '<b>Чек</b> — PDF от ОФД или текст. <b>JSON</b> — выгрузка из «Мои чеки онлайн» ФНС. '
        '<b>Таблица</b> — CSV или XLSX с заказом из личного кабинета магазина.<br>'
        'Позиции разбираются и связываются с товарами сами.</span></div>'
    )
    col1, col2 = st.columns([2, 1])
    with col1:
        uploaded = st.file_uploader("Чек, выгрузка или таблица",
                                    type=["pdf", "txt", "json", "csv", "xlsx"],
                                    key="hist_upload")
    with col2:
        store = store_selectbox(stores, "Магазин чека (необязательно)", key="hist_store_imp", with_all=True)

    if uploaded is None:
        return

    if st.button("Загрузить", type="primary", key="hist_import_btn"):
        fn, err = load("app.importers", "import_receipt")
        if err:
            module_warning(err)
            return
        path = save_upload(uploaded)
        try:
            with st.spinner("Разбираем чек…"):
                result = fn(path, store.code if store else None)
        except Exception as exc:  # noqa: BLE001 — показываем пользователю, а не падаем
            show_exception(exc, "Не удалось прочитать чек")
            return
        finally:
            drop_file(path)
        _show_import_result(result)


def _show_import_result(result) -> None:
    if not isinstance(result, dict):
        st.success("Чек загружен.")
        st.write(result)
        return

    rows = result.get("rows") or []
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Позиций", len(rows) if isinstance(rows, list) else rows)
    c2.metric("Новых товаров", result.get("products_created", 0))
    c3.metric("Итог чека", rub(result.get("total")))
    c4.metric("Дата / магазин", f"{result.get('date') or '—'} · {result.get('store') or '—'}")

    if isinstance(rows, list) and rows:
        table = []
        for r in rows:
            if isinstance(r, dict):
                table.append(
                    {
                        "Строка чека": r.get("raw_name") or r.get("name") or "",
                        "Товар": r.get("product_name") or r.get("product") or "—",
                        "Кол-во": f"{num(r.get('qty'))} {unit_label(r.get('unit'))}",
                        "Цена": rub(r.get("unit_price") or r.get("price")),
                        "Сумма": rub(r.get("total")),
                    }
                )
            else:
                table.append({"Строка чека": str(r)})
        st.dataframe(pd.DataFrame(table), hide_index=True)

    st.success("Чек загружен — позиции уже в таблице выше.")


def header_stats() -> str:
    rows = repo.list_history()
    total = sum(float(r.get("total") or 0) for r in rows)
    dates = sorted({str(r.get("date") or "") for r in rows if r.get("date")})
    return theme.stat_chips([
        ("Строк", str(len(rows))),
        ("Сумма покупок", rub(total)),
        ("Последний чек", dates[-1] if dates else None),
    ])
