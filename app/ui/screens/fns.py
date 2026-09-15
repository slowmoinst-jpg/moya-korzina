"""Экран «Мои чеки»: загрузка покупок из сервиса ФНС без интеграции.

ЧТО РАБОТАЕТ. Кабинет ФНС встроен прямо в экран и открывается: проверено
15.09.2026 в живом приложении, форма входа по номеру телефона видна на нашей же
странице. Первая проверка показывала пустое окно — это оказался артефакт той
среды, а не отказ сервиса; прежний вывод «в чужом окне не рисуется» был неверен.

ЧТО НЕ РАБОТАЕТ И НЕ ЗАРАБОТАЕТ. Прочитать это окно приложение не может, и здесь
дело уже не в среде, а в правиле браузера: содержимое чужого источника странице
недоступно. Замерено:

    frame.contentDocument .......... null
    frame.contentWindow.location ... SecurityError
    frame.contentDocument.cookie ... TypeError

Поэтому кнопки «загрузить само» здесь нет и быть не может — она была бы обманом.
Человек копирует чек прямо из встроенного окна и вставляет в поле ниже, не уходя
со страницы: один жест руками, всё остальное делает приложение — разбор,
предпросмотр, накопление нескольких чеков и одно сохранение.

Обойти правило можно, пропуская сайт ФНС через свой сервер с подменой адресов, —
тогда через нас пойдут код из СМС и живая сессия к налоговой. Туда мы не идём.

Полностью без копирования это заработает в мобильном приложении, где WebView
принадлежит нам, — расписано в docs/spec-moi-cheki.md. А совсем без ручного
шага — только с партнёрством ФНС, см. docs/legal/02-obrashchenie-mco.md.
"""
from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from app.ui import theme
from app.ui.helpers import goto, load, module_warning, rub, show_exception, store_selectbox

esc = theme.esc

CABINET_URL = "https://lkdr.nalog.ru/"
BUFFER = "fns_buffer"          # накопленные чеки: список {text, rows, total, date}


def render() -> None:
    _intro()
    _frame()
    _paste()
    _buffer()


def _frame() -> None:
    """Кабинет ФНС прямо в экране.

    Он открывается — проверено в живом приложении: форма входа по номеру телефона
    видна на нашей же странице. Первая проверка показывала пустое окно, но это был
    артефакт той среды, а не отказ сервиса; вывод исправлен.

    Прочитать это окно приложение не может ни при каком браузере: содержимое
    чужого источника недоступно странице (contentDocument равен null, обращение
    к адресу даёт SecurityError). Поэтому «загрузить» — это выделить в окне и
    вставить ниже, а не кнопка.

    Тумблер оставлен на случай браузера с жёсткими настройками: там окно будет
    пустым, и человеку нужен запасной путь, а не безответный белый прямоугольник.
    """
    show = st.toggle("Кабинет ФНС прямо здесь", value=True, key="fns_frame",
                     help="Окно с сервисом налоговой внутри приложения. Если браузер "
                          "запрещает сторонние окна, оно будет пустым — тогда "
                          "откройте кабинет соседней вкладкой кнопкой выше.")
    if not show:
        return

    st.caption("Войдите в окне, откройте нужный чек, выделите его и скопируйте — "
               "и вставьте в поле ниже, не уходя с этой страницы. Кнопки "
               "«загрузить само» здесь нет намеренно: браузер не даёт странице "
               "читать содержимое чужого сайта, и такая кнопка была бы обманом.")
    components.iframe(CABINET_URL, height=620, scrolling=True)
    st.caption("Если окно осталось пустым — ваш браузер запрещает сторонние окна. "
               "Это его настройка, не наша: откройте кабинет соседней вкладкой "
               "кнопкой выше, вставка всё равно останется здесь.")


def _intro() -> None:
    theme.block(
        '<div class="mk-card" style="padding:24px 26px;margin-bottom:16px;">'
        '<div class="mk-eyebrow" style="color:var(--warm);">Шаг первый</div>'
        '<div style="font-size:17px;font-weight:600;margin:6px 0 10px;">'
        'Откройте «Мои чеки онлайн» — это сервис налоговой, где хранятся ваши чеки</div>'
        '<div style="font-size:14px;color:var(--ink2);line-height:1.55;">'
        'Вход по номеру телефона и коду из СМС. Код вы вводите на сайте налоговой — '
        'мы его не видим и не запрашиваем.<br>'
        'Откройте нужный чек, выделите его <b>(Ctrl+A)</b>, скопируйте <b>(Ctrl+C)</b> '
        'и вернитесь сюда.</div>'
        f'<a class="mk-cta" style="max-width:320px;margin-top:16px;" href="{CABINET_URL}" '
        'target="_blank" rel="noopener">Открыть «Мои чеки онлайн» →</a>'
        "</div>"
    )
    with st.expander("Почему нет кнопки «загрузить само»"):
        st.markdown(
            "Показать кабинет — можно, окно ниже это и делает. А вот прочитать его "
            "приложение не может: браузер не даёт странице читать содержимое чужого "
            "сайта. Это его правило безопасности, а не наша недоработка, и обойти "
            "его нельзя никаким кодом с нашей стороны.\n\n"
            "Обойти это можно, только пропуская сайт налоговой через наш сервер — тогда "
            "через нас пойдут и код из СМС, и ваш вход. Мы так делать не будем.\n\n"
            "Ручным остаётся один жест — выделить и скопировать. Он исчезнет в "
            "мобильном приложении, а совсем — когда заработает партнёрство с ФНС: "
            "тогда чеки начнут приходить сами."
        )


def _paste() -> None:
    theme.heading("Шаг второй", "Вставьте чек")
    # Ключ с номером: очистить поле через session_state нельзя — Streamlit запрещает
    # менять состояние виджета после того, как тот отрисован. Поэтому после добавления
    # чека номер растёт, и на следующем прогоне создаётся новое, уже пустое поле.
    round_no = st.session_state.get("fns_round", 0)
    text = st.text_area(
        "Скопированный чек", height=170, key=f"fns_text_{round_no}",
        label_visibility="collapsed",
        placeholder="Ctrl+V — сюда. Понимает и чек из кабинета, и письмо из доставки, "
                    "и текст, распознанный с фото.")
    if not (text or "").strip():
        return

    preview_fn, err = load("app.importers.text_import", "preview")
    if err:
        module_warning(err)
        return
    try:
        seen = preview_fn(text)
    except Exception as exc:  # noqa: BLE001
        show_exception(exc, "Не удалось разобрать текст")
        return

    if not seen["rows"]:
        st.warning("Ни одной строки с товаром и ценой не нашлось. Скопируйте чек "
                   "вместе с ценами — без них позицию не отличить от заголовка.")
        return

    st.markdown(f"Понял **{len(seen['rows'])}** позиций на {rub(seen['total'])}"
                + (f", дата {seen['date']}" if seen.get("date") else ""))
    st.dataframe([{"Товар": r["name"], "Кол-во": r["qty"], "Цена": r["price"], "Сумма": r["total"]}
                  for r in seen["rows"]], hide_index=True, use_container_width=True)
    if seen["skipped"]:
        st.info("Эти строки я не понял, они не попадут в историю:\n\n"
                + "\n".join(f"- {line}" for line in seen["skipped"][:8]))

    if st.button("Добавить этот чек", type="primary", key="fns_add"):
        stack = st.session_state.setdefault(BUFFER, [])
        stack.append({"text": text, "rows": len(seen["rows"]),
                      "total": seen["total"], "date": seen.get("date") or ""})
        st.session_state["fns_round"] = round_no + 1
        st.rerun()


def _buffer() -> None:
    """Накопленные чеки. Их может быть несколько: человек ходит по кабинету и носит сюда."""
    stack = st.session_state.get(BUFFER) or []
    if not stack:
        return

    theme.heading("Шаг третий", f"Сохранить накопленное ({len(stack)})")
    rows = "".join(
        f'<div class="mk-row"><span>Чек {i + 1}'
        + (f' <span style="color:var(--ink3);">от {esc(item["date"])}</span>' if item["date"] else "")
        + f' · {item["rows"]} позиций</span>'
        f'<span style="white-space:nowrap;">{rub(item["total"])}</span></div>'
        for i, item in enumerate(stack)
    )
    total = round(sum(item["total"] or 0 for item in stack), 2)
    st.markdown(f'<div class="mk-card" style="padding:6px 18px 12px;">{rows}</div>',
                unsafe_allow_html=True)

    from app import repo

    store = store_selectbox(repo.list_stores(), "Магазин этих чеков (необязательно)",
                            key="fns_store", with_all=True)

    left, right = st.columns([1, 1])
    with left:
        if st.button(f"Сохранить в историю · {rub(total)}", type="primary", key="fns_save"):
            _save(stack, store.code if store else None)
    with right:
        if st.button("Очистить", key="fns_clear"):
            st.session_state[BUFFER] = []
            st.rerun()


def _save(stack: list[dict], store_code: str | None) -> None:
    fn, err = load("app.importers.text_import", "import_order_text")
    if err:
        module_warning(err)
        return
    saved = created = 0
    try:
        with st.spinner("Сохраняем…"):
            for item in stack:
                result = fn(item["text"], store_code)
                saved += result["rows"]
                created += result["products_created"]
    except Exception as exc:  # noqa: BLE001
        show_exception(exc, "Не удалось сохранить")
        return

    st.session_state[BUFFER] = []
    st.success(f"Добавлено {saved} позиций из {len(stack)} чеков · новых товаров {created}.")
    if st.button("Перейти в историю", key="fns_goto"):
        goto("История")
        st.rerun()


def header_stats() -> str:
    stack = st.session_state.get(BUFFER) or []
    if not stack:
        return "Загрузка чеков из сервиса ФНС"
    return f"Накоплено чеков: {len(stack)}"
