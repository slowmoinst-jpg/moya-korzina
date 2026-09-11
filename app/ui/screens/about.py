"""Экран «О продукте»: зачем это нужно и почему расчёт не выбирает «где дешевле».

Числа берутся из текущего расчёта, а не вписаны в текст: если корзина посчитана,
экран показывает, насколько близко чеки подобрались к порогам.
"""
from __future__ import annotations

import streamlit as st

from app import repo
from app.ui import theme
from app.ui.helpers import pct, rub

esc = theme.esc

APP_URL = "https://moya-korzina-cfvunmlj6hhzc7qe3curbx.streamlit.app"
PAGE_URL = "https://slowmoinst-jpg.github.io/moya-korzina/"
REPO_URL = "https://github.com/slowmoinst-jpg/moya-korzina"

FORMULA = """subtotal = сумма(цена × количество) по позициям, отнесённым в магазин
доставка = 0, если subtotal ≥ порога бесплатной доставки, иначе тариф
кэшбэк   = 0, если subtotal < минимального чека,
           иначе min(subtotal × процент / 100, остаток лимита карты)
итог     = subtotal + доставка − кэшбэк
вариант  = сумма итогов + штраф × (число магазинов − 1)"""

STEPS = [
    ("Покупки", "Чек PDF от ОФД, JSON из «Мои чеки онлайн» ФНС или таблица заказа "
                "из личного кабинета магазина. Весовые узнаются по дробному количеству."),
    ("Сопоставление", "«ВК/ПОЛ.Сыр СТРАЧАТЕЛЛА мяг.200г» превращается в понятный товар и связывается "
                      "с тем, что лежит в магазинах. Один раз связали — больше не спросим."),
    ("Цены", "Магазин отдаёт актуальную цену. Снимки не перезаписываются — "
             "из них растёт история цен."),
    ("Перебор", "Все разбиения корзины между магазинами, для каждого — лучшая карта. "
                "Наверх выходят три варианта."),
]

# что откуда приходит: покупки, цены магазинов, условия карт
SOURCES = [
    ("Покупки", [
        ("Чек PDF от ОФД, он же текстом", "ok", "работает"),
        ("JSON из «Мои чеки онлайн» ФНС и от операторов данных", "ok", "работает"),
        ("Таблица заказа CSV или XLSX из личного кабинета", "ok", "работает"),
        ("Выгрузка из ФНС по авторизации", "no", "в планах"),
    ]),
    ("Сайты магазинов", [
        ("Магнит — каталог сайта, регион задаётся кодом магазина", "ok", "живые цены"),
        ("ВкусВилл — сайт не отвечает, берём резервный прайс", "no", "не отвечает"),
        ("Пятёрочка — цены приходят с вашим чеком", "ok", "из чеков"),
        ("Едадил: акции офлайн-каталогов Пятёрочки, Ленты, Дикси", "no", "в планах"),
        ("Ozon Fresh, Лента, Самокат, Яндекс Лавка — нужен браузер", "no", "в планах"),
        ("Перекрёсток — только расширение браузера или партнёрство", "no", "другой путь"),
    ]),
    ("Банки", [
        ("Свой справочник карт и акций", "ok", "работает"),
        ("Загрузка акций из CSV", "ok", "работает"),
        ("Остаток лимита за период", "ok", "учитывается"),
        ("Персональные предложения из приложений банков", "no", "в планах"),
        ("Карты лояльности и подписки магазинов", "no", "в планах"),
        ("Промокоды на первый и повторный заказ", "no", "в планах"),
    ]),
]

STATUS = [
    ("Точность цен", "no", "ориентир",
     "Цены на сайте и в зале расходятся на 5–15 %. Пороги доставки и кэшбэка считаются точно, "
     "цены — как получится у магазина в момент запроса."),
    ("Глубина перебора", "ok", "2 магазина",
     "Разбиение на три и больше — в планах, вместе с учётом замен, когда товара нет в наличии."),
    ("Данные в примере", "no", "вымышленные",
     "Чек, условия карт и названия банков демонстрационные. Свои подставятся при первой загрузке."),
    ("Проверки", "ok", "71 тест",
     "Следят за тем, чтобы формула, разбор чеков и разбиение считались одинаково от сборки к сборке."),
]


def render() -> None:
    _intro()
    _thresholds()
    _steps()
    _sources()
    _status()


def _intro() -> None:
    theme.block(
        '<div class="mk-card" style="padding:24px 26px;display:flex;flex-direction:column;gap:12px;">'
        '<div class="mk-eyebrow" style="color:var(--warm);">Экономия на домашних покупках — без смены магазинов и привычек</div>'
        '<div style="font-size:19px;line-height:1.5;max-width:62ch;">Вы покупаете то же, что и всегда, — а платите меньше. '
        'Продукт сам сверяет цены магазинов, считает кэшбэк ваших карт и говорит, что где брать, '
        'чтобы недельная закупка обошлась дешевле.</div>'
        '<div style="display:flex;gap:12px;flex-wrap:wrap;padding-top:4px;font-size:14px;">'
        f'<a href="{PAGE_URL}" target="_blank">Страница о продукте</a>'
        f'<a href="{REPO_URL}" target="_blank">Исходный код</a></div></div>'
    )


def _thresholds() -> None:
    """Насколько близко чеки текущего варианта подобрались к порогам."""
    theme.heading("Дешевле по строке ≠ выгоднее итогом")
    calc = st.session_state.get("calc") or {}
    variants = calc.get("variants") or []

    st.markdown(
        "Перебор ищет не самую низкую цену на каждую позицию, а самый низкий итог. "
        "Поэтому в разбиении попадаются строки, которые на первый взгляд стоят не в том магазине: "
        "переплата в несколько рублей держит чек выше порога, за которым включается кэшбэк "
        "или пропадает доставка."
    )

    if not variants:
        st.info("Посчитайте корзину — и здесь появится, насколько близко чеки подошли к порогам.")
    else:
        best = variants[0]
        stores = {s.code: s for s in repo.list_stores()}
        cards = []
        for sb in getattr(best, "stores", None) or []:
            store = stores.get(getattr(sb, "store_code", ""))
            if not store:
                continue
            subtotal = float(getattr(sb, "subtotal", 0) or 0)
            # интересен только порог, к которому чек подошёл вплотную:
            # «на 7 ₽ выше 2 000» — находка, «на 1 007 ₽ выше 1 000» — шум
            def near(threshold: float) -> bool:
                return bool(threshold) and 0 <= subtotal - threshold <= threshold * 0.1

            edges = []
            if near(store.free_delivery_from):
                edges.append(f"на {rub(subtotal - store.free_delivery_from)} выше порога бесплатной "
                             f"доставки в {rub(store.free_delivery_from)}")
            offer_min = min((o.min_check_rub for o in repo.offers_for_store(store.id)
                             if near(o.min_check_rub)), default=None)
            if offer_min:
                edges.append(f"на {rub(subtotal - offer_min)} выше минимального чека "
                             f"в {rub(offer_min)}, с которого работает кэшбэк")
            body = "".join(f'<div style="font-size:13px;color:var(--ink2);">{esc(e)}</div>' for e in edges) \
                or '<div style="font-size:13px;color:var(--ink3);">порогов не касается</div>'
            cards.append(
                '<div class="mk-card" style="padding:16px 18px;display:flex;flex-direction:column;gap:8px;">'
                f'<span style="display:inline-flex;align-items:center;gap:9px;font-weight:600;">'
                f'{theme.dot(store.code)}{esc(store.name)}</span>'
                f'<span class="mk-serif" style="font-size:20px;">{rub(subtotal)}</span>{body}</div>'
            )
        if cards:
            theme.block('<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));'
                        'gap:16px;">' + "".join(cards) + "</div>")
        theme.block(
            '<div style="display:flex;gap:26px;flex-wrap:wrap;padding:14px 18px;margin-top:12px;'
            'background:var(--tint);border-radius:12px;font-size:13px;">'
            f'<span>Базовая сумма <b>{rub(calc.get("baseline"))}</b></span>'
            f'<span>Лучший итог <b>{rub(getattr(best, "total", None))}</b></span>'
            f'<span style="color:var(--green);">Экономия <b>{rub(getattr(best, "savings_rub", None))} · '
            f'{pct(getattr(best, "savings_pct", None))}</b></span></div>'
        )

    theme.block(
        '<pre style="margin-top:14px;overflow-x:auto;background:var(--tint);border-radius:13px;'
        'padding:18px 20px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;'
        f'font-size:13px;line-height:1.65;">{esc(FORMULA)}</pre>'
    )


def _steps() -> None:
    theme.heading("Четыре шага от чека до разбиения")
    cells = []
    for i, (title, text) in enumerate(STEPS, start=1):
        cells.append(
            '<div style="display:flex;flex-direction:column;gap:7px;padding-top:13px;'
            'border-top:2px solid var(--line2);">'
            f'<span class="mk-serif" style="font-size:13px;color:var(--warm);">0{i}</span>'
            f'<span style="font-weight:600;font-size:16px;">{esc(title)}</span>'
            f'<span style="font-size:13px;color:var(--ink2);line-height:1.5;">{esc(text)}</span></div>'
        )
    theme.block('<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));'
                'gap:18px;">' + "".join(cells) + "</div>")


def _sources() -> None:
    """Три источника: покупки, цены магазинов, условия карт."""
    theme.heading("Откуда берутся данные")
    st.markdown(
        "Чтобы расчёт был честным, нужны три вещи: что семья обычно покупает, сколько это стоит "
        "сегодня в каждом магазине и какие условия дают карты. Каждая приходит своим путём."
    )
    groups = []
    for title, items in SOURCES:
        rows = "".join(
            '<div style="display:flex;align-items:baseline;justify-content:space-between;gap:12px;'
            'padding:9px 18px;border-top:1px solid var(--line);">'
            f'<span style="font-size:13px;">{esc(text)}</span>'
            f'{theme.pill(esc(label), "green" if tone == "ok" else "warm")}</div>'
            for text, tone, label in items
        )
        groups.append(
            '<div class="mk-card" style="overflow:hidden;">'
            f'<div style="padding:15px 18px;font-weight:600;font-size:16px;">{esc(title)}</div>'
            f"{rows}</div>"
        )
    theme.block('<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));'
                'gap:16px;">' + "".join(groups) + "</div>")
    st.caption("Магазины не обязаны отдавать цены роботам: разметка меняется без предупреждения, "
               "часть витрин закрыта намеренно. Поэтому источники изолированы — упавший не мешает остальным.")


def _status() -> None:
    theme.heading("Что работает, а что нет")
    rows = []
    for part, tone, label, detail in STATUS:
        rows.append([
            esc(part),
            theme.pill(esc(label), "green" if tone == "ok" else "warm"),
            f'<span style="font-size:13px;color:var(--ink2);">{esc(detail)}</span>',
        ])
    theme.block(theme.table(
        grid="190px 150px minmax(0,1fr)",
        header=["Часть", "Состояние", "Подробности"],
        rows=rows,
    ))
    st.caption("Источники изолированы: если магазин сменил разметку или не отвечает, "
               "расчёт идёт по остальным, а не падает целиком.")


def header_stats() -> str:
    return theme.stat_chips([
        ("Товаров", str(len(repo.list_products(active_only=False)))),
        ("Магазинов", str(len(repo.list_stores()))),
        ("Акций", str(len(repo.list_offers()))),
    ])
