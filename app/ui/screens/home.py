"""Экран «Главная» — первое, что видит человек, открыв приложение.

Делает две вещи и обе нужны.

ГОВОРИТ, ЧТО ЭТО. Человек, открывший приложение впервые, видел раньше таблицу
покупок и не понимал, зачем она. Здесь одной фразой сказано, ради чего всё:
купить тот же список дешевле, разложив его между доставками.

ПОКАЗЫВАЕТ, С ЧЕГО НАЧАТЬ. Путей ровно три, и они не равнозначны — это не меню,
а порядок. Магазины дают цены, чеки дают историю покупок, корзина превращает то
и другое в ответ «где дешевле». Поэтому у каждого пути видно его состояние:
сколько магазинов на связи, сколько покупок загружено, что лежит в корзине.
Пустое состояние — тоже подсказка, куда идти дальше.

Карточки намеренно не прячут того, чего нет. Если чеков ноль, так и написано:
это единственный способ дать человеку понять, почему расчёт пока беден.
"""
from __future__ import annotations

import streamlit as st

from app import repo
from app.ui import theme
from app.ui.helpers import goto, rub

esc = theme.esc

# Как приложение получает цены у каждой сети. Держится здесь, потому что это
# ответ человеку «почему у одних магазинов цены живые, а у других нет», а не
# внутренняя подробность коннекторов.
PRICE_SOURCE = {
    "lenta": ("Живые цены", "Сеть сама открыла доступ — цены и остаток по вашему адресу"),
    "vkusvill": ("Живые цены", "Сеть сама открыла доступ — цены по вашему адресу"),
    "magnit": ("Живые цены", "Читаем витрину сайта; магазин задаётся в настройках"),
    "dixy": ("Частично", "Каталог читается, цены сейчас закрыты защитой сайта"),
    "pyaterochka": ("Прайс и чеки", "Каталог закрыт — цены берём из вашего прайса и чеков"),
    "samokat": ("Прайс и чеки", "Каталог закрыт — цены берём из вашего прайса и чеков"),
}


def render() -> None:
    _hero()
    st.divider()
    theme.heading("С чего начать", "Три шага, и каждый полезен сам по себе")
    col1, col2, col3 = st.columns(3)
    with col1:
        _stores_card()
    with col2:
        _receipts_card()
    with col3:
        _basket_card()


# ---------- приветствие ----------
def _hero() -> None:
    st.markdown(
        '<div class="mk-card" style="padding:26px 28px;">'
        '<div class="mk-eyebrow" style="margin-bottom:9px;">Зачем это</div>'
        '<div class="mk-serif" style="font-size:30px;line-height:1.16;margin-bottom:12px;">'
        'Тот же список — меньше итог</div>'
        '<div style="max-width:720px;color:var(--ink2);line-height:1.55;">'
        'Приложение покупает ваши привычные товары через сервисы доставки и выбирает, '
        'где каждый из них дешевле. Оно сравнивает цены нескольких сетей по вашему адресу, '
        'учитывает платную доставку и порог бесплатной, добавляет кэшбэк по вашим картам — '
        'и показывает, как разложить корзину, чтобы заплатить меньше.'
        '</div></div>',
        unsafe_allow_html=True,
    )


# ---------- карточки путей ----------
def _card_open(number: str, title: str, text: str) -> None:
    st.markdown(
        '<div style="border:1px solid var(--line);border-radius:14px;padding:18px 18px 12px;'
        'background:var(--surface);min-height:186px;">'
        f'<div class="mk-eyebrow" style="margin-bottom:7px;">Шаг {esc(number)}</div>'
        f'<div style="font-weight:600;font-size:17px;margin-bottom:7px;">{esc(title)}</div>'
        f'<div style="color:var(--ink2);font-size:13px;line-height:1.5;">{text}</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _stores_card() -> None:
    stores = repo.list_stores()
    live = sum(1 for s in stores if PRICE_SOURCE.get(s.code, ("", ""))[0] == "Живые цены")
    _card_open(
        "первый", "Магазины",
        f"Откуда берутся цены. Сейчас на связи <b>{live}</b> из {len(stores)}: "
        "они отвечают ценами и наличием по вашему адресу. Остальным можно принести "
        "свой прайс.",
    )
    if st.button("Посмотреть магазины", key="home_to_stores", width="stretch"):
        goto("Магазины")
        st.rerun()


def _receipts_card() -> None:
    rows = repo.list_history()
    last = rows[0].get("date") if rows else None
    if rows:
        text = (f"Что вы покупаете обычно. Загружено <b>{len(rows)}</b> покупок, "
                f"последняя от {esc(str(last))}. Из них собирается корзина в один клик.")
    else:
        text = ("Что вы покупаете обычно. Пока не загружено ничего — без истории "
                "корзину придётся набирать руками. Чеки берутся из «Моих чеков» ФНС "
                "или из письма о доставке.")
    _card_open("второй", "Мои чеки", text)
    if st.button("Загрузить покупки", key="home_to_fns", width="stretch"):
        goto("Мои чеки")
        st.rerun()


def _basket_card() -> None:
    baskets = repo.list_baskets()
    items = repo.basket_items(int(baskets[0]["id"])) if baskets else []
    if items:
        text = (f"Что покупаем сейчас. В корзине <b>{len(items)}</b> позиций — "
                "спросите цены по магазинам и посчитайте, как разложить дешевле.")
    else:
        text = ("Что покупаем сейчас. Соберите её «как в прошлый раз», по среднему "
                "за месяц — или с нуля, выбирая товары по одному.")
    _card_open("третий", "Корзина", text)
    if st.button("Собрать корзину", key="home_to_basket", width="stretch", type="primary"):
        goto("Корзина")
        st.rerun()


def header_stats() -> str:
    rows = repo.list_history()
    if not rows:
        return "Начните с загрузки чеков"
    total = sum(float(r.get("total") or 0) for r in rows)
    return f"Загружено покупок: {len(rows)} на {rub(total)}"
