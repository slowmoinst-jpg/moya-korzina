"""Экран «Сравнение»: один товар во всех доставках сразу.

Корзину считает оптимизатор, но перед этим у человека есть вопрос проще: а где
это вообще есть и почём? Ответ по ценникам не собирается — 930 мл за 149 ₽ и 1 л
за 155 ₽ не сравнить на глаз. Поэтому главная колонка здесь не цена, а
приведённая цена: сколько стоит килограмм или литр.

И вторая важная колонка — наличие. Пустая клетка значит «здесь не купишь», и для
сборки корзины это важнее цены.
"""
from __future__ import annotations

import streamlit as st

from app.ui import address as address_block
from app.ui import theme
from app.ui.helpers import module_warning, rub, show_exception

esc = theme.esc


def render() -> None:
    # Адрес — первым: без него цены ниже относятся к чужому магазину, и об этом
    # надо сказать до того, как человек их прочтёт, а не после.
    if address_block.editor("cmp_addr"):
        st.session_state.pop("cmp_last", None)      # цены старого адреса больше не годятся

    query = st.text_input("Что ищем", key="cmp_query",
                          placeholder="молоко 2,5% · огурцы · хлеб бородинский")
    col1, col2 = st.columns([1, 3])
    with col1:
        per_store = st.selectbox("Позиций от магазина", [1, 2, 3], key="cmp_per")
    with col2:
        st.caption("Спрашиваем все доставки разом. Ответы кэшируются на 6 часов, "
                   "поэтому повторный поиск мгновенный.")

    if not (query or "").strip():
        st.info("Введите название товара — покажу, где он есть, почём и во что обходится "
                "килограмм или литр в каждой доставке.")
        return

    if not st.button("Сравнить", type="primary", key="cmp_go") and not st.session_state.get("cmp_last"):
        return

    from app import compare

    try:
        with st.spinner("Спрашиваем магазины…"):
            offers = compare.compare_query(query, per_store=int(per_store))
    except Exception as exc:  # noqa: BLE001
        show_exception(exc, "Сравнение не получилось")
        return
    st.session_state["cmp_last"] = query

    found = [o for o in offers if o.found]
    if not found:
        st.warning("Ни в одной доставке ничего похожего не нашлось. "
                   "Попробуйте назвать товар проще — «молоко» вместо «молоко пастеризованное 2,5%».")
        _misses(offers)
        return

    _summary(compare, offers)
    _table(compare, offers)
    _misses(offers)


def _summary(compare, offers) -> None:
    best = compare.cheapest(offers)
    gap = compare.spread(offers)
    if not best:
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Дешевле всего", best.store_name, f"{rub(best.per_unit)} {best.per_unit_label}")
    if gap["diff"]:
        c2.metric("Разброс", rub(gap["diff"]), f"{gap['pct']}%")
        c3.metric("Стоит ли смотреть по сторонам",
                  "да" if (gap["pct"] or 0) >= 10 else "почти нет")


def _table(compare, offers) -> None:
    rows = sorted([o for o in offers if o.found],
                  key=lambda o: (not o.in_stock, o.per_unit or 1e9))
    best = compare.cheapest(offers)
    body = ""
    for offer in rows:
        win = best is not None and offer.sku == best.sku and offer.store_code == best.store_code
        price = (f'<b>{rub(offer.per_unit)}</b> <span style="color:var(--ink3);">'
                 f'{esc(offer.per_unit_label)}</span>')
        stock = ("" if offer.in_stock else
                 '<span style="color:var(--red);font-size:12px;">нет в наличии</span>')
        link = (f'<a href="{esc(offer.url)}" target="_blank" rel="noopener" '
                'style="font-size:12px;">карточка →</a>' if offer.url else "")
        body += (
            '<div class="mk-row" style="align-items:flex-start;'
            + ('background:var(--green-soft);' if win else "")
            + '">'
            f'<span>{theme.dot(offer.store_code)}<b>{esc(offer.store_name)}</b> '
            f'<span style="color:var(--ink2);">{esc(offer.name)}</span> {stock} {link}</span>'
            f'<span style="white-space:nowrap;text-align:right;">{price}<br>'
            f'<span style="font-size:12px;color:var(--ink3);">за упаковку {rub(offer.price)}</span>'
            "</span></div>"
        )
    st.markdown(f'<div class="mk-card" style="padding:6px 18px 12px;">{body}</div>',
                unsafe_allow_html=True)


def _misses(offers) -> None:
    misses = [o for o in offers if not o.found]
    if not misses:
        return
    text = " · ".join(f"{o.store_name}: {o.note or 'пусто'}" for o in misses)
    st.caption("Не показали: " + text)


def header_stats() -> str:
    return address_block.summary()
