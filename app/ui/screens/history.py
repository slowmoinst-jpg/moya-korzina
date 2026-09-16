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


MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря")


def _human_date(iso: str) -> str:
    """«2026-08-16» -> «16 августа 2026». Дата чека читается глазами, а не машиной."""
    try:
        year, month, day = (int(x) for x in str(iso).split("-")[:3])
        return f"{day} {MONTHS[month - 1]} {year}"
    except (ValueError, IndexError):
        return str(iso or "—")


def _receipts(rows: list[dict]) -> list[dict]:
    """Строки покупок -> чеки: дата плюс магазин, внутри состав.

    ОГОВОРКА, КОТОРУЮ НАДО ЗНАТЬ: отдельной сущности «чек» в базе нет, строки
    хранятся плоско (см. purchase_history). Поэтому чек здесь — это всё, что
    куплено в один день в одном магазине. Два похода в одну сеть за день
    склеятся в один чек, и развести их нечем: время покупки не хранится.
    Появится номер чека из ФНС — группировать надо будет по нему.

    Порядок сохраняется тот, в котором пришли строки: list_history отдаёт их
    от свежих к старым, значит и чеки выйдут так же.
    """
    out: list[dict] = []
    index: dict[tuple, int] = {}
    for row in rows:
        key = (row.get("date"), row.get("store_code"))
        if key not in index:
            index[key] = len(out)
            out.append({
                "date": row.get("date"),
                "store_code": row.get("store_code"),
                "store_name": row.get("store_name") or "—",
                "lines": [],
                "total": 0.0,
            })
        receipt = out[index[key]]
        receipt["lines"].append(row)
        receipt["total"] += float(row.get("total") or 0)
    return out


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

    receipts = _receipts(rows)
    st.caption(f"Чеков: {len(receipts)} · позиций: {len(rows)}. "
               "Раскройте чек, чтобы увидеть состав.")

    # Один чек — открыт сразу: чаще всего смотрят последний, и лишний клик здесь лишний.
    for n, receipt in enumerate(receipts):
        with st.expander(_receipt_label(receipt), expanded=(len(receipts) == 1 or n == 0)):
            _receipt_lines(receipt)


def _receipt_label(receipt: dict) -> str:
    """Строка свёрнутого чека. Только текст: заголовок раскрывающегося блока разметку не принимает."""
    count = len(receipt["lines"])
    return (f"{_human_date(receipt['date'])}  ·  {receipt['store_name']}  ·  "
            f"{format_plural(count)}  ·  {rub(receipt['total'])}")


def format_plural(count: int) -> str:
    """«1 позиция», «2 позиции», «5 позиций» — иначе в заголовке видно машину."""
    tail = count % 100
    if 11 <= tail <= 14:
        word = "позиций"
    else:
        last = count % 10
        word = "позиция" if last == 1 else "позиции" if 2 <= last <= 4 else "позиций"
    return f"{count} {word}"


def _receipt_lines(receipt: dict) -> None:
    esc = theme.esc
    body = []
    for r in receipt["lines"]:
        name = r.get("product_name") or r.get("raw_name") or "—"
        raw = r.get("raw_name") or ""
        hint = (f' <span style="color:var(--ink3);font-size:12px;">{esc(raw)}</span>'
                if raw and raw != name else "")
        body.append([
            f'{esc(name)}{hint}',
            f'<span style="color:var(--ink2);">{num(r.get("qty"))} {unit_label(r.get("unit"))}</span>',
            f'<span style="color:var(--ink2);">{rub(r.get("unit_price"))}</span>',
            f'<span style="font-weight:500;">{rub(r.get("total"))}</span>',
        ])
    theme.block(theme.table(
        grid="minmax(0,1fr) 96px 116px 116px",
        header=["Позиция", "Кол-во", "Цена", "Сумма"],
        rows=body,
        foot=[
            f'<span style="font-weight:600;">{format_plural(len(receipt["lines"]))}</span>', "",
            '<span class="mk-eyebrow">Итого</span>',
            f'<span class="mk-serif" style="font-size:19px;">{rub(receipt["total"])}</span>',
        ],
        aligns=["left", "right", "right", "right"],
    ))


def _import(stores) -> None:
    theme.heading("Загрузить покупки")
    _paste_block(stores)
    theme.block(
        '<div class="mk-card" style="border-style:dashed;border-color:var(--line2);padding:18px 20px;'
        'display:flex;align-items:center;gap:14px;margin-bottom:10px;">'
        '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" '
        'stroke-linecap="round" stroke-linejoin="round" style="color:var(--warm);flex:none;">'
        '<path d="M12 16V4.5M7.5 9 12 4.5 16.5 9"></path>'
        '<path d="M4.5 15.5v2.8a1.7 1.7 0 0 0 1.7 1.7h11.6a1.7 1.7 0 0 0 1.7-1.7v-2.8"></path></svg>'
        '<span style="font-size:13px;color:var(--ink2);line-height:1.45;">'
        '<b>Чек</b> — PDF от ОФД или текст. <b>JSON</b> — выгрузка из «Мои чеки онлайн» ФНС. '
        '<b>Таблица</b> — CSV или XLSX с заказом из личного кабинета магазина. '
        '<b>Письмо</b> — HTML или EML из доставки.<br>'
        'Позиции разбираются и связываются с товарами сами.</span></div>'
    )
    col1, col2 = st.columns([2, 1])
    with col1:
        uploaded = st.file_uploader("Чек, выгрузка или таблица",
                                    type=["pdf", "txt", "json", "csv", "xlsx", "html", "htm", "eml"],
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


def _paste_block(stores) -> None:
    """Вставить письмо из доставки или страницу заказа.

    Файл с чеком есть не у всех. А письмо «Чек на ваш заказ» от Самоката или
    страница заказа в Ленте есть у любого, кто этими доставками пользуется:
    выделить, скопировать, вставить. Это единственный способ загрузить историю
    сегодня, не дожидаясь ни выгрузок, ни договорённостей с ФНС.
    """
    with st.expander("Вставить письмо или страницу заказа"):
        st.caption("Откройте письмо из доставки или страницу «Мои заказы», выделите всё "
                   "(Ctrl+A), скопируйте и вставьте сюда. Разбор терпимый: поймёт и таблицу, "
                   "и список в столбик, и HTML письма.")
        col1, col2 = st.columns([3, 1])
        with col1:
            text = st.text_area("Текст заказа", height=180, key="hist_paste",
                                placeholder="Молоко Простоквашино 930 мл   2 шт × 149,00 ₽ = 298,00 ₽")
        with col2:
            store = store_selectbox(stores, "Магазин", key="hist_paste_store", with_all=True)

        if not (text or "").strip():
            return

        preview_fn, err = load("app.importers.text_import", "preview")
        if err:
            module_warning(err)
            return
        try:
            seen = preview_fn(text, store.code if store else None)
        except Exception as exc:  # noqa: BLE001
            show_exception(exc, "Не удалось разобрать текст")
            return

        if not seen["rows"]:
            st.warning("Ни одной строки с товаром и ценой не нашлось. "
                       "Скопируйте вместе с ценами — без них позицию не отличить от заголовка.")
            return

        st.markdown(f"Понял **{len(seen['rows'])}** позиций на {rub(seen['total'])}"
                    + (f", дата {seen['date']}" if seen.get("date") else ""))
        st.dataframe([{"Товар": r["name"], "Кол-во": r["qty"], "Цена": r["price"], "Сумма": r["total"]}
                      for r in seen["rows"]], hide_index=True, use_container_width=True)
        if seen["skipped"]:
            lines = "\n".join(f"- {line}" for line in seen["skipped"][:10])
            st.info("Эти строки я не понял, они не попадут в историю:\n\n" + lines)

        if st.button("Добавить в историю", type="primary", key="hist_paste_btn"):
            fn, err = load("app.importers.text_import", "import_order_text")
            if err:
                module_warning(err)
                return
            try:
                result = fn(text, store.code if store else None)
            except Exception as exc:  # noqa: BLE001
                show_exception(exc, "Не удалось сохранить заказ")
                return
            st.success(f"Добавлено {result['rows']} позиций"
                       f" · новых товаров {result['products_created']}.")
            st.session_state.pop("hist_paste", None)
            st.rerun()


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
