"""Экран «Результат»: топ-3 варианта с экономией в рублях и процентах.

Оформление повторяет макет канваса: лучший вариант показан крупно с полосой экономии,
остальные — компактными строками с раскрытием. Зелёный только про выгоду.
"""
from __future__ import annotations

import streamlit as st

from app import repo
from app.ui import theme
from app.ui.helpers import goto, num, pct, rub, unit_label

esc = theme.esc

# product_id -> unit: у VariantLine единицы нет, а весовые надо показывать в кг
_UNITS: dict[int, str] = {}


def _refresh_units() -> None:
    try:
        _UNITS.clear()
        _UNITS.update({p.id: p.unit for p in repo.list_products(active_only=False) if p.id})
    except Exception:  # noqa: BLE001 — подпись единиц не повод ронять экран
        pass


def render() -> None:
    calc = st.session_state.get("calc")
    if not calc:
        st.info("Расчёта ещё не было. Соберите корзину и нажмите «Рассчитать».")
        if st.button("К корзине", key="res_to_basket"):
            goto("Корзина")
            st.rerun()
        return

    _refresh_units()
    variants = calc.get("variants") or []
    baseline = calc.get("baseline") or 0.0
    st.caption(
        f"Корзина: {calc.get('basket_name') or '—'} (#{calc.get('basket_id')}) · "
        f"базовая сумма {rub(baseline)}"
    )

    if not variants:
        st.warning(
            "Ни одного варианта не вышло. Обычно это значит, что у позиций нет цен: "
            "загляните на экран «Товары» и нажмите «Обновить цены»."
        )
        return

    for i, variant in enumerate(variants, start=1):
        if i == 1:
            _best(variant, baseline)
        else:
            _compact(i, variant, baseline)


# ---------- разбор варианта ----------
def _figures(variant, baseline: float) -> tuple[float, float, float, float]:
    total = float(getattr(variant, "total", 0.0) or 0.0)
    base = float(getattr(variant, "baseline", None) or baseline or 0.0)
    savings_rub = float(getattr(variant, "savings_rub", base - total) or 0.0)
    savings_pct = float(getattr(variant, "savings_pct", (savings_rub / base * 100) if base else 0.0) or 0.0)
    return total, base, savings_rub, savings_pct


def _bar(total: float, base: float, height: int = 14) -> str:
    """Полоса: тёмное — что платим, зелёное — что сберегли."""
    paid = 100.0 if base <= 0 else max(0.0, min(100.0, total / base * 100.0))
    saved = max(0.0, 100.0 - paid)
    return (
        f'<div class="mk-bar" style="height:{height}px;">'
        f'<div style="width:{paid:.2f}%;background:var(--ink);"></div>'
        f'<div style="width:{saved:.2f}%;background:var(--green);"></div></div>'
    )


def _stores_line(variant) -> str:
    parts = []
    for store in getattr(variant, "stores", None) or []:
        code = getattr(store, "store_code", None)
        name = esc(getattr(store, "store_name", None) or code or "Магазин")
        parts.append(
            f'<span style="display:inline-flex;align-items:center;gap:7px;font-size:19px;'
            f'font-weight:600;">{theme.dot(code)}{name}</span>'
        )
    return '<span style="color:var(--ink3);font-size:17px;"> + </span>'.join(parts)


# ---------- лучший вариант ----------
def _best(variant, baseline: float) -> None:
    total, base, savings_rub, savings_pct = _figures(variant, baseline)
    penalty = float(getattr(variant, "penalty", 0.0) or 0.0)
    stores = getattr(variant, "stores", None) or []

    note = f"{len(stores)} заказа · включён штраф {rub(penalty)}" if penalty else "Один заказ, без штрафа"

    st.markdown(
        f"""
<div class="mk-card" style="padding:26px 28px;display:flex;flex-direction:column;gap:20px;margin-bottom:18px;">
  <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:32px;flex-wrap:wrap;">
    <div style="display:flex;flex-direction:column;gap:10px;">
      <div class="mk-eyebrow" style="color:var(--green);">Лучший вариант</div>
      <div style="display:flex;align-items:center;gap:12px;">{_stores_line(variant)}</div>
      <div style="font-size:13px;color:var(--ink2);">{esc(note)}</div>
    </div>
    <div style="display:flex;align-items:flex-end;gap:40px;">
      <div style="display:flex;flex-direction:column;gap:3px;align-items:flex-end;">
        <div class="mk-eyebrow">К оплате</div>
        <div class="mk-serif" style="font-size:44px;line-height:1;">{rub(total)}</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:3px;align-items:flex-end;
                  padding-left:36px;border-left:1px solid var(--line);">
        <div class="mk-eyebrow" style="color:var(--green);">Экономия</div>
        <div class="mk-serif" style="font-size:44px;line-height:1;color:var(--green);">{rub(savings_rub)}</div>
        <div style="font-size:14px;font-weight:600;color:var(--green);">{pct(savings_pct)}</div>
      </div>
    </div>
  </div>
  <div style="display:flex;flex-direction:column;gap:9px;">
    {_bar(total, base)}
    <div style="display:flex;justify-content:space-between;font-size:12px;color:var(--ink2);">
      <span>Заплатим — {rub(total)}</span>
      <span>Было бы в одном магазине — {rub(base)}</span>
    </div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    missing = getattr(variant, "missing_products", None) or []
    if missing:
        st.warning("Не нашли цену для: " + ", ".join(str(m) for m in missing))

    if stores:
        for column, store in zip(st.columns(len(stores)), stores):
            with column:
                _store_card(store)


# ---------- остальные варианты ----------
def _compact(index: int, variant, baseline: float) -> None:
    total, base, savings_rub, savings_pct = _figures(variant, baseline)
    title = esc(getattr(variant, "title", None) or f"Вариант {index}")
    dots = "".join(theme.dot(getattr(s, "store_code", None)) for s in getattr(variant, "stores", None) or [])

    st.markdown(
        f"""
<div class="mk-card" style="padding:14px 20px;display:flex;align-items:center;gap:20px;margin-top:10px;">
  <span class="mk-serif" style="font-size:17px;color:var(--ink3);width:18px;">{index}</span>
  <span style="display:inline-flex;align-items:center;gap:8px;min-width:220px;font-size:15px;font-weight:600;">
    {dots}&nbsp;{title}
  </span>
  <span style="flex:1;min-width:120px;">{_bar(total, base, height=9)}</span>
  <span style="font-size:15px;font-weight:600;width:120px;text-align:right;">{rub(total)}</span>
  <span style="font-size:14px;font-weight:600;color:var(--green);width:170px;text-align:right;">
    −{rub(savings_rub)} · {pct(savings_pct)}
  </span>
</div>
""",
        unsafe_allow_html=True,
    )

    with st.expander("Разбивка по магазинам"):
        missing = getattr(variant, "missing_products", None) or []
        if missing:
            st.warning("Не нашли цену для: " + ", ".join(str(m) for m in missing))
        stores = getattr(variant, "stores", None) or []
        if stores:
            for column, store in zip(st.columns(len(stores)), stores):
                with column:
                    _store_card(store)


# ---------- карточка магазина ----------
def _store_card(store) -> None:
    code = getattr(store, "store_code", None)
    name = esc(getattr(store, "store_name", None) or code or "Магазин")
    card = getattr(store, "card_name", None)
    below = bool(getattr(store, "below_min_order", False))
    lines = getattr(store, "lines", None) or []

    soft = {"magnit": "--red-soft", "vkusvill": "--green-soft", "pyaterochka": "--amber-soft"}.get(code or "", "--tint")

    rows = "".join(
        f'<div class="mk-row"><span>{esc(getattr(line, "product_name", "—"))} '
        f'<span style="color:var(--ink3);">{num(getattr(line, "qty", 0))}&nbsp;'
        f'{unit_label(_UNITS.get(getattr(line, "product_id", None)))}</span></span>'
        f'<span style="white-space:nowrap;">{rub(getattr(line, "price", 0.0))}</span></div>'
        for line in lines
    ) or '<div style="padding:8px 0;font-size:13px;color:var(--ink3);">Позиций нет.</div>'

    st.markdown(
        f"""
<div class="mk-card" style="overflow:hidden;">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;
              padding:15px 20px;background:var({soft});border-bottom:1px solid var(--line);">
    <span style="display:inline-flex;align-items:center;gap:9px;font-size:17px;font-weight:600;">
      {theme.dot(code)}{name}
      <span style="font-size:12px;font-weight:400;color:var(--ink2);">{len(lines)} поз.</span>
    </span>
    <span class="mk-pill">{esc(card) if card else "без карты"}</span>
  </div>
  <div style="padding:14px 20px;display:flex;flex-direction:column;gap:9px;border-bottom:1px solid var(--line);">
    <div style="display:flex;justify-content:space-between;font-size:13px;">
      <span style="color:var(--ink2);">Товары</span><span style="font-weight:500;">{rub(getattr(store, "subtotal", 0.0))}</span></div>
    <div style="display:flex;justify-content:space-between;font-size:13px;">
      <span style="color:var(--ink2);">Доставка</span>
      <span style="font-weight:500;color:{'var(--green)' if not getattr(store, 'delivery', 0.0) else 'var(--ink)'};">
        {rub(getattr(store, "delivery", 0.0))}</span></div>
    <div style="display:flex;justify-content:space-between;font-size:13px;">
      <span style="color:var(--ink2);">Скидка и кэшбэк</span>
      <span style="font-weight:500;color:var(--green);">−{rub(getattr(store, "discount", 0.0))}</span></div>
    <div style="display:flex;justify-content:space-between;align-items:baseline;
                padding-top:8px;border-top:1px solid var(--line);">
      <span style="font-size:13px;font-weight:600;">Итого по магазину</span>
      <span class="mk-serif" style="font-size:22px;">{rub(getattr(store, "total", 0.0))}</span></div>
  </div>
  <div style="padding:10px 20px 16px;">{rows}</div>
</div>
""",
        unsafe_allow_html=True,
    )

    if below:
        st.warning("Заказ меньше минимальной суммы магазина — его могут не принять.")


def header_stats() -> str:
    calc = st.session_state.get("calc") or {}
    variants = calc.get("variants") or []
    if not variants:
        return ""
    best = variants[0]
    return theme.stat_chips([
        ("Baseline", rub(calc.get("baseline"))),
        ("Лучший итог", rub(getattr(best, "total", None))),
        ("Экономия", rub(getattr(best, "savings_rub", None))),
        ("Доля", pct(getattr(best, "savings_pct", None))),
    ])
