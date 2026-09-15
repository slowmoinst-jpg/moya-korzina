"""Поле адреса доставки — общий блок для экранов, которые считают цены.

Почему адрес спрашивается на видном месте, а не прячется в настройки: без него
цифры на экране не значат ничего конкретного. У Ленты одно и то же молоко стоит
99,99 ₽ в Москве и 87,99 ₽ в Екатеринбурге, а ассортимент точек различается —
бывает, что в одном городе в наличии топлёное, а в другом пастеризованное.
Показать такую цену без адреса — значит показать цену чужого города и никак об
этом не сказать.

Устройство блока — две ступени, как в app/location.py: человек вводит АДРЕС,
приложение показывает найденные по нему ТОЧКИ и запоминает выбранную. Вторая
ступень необязательна: пока точка не выбрана, запросы идут по адресу, просто
сеть разбирает его сама и может выбрать соседний магазин.
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
    addr = client_place.address()
    if not addr:
        return "Адрес не указан"
    _, name = client_place.point("lenta")
    return f"{addr} · Лента: {name}" if name else addr


def editor(key: str = "addr") -> bool:
    """Поле адреса и выбор точки. Возвращает True, если что-то изменилось.

    True нужен вызывающему, чтобы пересчитать показанное: цены, снятые по старому
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
                changed = True
                if (typed or "").strip():
                    st.success("Адрес сохранён. Теперь цены считаются по нему.")
                else:
                    st.info("Адрес убран — вернулись к магазину из настроек.")

        with col2:
            if saved and st.button("Подобрать точку Ленты", key=f"{key}_resolve"):
                changed = _pick_point(key) or changed

        if saved:
            changed = _current_point(key) or changed

    return changed


def _title() -> str:
    addr = client_place.address()
    return f"Адрес доставки — {addr}" if addr else "Адрес доставки не указан"


def _pick_point(key: str) -> bool:
    """Спрашивает у Ленты точки рядом с адресом и кладёт их в состояние экрана."""
    try:
        with st.spinner("Ищем ближайшие магазины…"):
            found = client_place.nearby("lenta")
    except Exception as exc:  # noqa: BLE001
        show_exception(exc, "Не получилось разобрать адрес")
        return False
    st.session_state[f"{key}_points"] = found
    if not found:
        st.warning("По этому адресу Лента точек не нашла. Проверьте написание — "
                   "или сеть просто не работает в этом городе.")
    return False


def _current_point(key: str) -> bool:
    """Показывает выбранную точку и список найденных. True — выбор изменился."""
    changed = False
    store_id, name = client_place.point("lenta")

    if store_id:
        col1, col2 = st.columns([3, 1])
        col1.caption(f"Лента отвечает по точке **{name or store_id}** — "
                     "по коду точки, он однозначнее адреса.")
        if col2.button("Забыть точку", key=f"{key}_forget"):
            client_place.forget_point("lenta")
            changed = True

    found = st.session_state.get(f"{key}_points") or []
    if not found:
        return changed

    st.caption("Найденные точки — выберите свою:")
    for hub in found[:5]:
        hid = str(hub.get("id") or "")
        if not hid:
            continue
        label = str(hub.get("name") or hid)
        where = str(hub.get("address") or "")
        distance = hub.get("distance")
        suffix = f" · {round(float(distance))} м" if isinstance(distance, (int, float)) else ""
        col1, col2 = st.columns([4, 1])
        col1.markdown(f"**{label}** — {where}{suffix}")
        if col2.button("Выбрать", key=f"{key}_pick_{hid}", disabled=(hid == store_id)):
            client_place.save_point("lenta", hid, label)
            st.session_state.pop(f"{key}_points", None)
            changed = True

    return changed
