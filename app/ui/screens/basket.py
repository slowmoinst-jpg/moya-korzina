"""Экран «Корзина»: выбор/создание корзины, позиции, запуск расчёта."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app import repo
from app.ui import theme
from app.ui.helpers import goto, load, module_warning, num, rub, show_exception, unit_label


def render() -> None:
    basket = _pick_basket()
    if basket is None:
        return

    basket_id = int(basket["id"])
    st.caption(f"Корзина #{basket_id} · создана {basket.get('created_at')} · источник: {basket.get('source')}")

    products = repo.list_products()
    _add_item(basket_id, products)
    st.divider()
    items = repo.basket_items(basket_id)
    _items(basket_id, items)
    st.divider()
    _calculate(basket_id, basket.get("name"), items)


# ---------- выбор / создание корзины ----------
def _pick_basket():
    baskets = repo.list_baskets()
    col1, col2 = st.columns([2, 2])

    with col1:
        if baskets:
            ids = [b["id"] for b in baskets]
            saved = st.session_state.get("basket_id")
            index = ids.index(saved) if saved in ids else 0
            chosen = st.selectbox(
                "Корзина",
                baskets,
                index=index,
                format_func=lambda b: f"{b['name']} (#{b['id']})",
                key="basket_select",
            )
            st.session_state["basket_id"] = chosen["id"]
        else:
            chosen = None
            st.info("Корзин пока нет — создайте первую справа.")

    with col2:
        _from_history()
        with st.form("new_basket_form", clear_on_submit=True):
            name = st.text_input("Название новой корзины", placeholder="Например: Неделя 38")
            if st.form_submit_button("Создать корзину"):
                if not name.strip():
                    st.error("Напишите название корзины.")
                else:
                    new_id = repo.create_basket(name.strip())
                    st.session_state["basket_id"] = new_id
                    st.rerun()

    return chosen


def _price_matrix(items, stores) -> tuple[dict, dict]:
    """(product_id, store_code) -> стоимость позиции целиком; и сумма корзины по магазину."""
    cell: dict[tuple[int, str], float] = {}
    totals: dict[str, float] = {s.code: 0.0 for s in stores}
    for item in items:
        pid = int(item["product_id"])
        qty = float(item.get("qty") or 0)
        is_kg = (item.get("unit") or "pcs") == "kg"
        for store in stores:
            snap = repo.latest_price_for(pid, store.id)
            if not snap:
                continue
            base = (snap.get("price_per_kg") or snap.get("price")) if is_kg else snap.get("price")
            if base is None:
                continue
            value = round(float(base) * qty, 2)
            cell[(pid, store.code)] = value
            totals[store.code] += value
    return cell, {k: round(v, 2) for k, v in totals.items()}


def _price_html(pid: int, stores, cell, live: dict | None = None) -> str:
    """Цены этой позиции по магазинам одной строкой.

    Два источника, и они не равны. Снимок из базы — то, что когда-то сняли, он
    может быть недельной давности и снят по другому адресу. Живой опрос — цена
    сегодняшняя и по адресу из шапки, и у него же есть наличие. Поэтому живая
    цена вытесняет снимок, а не дополняет его.

    Пустая клетка и «нет» — разные ответы. Пусто значит «не спрашивали»,
    «нет» — «спросили, и товара там не продают». Второе для сборки корзины
    важнее цены: в такой магазин человека посылать незачем.
    """
    known: dict[str, tuple[float, bool]] = {}
    for store in stores:
        offer = (live or {}).get((pid, store.code))
        if offer is not None:
            if offer.price is not None:
                known[store.code] = (float(offer.price), bool(offer.in_stock))
            continue
        if (pid, store.code) in cell:
            known[store.code] = (cell[(pid, store.code)], True)

    if not known:
        return '<span style="font-size:12px;color:var(--ink3);">цен нет</span>'

    # победителя выбираем только среди того, что реально можно купить
    available = [value for value, in_stock in known.values() if in_stock]
    best = min(available) if available else None

    parts = []
    for store in stores:
        found = known.get(store.code)
        if found is None:
            continue
        value, in_stock = found
        if not in_stock:
            parts.append(
                f'<span style="display:inline-flex;align-items:center;gap:6px;color:var(--ink3);'
                f'text-decoration:line-through;">{theme.dot(store.code)}{rub(value)}</span>'
            )
            continue
        is_best = best is not None and abs(value - best) < 0.005
        style = "font-weight:600;color:var(--green);" if is_best else "color:var(--ink3);"
        parts.append(
            f'<span style="display:inline-flex;align-items:center;gap:6px;{style}">'
            f'{theme.dot(store.code)}{rub(value)}</span>'
        )
    return '<span style="display:flex;gap:14px;flex-wrap:wrap;font-size:13px;">' + "".join(parts) + "</span>"


# ---------- живые цены по магазинам ----------
def _live_key(basket_id: int) -> str:
    """Ключ хранения. Адрес в ключе обязателен: цены другого города — чужие цены."""
    from app import location as client_place
    return f"live_{basket_id}_{client_place.address() or 'нет'}"


def _ask_stores(basket_id: int, items) -> None:
    """Спрашивает цены и наличие по всем позициям сразу.

    Идём по позициям, а не по магазинам, потому что показать прогресс осмысленно
    можно только так: человек видит, какой товар сейчас спрашивается.
    """
    from app import compare

    found: dict[tuple[int, str], object] = {}
    bar = st.progress(0.0, text="Спрашиваем магазины…")
    total = max(1, len(items))
    for n, item in enumerate(items, start=1):
        pid = int(item["product_id"])
        name = item.get("name") or ""
        bar.progress(n / total, text=f"{name[:44]} — {n} из {total}")
        try:
            for offer in compare.compare_query(name, per_store=1):
                if offer.found:
                    found[(pid, offer.store_code)] = offer
        except Exception as exc:  # noqa: BLE001 — один товар не должен ронять весь опрос
            show_exception(exc, f"«{name}» спросить не удалось")
    bar.empty()
    st.session_state[_live_key(basket_id)] = found


def _live_summary(items, stores, live: dict) -> None:
    """Сколько позиций каждый магазин закрывает. Без этого цена магазина обманчива.

    Магазин с самой низкой суммой может просто не иметь половины корзины: сумма
    у него меньше, потому что в ней меньше товаров, а не потому что дешевле.
    """
    if not live:
        return
    rows = []
    for store in stores:
        covered = sum(1 for it in items
                      if (int(it["product_id"]), store.code) in live
                      and live[(int(it["product_id"]), store.code)].in_stock)
        if covered:
            rows.append((store, covered))
    if not rows:
        return
    cells = " ".join(
        f'<span style="display:inline-flex;align-items:center;gap:7px;margin-left:16px;">'
        f'{theme.dot(s.code)}<span>{n} из {len(items)}</span></span>'
        for s, n in rows
    )
    st.markdown(
        '<div style="display:flex;justify-content:space-between;align-items:center;gap:16px;'
        'flex-wrap:wrap;padding:11px 16px;border:1px solid var(--line);border-radius:11px;'
        'margin-top:8px;font-size:13px;color:var(--ink2);">'
        '<span style="font-weight:600;">Позиций в наличии</span>'
        f'<span>{cells}</span></div>',
        unsafe_allow_html=True,
    )


def _ask_button(basket_id: int, items, live: dict) -> None:
    """Кнопка живого опроса и честное предупреждение о его цене во времени."""
    from app import location as client_place

    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("Узнать цены по магазинам", key=f"ask_{basket_id}",
                     type="primary", use_container_width=True):
            _ask_stores(basket_id, items)
            st.rerun()
    with col2:
        addr = client_place.address()
        if not addr:
            st.caption("Адрес не указан — магазины ответят ценами не вашей точки. "
                       "Укажите его в шапке, кнопкой с булавкой.")
        elif live:
            st.caption(f"Цены по адресу «{addr}». Ответы держатся 6 часов, "
                       "повторный опрос мгновенный.")
        else:
            st.caption(f"Спросим все доставки по адресу «{addr}». "
                       f"Позиций {len(items)}, это займёт около минуты.")


def _live_totals(items, stores, live: dict, cell: dict, totals: dict) -> dict:
    """Пересчёт сумм по магазинам с учётом живых цен.

    В сумму идёт только то, что в магазине ЕСТЬ. Складывать цену отсутствующего
    товара — значит обещать корзину, которую не соберут.
    """
    out = dict(totals)
    for store in stores:
        total = 0.0
        for item in items:
            pid = int(item["product_id"])
            qty = float(item.get("qty") or 0)
            is_kg = (item.get("unit") or "pcs") == "kg"
            offer = live.get((pid, store.code))
            if offer is not None:
                if offer.price is None or not offer.in_stock:
                    continue
                base = offer.per_unit if (is_kg and offer.per_unit) else offer.price
                total += round(float(base) * qty, 2)
            elif (pid, store.code) in cell:
                total += cell[(pid, store.code)]
        out[store.code] = round(total, 2)
    return out


# ---------- добавление позиции ----------
def _add_item(basket_id: int, products) -> None:
    theme.heading("Добавить позицию")
    if not products:
        st.info("Список товаров пуст. Заведите их на экране «Товары».")
        return

    col1, col2, col3 = st.columns([4, 2, 2])
    with col1:
        product = st.selectbox(
            "Товар",
            products,
            format_func=lambda p: f"{p.name} ({unit_label(p.unit)})",
            key="basket_add_product",
        )
    is_kg = bool(product) and product.unit == "kg"
    with col2:
        qty = st.number_input(
            "Количество, " + unit_label(product.unit if product else "pcs"),
            min_value=0.0,
            step=0.001 if is_kg else 1.0,
            value=1.0,
            format="%.3f" if is_kg else "%.0f",
            key="basket_add_qty",
        )
    with col3:
        st.write("")
        st.write("")
        if st.button("Добавить", type="primary", key="basket_add_btn"):
            if not product or qty <= 0:
                st.error("Количество должно быть больше нуля.")
            else:
                repo.set_basket_item(basket_id, product.id, float(qty))
                st.rerun()


# ---------- редактирование позиций ----------
def _items(basket_id: int, items) -> None:
    theme.heading("Позиции корзины")
    if not items:
        st.info("Корзина пуста — добавьте позиции выше.")
        return

    stores = repo.list_stores()
    cell, totals = _price_matrix(items, stores)
    live = st.session_state.get(_live_key(basket_id)) or {}

    # Кнопка вне формы: внутри формы она сработала бы только вместе с сохранением.
    _ask_button(basket_id, items, live)
    if live:
        totals = _live_totals(items, stores, live, cell, totals)

    with st.form("basket_items_form"):
        head = st.columns([4, 2, 1, 3])
        head[0].markdown("**Товар**")
        head[1].markdown("**Количество**")
        head[2].markdown("**Удалить**")
        head[3].markdown("**Цены по магазинам**")

        widgets = []
        for item in items:
            pid = int(item["product_id"])
            unit = item.get("unit") or "pcs"
            is_kg = unit == "kg"
            cols = st.columns([4, 2, 1, 3])
            cols[0].write(f"{item.get('name')}" + (f" · {item['brand']}" if item.get("brand") else ""))
            qty = cols[1].number_input(
                unit_label(unit),
                min_value=0.0,
                step=0.001 if is_kg else 1.0,
                value=float(item.get("qty") or 0),
                format="%.3f" if is_kg else "%.0f",
                key=f"qty_{basket_id}_{pid}",
                label_visibility="collapsed",
            )
            remove = cols[2].checkbox("x", key=f"del_{basket_id}_{pid}", label_visibility="collapsed")
            cols[3].markdown(_price_html(pid, stores, cell, live), unsafe_allow_html=True)
            widgets.append((pid, qty, remove))

        priced = [s for s in stores if totals.get(s.code)]
        if priced:
            cells = " ".join(
                f'<span style="display:inline-flex;align-items:center;gap:7px;margin-left:18px;">'
                f'{theme.dot(s.code)}<span class="mk-serif" style="font-size:18px;">{rub(totals[s.code])}</span></span>'
                for s in priced
            )
            st.markdown(
                '<div style="display:flex;justify-content:space-between;align-items:center;gap:16px;'
                'padding:13px 16px;background:var(--tint);border-radius:11px;margin-top:8px;">'
                '<span style="font-weight:600;font-size:13px;">Вся корзина в одном магазине</span>'
                f'<span>{cells}</span></div>',
                unsafe_allow_html=True,
            )

        _live_summary(items, stores, live)

        c1, c2 = st.columns(2)
        save = c1.form_submit_button("Сохранить изменения", type="primary")
        clear = c2.form_submit_button("Очистить корзину")

    if save:
        for pid, qty, remove in widgets:
            repo.set_basket_item(basket_id, pid, 0.0 if remove else float(qty))
        st.success("Сохранено.")
        st.rerun()
    if clear:
        repo.clear_basket(basket_id)
        st.rerun()


# ---------- расчёт ----------
def _calculate(basket_id: int, basket_name, items) -> None:
    theme.heading("Расчёт")
    if not items:
        st.info("Добавьте позиции — тогда посчитаем.")
        return

    if st.button("Рассчитать", type="primary", key="calc_btn"):
        fn, err = load("app.service", "calculate")
        if err:
            module_warning(err)
            return
        try:
            with st.spinner("Обновляем цены и подбираем варианты…"):
                variants, baseline = fn(basket_id, True)
        except ImportError as exc:
            module_warning(f"Расчёт недоступен: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            show_exception(exc, "Расчёт не получился")
            return

        st.session_state["calc"] = {
            "basket_id": basket_id,
            "basket_name": basket_name,
            "variants": list(variants or []),
            "baseline": baseline,
        }
        goto("Результат")
        st.rerun()

    previous = repo.list_variants(basket_id)
    if previous:
        with st.expander(f"Сохранённые расчёты ({len(previous)})"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Когда": v.get("created_at"),
                            "Итого": rub(v.get("total")),
                            "Базовая сумма": rub(v.get("baseline")),
                            "Экономия": rub(v.get("savings_rub")),
                            "%": num(v.get("savings_pct")),
                        }
                        for v in previous
                    ]
                ),
                hide_index=True,
            )


def header_stats() -> str:
    # на первом заходе ключа в session_state ещё нет — берём первую корзину
    basket_id = st.session_state.get("basket_id")
    if not basket_id:
        baskets = repo.list_baskets()
        basket_id = baskets[0]["id"] if baskets else None
    if not basket_id:
        return ""
    items = repo.basket_items(int(basket_id))
    if not items:
        return theme.stat_chips([("Позиций", "0")])
    _, totals = _price_matrix(items, repo.list_stores())
    priced = [(s.name, totals[s.code]) for s in repo.list_stores() if totals.get(s.code)]
    return theme.stat_chips([("Позиций", str(len(items)))] + [(n, rub(v)) for n, v in priced])


# ---------- корзина из истории покупок ----------
def _from_history() -> None:
    """Собрать корзину по прошлой покупке или по среднему за месяц (раздел 5.3)."""
    from app import baskets

    left, right = st.columns(2)
    made = None
    with left:
        if st.button("Как в прошлый раз", key="basket_from_last", width="stretch"):
            made = ("history", baskets.build_from_history(baskets.LAST))
    with right:
        if st.button("По среднему за месяц", key="basket_from_avg", width="stretch"):
            made = ("average", baskets.build_from_history(baskets.AVERAGE))

    if not made:
        return
    kind, result = made
    if not result["basket_id"]:
        st.warning("В истории пока нет покупок, из которых можно собрать корзину. "
                   "Загрузите чек на экране «История».")
        return

    st.session_state["basket_id"] = result["basket_id"]
    if kind == "average":
        st.success(f"Собрали корзину по среднему: {len(result['items'])} позиций "
                   f"за {result['months']} мес.")
    else:
        st.success(f"Повторили покупку от {result['date']}: {len(result['items'])} позиций.")
    if result["skipped"]:
        st.caption(f"Пропустили {result['skipped']}: этих товаров больше нет в справочнике.")
    st.rerun()
