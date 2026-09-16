"""Экран «Магазины» — откуда приложение берёт цены и что с этим может человек.

Здесь нарочно не обещано того, чего нет. Заманчиво нарисовать у каждой сети
кнопку «Подключить» и поле для входа, но правда такая: цены по адресу отдают
только те сети, которые сами открыли доступ. Остальные либо читаются с витрины
сайта, пока она не сменилась, либо не читаются вовсе — Пятёрочка и Самокат
закрыты защитой от автоматического доступа, и вход под чьей-либо учётной
записью этого не меняет, потому что проверка стоит до входа.

Поэтому у каждой сети написано ровно две вещи: как приложение получает её цены
сейчас и что человек может сделать, чтобы стало лучше. Там, где сделать нельзя
ничего, так и сказано — это честнее неработающей кнопки.
"""
from __future__ import annotations

import streamlit as st

from app import repo
from app.ui import theme
from app.ui.helpers import goto, rub

esc = theme.esc

# состояние, объяснение, что может человек
STATE_LIVE = "живые"
STATE_PARTIAL = "частично"
STATE_MANUAL = "прайс"

STORES = {
    "lenta": (
        STATE_LIVE,
        "Сеть сама открыла доступ для приложений. Отдаёт цену и остаток числом "
        "по вашему адресу — единственная из проверенных, кто сообщает наличие цифрой.",
        None,
    ),
    "vkusvill": (
        STATE_LIVE,
        "Сеть сама открыла доступ. Отдаёт цены и умеет собрать готовую корзину "
        "ссылкой — по ней товары окажутся в корзине на сайте сети.",
        None,
    ),
    "magnit": (
        STATE_LIVE,
        "Читаем витрину сайта. Работает, пока сеть не сменила вёрстку. Магазин "
        "выбирается кодом в настройках, а не адресом: по адресу он не подбирается.",
        "Код магазина задаётся в config.yaml, параметр magnit_shop_code.",
    ),
    "dixy": (
        STATE_PARTIAL,
        "Каталог читается через сторонний поисковый движок — названия и артикулы "
        "приходят. Сами цены сейчас закрыты защитой сайта, поэтому в расчёт идут "
        "ваш прайс и чеки.",
        "Принесите прайс — он закроет то, чего не даёт сайт.",
    ),
    "pyaterochka": (
        STATE_MANUAL,
        "Каталог закрыт для приложений. Цены берём из ваших чеков — это цена, "
        "по которой вы реально заплатили, — и из прайса, если вы его принесёте.",
        "Загрузите чеки на экране «Мои чеки» или положите прайс.",
    ),
    "samokat": (
        STATE_MANUAL,
        "Каталог закрыт защитой от автоматического доступа: сайт требует пройти "
        "проверку «вы не робот» ещё до входа, поэтому учётная запись тут не помогает. "
        "Цены берём из ваших чеков и прайса.",
        "Загрузите чеки на экране «Мои чеки» или положите прайс.",
    ),
}

BADGE = {
    STATE_LIVE: ("var(--green)", "Цены живые"),
    STATE_PARTIAL: ("var(--warm)", "Только каталог"),
    STATE_MANUAL: ("var(--ink3)", "Прайс и чеки"),
}


def render() -> None:
    st.caption("Цена и наличие у каждой сети свои в каждой точке, поэтому всё считается "
               "по адресу из шапки. Сменили адрес — цифры пересчитаются по нему.")

    stores = repo.list_stores()
    for store in stores:
        state, what, todo = STORES.get(store.code, (STATE_MANUAL, "Источник цен не описан.", None))
        _card(store, state, what, todo)

    st.divider()
    _prices_note()


def _card(store, state: str, what: str, todo: str | None) -> None:
    color, label = BADGE[state]
    rows = repo.list_history(store_id=store.id)
    known = f"{len(rows)} покупок в истории" if rows else "покупок в истории нет"

    terms = (f"доставка {rub(store.delivery_fee)}, бесплатно от {rub(store.free_delivery_from)}"
             if store.free_delivery_from else f"доставка {rub(store.delivery_fee)}")
    if store.min_order:
        terms += f", минимальный заказ {rub(store.min_order)}"

    st.markdown(
        '<div class="mk-card" style="padding:16px 18px;margin-bottom:10px;">'
        '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px;">'
        f'{theme.dot(store.code)}'
        f'<span style="font-weight:600;font-size:16px;">{esc(store.name)}</span>'
        f'<span style="font-size:11px;padding:3px 9px;border-radius:999px;'
        f'border:1px solid {color};color:{color};">{esc(label)}</span>'
        f'<span style="margin-left:auto;font-size:12px;color:var(--ink3);">{esc(terms)}</span>'
        '</div>'
        f'<div style="color:var(--ink2);font-size:13px;line-height:1.5;">{esc(what)}</div>'
        + (f'<div style="margin-top:8px;font-size:12px;color:var(--ink3);">'
           f'Что можно сделать: {esc(todo)}</div>' if todo else "")
        + f'<div style="margin-top:6px;font-size:12px;color:var(--ink3);">{esc(known)}</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _prices_note() -> None:
    theme.heading("Про заказ в самих магазинах")
    st.markdown(
        "Приложение считает, где дешевле, но не оформляет заказ за вас: оформление "
        "требует ваших платёжных данных, и их приложение не спрашивает и не хранит. "
        "Ближайшее, что возможно сегодня, — собрать корзину здесь и открыть её в самой "
        "сети. Так уже умеет ВкусВилл: корзина уезжает туда ссылкой."
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("К корзине", key="stores_to_basket", width="stretch", type="primary"):
            goto("Корзина")
            st.rerun()
    with col2:
        if st.button("Загрузить чеки", key="stores_to_fns", width="stretch"):
            goto("Мои чеки")
            st.rerun()


def header_stats() -> str:
    live = sum(1 for code, (state, *_) in STORES.items() if state == STATE_LIVE)
    return f"Цены живые у {live} из {len(STORES)}"
