"""Экран «Мои чеки»: загрузка покупок из сервиса ФНС.

ЧТО РАБОТАЕТ САМО. Закладка «Забрать все чеки» (docs/grab.src.js) запускается на
странице кабинета и обходит его целиком: список чеков постранично, затем позиции
по каждому чеку — теми же запросами, какие кабинет делает сам, когда человек
листает список и открывает чек. На выходе один файл, который здесь и загружается.
Проверено на поддельном кабинете: 250 чеков, 250 запросов за позициями, ни одного
без пропуска, повторный заход не берёт ничего.

ПОЧЕМУ ЭТО ЗАКЛАДКА, А НЕ КНОПКА ЗДЕСЬ. Кабинет встроен в экран окном ниже и
открывается, но прочитать это окно приложение не может ни при каком браузере:
содержимое чужого источника странице недоступно. Замерено:

    frame.contentDocument .......... null
    frame.contentWindow.location ... SecurityError
    frame.contentDocument.cookie ... TypeError

Закладка обходит это не хитростью, а тем, что запускается ВНУТРИ страницы
кабинета: для браузера она часть этой страницы. Ключ доступа остаётся в браузере
и уходит только обратно в кабинет — через наш сервер не идёт ни вход, ни код из
СМС, и это та граница, за которую мы не пойдём (см. обсуждение прокси ниже).

ЧТО ОСТАЛОСЬ РУЧНЫМ. Нажать закладку в кабинете и принести сюда сохранённый файл.
Полностью без этого заработает в мобильном приложении, где WebView принадлежит
нам (docs/spec-moi-cheki.md), а совсем — по партнёрству с ФНС
(docs/legal/02-obrashchenie-mco.md). Вставка текстом осталась запасным путём: она
не знает ничего об устройстве кабинета и потому переживёт любые его изменения.
"""
from __future__ import annotations

import os

import streamlit as st
import streamlit.components.v1 as components

from app.ui import theme
from app.ui.helpers import (drop_file, goto, load, module_warning, rub, save_upload,
                            show_exception, store_selectbox)

esc = theme.esc

CABINET_URL = "https://lkdr.nalog.ru/"
BUFFER = "fns_buffer"          # накопленные чеки: список {text, rows, total, date}
BOOKMARKLET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "docs", "grab.min.txt")


def render() -> None:
    _status()
    _auto()
    _frame()
    _paste()
    _buffer()


# ---------- сколько загружено и что ещё можно ----------
def _status() -> None:
    """Первое, что человек видит: сколько у него уже есть и чего не хватает.

    Без этого совет «загрузите чеки» невыполним: непонятно, сколько нужно, сколько
    уже сделано и когда можно остановиться.
    """
    mod, err = load("app.receipts", "summary")
    if err:
        module_warning(err)
        return
    data = mod()
    have, left = data["loaded"], data["pending"]

    # «Чеков 0» рядом с «Позиций 16» выглядит поломкой, а это не поломка: так
    # показываются покупки, загруженные до того, как появился учёт чеков. Поэтому
    # пустую плашку не рисуем вовсе, а причину объясняем строкой ниже.
    chips = [("Позиций", str(have["rows"]))]
    if have["receipts"]:
        chips.insert(0, ("Чеков", str(have["receipts"])))
    if have["total"]:
        chips.append(("На сумму", rub(have["total"])))
    if have["months"]:
        chips.append(("Срок", f"{have['months']} мес."))
    if left["receipts"]:
        chips.append(("Не загружено", str(left["receipts"])))

    theme.block(
        '<div class="mk-card" style="padding:20px 24px;margin-bottom:16px;">'
        '<div class="mk-eyebrow" style="color:var(--warm);">Ваша история</div>'
        f'<div style="font-size:16px;line-height:1.5;margin:6px 0 12px;">{esc(data["headline"])}</div>'
        + theme.stat_chips(chips)
        + (f'<div style="font-size:13px;color:var(--ink3);margin-top:10px;">'
           f'{have["rows_without_receipt"]} позиций загружены до того, как появился учёт '
           f'чеков, — какому чеку они принадлежат, теперь уже не узнать.</div>'
           if have["rows_without_receipt"] and not have["receipts"] else "")
        + "</div>"
    )

    steps = data["next"]
    if steps:
        rows = "".join(
            f'<div class="mk-row"><span><b>{esc(s["name"])}</b><br>'
            f'<span style="font-size:13px;color:var(--ink3);">{esc(s["gives"])}</span></span>'
            f'<span style="font-size:13px;color:var(--ink2);text-align:right;max-width:46%;">'
            f'{esc(s["how"])}</span></div>'
            for s in steps[:5])
        with st.expander(f"Что можно загрузить ещё · {len(steps)}", expanded=bool(left["receipts"])):
            theme.block(f'<div class="mk-card" style="padding:4px 18px 10px;">{rows}</div>')


# ---------- автоматический сбор ----------
def _auto() -> None:
    theme.heading("Шаг первый", "Заберите чеки из кабинета одной кнопкой")
    try:
        with open(BOOKMARKLET, encoding="utf-8") as fh:
            href = fh.read().strip()
    except OSError:
        href = ""

    theme.block(
        '<div class="mk-card" style="padding:22px 24px 14px;margin-bottom:0;">'
        '<div style="font-size:14px;color:var(--ink2);line-height:1.6;">'
        'Перетащите ссылку ниже на панель закладок браузера, откройте кабинет ФНС и '
        'нажмите её там. Закладка обойдёт весь список чеков, заберёт позиции по каждому '
        'и сохранит один файл — его и загрузите следом.<br>'
        '<b>Второй раз</b> она возьмёт только новые чеки, так что нажимать можно хоть '
        'каждую неделю.</div></div>'
    )
    if not href:
        st.error("Закладка не собрана — выполните `python tools/build_bookmarklet.py`")
    else:
        # Ссылку рисуем компонентом, а не обычной разметкой, и это не украшательство:
        # Streamlit вырезает адреса вида javascript: из своего HTML — проверено, на
        # экране оставался «#». Внутри компонента свой документ, и адрес доживает до
        # панели закладок целым.
        components.html(
            '<div style="font:500 14px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;'
            'padding:2px 0 0;">'
            f'<a href="{href}" draggable="true" onclick="return false;" '
            'title="Перетащите эту ссылку на панель закладок" '
            'style="display:inline-block;background:#BD6533;color:#fff;text-decoration:none;'
            'padding:12px 22px;border-radius:11px;font-weight:600;cursor:grab;'
            'box-shadow:0 8px 20px -10px rgba(189,101,51,.9);">⬆ Забрать все чеки</a>'
            '<span style="color:#8A8178;margin-left:12px;font-weight:400;">'
            '← потяните мышью на панель закладок</span></div>',
            height=64)
    if href:
        with st.expander("Ссылка не перетаскивается — завести закладку руками"):
            st.caption("Создайте любую закладку, откройте её свойства и вставьте в адрес "
                       "этот текст целиком. Имя — любое.")
            st.code(href, language=None)

    _upload()


def _upload() -> None:
    theme.heading("Шаг второй", "Загрузите сохранённый файл")
    col1, col2 = st.columns([2, 1])
    with col1:
        uploaded = st.file_uploader("Файл с чеками из кабинета", type=["json"],
                                    key="fns_bundle", label_visibility="collapsed")
    with col2:
        from app import repo

        store = store_selectbox(repo.list_stores(), "Магазин (обычно не нужен)",
                                key="fns_bundle_store", with_all=True)
    if uploaded is None:
        return

    if st.button("Загрузить чеки", type="primary", key="fns_bundle_btn"):
        fn, err = load("app.importers", "import_bundle")
        if err:
            module_warning(err)
            return
        path = save_upload(uploaded)
        try:
            with st.spinner("Разбираем чеки…"):
                with open(path, encoding="utf-8") as fh:
                    result = fn(fh.read(), store.code if store else None)
        except Exception as exc:  # noqa: BLE001 — показываем человеку, а не падаем
            show_exception(exc, "Не удалось разобрать файл")
            return
        finally:
            drop_file(path)
        _report(result)


def _report(result: dict) -> None:
    """Сколько загружено — числами, а не словом «готово»."""
    if not result["receipts"] and not result["skipped"]:
        st.warning("В файле не нашлось ни одного чека с позициями.")
        return

    # Числа склоняем: «2 чеков» в отчёте о собственной работе читается как небрежность.
    words, err = load("app.receipts", "plural", "human")
    say = words[0] if not err else (lambda n, *forms: f"{n} {forms[-1]}")
    day = words[1] if not err else (lambda iso: iso)

    if result["receipts"]:
        line = (f"Загружено **{say(result['receipts'], 'чек', 'чека', 'чеков')}** · "
                f"**{say(result['rows'], 'позиция', 'позиции', 'позиций')}** "
                f"на {rub(result['total'])}")
        if result.get("period"):
            first, last = result["period"]
            line += (f" · с {day(first)} по {day(last)}" if first != last else f" · за {day(last)}")
        st.success(line)
    if result["skipped"]:
        st.info(f"Пропущено {say(result['skipped'], 'чек', 'чека', 'чеков')} — "
                "они уже были загружены раньше.")
    if result.get("products_created"):
        st.caption(f"Новых товаров в справочнике: {result['products_created']}.")
    if result.get("failed"):
        st.warning(f"Не удалось разобрать {say(len(result['failed']), 'чек', 'чека', 'чеков')}. "
                   "Они остались в списке невзятых — попробуйте забрать их ещё раз.")
    if result.get("pending"):
        st.caption(f"В кабинете видно ещё {say(result['pending'], 'чек', 'чека', 'чеков')}, "
                   "которых у вас нет.")
    if st.button("Посмотреть историю", key="fns_bundle_goto"):
        goto("История")
        st.rerun()


def _frame() -> None:
    """Кабинет ФНС прямо в экране.

    Он открывается — проверено в живом приложении: форма входа по номеру телефона
    видна на нашей же странице. Прочитать это окно приложение не может ни при
    каком браузере, поэтому окно нужно ровно для двух вещей: войти не уходя со
    страницы и нажать здесь же закладку.
    """
    show = st.toggle("Кабинет ФНС прямо здесь", value=True, key="fns_frame",
                     help="Окно с сервисом налоговой внутри приложения. Если браузер "
                          "запрещает сторонние окна, оно будет пустым — тогда "
                          "откройте кабинет соседней вкладкой кнопкой ниже.")
    if not show:
        return

    st.caption("Войдите по номеру телефона — код из СМС вы вводите на сайте налоговой, "
               "мы его не видим. После входа нажмите закладку **«Забрать все чеки»**: "
               "она соберёт весь список сама. Кнопки «загрузить само» здесь нет "
               "намеренно — браузер не даёт странице читать содержимое чужого сайта, "
               "и такая кнопка была бы обманом.")
    components.iframe(CABINET_URL, height=760, scrolling=True)
    st.markdown(f'<a class="mk-cta" style="max-width:320px;" href="{CABINET_URL}" '
                'target="_blank" rel="noopener">Открыть кабинет отдельной вкладкой →</a>',
                unsafe_allow_html=True)
    st.caption("Если окно осталось пустым — ваш браузер запрещает сторонние окна. "
               "Это его настройка, не наша: откройте кабинет соседней вкладкой, "
               "закладка и там работает одинаково.")
    with st.expander("Почему нельзя совсем без закладки"):
        st.markdown(
            "Прочитать окно кабинета из нашей страницы нельзя: браузер запрещает "
            "странице доступ к содержимому чужого сайта. Это его правило "
            "безопасности, а не наша недоработка.\n\n"
            "Остаётся два пути. Первый — пропускать сайт налоговой через наш сервер: "
            "тогда через нас пойдут и код из СМС, и ваш вход. Мы так делать не "
            "будем.\n\n"
            "Второй — тот, что здесь: маленький скрипт работает **внутри** страницы "
            "кабинета, в вашем браузере. Ключ доступа остаётся там же, наружу "
            "выходит только файл с чеками, и только когда вы его сохраните.\n\n"
            "Ручным остаётся одно нажатие. Оно исчезнет в мобильном приложении и "
            "совсем — когда заработает партнёрство с ФНС."
        )


def _paste() -> None:
    theme.heading("Запасной путь", "Вставить чек текстом")
    st.caption("Если закладка почему-то не сработала — откройте чек, выделите (Ctrl+A), "
               "скопируйте (Ctrl+C) и вставьте сюда. Этот путь понимает и письма из "
               "доставок, и текст, распознанный с фотографии чека.")
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

    theme.heading("Накоплено", f"Сохранить ({len(stack)})")
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
    saved = created = doubles = 0
    try:
        with st.spinner("Сохраняем…"):
            for item in stack:
                result = fn(item["text"], store_code)
                saved += result["rows"]
                created += result["products_created"]
                doubles += result.get("skipped_receipts") or 0
    except Exception as exc:  # noqa: BLE001
        show_exception(exc, "Не удалось сохранить")
        return

    st.session_state[BUFFER] = []
    st.success(f"Добавлено {saved} позиций из {len(stack)} чеков · новых товаров {created}.")
    if doubles:
        st.info(f"Ещё {doubles} чеков уже были в истории — второй раз их не добавляли.")
    if st.button("Перейти в историю", key="fns_goto"):
        goto("История")
        st.rerun()


def header_stats() -> str:
    stack = st.session_state.get(BUFFER) or []
    if stack:
        return f"Накоплено чеков: {len(stack)}"
    fn, err = load("app.receipts", "short")
    if err:
        return "Загрузка чеков из сервиса ФНС"
    try:
        return fn()
    except Exception:  # noqa: BLE001 — заголовок не повод падать экрану
        return "Загрузка чеков из сервиса ФНС"
