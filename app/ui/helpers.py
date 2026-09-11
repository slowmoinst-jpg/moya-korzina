"""Вспомогательные функции UI: форматирование, ленивый импорт внешних модулей, файлы."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import traceback
from typing import Any

import streamlit as st

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NBSP = " "


def ensure_root_on_path() -> None:
    """Корень проекта в sys.path — на случай запуска не из корня."""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)


# ---------- форматирование ----------
def rub(value: Any) -> str:
    """1234.56 -> «1 234,56 ₽»."""
    if value is None or value == "":
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{v:,.2f}".replace(",", NBSP).replace(".", ",") + NBSP + "₽"


def pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}".replace(".", ",") + NBSP + "%"
    except (TypeError, ValueError):
        return "—"


def num(value: Any, digits: int = 3) -> str:
    """Число без хвостовых нулей, с запятой как разделителем."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.{digits}f}".rstrip("0").rstrip(".").replace(".", ",")


def unit_label(unit: str | None) -> str:
    return "кг" if (unit or "pcs") == "kg" else "шт"


def qty_str(value: Any, unit: str | None = "pcs") -> str:
    return f"{num(value)} {unit_label(unit)}"


# ---------- ленивый импорт модулей, которые пишут параллельно ----------
def load(module_path: str, *names: str):
    """Ленивый импорт. Возвращает (объект | кортеж объектов, None) либо (None, текст ошибки)."""
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        return None, f"Модуль `{module_path}` ещё не готов ({exc})."
    except Exception as exc:  # модуль есть, но падает при импорте
        return None, f"Не удалось загрузить `{module_path}`: {type(exc).__name__}: {exc}"
    out = []
    for name in names:
        obj = getattr(mod, name, None)
        if obj is None:
            return None, f"В модуле `{module_path}` пока нет `{name}` — функция ещё не написана."
        out.append(obj)
    return (out[0] if len(out) == 1 else tuple(out)), None


def module_warning(message: str) -> None:
    st.warning(f"{message}\n\nЭкран работает, возможность появится, когда соседний модуль будет готов.")


def show_exception(exc: BaseException, prefix: str = "Ошибка") -> None:
    st.error(f"{prefix}: {type(exc).__name__}: {exc}")
    with st.expander("Подробности"):
        st.code("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))


# ---------- файлы ----------
def save_upload(uploaded) -> str:
    """Сохраняет загруженный файл во временный и возвращает путь."""
    suffix = os.path.splitext(uploaded.name)[1] or ".txt"
    fd, path = tempfile.mkstemp(prefix="basket_upload_", suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(uploaded.getbuffer())
    return path


def drop_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ---------- общие виджеты ----------
def store_selectbox(stores, label: str = "Магазин", key: str | None = None, with_all: bool = False):
    """Выбор магазина. with_all=True добавляет пункт «Все магазины» (значение None)."""
    if not stores:
        st.info("Справочник магазинов пуст — он заполняется при инициализации базы.")
        return None
    options = ([None] + list(stores)) if with_all else list(stores)
    return st.selectbox(
        label,
        options,
        format_func=lambda s: "Все магазины" if s is None else s.name,
        key=key,
    )


def goto(screen: str) -> None:
    """Переключить экран на следующем прогоне (до создания виджета радио)."""
    st.session_state["_goto"] = screen
