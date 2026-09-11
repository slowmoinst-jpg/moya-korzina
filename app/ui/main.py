"""Streamlit UI «Оптимизатор продуктовой корзины».

Запуск из корня проекта:
    .venv\\Scripts\\python.exe -m streamlit run app/ui/main.py

Файл намеренно называется main.py, а не app.py: Streamlit кладёт каталог запускаемого
скрипта в sys.path, и app.py перекрыл бы собой пакет app — импорты уходили бы в себя.
"""
from __future__ import annotations

import os
import sys

# корень проекта в sys.path — на случай запуска не из корня
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st  # noqa: E402

from app import repo  # noqa: E402
from app.ui import theme  # noqa: E402
from app.ui.helpers import show_exception  # noqa: E402
from app.ui.screens import basket as basket_screen  # noqa: E402
from app.ui.screens import cards as cards_screen  # noqa: E402
from app.ui.screens import history as history_screen  # noqa: E402
from app.ui.screens import products as products_screen  # noqa: E402
from app.ui.screens import result as result_screen  # noqa: E402

SCREENS = {
    "История": history_screen.render,
    "Номенклатура": products_screen.render,
    "Корзина": basket_screen.render,
    "Результат": result_screen.render,
    "Карты и акции": cards_screen.render,
}

SCREEN_MODULES = {
    "История": history_screen,
    "Номенклатура": products_screen,
    "Корзина": basket_screen,
    "Результат": result_screen,
    "Карты и акции": cards_screen,
}

# надзаголовок над названием экрана
EYEBROWS = {
    "История": "Покупки семьи",
    "Номенклатура": "Эталоны и артикулы магазинов",
    "Корзина": "Что покупаем",
    "Результат": "Разбиение и экономия",
    "Карты и акции": "Условия, которые учитывает расчёт",
}

st.set_page_config(page_title="Оптимизатор продуктовой корзины", page_icon="🛒",
                   layout="wide", initial_sidebar_state="collapsed")
theme.inject()


@st.cache_resource
def _init_db() -> bool:
    """База создаётся сама при первом запуске (идемпотентно).

    На хостинге с эфемерным диском (Streamlit Community Cloud) база пересоздаётся при
    каждом перезапуске, поэтому пустую наполняем демо-данными — иначе приложение
    открывается пустым. Цены и сопоставления не трогаем: их подтягивает кнопка
    «Автосопоставить всё» на экране «Номенклатура».
    """
    repo.init_db()
    if not repo.list_products(active_only=False):
        try:
            tools_dir = os.path.join(_ROOT, "tools")
            if tools_dir not in sys.path:
                sys.path.insert(0, tools_dir)
            import seed

            seed.main()
        except Exception as exc:  # noqa: BLE001 — пустая база не повод не открыться
            print(f"Демо-данные не налились: {exc}")
    return True


def _header_stats(name: str) -> str:
    """Сводка справа в шапке: экран отдаёт её функцией header_stats(), если умеет."""
    module = SCREEN_MODULES.get(name)
    getter = getattr(module, "header_stats", None)
    if not getter:
        return ""
    try:
        return getter()
    except Exception:  # noqa: BLE001 — сводка не повод ронять экран
        return ""


def main() -> None:
    try:
        _init_db()
    except Exception as exc:  # noqa: BLE001
        show_exception(exc, "Не удалось инициализировать базу")
        return

    # программное переключение экрана (например, после кнопки «Рассчитать»)
    pending = st.session_state.pop("_goto", None)
    if pending in SCREENS:
        st.session_state["screen"] = pending
    if st.session_state.get("screen") not in SCREENS:
        st.session_state["screen"] = "История"

    with st.container(key="topbar", horizontal=True, vertical_alignment="center", gap="medium"):
        st.markdown(theme.logo(), unsafe_allow_html=True)
        st.radio("Экран", list(SCREENS), key="screen", horizontal=True, label_visibility="collapsed")

    name = st.session_state["screen"]
    theme.page_header(name, EYEBROWS.get(name), _header_stats(name))
    try:
        SCREENS[name]()
    except Exception as exc:  # noqa: BLE001 — UI не должен падать целиком
        show_exception(exc, f"Ошибка на экране «{name}»")


main()
