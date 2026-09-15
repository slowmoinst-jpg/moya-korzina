"""Поле адреса доставки — общий блок для экранов, которые считают цены.

Почему адрес спрашивается на видном месте, а не прячется в настройки: без него
цифры на экране не значат ничего конкретного. У Ленты одно и то же молоко стоит
99,99 ₽ в Москве и 87,99 ₽ в Екатеринбурге, а ассортимент точек различается —
бывает, что в одном городе в наличии топлёное, а в другом пастеризованное.
Показать такую цену без адреса — значит показать цену чужого города и никак об
этом не сказать.

Блок делает ровно одно: спрашивает адрес и даёт его проверить. Выбора магазина
здесь нет намеренно — Лента отдаёт по адресу список своих физических магазинов,
но её витрина доставки эти коды не принимает (проверено 15.09.2026, подробности в
app/connectors/lenta.py, _where). Поэтому список показывается как подтверждение
«адрес понят», а считается всё по самому адресу.

"""
from __future__ import annotations

import streamlit as st

from app import location as client_place
from app.ui.helpers import show_exception

PLACEHOLDER = "Москва, Ходынский бульвар 4"


def banner() -> None:
    """Строка-напоминание для экранов, где адрес нужен, но поля нет."""
    if client_place.is_set():
        return
    st.info("Адрес доставки не указан — цены считаются по магазину из настроек, "
            "а не по вашему. Укажите его на экране «Сравнение».")


def summary() -> str:
    """Короткая строка для шапки экрана."""
    return client_place.address() or "Адрес не указан"


def editor(key: str = "addr") -> bool:
    """Поле адреса и его проверка. Возвращает True, если адрес изменился.

    True нужен вызывающему, чтобы выбросить показанное: цены, снятые по старому
    адресу, к новому отношения не имеют.
    """
    changed = False
    saved = client_place.address() or ""

    with st.expander(_title(), expanded=not saved):
        st.caption("Цена и наличие у каждой сети свои в каждой точке. Без адреса "
                   "приложение посчитает по магазину из настроек — скорее всего, не вашему.")

        typed = st.text_input("Адрес доставки", value=saved, key=f"{key}_text",
                              placeholder=PLACEHOLDER)
        col1, col2 = st.columns([1, 3])

        with col1:
            if st.button("Сохранить", key=f"{key}_save", type="primary"):
                client_place.save_address(typed)
                st.session_state.pop(f"{key}_near", None)   # проверка была про старый адрес
                changed = True
                # toast, а не success: вызывающий сразу перерисует экран, и обычное
                # сообщение исчезло бы, не успев прочитаться.
                st.toast("Адрес сохранён — цены считаются по нему"
                         if (typed or "").strip()
                         else "Адрес убран — вернулись к магазину из настроек")

        with col2:
            if saved and st.button("Проверить адрес", key=f"{key}_check"):
                _check(key)

        if saved:
            _show_check(key)

    return changed


def _title() -> str:
    addr = client_place.address()
    return f"Адрес доставки — {addr}" if addr else "Адрес доставки не указан"


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
