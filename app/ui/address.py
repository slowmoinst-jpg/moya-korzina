"""Адрес доставки — в шапке приложения, на виду и на каждом экране.

Так устроены все доставки, и не от моды: адрес определяет, что человек вообще
видит. У Ленты одно и то же молоко стоит 75,99 ₽ в Екатеринбурге и 99,99 ₽ в
Москве, а в наличии оказывается разное — бывает, что в одном городе топлёное, в
другом пастеризованное. Цена без адреса — это цена чужого города, показанная как
своя. Поэтому адрес не прячется в настройки и не спрашивается на одном экране из
восьми: он стоит в шапке, всегда видим и всегда переключаем.

Выбора магазина здесь нет намеренно. Лента отдаёт по адресу список своих
физических магазинов, но витрина доставки эти коды не принимает (проверено
15.09.2026, подробности в app/connectors/lenta.py, _where). Поэтому список
показывается как подтверждение «адрес понят», а считается всё по самому адресу.
"""
from __future__ import annotations

import streamlit as st

from app import location as client_place
from app.ui.helpers import show_exception

PLACEHOLDER = "Москва, Ходынский бульвар 4"


def topbar(key: str = "topaddr") -> None:
    """Кнопка адреса в шапке. Нажатие открывает панель прямо под ней.

    Перерисовка после правки обязательна и делается здесь, а не вызывающим: шапка
    рисуется раньше экрана, поэтому без неё на кнопке ещё висел бы прежний адрес,
    а под ней — уже новые цены.
    """
    # обёртка с ключом — чтобы стиль мог прижать адрес к правому краю строки
    with st.container(key="topaddr"):
        with st.popover(_button_label(), use_container_width=False):
            if _panel(key):
                _drop_shown_prices()
                st.rerun()


def _button_label() -> str:
    """Что написано на кнопке. Длинный адрес режется — шапка одна на все экраны."""
    addr = client_place.address()
    if not addr:
        return "📍 Укажите адрес"
    short = addr if len(addr) <= 34 else addr[:33].rstrip(" ,") + "…"
    return f"📍 {short}"


def _drop_shown_prices() -> None:
    """Забыть показанное: цены, снятые по прежнему адресу, к новому отношения не имеют."""
    for stale in ("cmp_last", "calc"):
        st.session_state.pop(stale, None)


def _panel(key: str) -> bool:
    """Поле адреса и его проверка. True — адрес изменился."""
    changed = False
    saved = client_place.address() or ""

    st.caption("Цена и наличие у каждой сети свои в каждой точке. Без адреса "
               "приложение посчитает по магазину из настроек — скорее всего, не вашему.")

    typed = st.text_input("Адрес доставки", value=saved, key=f"{key}_text",
                          placeholder=PLACEHOLDER)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Сохранить", key=f"{key}_save", type="primary", use_container_width=True):
            client_place.save_address(typed)
            st.session_state.pop(f"{key}_near", None)   # проверка была про старый адрес
            changed = True
            st.toast("Адрес сохранён — цены считаются по нему"
                     if (typed or "").strip()
                     else "Адрес убран — вернулись к магазину из настроек")
    with col2:
        if saved and st.button("Проверить", key=f"{key}_check", use_container_width=True):
            _check(key)

    if saved:
        _show_check(key)
    return changed


def _check(key: str) -> None:
    """Спрашивает у Ленты магазины рядом — это и есть проверка, что адрес понят."""
    try:
        with st.spinner("Проверяем адрес…"):
            st.session_state[f"{key}_near"] = client_place.nearby()
    except Exception as exc:  # noqa: BLE001
        show_exception(exc, "Не получилось проверить адрес")


def _show_check(key: str) -> None:
    """Итог проверки. Пустой ответ — тоже итог, и важный."""
    if f"{key}_near" not in st.session_state:
        return
    found = st.session_state[f"{key}_near"] or []

    if not found:
        st.warning("Лента не нашла рядом с этим адресом ни одного своего магазина. "
                   "Скорее всего, опечатка — или сеть не работает в этом городе. "
                   "Цены Ленты, скорее всего, окажутся пустыми.")
        return

    nearest = min(found, key=_distance)
    st.success(f"Адрес понят: ближайший магазин Ленты — {nearest.get('name') or '—'}, "
               f"{nearest.get('address') or ''}{_distance_text(nearest)}.")
    st.caption("Список нужен только для проверки: считается всё по самому адресу, "
               "потому что витрина доставки работает не по этим магазинам.")


def _distance(hub: dict) -> float:
    """Расстояние до магазина. Без него магазин уходит в конец, а не в начало."""
    try:
        return float(hub.get("distance"))
    except (TypeError, ValueError):
        return float("inf")


def _distance_text(hub: dict) -> str:
    """«394 м» и «9,1 км»: девять тысяч метров человек читает дольше, чем нужно."""
    metres = _distance(hub)
    if metres == float("inf"):
        return ""
    if metres < 1000:
        return f" · {round(metres)} м"
    return f" · {metres / 1000:.1f} км".replace(".", ",")
