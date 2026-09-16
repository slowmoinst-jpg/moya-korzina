"""Экран «Корзина»: выбор/создание корзины, позиции, запуск расчёта."""
from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from app import repo
from app.ui import theme
from app.ui.helpers import goto, load, module_warning, num, rub, show_exception, unit_label
from app.ui.screens.history import format_plural

esc = theme.esc

log = logging.getLogger(__name__)


def render() -> None:
    basket = _pick_basket()
    if basket is None:
        return

    basket_id = int(basket["id"])
    st.caption(f"Корзина #{basket_id} · создана {basket.get('created_at')} · источник: {basket.get('source')}")

    products = repo.list_products()
    items = repo.basket_items(basket_id)

    # Пустая корзина наполняется сама и целиком: человеку не за что нажимать, если
    # он и так собирался купить примерно то же. Нечем наполнить — тогда объясняем.
    if not items:
        if _autofill(basket_id):
            st.rerun()
        items = repo.basket_items(basket_id)
        if not items:
            if _empty_start(basket_id):
                st.rerun()
            st.divider()

    _autofill_note(basket_id)

    _add_item(basket_id, products)
    _usual_chips(basket_id)
    st.divider()
    items = repo.basket_items(basket_id)
    _items(basket_id, items)
    st.divider()
    _calculate(basket_id, basket.get("name"), items)


# ---------- выбор / создание корзины ----------
def _pick_basket():
    baskets = repo.list_baskets()
    col1, col2 = st.columns([2, 2])

    with col1:
        if baskets:
            ids = [b["id"] for b in baskets]
            saved = st.session_state.get("basket_id")
            index = ids.index(saved) if saved in ids else 0
            chosen = st.selectbox(
                "Корзина",
                baskets,
                index=index,
                format_func=lambda b: f"{b['name']} (#{b['id']})",
                key="basket_select",
            )
            st.session_state["basket_id"] = chosen["id"]
        else:
            chosen = None
            st.info("Корзин пока нет — создайте первую справа.")

    with col2:
        _from_history()
        with st.form("new_basket_form", clear_on_submit=True):
            name = st.text_input("Название новой корзины", placeholder="Например: Неделя 38")
            if st.form_submit_button("Создать корзину"):
                if not name.strip():
                    st.error("Напишите название корзины.")
                else:
                    new_id = repo.create_basket(name.strip())
                    st.session_state["basket_id"] = new_id
                    st.rerun()

    return chosen


def _price_matrix(items, stores) -> tuple[dict, dict]:
    """(product_id, store_code) -> стоимость позиции целиком; и сумма корзины по магазину."""
    cell: dict[tuple[int, str], float] = {}
    totals: dict[str, float] = {s.code: 0.0 for s in stores}
    for item in items:
        pid = int(item["product_id"])
        qty = float(item.get("qty") or 0)
        is_kg = (item.get("unit") or "pcs") == "kg"
        for store in stores:
            snap = repo.latest_price_for(pid, store.id)
            if not snap:
                continue
            base = (snap.get("price_per_kg") or snap.get("price")) if is_kg else snap.get("price")
            if base is None:
                continue
            value = round(float(base) * qty, 2)
            cell[(pid, store.code)] = value
            totals[store.code] += value
    return cell, {k: round(v, 2) for k, v in totals.items()}


def _price_html(pid: int, stores, cell, live: dict | None = None) -> str:
    """Цены этой позиции по магазинам одной строкой.

    Два источника, и они не равны. Снимок из базы — то, что когда-то сняли, он
    может быть недельной давности и снят по другому адресу. Живой опрос — цена
    сегодняшняя и по адресу из шапки, и у него же есть наличие. Поэтому живая
    цена вытесняет снимок, а не дополняет его.

    Пустая клетка и «нет» — разные ответы. Пусто значит «не спрашивали»,
    «нет» — «спросили, и товара там не продают». Второе для сборки корзины
    важнее цены: в такой магазин человека посылать незачем.
    """
    known: dict[str, tuple[float, bool, bool]] = {}     # цена, есть в наличии, подтверждена
    for store in stores:
        offer = (live or {}).get((pid, store.code))
        if offer is not None:
            if offer.price is not None:
                known[store.code] = (float(offer.price), bool(offer.in_stock),
                                     bool(getattr(offer, "confirmed", False)))
            continue
        if (pid, store.code) in cell:
            # снимок снят по подтверждённому сопоставлению — это тот товар
            known[store.code] = (cell[(pid, store.code)], True, True)

    if not known:
        return '<span style="font-size:12px;color:var(--ink3);">цен нет</span>'

    # победителя выбираем только среди того, что реально можно купить
    available = [value for value, in_stock, _ in known.values() if in_stock]
    best = min(available) if available else None

    parts = []
    for store in stores:
        found = known.get(store.code)
        if found is None:
            continue
        value, in_stock, confirmed = found
        if not in_stock:
            parts.append(
                f'<span style="display:inline-flex;align-items:center;gap:6px;color:var(--ink3);'
                f'text-decoration:line-through;">{theme.dot(store.code)}{rub(value)}</span>'
            )
            continue
        is_best = best is not None and abs(value - best) < 0.005
        style = "font-weight:600;color:var(--green);" if is_best else "color:var(--ink3);"
        # неподтверждённую цену подчёркиваем пунктиром: это похожий по названию
        # товар, а не обязательно тот самый
        mark = ("" if confirmed else
                "border-bottom:1px dotted var(--ink3);cursor:help;")
        hint = ("" if confirmed else
                ' title="Подобрано поиском по названию — возможно, это другой товар. '
                'Подтвердить можно на экране «Товары»."')
        parts.append(
            f'<span{hint} style="display:inline-flex;align-items:center;gap:6px;{style}{mark}">'
            f'{theme.dot(store.code)}{rub(value)}</span>'
        )
    return '<span style="display:flex;gap:14px;flex-wrap:wrap;font-size:13px;">' + "".join(parts) + "</span>"


# ---------- живые цены по магазинам ----------
def _live_key(basket_id: int) -> str:
    """Ключ хранения. Адрес в ключе обязателен: цены другого города — чужие цены."""
    from app import location as client_place
    return f"live_{basket_id}_{client_place.address() or 'нет'}"


def _ask_stores(basket_id: int, items) -> None:
    """Спрашивает цены и наличие по всем позициям сразу.

    Идём по позициям, а не по магазинам, потому что показать прогресс осмысленно
    можно только так: человек видит, какой товар сейчас спрашивается.
    """
    from app import compare

    found: dict[tuple[int, str], object] = {}
    bar = st.progress(0.0, text="Спрашиваем магазины…")
    total = max(1, len(items))
    for n, item in enumerate(items, start=1):
        pid = int(item["product_id"])
        name = item.get("name") or ""
        bar.progress(n / total, text=f"{name[:44]} — {n} из {total}")
        try:
            for offer in compare.compare_query(name, per_store=1):
                # Артикул сохраняем ДАЖЕ БЕЗ ЦЕНЫ. Так устроен Дикси: его каталог
                # отвечает через сторонний поисковый движок и отдаёт идентификатор
                # с адресом карточки, а цену прячет за защитой сайта. Отбросив такой
                # ответ, мы лишаемся не только цены, но и возможности открыть товар
                # в приложении сети — а это ровно то, ради чего всё.
                if not offer.sku:
                    continue
                keep, confirmed = _agrees_with_mapping(pid, offer)
                if keep:
                    _remember(pid, offer)
                if not offer.found:
                    continue
                if not keep:
                    continue
                offer.confirmed = confirmed
                found[(pid, offer.store_code)] = offer
        except Exception as exc:  # noqa: BLE001 — один товар не должен ронять весь опрос
            show_exception(exc, f"«{name}» спросить не удалось")
    bar.empty()
    st.session_state[_live_key(basket_id)] = found


def _unconfirmed_note(live: dict) -> None:
    """Сколько цен — догадки поиска. Молчать об этом нельзя: они уже в суммах."""
    guessed = sum(1 for o in live.values() if not getattr(o, "confirmed", False))
    if not guessed:
        return
    st.caption(
        f"⚠️ {guessed} цен подобрано поиском по названию и может относиться к другому "
        "товару — они подчёркнуты пунктиром и уже вошли в суммы. Подтвердить "
        "сопоставления можно на экране «Товары»."
    )


def _remember(pid: int, offer) -> bool:
    """Запоминает найденный артикул как сопоставление. True — запомнили.

    БЕЗ ЭТОГО ШАГА КОРЗИНА НЕ УЕЗЖАЕТ В МАГАЗИН. Ссылка на готовую корзину
    принимает не название, а идентификатор товара В ЭТОЙ СЕТИ: у Ленты число,
    у ВкусВилла xml_id. Поиск его уже приносит — до сих пор мы показывали по
    нему цену и выбрасывали, и потому ссылка не строилась ни для одной сети,
    хотя обе это умеют.

    Заодно сохраняется снимок цены: живой опрос иначе жил только до закрытия
    вкладки, и назавтра корзина снова оказывалась без цен.

    Решение владельца 15.09.2026 — сохранять сразу, без отдельного подтверждения
    человеком. Риск известен и назван: поиск ошибается, и в ссылку может уехать
    не тот товар. Смягчает его сама сеть — Лента пишет в описании своего
    инструмента, что ссылка не добавляет товары молча, а открывает экран с
    предложением их добавить, то есть состав человек увидит до заказа.
    """
    store = repo.get_store(offer.store_code)
    if store is None or not offer.sku:
        return False
    try:
        sp_id = repo.upsert_store_product(
            store.id, str(offer.sku), offer.name or "",
            weight_g=offer.weight_g, unit=offer.unit, url=offer.url,
        )
        repo.confirm_mapping(pid, sp_id, confirmed=True)
        if offer.price is not None:
            repo.save_price(sp_id, float(offer.price),
                            price_per_kg=offer.per_unit if offer.unit == "kg" else None,
                            in_stock=bool(offer.in_stock))
        return True
    except Exception as exc:  # noqa: BLE001 — не сохранили, но цену показать всё равно можем
        log.warning("Сопоставление %s в %s не сохранилось: %s", pid, offer.store_code, exc)
        return False


def _agrees_with_mapping(pid: int, offer) -> tuple[bool, bool]:
    """(брать ли предложение, подтверждено ли оно) — сверка поиска с сопоставлением.

    Поиск ищет по названию эталона и берёт лучшее совпадение. На коротких и общих
    названиях он промахивается дорого: на «Икра лососевая копчёная 180 г» прилетела
    банка за 2 090 ₽, на «Салями сырокопчёная» — палка за 1 790 ₽. В сумме магазина
    такая цена выглядит как настоящая.

    Поэтому там, где человек уже подтвердил, какой это товар в магазине, его слово
    главнее находки поиска. Три случая:

        сопоставления нет          — берём находку, но помечаем как неподтверждённую;
        сопоставление совпало      — берём и считаем подтверждённой;
        сопоставление НЕ совпало   — находку выбрасываем: покажется цена из снимка
                                     по подтверждённому товару, а не догадка.
    """
    store = repo.get_store(offer.store_code)
    if store is None:
        return True, False
    mapping = repo.confirmed_mapping(pid, store.id)
    if not mapping:
        return True, False
    known = str(mapping.get("sku") or "")
    if known == str(offer.sku):
        return True, True
    if _is_placeholder(known, offer.store_code):
        # Сопоставление ведёт на выдуманный артикул из резервного CSV («vkusvill-strachatella-200»).
        # Сеть такого не знает, и ссылка на корзину по нему не строится. Настоящая
        # находка поиска главнее: она загораживалась подделкой, и из-за этого
        # корзина не уезжала ни в одну сеть, хотя две это умеют.
        return True, False
    return False, True


def _is_placeholder(sku: str, store_code: str) -> bool:
    """Артикул-заглушка из data/fallback_prices.csv, а не настоящий код сети.

    Заглушки называются «<код магазина>-что-то». Артикулы из чеков («hist-…») сюда
    НЕ попадают: для Пятёрочки и Самоката это единственное, что у нас есть, и они
    настоящие в нашем же пространстве.
    """
    return str(sku).startswith(f"{store_code}-")


def _live_summary(items, stores, live: dict) -> None:
    """Сколько позиций каждый магазин закрывает. Без этого цена магазина обманчива.

    Магазин с самой низкой суммой может просто не иметь половины корзины: сумма
    у него меньше, потому что в ней меньше товаров, а не потому что дешевле.

    Считается только по живому опросу, и подпись говорит об этом прямо. Старый
    снимок цены наличия не помнит вовсе, поэтому магазин, у которого есть сумма
    из снимков, но нет живых ответов, в этой строке не появится — и это честнее,
    чем поставить ему выдуманное «16 из 16».
    """
    if not live:
        return
    rows = []
    for store in stores:
        covered = sum(1 for it in items
                      if (int(it["product_id"]), store.code) in live
                      and live[(int(it["product_id"]), store.code)].in_stock)
        if covered:
            rows.append((store, covered))
    if not rows:
        return
    cells = " ".join(
        f'<span style="display:inline-flex;align-items:center;gap:7px;margin-left:16px;">'
        f'{theme.dot(s.code)}<span>{n} из {len(items)}</span></span>'
        for s, n in rows
    )
    st.markdown(
        '<div style="display:flex;justify-content:space-between;align-items:center;gap:16px;'
        'flex-wrap:wrap;padding:11px 16px;border:1px solid var(--line);border-radius:11px;'
        'margin-top:8px;font-size:13px;color:var(--ink2);">'
        '<span style="font-weight:600;">Есть в наличии, по живому опросу</span>'
        f'<span>{cells}</span></div>',
        unsafe_allow_html=True,
    )


def _ask_button(basket_id: int, items, live: dict) -> None:
    """Кнопка живого опроса и честное предупреждение о его цене во времени."""
    from app import location as client_place

    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("Узнать цены по магазинам", key=f"ask_{basket_id}",
                     type="primary", use_container_width=True):
            _ask_stores(basket_id, items)
            st.rerun()
    with col2:
        addr = client_place.address()
        if not addr:
            st.caption("Адрес не указан — магазины ответят ценами не вашей точки. "
                       "Укажите его в шапке, кнопкой с булавкой.")
        elif live:
            st.caption(f"Цены по адресу «{addr}». Ответы держатся 6 часов, "
                       "повторный опрос мгновенный.")
        else:
            # Замер 15.09.2026: холодный опрос ≈7 с на позицию, по кэшу ≈1,5 с.
            # Округляем вверх и говорим вслух: пустая полоса прогресса без срока
            # выглядит как зависание.
            minutes = max(1, round(len(items) * 7 / 60))
            st.caption(f"Спросим все доставки по адресу «{addr}». "
                       f"Позиций {len(items)} — это {minutes} мин в первый раз "
                       "и несколько секунд потом, пока держится кэш.")


def _live_totals(items, stores, live: dict, cell: dict, totals: dict) -> dict:
    """Пересчёт сумм по магазинам с учётом живых цен.

    В сумму идёт только то, что в магазине ЕСТЬ. Складывать цену отсутствующего
    товара — значит обещать корзину, которую не соберут.
    """
    out = dict(totals)
    for store in stores:
        total = 0.0
        for item in items:
            pid = int(item["product_id"])
            qty = float(item.get("qty") or 0)
            is_kg = (item.get("unit") or "pcs") == "kg"
            offer = live.get((pid, store.code))
            if offer is not None:
                if offer.price is None or not offer.in_stock:
                    continue
                base = offer.per_unit if (is_kg and offer.per_unit) else offer.price
                total += round(float(base) * qty, 2)
            elif (pid, store.code) in cell:
                total += cell[(pid, store.code)]
        out[store.code] = round(total, 2)
    return out


# ---------- добавление позиции ----------
def _ready_in(product_id: int, matrix: dict, stores) -> int:
    """В скольких магазинах товар уже опознан.

    Это и есть «синхронизированность» каталога: товар, сопоставленный со всеми
    сетями, можно положить в корзину и сразу отдать её магазину ссылкой. Товар,
    не сопоставленный ни с кем, придётся искать руками в каждом.
    """
    return sum(1 for st_ in stores if matrix.get((product_id, st_.id)))


def _autofill(basket_id: int) -> bool:
    """Наполняет пустую корзину обычным набором САМА. True — наполнили, надо перерисовать.

    Почему без спроса. Самая частая работа семьи — купить примерно то же, что и в
    прошлый раз. Пустая корзина при загруженной истории — это не выбор человека,
    а состояние, из которого ему в любом случае придётся выбираться, и предлагать
    ему нажать кнопку ради предсказуемого действия значит брать с него плату за
    нашу осторожность. Поэтому корзина собирается сразу и целиком.

    Три ограничителя, чтобы это не превратилось в самоуправство:
      * наполняем ТОЛЬКО пустую корзину — набранное руками не трогаем никогда;
      * делаем это один раз на корзину (отметка в session_state), иначе очистка
        корзины тут же откатывалась бы обратно и убрать позиции стало бы нельзя;
      * говорим, что сделали, и рядом кладём отмену в одно нажатие.
    """
    ключ = f"autofilled_{basket_id}"
    if st.session_state.get(ключ):
        return False
    st.session_state[ключ] = True

    из_истории, err = load("app.baskets", "build_from_history")
    if err:
        return False
    дата, позиции = _peek_usual()
    if not позиции:
        return False
    return _fill(из_истории, "history", basket_id)


def _autofill_note(basket_id: int) -> None:
    """Что именно собралось само — и как это отменить или пересобрать иначе."""
    if not st.session_state.get(f"shown_autofill_{basket_id}"):
        return
    дата = st.session_state.get(f"autofill_date_{basket_id}")
    из_истории, err = load("app.baskets", "build_from_history")

    col1, col2, col3 = st.columns([4, 2, 2])
    col1.caption(f"Корзина собралась сама по покупке от {дата}. "
                 "Поменяйте количество, уберите лишнее или доберите ниже.")
    if col2.button("По среднему за месяц", key=f"re_avg_{basket_id}", width="stretch") and not err:
        repo.clear_basket(basket_id)
        _fill(из_истории, "average", basket_id)
        st.rerun()
    if col3.button("Очистить", key=f"re_clear_{basket_id}", width="stretch"):
        repo.clear_basket(basket_id)
        st.session_state.pop(f"shown_autofill_{basket_id}", None)
        st.rerun()


def _empty_start(basket_id: int) -> bool:
    """Пустая корзина, которую не наполнили. Два разных случая, и путать их нельзя.

    Наполнить нечем — истории покупок нет, и человеку надо идти за чеками.
    Наполнять не стали — история есть, но корзину уже собирали, а человек её
    очистил; тогда предлагать надо не чеки, а собрать заново. Первая редакция
    писала «Истории покупок пока нет» в обоих случаях — то есть врала человеку,
    у которого шестнадцать покупок загружено.
    """
    дата, позиции = _peek_usual()

    if not позиции:
        st.markdown(
            '<div class="mk-card" style="padding:22px 24px;">'
            '<div class="mk-eyebrow" style="margin-bottom:8px;">Корзина пуста</div>'
            '<div class="mk-serif" style="font-size:22px;margin-bottom:8px;">'
            'Наполнить нечем</div>'
            '<div style="color:var(--ink2);font-size:13px;line-height:1.5;">'
            'Истории покупок пока нет — загрузите чеки на экране «Мои чеки», и дальше '
            'корзина будет собираться сама при каждом открытии.'
            '</div></div>',
            unsafe_allow_html=True,
        )
        return False

    st.markdown(
        '<div class="mk-card" style="padding:22px 24px;">'
        '<div class="mk-eyebrow" style="margin-bottom:8px;">Корзина пуста</div>'
        '<div class="mk-serif" style="font-size:22px;margin-bottom:8px;">'
        'Вы её очистили</div>'
        '<div style="color:var(--ink2);font-size:13px;line-height:1.5;">'
        f'Можно собрать заново по покупке от {esc(str(дата))} — там было '
        f'{format_plural(позиции)}, — или добрать товары из каталога ниже.'
        '</div></div>',
        unsafe_allow_html=True,
    )
    из_истории, err = load("app.baskets", "build_from_history")
    if err:
        return False
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Собрать заново", key=f"again_{basket_id}", type="primary", width="stretch"):
            return _fill(из_истории, "history", basket_id)
    with col2:
        if st.button("По среднему за месяц", key=f"again_avg_{basket_id}", width="stretch"):
            return _fill(из_истории, "average", basket_id)
    return False


def _peek_usual() -> tuple[str | None, int]:
    """Дата и размер последней покупки — чтобы предложение было не абстрактным."""
    сколько, err = load("app.baskets", "last_purchase_items")
    if err:
        return None, 0
    try:
        дата, строки = сколько()
        return дата, len(строки)
    except Exception:  # noqa: BLE001
        return None, 0


def _fill(builder, kind: str, basket_id: int) -> bool:
    """Наполняет ТЕКУЩУЮ корзину, а не заводит новую: человек уже стоит в этой."""
    try:
        результат = builder(kind)
    except Exception as exc:  # noqa: BLE001
        show_exception(exc, "Не получилось собрать корзину")
        return False
    источник = int(результат.get("basket_id") or 0)
    if not источник:
        st.warning("В истории не нашлось позиций для сборки.")
        return False
    for строка in repo.basket_items(источник):
        repo.set_basket_item(basket_id, int(строка["product_id"]), float(строка["qty"]))
    # Сборка завела свою корзину, а наполнили мы текущую — за собой убираем,
    # иначе список корзин копит мусорную запись на каждое открытие экрана.
    if источник != basket_id:
        repo.delete_basket(источник)
    дата, _ = _peek_usual()
    st.session_state[f"shown_autofill_{basket_id}"] = True
    st.session_state[f"autofill_date_{basket_id}"] = дата or "истории"
    return True


def _add_item(basket_id: int, products) -> None:
    theme.heading("Добавить позицию")
    if not products:
        st.info("Список товаров пуст. Заведите их на экране «Товары».")
        return

    stores = repo.list_stores()
    matrix = repo.mapping_matrix()
    в_корзине = {int(i["product_id"]) for i in repo.basket_items(basket_id)}

    запрос = st.text_input("Поиск по каталогу", key="basket_add_search",
                           placeholder="молоко · хлеб · огурцы")
    подходящие = [p for p in products
                  if p.id not in в_корзине
                  and (not запрос.strip() or запрос.strip().lower() in (p.name or "").lower())]

    # Сверху — товары, известные наибольшему числу сетей: их корзина уедет
    # в магазин ссылкой, а не превратится в список для ручного поиска.
    подходящие.sort(key=lambda p: (-_ready_in(p.id, matrix, stores), p.name or ""))

    if not подходящие:
        st.caption("Ничего не нашлось — или всё уже в корзине.")
        return

    st.caption(f"Показано {min(len(подходящие), 8)} из {len(подходящие)}. "
               "Цифра справа — в скольких магазинах товар уже опознан: "
               "чем больше, тем проще потом отдать корзину.")
    for product in подходящие[:8]:
        готов = _ready_in(product.id, matrix, stores)
        col1, col2, col3 = st.columns([6, 2, 1])
        col1.markdown(f"**{esc(product.name)}** "
                      f'<span style="color:var(--ink3);font-size:12px;">'
                      f"{esc(unit_label(product.unit))}"
                      + (f" · {esc(product.brand)}" if product.brand else "")
                      + "</span>", unsafe_allow_html=True)
        col2.markdown(
            f'<span style="font-size:12px;color:{"var(--green)" if готов else "var(--ink3)"};">'
            f'в {готов} из {len(stores)} магазинов</span>', unsafe_allow_html=True)
        if col3.button("＋", key=f"add_{basket_id}_{product.id}", help="Добавить одну штуку"):
            repo.set_basket_item(basket_id, product.id, 1.0)
            st.rerun()


def _usual_chips(basket_id: int) -> None:
    """«Обычно берёте, но сейчас нет» — одним нажатием вернуть в корзину.

    Забытый товар стоит дороже разницы в ценах: за ним придётся идти отдельно.
    """
    fn, err = load("app.baskets", "missing_regulars")
    if err:
        return
    try:
        забытые = fn(basket_id)
    except Exception:  # noqa: BLE001
        return
    if not забытые:
        return
    theme.heading("Обычно берёте, но сейчас нет")
    колонки = st.columns(min(4, len(забытые)))
    for n, товар in enumerate(забытые[:8]):
        с = колонки[n % len(колонки)]
        подпись = f"＋ {товар['name'][:24]}"
        if с.button(подпись, key=f"reg_{basket_id}_{товар['product_id']}",
                    help=f"Покупали в {товар['times']} разных чеках", width="stretch"):
            repo.set_basket_item(basket_id, int(товар["product_id"]), 1.0)
            st.rerun()


# ---------- редактирование позиций ----------
def _items(basket_id: int, items) -> None:
    theme.heading("Позиции корзины")
    if not items:
        st.info("Корзина пуста — добавьте позиции выше.")
        return

    stores = repo.list_stores()
    cell, totals = _price_matrix(items, stores)
    live = st.session_state.get(_live_key(basket_id)) or {}

    # Кнопка вне формы: внутри формы она сработала бы только вместе с сохранением.
    _ask_button(basket_id, items, live)
    if live:
        totals = _live_totals(items, stores, live, cell, totals)

    # Формы здесь нет намеренно. Раньше строки жили внутри st.form, и любая правка
    # — поменять количество, убрать позицию — требовала отдельного нажатия
    # «Сохранить изменения». Для корзины это лишний шаг на каждое движение:
    # человек правит её по одной строке и ждёт, что изменение уже случилось.
    head = st.columns([4, 2, 1, 3])
    head[0].markdown("**Товар**")
    head[1].markdown("**Количество**")
    head[2].markdown("**Убрать**")
    head[3].markdown("**Цены по магазинам**")

    for item in items:
        pid = int(item["product_id"])
        unit = item.get("unit") or "pcs"
        is_kg = unit == "kg"
        cols = st.columns([4, 2, 1, 3])
        cols[0].write(f"{item.get('name')}" + (f" · {item['brand']}" if item.get("brand") else ""))
        qty = cols[1].number_input(
            unit_label(unit),
            min_value=0.0,
            step=0.001 if is_kg else 1.0,
            value=float(item.get("qty") or 0),
            format="%.3f" if is_kg else "%.0f",
            key=f"qty_{basket_id}_{pid}",
            label_visibility="collapsed",
        )
        # Количество пишем сразу, но только когда оно правда изменилось: иначе
        # каждая перерисовка экрана стучалась бы в базу шестнадцать раз подряд.
        if abs(float(qty) - float(item.get("qty") or 0)) > 1e-9:
            repo.set_basket_item(basket_id, pid, float(qty))
            st.rerun()
        if cols[2].button("✕", key=f"del_{basket_id}_{pid}", help="Убрать из корзины"):
            repo.set_basket_item(basket_id, pid, 0.0)
            st.rerun()
        cols[3].markdown(_price_html(pid, stores, cell, live), unsafe_allow_html=True)

    priced = [s for s in stores if totals.get(s.code)]
    if priced:
        cells = " ".join(
            f'<span style="display:inline-flex;align-items:center;gap:7px;margin-left:18px;">'
            f'{theme.dot(s.code)}<span class="mk-serif" style="font-size:18px;">{rub(totals[s.code])}</span></span>'
            for s in priced
        )
        st.markdown(
            '<div style="display:flex;justify-content:space-between;align-items:center;gap:16px;'
            'padding:13px 16px;background:var(--tint);border-radius:11px;margin-top:8px;">'
            '<span style="font-weight:600;font-size:13px;">Вся корзина в одном магазине</span>'
            f'<span>{cells}</span></div>',
            unsafe_allow_html=True,
        )

    _live_summary(items, stores, live)
    _unconfirmed_note(live)

    if st.button("Очистить корзину", key=f"clear_{basket_id}"):
        repo.clear_basket(basket_id)
        st.rerun()


# ---------- расчёт ----------
def _calculate(basket_id: int, basket_name, items) -> None:
    theme.heading("Расчёт")
    if not items:
        st.info("Добавьте позиции — тогда посчитаем.")
        return

    # Опрос цен вынесен в отдельную кнопку выше и сохраняет снимки в базу, поэтому
    # расчёт по умолчанию считает по ним и отвечает мгновенно. Раньше он молча шёл
    # в сеть за всеми ценами заново: кнопка на две минуты выглядела мёртвой, без
    # полосы, без срока и без единой подсказки, что вообще происходит.
    свежие = st.checkbox(
        "Сначала обновить цены (займёт пару минут)", value=False, key="calc_refresh",
        help="Обычно не нужно: расчёт берёт цены последнего опроса. "
             "Отметьте, если цены собирались давно.",
    )
    if st.button("Рассчитать", type="primary", key="calc_btn"):
        fn, err = load("app.service", "calculate")
        if err:
            module_warning(err)
            return
        try:
            with st.spinner("Обновляем цены и подбираем варианты…" if свежие
                            else "Подбираем варианты…"):
                variants, baseline = fn(basket_id, bool(свежие))
        except ImportError as exc:
            module_warning(f"Расчёт недоступен: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            show_exception(exc, "Расчёт не получился")
            return

        st.session_state["calc"] = {
            "basket_id": basket_id,
            "basket_name": basket_name,
            "variants": list(variants or []),
            "baseline": baseline,
        }
        goto("Результат")
        st.rerun()

    previous = repo.list_variants(basket_id)
    if previous:
        with st.expander(f"Сохранённые расчёты ({len(previous)})"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Когда": v.get("created_at"),
                            "Итого": rub(v.get("total")),
                            "Базовая сумма": rub(v.get("baseline")),
                            "Экономия": rub(v.get("savings_rub")),
                            "%": num(v.get("savings_pct")),
                        }
                        for v in previous
                    ]
                ),
                hide_index=True,
            )


def header_stats() -> str:
    # на первом заходе ключа в session_state ещё нет — берём первую корзину
    basket_id = st.session_state.get("basket_id")
    if not basket_id:
        baskets = repo.list_baskets()
        basket_id = baskets[0]["id"] if baskets else None
    if not basket_id:
        return ""
    items = repo.basket_items(int(basket_id))
    if not items:
        return theme.stat_chips([("Позиций", "0")])
    _, totals = _price_matrix(items, repo.list_stores())
    priced = [(s.name, totals[s.code]) for s in repo.list_stores() if totals.get(s.code)]
    return theme.stat_chips([("Позиций", str(len(items)))] + [(n, rub(v)) for n, v in priced])


# ---------- корзина из истории покупок ----------
def _from_history() -> None:
    """Собрать корзину по прошлой покупке или по среднему за месяц (раздел 5.3)."""
    from app import baskets

    left, right = st.columns(2)
    made = None
    with left:
        if st.button("Как в прошлый раз", key="basket_from_last", width="stretch"):
            made = ("history", baskets.build_from_history(baskets.LAST))
    with right:
        if st.button("По среднему за месяц", key="basket_from_avg", width="stretch"):
            made = ("average", baskets.build_from_history(baskets.AVERAGE))

    if not made:
        return
    kind, result = made
    if not result["basket_id"]:
        st.warning("В истории пока нет покупок, из которых можно собрать корзину. "
                   "Загрузите чек на экране «История».")
        return

    st.session_state["basket_id"] = result["basket_id"]
    if kind == "average":
        st.success(f"Собрали корзину по среднему: {len(result['items'])} позиций "
                   f"за {result['months']} мес.")
    else:
        st.success(f"Повторили покупку от {result['date']}: {len(result['items'])} позиций.")
    if result["skipped"]:
        st.caption(f"Пропустили {result['skipped']}: этих товаров больше нет в справочнике.")
    st.rerun()
