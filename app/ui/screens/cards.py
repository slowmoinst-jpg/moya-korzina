"""Экран «Карты и акции»: CRUD карт и акций + загрузка акций из CSV."""
from __future__ import annotations

import datetime as dt
import io

import pandas as pd
import streamlit as st

from app import repo
from app.ui import theme
from app.models import Card, Offer
from app.ui.helpers import num, rub, show_exception

CSV_COLUMNS = "bank,card,store_code,percent,cap_rub,min_check_rub,valid_from,valid_to"


def render() -> None:
    cards = repo.list_cards()
    stores = repo.list_stores()

    tab_cards, tab_offers, tab_csv = st.tabs(["Карты", "Акции", "Загрузка CSV"])
    with tab_cards:
        _cards(cards)
    with tab_offers:
        _offers(cards, stores)
    with tab_csv:
        _csv(stores)


# ---------- карты ----------
def _cards(cards) -> None:
    if cards:
        offers = repo.list_offers()
        stores = {s.id: s for s in repo.list_stores()}
        esc = theme.esc
        tiles = []
        for c in cards:
            mine = [o for o in offers if o.card_id == c.id]
            where = ", ".join(dict.fromkeys(stores[o.store_id].name for o in mine if o.store_id in stores))
            tiles.append(
                '<div class="mk-card" style="padding:18px 20px;display:flex;flex-direction:column;gap:10px;">'
                f'<span class="mk-eyebrow">{esc(c.bank)}</span>'
                f'<span style="font-size:18px;font-weight:600;letter-spacing:-0.01em;">{esc(c.name)}</span>'
                f'<span style="font-size:12px;color:var(--ink2);">{len(mine)} акц. · {esc(where) if where else "без акций"}</span>'
                "</div>"
            )
        theme.block(
            '<div style="display:grid;grid-template-columns:repeat(3, minmax(0, 1fr));gap:16px;'
            'margin-bottom:14px;">' + "".join(tiles) + "</div>"
        )
    else:
        st.info("Карт пока нет — добавьте первую ниже.")

    selected = st.selectbox(
        "Что редактируем",
        [None] + list(cards),
        format_func=lambda c: "+ Новая карта" if c is None else f"{c.id}. {c.bank} · {c.name}",
        key="card_edit_select",
    )

    with st.form("card_form"):
        c1, c2 = st.columns(2)
        bank = c1.text_input("Банк*", value=selected.bank if selected else "")
        name = c2.text_input("Название карты*", value=selected.name if selected else "")
        saved = st.form_submit_button("Сохранить", type="primary")

    if saved:
        if not bank.strip() or not name.strip():
            st.error("Банк и название обязательны.")
        else:
            repo.upsert_card(Card(selected.id if selected else None, bank.strip(), name.strip()))
            st.success("Карта сохранена.")
            st.rerun()

    if selected and st.button("Удалить карту", key="card_delete_btn"):
        repo.delete_card(selected.id)
        st.success("Карта удалена (вместе с её акциями).")
        st.rerun()


# ---------- акции ----------
def _offers(cards, stores) -> None:
    offers = repo.list_offers()
    card_by_id = {c.id: c for c in cards}
    store_by_id = {s.id: s for s in stores}

    if offers:
        esc = theme.esc
        body = []
        for o in offers:
            card = card_by_id.get(o.card_id)
            store = store_by_id.get(o.store_id)
            if o.requires_activation:
                status = theme.pill("Активирована", "green") if o.activated else theme.pill("Не активирована", "warm")
            else:
                status = theme.pill("Без активации")
            if not o.is_valid_on():
                status = theme.pill("Не действует сегодня", "warm")
            check = f"Мин. чек {rub(o.min_check_rub)}" if o.min_check_rub else "Без минимального чека"
            body.append([
                f'<span style="font-weight:600;">{esc(_card_label(card))}</span><br>{status}',
                f'<span style="display:inline-flex;align-items:center;gap:8px;font-size:13px;">'
                f'{theme.dot(store.code if store else None)}{esc(store.name if store else o.store_id)}</span>',
                '<div style="display:flex;flex-direction:column;gap:6px;">'
                f'<div style="display:flex;justify-content:space-between;font-size:12px;color:var(--ink2);">'
                f'<span>{num(o.percent)} % · использовано {rub(o.cap_used)}</span>'
                f'<span style="font-weight:600;color:var(--ink);">из {rub(o.cap_rub)}</span></div>'
                f'{theme.bar(o.cap_used, o.cap_rub)}</div>',
                f'<span style="font-size:12px;color:var(--ink2);">{check}</span>',
                f'<span style="font-size:12px;color:var(--ink2);">'
                f'{("до " + o.valid_to) if o.valid_to else "бессрочно"}</span>',
            ])
        theme.block(theme.table(
            grid="200px 132px minmax(0,1fr) 168px 118px",
            header=["Карта", "Магазин", "Лимит кэшбэка за период", "Условия", "Срок"],
            rows=body,
            aligns=["left", "left", "left", "left", "right"],
        ))
        st.caption("")
    else:
        st.info("Акций пока нет — добавьте вручную ниже или загрузите CSV на соседней вкладке.")

    if not cards:
        st.warning("Сначала заведите хотя бы одну карту на вкладке «Карты».")
        return
    if not stores:
        st.warning("Справочник магазинов пуст.")
        return

    selected = st.selectbox(
        "Что редактируем",
        [None] + list(offers),
        format_func=lambda o: "+ Новая акция"
        if o is None
        else f"{o.id}. {_card_label(card_by_id.get(o.card_id))} · "
        f"{store_by_id[o.store_id].name if o.store_id in store_by_id else o.store_id} · {num(o.percent)}%",
        key="offer_edit_select",
    )

    with st.form("offer_form"):
        c1, c2 = st.columns(2)
        card = c1.selectbox(
            "Карта",
            cards,
            index=_index_of([c.id for c in cards], selected.card_id if selected else None),
            format_func=_card_label,
        )
        store = c2.selectbox(
            "Магазин",
            stores,
            index=_index_of([s.id for s in stores], selected.store_id if selected else None),
            format_func=lambda s: s.name,
        )

        c1, c2, c3 = st.columns(3)
        percent = c1.number_input(
            "Процент кэшбэка", min_value=0.0, max_value=100.0, step=0.5,
            value=float(selected.percent) if selected else 5.0,
        )
        cap_rub = c2.number_input(
            "Лимит кэшбэка, ₽", min_value=0.0, step=100.0,
            value=float(selected.cap_rub) if selected else 1000.0,
        )
        cap_used = c3.number_input(
            "Использовано, ₽", min_value=0.0, step=100.0,
            value=float(selected.cap_used) if selected else 0.0,
        )

        c1, c2, c3 = st.columns(3)
        min_check = c1.number_input(
            "Минимальный чек, ₽", min_value=0.0, step=100.0,
            value=float(selected.min_check_rub) if selected else 0.0,
        )
        valid_from = c2.date_input(
            "Действует с", value=_as_date(selected.valid_from) if selected else None, format="DD.MM.YYYY"
        )
        valid_to = c3.date_input(
            "Действует по", value=_as_date(selected.valid_to) if selected else None, format="DD.MM.YYYY"
        )

        c1, c2 = st.columns(2)
        requires_activation = c1.checkbox(
            "Требует активации", value=bool(selected.requires_activation) if selected else False
        )
        activated = c2.checkbox("Активирована", value=bool(selected.activated) if selected else True)

        saved = st.form_submit_button("Сохранить акцию", type="primary")

    if saved:
        repo.upsert_offer(
            Offer(
                id=selected.id if selected else None,
                card_id=card.id,
                store_id=store.id,
                percent=float(percent),
                cap_rub=float(cap_rub),
                min_check_rub=float(min_check),
                valid_from=valid_from.isoformat() if valid_from else None,
                valid_to=valid_to.isoformat() if valid_to else None,
                requires_activation=requires_activation,
                activated=activated,
                cap_used=float(cap_used),
            )
        )
        st.success("Акция сохранена.")
        st.rerun()

    if selected and st.button("Удалить акцию", key="offer_delete_btn"):
        repo.delete_offer(selected.id)
        st.success("Акция удалена.")
        st.rerun()


# ---------- CSV ----------
def _csv(stores) -> None:
    st.caption(f"Ожидаемые колонки: `{CSV_COLUMNS}` (необязательные: requires_activation, activated, cap_used).")
    uploaded = st.file_uploader("CSV с акциями", type=["csv"], key="offers_csv")
    if uploaded is None:
        return

    try:
        raw = uploaded.getvalue().decode("utf-8-sig")
        df = pd.read_csv(io.StringIO(raw))
    except Exception as exc:  # noqa: BLE001
        show_exception(exc, "Не удалось прочитать CSV")
        return

    st.dataframe(df, hide_index=True)

    required = {"bank", "card", "store_code", "percent", "cap_rub"}
    missing = required - set(df.columns)
    if missing:
        st.error("В файле нет обязательных колонок: " + ", ".join(sorted(missing)))
        return

    if st.button("Загрузить акции", type="primary", key="offers_csv_btn"):
        try:
            created, updated, errors = _import_offers(df)
        except Exception as exc:  # noqa: BLE001
            show_exception(exc, "Загрузка не удалась")
            return
        st.success(f"Готово: добавлено {created}, обновлено {updated}.")
        for err in errors:
            st.warning(err)
        if created or updated:
            st.rerun()


def _import_offers(df) -> tuple[int, int, list[str]]:
    created = updated = 0
    errors: list[str] = []

    for i, row in df.iterrows():
        code = str(row.get("store_code") or "").strip()
        store = repo.get_store(code) if code else None
        if store is None:
            errors.append(f"Строка {i + 2}: магазин `{code}` не найден, пропущена.")
            continue

        bank = str(row.get("bank") or "").strip()
        card_name = str(row.get("card") or "").strip()
        if not bank or not card_name:
            errors.append(f"Строка {i + 2}: не заполнены банк или карта, пропущена.")
            continue

        card = next(
            (c for c in repo.list_cards() if c.bank.lower() == bank.lower() and c.name.lower() == card_name.lower()),
            None,
        )
        card_id = card.id if card else repo.upsert_card(Card(None, bank, card_name))

        existing = next((o for o in repo.list_offers(store.id) if o.card_id == card_id), None)
        offer = Offer(
            id=existing.id if existing else None,
            card_id=card_id,
            store_id=store.id,
            percent=_float(row.get("percent"), 0.0),
            cap_rub=_float(row.get("cap_rub"), 0.0),
            min_check_rub=_float(row.get("min_check_rub"), 0.0),
            valid_from=_date_str(row.get("valid_from")),
            valid_to=_date_str(row.get("valid_to")),
            requires_activation=_bool(row.get("requires_activation")),
            activated=_bool(row.get("activated"), default=True),
            cap_used=_float(row.get("cap_used"), 0.0),
        )
        repo.upsert_offer(offer)
        if existing:
            updated += 1
        else:
            created += 1

    return created, updated, errors


# ---------- мелочи ----------
def _card_label(card) -> str:
    return "—" if card is None else f"{card.bank} · {card.name}"


def _index_of(ids: list, value) -> int:
    return ids.index(value) if value in ids else 0


def _as_date(value):
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _date_str(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text[:10] if text and text.lower() != "nan" else None


def _float(value, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value, default: bool = False) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "да", "yes", "y"}:
        return True
    if text in {"0", "false", "нет", "no", "n", ""}:
        return False
    return default


def header_stats() -> str:
    offers = repo.list_offers()
    cap = sum(float(o.cap_rub or 0) for o in offers)
    used = sum(float(o.cap_used or 0) for o in offers)
    return theme.stat_chips([
        ("Карт", str(len(repo.list_cards()))),
        ("Акций", str(len(offers))),
        ("Лимит кэшбэка", rub(cap)),
        ("Использовано", rub(used)),
    ])
