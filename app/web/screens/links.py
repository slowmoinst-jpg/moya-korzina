"""Связи: один товар — разные названия в сетях.

САМЫЙ ОТВЕТСТВЕННЫЙ ЭКРАН ПРИЛОЖЕНИЯ, и это не преувеличение. Подтверждённое здесь
сопоставление определяет, что человек потом увидит в корзине и за что заплатит. Ошибка
не выглядит ошибкой: «Страчателла 200 г», подтверждённая мороженым «Maxibuo страчателла
92 г», даёт красивую цену и неправильный товар в заказе. Поэтому:

  · рядом с каждым кандидатом написано, ЧТО именно будет подтверждено — название сети
    целиком, артикул, цена и похожесть, а не одна кнопка «Подтвердить» в конце строки;
  · возражения матчера (app/matcher/quality.doubts) показаны ДО подтверждения, а не
    после. На прежнем экране они появлялись только у уже подтверждённой связи — то есть
    ровно тогда, когда предупреждать поздно;
  · «Убрать связь» спрятана в раскрывающийся блок с объяснением последствий. Не ради
    лишнего нажатия: снятая связь тихо выкидывает товар из расчёта, и человек узнаёт об
    этом по тому, что корзина подешевела «сама».

ТРИ УРОВНЯ, И У КАЖДОГО СВОЙ АДРЕС. `/links` — очередь: у каких товаров какие сети ещё
не опознаны. `/links?product=12` — один товар и все сети. `/links?product=12&store=lenta` —
работа над одной парой. В Streamlit это были два выпадающих списка, и очереди не было
видно вовсе: человек выбирал товар наугад и не знал, много ли осталось.

СЕТЬ ДЁРГАЕТСЯ ТОЛЬКО НАЖАТИЕМ. Подбор и опрос цен — это минуты и сотни запросов;
открытие страницы читает только базу. Само нажатие при этом отмечается как проход
единого каталога: без метки второе нажатие и одновременный чек из ФНС поднимали два
прохода разом (ревью 18.10.2026, находка №10).

ОТКАЗ ОТ СЕТИ И ЦЕНА-ЗАГЛУШКА — РАЗНОЕ. Подтверждение настоящего артикула значит
«сеть мне подходит» и снимает прежний отказ. Подтверждение заглушки (строка чека,
прайса или справочника) не значит ничего, кроме «эта цена мне годится»: в корзину
сети по ней ничего не положить, и отказ человека она не снимает — иначе убранный
артикул возвращался бы в наряд через подменённую связь (находка №4). У цены-заглушки
своя подпись и своё действие «убрать только её» — без отказа от сети (находка №11).

ПОЧЕМУ КАНДИДАТЫ ЖИВУТ В СЕССИИ. matcher.find_candidates сохраняет найденное в базу как
неподтверждённые сопоставления, но НЕ сохраняет похожесть — она считается на лету и
нигде не лежит. Прочитать кандидатов из базы значило бы показать их без главного числа,
а спросить магазин заново — сходить в сеть на открытии страницы. Поэтому список едет
через сессию от нажатия до следующей страницы, ровно как он ехал через session_state
прежнего экрана. Это передача результата действия, а не состояние экрана: фильтр и
выбранная пара по-прежнему целиком в адресе.
"""
from __future__ import annotations

import logging
from datetime import date

from flask import flash, redirect, render_template, request, session
from urllib.parse import urlencode

from app import repo
from app.catalog.model import placeholder_sku
from app.web.screens.products import num, rub, unit_label
from app.web.views import SCREEN_BY_KEY

log = logging.getLogger(__name__)

CANDIDATES = "links_candidates"
NAME_LIMIT = 200          # кука подписанной сессии мала: длинное название в ней не нужно


def _back(product_id=None, store_code: str | None = None, query: str = "") -> str:
    """Адрес возврата после действия. Собирается заново, а не берётся из формы.

    Взять его из скрытого поля было бы удобнее и опаснее: подставленный кем угодно
    чужой адрес превратил бы форму подтверждения в дорогу на подделку, причём для
    человека это выглядело бы как продолжение работы.
    """
    params = {k: v for k, v in (("product", product_id), ("store", store_code), ("q", query)) if v}
    path = SCREEN_BY_KEY["links"].path
    return f"{path}?{urlencode(params)}" if params else path


# ---------- что показать ----------
def _states() -> dict[tuple[int, int], str]:
    """Состояние каждой пары «товар — сеть»: done / seen / stub / new — одним запросом.

    Четыре состояния пары различать обязательно: подтверждён настоящий артикул (done);
    есть неподтверждённый настоящий кандидат (seen); подтверждена одна заглушка, и
    настоящего артикула у пары нет (stub); не искали вовсе (new). Слить stub и seen
    значило бы позвать человека искать там, где уже искали, или наоборот спрятать
    цену, которая уже есть. Считать это по паре отдельными запросами нельзя: на
    очереди в сотни товаров это сотни запросов на каждое открытие страницы. Поэтому
    один GROUP BY — тем же real_sku_sql, что и остальное приложение (ревью
    18.10.2026, №11).
    """
    with repo.get_conn() as conn:
        rows = conn.execute(
            "SELECT m.product_id AS pid, sp.store_id AS sid,"
            f" MAX(CASE WHEN m.confirmed=1 AND {repo.real_sku_sql()} THEN 1 ELSE 0 END) AS real_done,"
            " MAX(CASE WHEN m.confirmed=1 THEN 1 ELSE 0 END) AS any_done,"
            " MAX(CASE WHEN m.confirmed=0 THEN 1 ELSE 0 END) AS candidate"
            " FROM product_mapping m"
            " JOIN store_products sp ON sp.id = m.store_product_id"
            " JOIN stores s ON s.id = sp.store_id"
            " GROUP BY m.product_id, sp.store_id").fetchall()
    out: dict[tuple[int, int], str] = {}
    for r in rows:
        if r["real_done"]:
            out[(r["pid"], r["sid"])] = "done"
        elif r["candidate"]:
            out[(r["pid"], r["sid"])] = "seen"
        elif r["any_done"]:
            out[(r["pid"], r["sid"])] = "stub"
        else:
            out[(r["pid"], r["sid"])] = "new"
    return out


def _queue(products, stores, states, query: str) -> list[dict]:
    """Очередь: у каждого товара — сколько сетей опознано и каких не хватает.

    Три состояния пары, и различать их обязательно: подтверждена; искали, но человек
    ещё не выбрал; не искали вовсе. Слить последние два в «не опознано» значило бы
    отправить человека искать там, где уже искали и не нашли ничего подходящего.
    Четвёртое, «цена из чека без артикула», — не опознано и не искали: у пары есть
    строка чека или прайса, но товара сети за ней нет.
    """
    out = []
    for product in products:
        if query and query not in (product.name or "").lower():
            continue
        marks = []
        done = 0
        for store in stores:
            state = states.get((product.id, store.id), "new")
            done += 1 if state == "done" else 0
            marks.append({"code": store.code, "name": store.name, "state": state})
        out.append({"id": product.id, "name": product.name, "marks": marks,
                    "done": done, "total": len(stores), "left": len(stores) - done})
    # Незаконченные — наверх: экран про работу, и работа должна быть первой на глазах.
    out.sort(key=lambda r: (r["left"] == 0, -r["left"], r["name"] or ""))
    return out


def _human_date(day) -> str | None:
    """«2026-08-14T10:00:09» → «14.08.2026». Цена без даты выглядит сегодняшней."""
    try:
        return date.fromisoformat(str(day)[:10]).strftime("%d.%m.%Y") if day else None
    except ValueError:
        return str(day)


def _stub_words(stub: dict, product, store) -> dict:
    """Что это за заглушка: откуда цена, чья она и идёт ли она в сравнение.

    Одна подпись «Цена сейчас — из ваших чеков или прайса» врала дважды: справочную
    цену файла приложения называла ценой из чека, а двухмесячную строку чека —
    «ценой сейчас». По такой подписи человек решает, доверять ли числу, и решает
    неверно (ревью 18.10.2026, №11).
    """
    from app import freshness, pricelist
    from app.connectors import history

    sku = str(stub.get("sku") or "")
    if sku.startswith(history.PREFIX):
        kind = "receipt"
    elif sku.startswith(pricelist.SKU_PREFIX):
        kind = "list"
    else:
        kind = "reference"
    when = None
    foreign = False
    if kind == "receipt":
        pid = history._product_id(sku)
        # Строка чека чужого товара — не редкость: «Подобрать всё» подставляет
        # ближайшую по названию строку, и у молока 2,5 % это молоко 3,2 %.
        foreign = pid is not None and pid != product.id
        row = history.last_prices(store.code).get(pid) if pid else None
        when = row.get("date") if row else None
    elif kind == "list":
        when = pricelist.updated_at(store.code) or None
    return {"stub_kind": kind, "stub_foreign": foreign, "stub_date": _human_date(when),
            "stub_counts": freshness.price_for(product.id, store.id) is not None}


def _pair(product, store) -> dict:
    """Всё, что известно про одну пару «товар — сеть».

    base — кандидаты единой базы (слабая связь): база нашла товар, но сама в нём не
    уверена, и в корзину он уедет только после подтверждения здесь.
    """
    # «Подтверждено» — только настоящий артикул сети. Заглушка (цена из чека или
    # прайса) показывается отдельно: в корзину сети по ней ничего не положить, и
    # кандидаты базы с поиском остаются на виду (ревью 18.10.2026).
    mapping = repo.confirmed_mapping(product.id, store.id, real_only=True)
    if not mapping:
        stub = repo.confirmed_mapping(product.id, store.id)
        base = [{"sku": c.get("sku"), "name": (c.get("raw_name") or "")[:NAME_LIMIT],
                 "weight_g": c.get("weight_g"),
                 "doubts": _doubts(product, c.get("raw_name"), c.get("weight_g"))}
                for c in repo.base_candidates(product.id, store.id)]
        out = {"confirmed": None, "stub": stub, "doubts": [], "base": base,
               "price": repo.latest_price(stub["id"]) if stub else None}
        if stub:
            out.update(_stub_words(stub, product, store))
        return out
    return {"confirmed": mapping, "stub": None, "doubts": _doubts(product, mapping.get("raw_name"),
                                                                 mapping.get("weight_g")),
            "price": repo.latest_price(mapping["id"])}


def _doubts(product, raw_name: str | None, weight_g=None) -> list[str]:
    """Почему это сопоставление может быть неверным. Пустой список — возражений нет.

    Подтверждённое не значит проверенное: половина связей заводится автоматически.
    Разошлись родовое слово, марка или граммовка — человек должен увидеть это здесь,
    а не догадаться потом по странной цене в корзине.
    """
    try:
        from app.matcher.normalize import similarity
        from app.matcher.quality import doubts
    except Exception as exc:  # noqa: BLE001
        log.warning("проверка сомнений недоступна (%s)", exc)
        return []
    raw = raw_name or ""
    flags = list(doubts(product, raw, weight_g))
    if similarity(product.name, raw) < 0.75:
        flags.append("похожесть названий низкая")
    return flags


def _stashed(product_id: int, store_code: str) -> list[dict]:
    """Кандидаты последнего подбора — только если они про эту самую пару."""
    saved = session.get(CANDIDATES)
    if not isinstance(saved, dict):
        return []
    if saved.get("product") != product_id or saved.get("store") != store_code:
        return []
    return list(saved.get("items") or [])


# ---------- действия ----------
def _claim(phone: str | None) -> bool:
    """Занять проход к единому каталогу за это рабочее место.

    Тем же замком и тем же множеством, что link.link_soon: свой, второй замок дал бы
    ровно то, от чего метка и заводится, — два прохода разом. Пока кнопка работает
    синхронно, проход идёт в запросе, и без метки вход или загрузка чеков поднимали
    бы вдобавок фоновый поток: двойная работа сервера и двойные снимки цен (ревью
    18.10.2026, находка №10).

    Держится на состоянии link (_RUNNING, _AGAIN, _RUNNING_LOCK), см. link.claim/release:
    когда они появятся в app/catalog/link.py, эту пару надо звать вместо внутренностей.
    """
    from app.catalog import link

    if not phone:
        return True
    with link._RUNNING_LOCK:
        if phone in link._RUNNING:
            # Звали, пока шёл проход. Просьбу не теряем: её отдаст _release, а если
            # проход фоновый — он сам увидит _AGAIN и повторит сверку.
            link._AGAIN[phone] = link._AGAIN.get(phone, False) or True
            return False
        link._RUNNING.add(phone)
        return True


def _release(phone: str | None) -> None:
    """Отпустить проход и отдать накопленное фону, а не потерять.

    Пока кнопка работала, вход или загрузка чеков могли позвать link_soon: он увидел
    метку, записал _AGAIN и своего потока не поднял. Забыть про эту просьбу значит
    потерять сверку; выполнить её здесь, в запросе, — задержать ответ на минуты.
    """
    from app.catalog import link

    if not phone:
        return
    with link._RUNNING_LOCK:
        link._RUNNING.discard(phone)
        again = link._AGAIN.pop(phone, None)
    if again is not None:
        link.link_soon(phone, sync=again)


def _do_link_catalog(products) -> None:
    """Опознать товары в едином каталоге сетей: артикулы всех сетей разом, без поиска.

    Каталог (app/catalog) знает товары уже сопоставленными между собой, поэтому одно
    попадание закрывает сразу все сети. Живой подбор ниже остаётся для того, чего в
    каталоге нет.
    """
    try:
        from app.catalog.refresh import link_products
    except Exception as exc:  # noqa: BLE001
        flash(f"Единый каталог сейчас недоступен ({exc}). Остальное на экране работает.", "warn")
        return
    if not products:
        flash("Опознавать пока нечего — список товаров пуст.", "info")
        return
    from app import users

    phone = users.current()
    if not _claim(phone):
        # Фоновый проход этого рабочего места уже идёт (вход, загрузка чеков): второй
        # параллельный делал бы ту же работу. Нажатие превращается в сверку сразу
        # после него (ревью 18.10.2026).
        flash("Сверка с единым каталогом уже идёт — очередь ниже обновится, когда она"
              " закончится.", "info")
        return
    try:
        found = link_products()
    except Exception as exc:  # noqa: BLE001
        log.exception("опознание в едином каталоге не удалось")
        flash(f"Опознание не удалось: {exc}. Остальное на экране работает.", "bad")
        return
    finally:
        _release(phone)
    if found.get("failed"):
        flash(f"Не записано из-за сбоя — {found['failed']}: эти товары пройдут при следующей"
              " сверке, можно нажать ещё раз.", "warn")
    if found["linked"]:
        weak = found.get("weak") or 0
        doubtful = found.get("doubtful") or 0
        extra = ""
        if weak:
            extra += (f" Под вопросом — {weak}: артикулы записаны кандидатами и уедут в"
                      " корзину, когда вы подтвердите их у товара по сети.")
        if doubtful:
            extra += f" Не уверены — {doubtful}: подходят несколько товаров, угадывать не стали."
        flash(f"Опознано {found['linked']} из {found['tried']}: артикулы сетей записаны, "
              f"цены по адресу спросит «Обновить цены».{extra}", "ok")
    elif found.get("doubtful"):
        flash(f"Не уверены — {found['doubtful']}: подходят несколько товаров каталога,"
              " угадывать не стали. Ниже — живой подбор.", "info")
    elif not found.get("failed"):
        flash(f"Ни один из {found['tried']} товаров в каталоге не опознан — либо каталог "
              "ещё не собран, либо названия расходятся. Ниже — живой подбор.", "info")


def _do_auto_match(product_ids, store_codes) -> None:
    from app import matcher

    if not product_ids:
        flash("Связывать пока нечего — список товаров пуст.", "info")
        return
    try:
        report = matcher.auto_match(product_ids, store_codes)
    except Exception as exc:  # noqa: BLE001
        log.exception("подбор не удался")
        flash(f"Подбор не удался: {exc}. Остальное на экране работает.", "bad")
        return
    left = len(report.get("need_review") or [])
    # Список «требуют проверки» не пересказываем: это ровно то, что показывает очередь
    # ниже, и она читается из базы. Две копии одного списка разошлись бы на первом же
    # подтверждении, и одна из них молча врала бы.
    flash(f"Подтверждено автоматически: {report.get('auto', 0)}. Требуют ручной "
          f"проверки: {left} — они в очереди ниже.", "ok")


def _do_refresh_prices(product_ids, store_codes) -> None:
    from app import matcher

    if not product_ids:
        flash("Список товаров пуст.", "info")
        return
    try:
        # Ручное «обновить сейчас»: сети спрашиваются разом с общим сроком, чтобы
        # восемь последовательных запросов не держали экран минутами. force=True —
        # это нажатие человека, кулдаун фонового обновления здесь не указ; паузу
        # заблокированной сети force не обходит (matcher.refresh_prices_parallel).
        result = matcher.refresh_prices_parallel(product_ids, store_codes,
                                                 total_deadline=120, force=True)
    except Exception as exc:  # noqa: BLE001
        log.exception("обновление цен не удалось")
        flash(f"Не удалось обновить цены: {exc}. Остальное на экране работает.", "bad")
        return
    flash(f"Обновлено цен: {result.get('updated', 0)}.", "ok")
    for error in (result.get("errors") or []):
        flash(str(error), "warn")


def _do_find(product, store) -> None:
    from app import matcher

    try:
        found = matcher.find_candidates(product.id, store.code, 3)
    except Exception as exc:  # noqa: BLE001
        log.exception("поиск кандидатов не удался")
        flash(f"Поиск в магазине не удался: {exc}. Остальное на экране работает.", "bad")
        return
    items = [{
        "sku": getattr(c, "sku", "") or "",
        "name": (getattr(c, "name", "") or "")[:NAME_LIMIT],
        "price": getattr(c, "price", None),
        "score": getattr(c, "score", 0),
        "weight_g": getattr(c, "weight_g", None),
    } for c in (found or [])]
    session[CANDIDATES] = {"product": product.id, "store": store.code, "items": items}
    if not items:
        flash("Ничего не нашли. Попробуйте название покороче — например, без граммовки.", "warn")


def _resync(product) -> None:
    """Сверить товар с единым каталогом сразу после решения человека.

    Выбор в одной сети — мост к другим, отказ — повод снять то, что на нём держалось.
    Ждать ночной пересборки незачем: это доли секунды на один товар (ревью 18.10.2026).
    Само решение человека при сбое сверки остаётся в силе.
    """
    try:
        from app.catalog import link

        link.link_products([product.id], sync=True)
    except Exception:  # noqa: BLE001
        log.exception("сверка товара с единым каталогом после решения человека не удалась")


def _remember_variant(product, row: dict, chain: str) -> None:
    """Запомнить написание сети как решение ЧЕЛОВЕКА — сильную ветку словаря названий.

    ЗАЧЕМ. Словарь (app/catalog/store.py, name_variants) наполнялся сам: как пишут
    сети (chain) и как пишут чеки (receipt). Но сильную связь по его правилам даёт
    только источник «human», а его не писал никто: подтверждение человека на «Связях»
    нигде не оседало, и словарь не мог дать ни одного нового товара — замер на боевой
    базе 18.10.2026: 295 020 написаний сетей и 124 чека, а привязка та же 343 из 1159.
    Здесь решение человека и записывается: та же строка в следующем чеке находит товар
    сильной связью, а не кандидатом.

    Пишем только по настоящей связи и только когда товар привязан к единому: заглушка
    (строка чека, прайса, справочника) настоящей связи не даёт, а без номера единого
    товара написание не к чему привязать. Сбой словаря решения человека не отменяет —
    подтверждение уже записано, и терять его из-за словаря нельзя.
    """
    master = repo.master_of(product.id)
    if not master:
        return
    name = str((row or {}).get("raw_name") or "").strip()
    if not name:
        return
    try:
        from app.catalog import store as catalog_store

        if not catalog_store.remember_variant(int(master["master_id"]), name, "human", chain):
            # Пустой свод: в названии не осталось ни одного слова — привязывать нечего.
            log.warning("написание «%s» в словарь не легло: после свода не осталось слов", name)
    except Exception:  # noqa: BLE001 — словарь не повод терять подтверждение
        log.warning("написание «%s» в словарь не записалось (единый товар %s)",
                    name, master["master_id"], exc_info=True)


def _do_confirm(product, store, sku: str) -> None:
    from app import matcher

    try:
        matcher.confirm(product.id, store.code, sku)
    except Exception as exc:  # noqa: BLE001
        log.exception("подтверждение не прошло")
        flash(f"Не получилось связать: {exc}", "bad")
        return
    # «До нового подтверждения» наступает только от настоящего артикула сети. Заглушка
    # — это цена, а не связь: сеть по ней товара не узнаёт, и снятый отказ вернул бы
    # убранный артикул в наряд через ту же подменённую пару (ревью 18.10.2026, №4).
    stub = placeholder_sku(sku, store.code)
    # Кандидаты прошлого подбора — результат действия, а не состояние экрана: после
    # ЛЮБОГО решения по паре они устарели. Сброс стоял только в ветке заглушки, и после
    # подтверждения настоящего артикула список возвращался по «Убрать связь»
    # (adversarial-проверка 18.10.2026, T11).
    session.pop(CANDIDATES, None)
    if stub:
        # Про отказ говорим, только если он есть: без него это была бы выдумка о
        # решении человека (adversarial-проверка 18.10.2026, T11).
        tail = ("Отказ от сети, поставленный раньше, остаётся в силе, пока вы не подтвердите"
                " настоящий артикул." if repo.chain_rejected(product.id, store.id) else
                "Настоящего артикула сети в ней нет, и в корзину магазина по ней ничего не"
                " положить.")
        flash(f"Цена «{product.name}» в «{store.name}» записана по строке {sku}. {tail}", "info")
    else:
        repo.clear_rejects(product.id, store.id)
        # Строку сети, которую человек только что подтвердил, запоминаем его решением:
        # сильная ветка словаря названий живёт только этим (T13, 18.10.2026). Банк
        # названия берём из подтверждённой строки и сверяем артикул: подтвердить
        # могли не то, что лежало в паре раньше.
        row = repo.confirmed_mapping(product.id, store.id)
        if row and str(row.get("sku")) == str(sku):
            _remember_variant(product, row, store.code)
        flash(f"Готово: «{product.name}» связан с «{store.name}» по артикулу {sku}.", "ok")
    _resync(product)


def _do_drop(product, store) -> None:
    repo.drop_mapping(product.id, store.id)
    _resync(product)
    session.pop(CANDIDATES, None)
    flash(f"Связь «{product.name}» с «{store.name}» убрана. До нового подтверждения этот "
          "товар в расчёт по этой сети не попадёт.", "info")


def _do_drop_stub(product, store, sku: str) -> None:
    """Убрать одну цену-заглушку, не отказываясь от сети.

    «Убрать связь» тут не годится: она пишет отказ от сети целиком, а человек
    возражает не против сети, а против конкретной цены — справочной из файла,
    строки чужого чека или старой. Поэтому уходит ровно заглушка, а кандидаты и
    поиск остаются: отказ здесь был бы враньём о решении человека (ревью 18.10.2026,
    находка №11).

    repo.drop_mapping для пары без настоящего артикула удаляет подтверждённые
    заглушки и НЕ пишет link_rejects — это ровно нужное поведение. Присланный из
    формы артикул сверяем с тем, что лежит в базе: иначе подделанная форма убрала бы
    чужую связь.
    """
    stub = repo.confirmed_mapping(product.id, store.id)
    if (not stub or str(stub.get("sku")) != str(sku)
            or not placeholder_sku(sku, store.code)):
        flash("Убирать нечего: эту цену уже сменили.", "warn")
        return
    repo.drop_mapping(product.id, store.id)
    _resync(product)
    session.pop(CANDIDATES, None)
    # Про отказ человека говорим, только если он есть: пара могла быть убрана раньше
    # целиком, и тогда «вы не отказывались» — неправда (adversarial-проверка
    # 18.10.2026, T11).
    tail = ("Отказ от сети, поставленный раньше, остаётся в силе — убрана только цена."
            if repo.chain_rejected(product.id, store.id) else
            f"От сети «{store.name}» вы не отказывались: поиск и кандидаты остаются.")
    flash(f"Цена по строке {sku} убрана. {tail}", "info")


def page():
    products = repo.list_products(active_only=False)
    stores = repo.list_stores()
    query = (request.args.get("q") or "").strip()

    if request.method == "POST":
        action = request.form.get("action")
        product = next((p for p in products if str(p.id) == request.form.get("product")), None)
        store = next((s for s in stores if s.code == request.form.get("store")), None)
        active_ids = [p.id for p in products if p.active]
        codes = [s.code for s in stores]

        if action == "link_catalog":
            _do_link_catalog(products)
        elif action == "auto_match":
            _do_auto_match(active_ids, codes)
        elif action == "refresh_prices":
            _do_refresh_prices(active_ids, codes)
        elif product is None or store is None:
            flash("Товар или магазин не найдены — возможно, их успели изменить.", "warn")
        elif action == "find":
            _do_find(product, store)
        elif action == "confirm":
            _do_confirm(product, store, request.form.get("sku") or "")
        elif action == "drop_stub":
            _do_drop_stub(product, store, request.form.get("sku") or "")
        elif action == "drop":
            _do_drop(product, store)

        return redirect(_back(request.form.get("product"), request.form.get("store"),
                              request.form.get("q") or ""))

    # Подтверждённой считается связь с настоящим артикулом: заглушка строки чека в
    # корзину сети не уедет, и пара с ней ещё ждёт работы.
    states = _states()
    product = next((p for p in products if str(p.id) == request.args.get("product")), None)
    store = next((s for s in stores if s.code == request.args.get("store")), None)

    chosen = None
    if product is not None:
        chosen = {
            "product": product,
            "unit": unit_label(product.unit),
            "stores": [{"code": s.code, "name": s.name,
                        "state": states.get((product.id, s.id), "new")}
                       for s in stores],
        }

    pair = None
    if product is not None and store is not None:
        pair = _pair(product, store)
        pair["store"] = store
        pair["candidates"] = _stashed(product.id, store.code)
        # Возражения к каждому кандидату считаются теми же правилами, что и к
        # подтверждённой связи: предупреждать после подтверждения поздно.
        for candidate in pair["candidates"]:
            candidate["doubts"] = _doubts(product, candidate.get("name"),
                                          candidate.get("weight_g"))

    done = sum(1 for p in products for s in stores if states.get((p.id, s.id)) == "done")
    total = len(products) * len(stores)

    return render_template(
        "links.html",
        screen=SCREEN_BY_KEY["links"],
        rub=rub, num=num,
        query=query,
        stats=[("Товаров", str(len(products))),
               ("Связок подтверждено", f"{done} из {total}")],
        queue=_queue(products, stores, states, query.lower()),
        chosen=chosen,
        pair=pair,
        back=_back(product.id if product else None, store.code if store else None, query),
    )


# Действия экрана живут на его же адресе — см. пояснение в app/web/screens/products.py.
page.methods = ("GET", "POST")

__all__ = ["page"]
