"""Экран «Товары»: список, связи с магазинами, поиск в каталоге, автоподбор."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app import repo
from app.models import Product
from app.ui import theme
from app.ui.helpers import load, module_warning, num, rub, show_exception

UNITS = {"pcs": "шт", "kg": "кг"}


def render() -> None:
    products = repo.list_products(active_only=False)
    stores = repo.list_stores()

    tab_list, tab_map, tab_edit = st.tabs(["Список", "Связать с магазином", "Добавить или изменить"])
    with tab_list:
        _matrix(products, stores)
    with tab_map:
        _mapping(products, stores)
    with tab_edit:
        _form(products)


# ---------- таблица эталонов со статусом сопоставления ----------
def _matrix(products, stores) -> None:
    if not products:
        st.info("Товаров пока нет. Заведите их на вкладке «Добавить или изменить» — "
                "или загрузите чек на экране «История», и они появятся сами.")
        return

    matrix = repo.mapping_matrix()
    esc = theme.esc
    body = []
    for p in products:
        weight = f'<span style="color:var(--ink3);">{num(p.weight_g)} г</span>' if p.weight_g else ""
        name = esc(p.name) + (f' <span style="color:var(--ink3);">{esc(p.brand)}</span>' if p.brand else "")
        row = [
            name,
            f'<span style="color:var(--ink2);font-size:13px;">{esc(p.category) if p.category else "—"}</span>',
            weight or '<span style="color:var(--ink3);">—</span>',
            f'<span style="color:var(--ink2);font-size:13px;">{UNITS.get(p.unit, p.unit)}</span>',
        ]
        row += [theme.mark(bool(matrix.get((p.id, st_.id)))) for st_ in stores]
        body.append(row)

    theme.block(theme.table(
        grid="minmax(0,1fr) 150px 78px 52px " + " ".join("96px" for _ in stores),
        header=["Товар", "Категория", "Вес", "Ед."] + [st_.name for st_ in stores],
        rows=body,
        aligns=["left", "left", "right", "center"] + ["center" for _ in stores],
    ))

    done = sum(1 for p in products for s in stores if matrix.get((p.id, s.id)))
    any_store = sum(1 for p in products if any(matrix.get((p.id, s.id)) for s in stores))
    st.caption(
        f"Товаров: {len(products)} · нашлись хотя бы в одном магазине: {any_store} · "
        f"связок с магазинами: {done}"
    )


# ---------- сопоставление ----------
def _doubts_note(product, mapping: dict) -> None:
    """Почему это сопоставление может быть неверным.

    Подтверждённое не значит проверенное: половина связей заводится автоматически.
    Если родовое слово или марка разошлись — человек должен это увидеть здесь,
    а не догадываться потом по странной цене.
    """
    from app.matcher.normalize import similarity
    from app.matcher.quality import doubts

    raw = mapping.get("raw_name") or ""
    flags = doubts(product, raw, mapping.get("weight_g"))
    if similarity(product.name, raw) < 0.75:
        flags.append("похожесть названий низкая")
    if flags:
        st.warning("Стоит проверить: " + ", ".join(flags))


def _mapping(products, stores) -> None:
    product_ids = [p.id for p in products if p.active]
    store_codes = [s.code for s in stores]

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Подобрать всё", key="auto_match_btn"):
            fn, err = load("app.matcher", "auto_match")
            if err:
                module_warning(err)
            elif not product_ids:
                st.info("Связывать пока нечего — список товаров пуст.")
            else:
                try:
                    with st.spinner("Подбираем товары в магазинах…"):
                        st.session_state["match_report"] = ("auto", fn(product_ids, store_codes))
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    show_exception(exc, "Подбор не удался")
    with c2:
        if st.button("Обновить цены", key="refresh_prices_btn"):
            fn, err = load("app.matcher", "refresh_prices")
            if err:
                module_warning(err)
            elif not product_ids:
                st.info("Список товаров пуст.")
            else:
                try:
                    with st.spinner("Спрашиваем цены у магазинов…"):
                        st.session_state["match_report"] = ("prices", fn(product_ids, store_codes))
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    show_exception(exc, "Не удалось обновить цены")

    _report()
    st.divider()

    if not products:
        st.info("Сначала добавьте хотя бы один товар.")
        return
    if not stores:
        st.info("Справочник магазинов пуст.")
        return

    c1, c2 = st.columns(2)
    with c1:
        product = st.selectbox("Товар", products, format_func=lambda p: p.name, key="map_product")
    with c2:
        store = st.selectbox("Магазин", stores, format_func=lambda s: s.name, key="map_store")

    if product is None or store is None:
        return

    current = repo.confirmed_mapping(product.id, store.id)
    if current:
        st.success(
            f"Подтверждено: **{current.get('raw_name')}** (артикул `{current.get('sku')}`)"
        )
        _doubts_note(product, current)
        price = repo.latest_price_for(product.id, store.id)
        if price:
            st.caption(
                f"Последняя цена: {rub(price.get('price'))}"
                + (f" · за кг {rub(price.get('price_per_kg'))}" if price.get("price_per_kg") else "")
                + f" · снято {price.get('fetched_at')}"
                + ("" if price.get("in_stock", 1) else " · нет в наличии")
            )
        else:
            st.caption("Цена ещё не загружена — нажмите «Обновить цены».")
        if st.button("Убрать связь", key="drop_map_btn"):
            repo.drop_mapping(product.id, store.id)
            st.session_state.pop("candidates", None)
            st.rerun()
        return

    st.info("Связь убрали.")
    if st.button("Найти кандидатов", type="primary", key="find_cand_btn"):
        fn, err = load("app.matcher", "find_candidates")
        if err:
            module_warning(err)
        else:
            try:
                with st.spinner("Ищем в каталоге магазина…"):
                    cands = fn(product.id, store.code, 3)
                st.session_state["candidates"] = (product.id, store.code, list(cands or []))
            except Exception as exc:  # noqa: BLE001
                show_exception(exc, "Поиск в магазине не удался")

    saved = st.session_state.get("candidates")
    if not saved or saved[0] != product.id or saved[1] != store.code:
        return

    candidates = saved[2]
    if not candidates:
        st.warning("Ничего не нашли. Попробуйте название покороче — например, без граммовки.")
        return

    st.write("**Что нашлось в магазине**")
    for i, cand in enumerate(candidates):
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([5, 2, 2, 2])
            c1.markdown(f"**{getattr(cand, 'name', '—')}**\n\nартикул `{getattr(cand, 'sku', '—')}`")
            c2.markdown(f"Цена\n\n{rub(getattr(cand, 'price', None))}")
            c3.markdown(f"Похожесть\n\n{num(getattr(cand, 'score', 0))}")
            if c4.button("Подтвердить", key=f"confirm_{i}"):
                fn, err = load("app.matcher", "confirm")
                if err:
                    module_warning(err)
                else:
                    try:
                        fn(product.id, store.code, getattr(cand, "sku", None))
                        st.session_state.pop("candidates", None)
                        st.success("Готово: товар связан с этим магазином.")
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        show_exception(exc, "Не получилось связать")


def _report() -> None:
    """Итог последнего подбора или обновления цен."""
    report = st.session_state.get("match_report")
    if not report:
        return
    kind, data = report
    if not isinstance(data, dict):
        st.success(f"Готово.")
        return

    if kind == "auto":
        review = data.get("need_review") or []
        st.success(f"Подтверждено автоматически: {data.get('auto', 0)}. Требуют ручной проверки: {len(review)}.")
        if review:
            with st.expander(f"Нужна ручная проверка ({len(review)})"):
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Товар": r.get("product_name") or r.get("product_id"),
                                "Магазин": r.get("store_code"),
                                "Причина": r.get("reason") or "—",
                                "Лучший score": num(r.get("best_score")) if r.get("best_score") else "—",
                            }
                            for r in review
                            if isinstance(r, dict)
                        ]
                    ),
                    hide_index=True,
                )
    else:
        errors = data.get("errors") or []
        st.success(f"Обновлено цен: {data.get('updated', 0)}.")
        for err in errors:
            st.warning(str(err))


# ---------- форма эталона ----------
def _form(products) -> None:
    options = [None] + list(products)
    selected = st.selectbox(
        "Что редактируем",
        options,
        format_func=lambda p: "+ Новый товар" if p is None else f"{p.id}. {p.name}",
        key="prod_edit_select",
    )

    with st.form("product_form"):
        name = st.text_input("Название", value=selected.name if selected else "")
        c1, c2 = st.columns(2)
        with c1:
            brand = st.text_input("Бренд", value=(selected.brand or "") if selected else "")
            weight_g = st.number_input(
                "Вес / объём, г",
                min_value=0.0,
                step=10.0,
                value=float(selected.weight_g) if selected and selected.weight_g else 0.0,
            )
        with c2:
            unit_keys = list(UNITS)
            unit = st.selectbox(
                "Единица",
                unit_keys,
                index=unit_keys.index(selected.unit) if selected and selected.unit in unit_keys else 0,
                format_func=lambda u: UNITS[u],
            )
            category = st.text_input("Категория", value=(selected.category or "") if selected else "")
        barcode = st.text_input(
            "Штрихкод",
            value=(getattr(selected, "barcode", None) or "") if selected else "",
            help="Самый надёжный способ связать товар с магазином: по названию «Страчателла» "
                 "находится и сыр, и мороженое, а по штрихкоду — только он сам. "
                 "Подставляется из чека, если оператор его передал.",
        )
        active = st.checkbox("Активен", value=bool(selected.active) if selected else True)
        submitted = st.form_submit_button("Сохранить", type="primary")

    if submitted:
        if not name.strip():
            st.error("Напишите название.")
            return
        product = Product(
            id=selected.id if selected else None,
            name=name.strip(),
            barcode="".join(ch for ch in barcode if ch.isdigit()) or None,
            brand=brand.strip() or None,
            weight_g=weight_g or None,
            unit=unit,
            category=category.strip() or None,
            active=active,
        )
        pid = repo.upsert_product(product)
        st.success(f"Сохранено (ID {pid}).")
        st.rerun()


def header_stats() -> str:
    products = repo.list_products(active_only=False)
    stores = [s for s in repo.list_stores() if s.code != "pyaterochka"]
    matrix = repo.mapping_matrix()
    ready = sum(1 for p in products if any(matrix.get((p.id, s.id)) for s in stores))
    pairs = sum(1 for p in products for s in stores if matrix.get((p.id, s.id)))
    return theme.stat_chips([
        ("Товаров", str(len(products))),
        ("Готовы к расчёту", f"{ready} из {len(products)}"),
        ("Связок с магазинами", str(pairs)),
    ])
