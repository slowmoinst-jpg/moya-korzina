"""Корзина: что покупаем, почём это в каждой сети и куда выгоднее ехать.

САМОЕ ГЛАВНОЕ ПРИОБРЕТЕНИЕ ПЕРЕЕЗДА — У КОРЗИНЫ ПОЯВИЛСЯ АДРЕС. В Streamlit
выбранная корзина жила в session_state: ссылку на неё дать было нельзя, «назад»
уводило из приложения, а две вкладки рядом дрались за одно значение. Здесь
корзина названа в адресе (/basket?id=7), поиск по каталогу — тоже (&q=молоко), и
страница целиком определяется тем, что написано в строке браузера. Ничего своего
экран в сессии не держит.

ПОЧЕМУ ВСЕ ДЕЙСТВИЯ — ОДНА ФОРМА. Позиции, их количества, «убрать», «очистить» и
кнопки «−/+» лежат в ОДНОЙ форме, а различает их нажатая кнопка (do=save,
do=drop:12, do=plus:12…). Так набранные, но не сохранённые количества доезжают до
сервера при любом нажатии: человек правит корзину стоя у полки, и потерять его
ввод из-за того, что он нажал «убрать» раньше «сохранить», — худшее, что может
сделать этот экран. После действия всегда перевод обратно на адрес корзины: ни
одно нажатие не остаётся в истории браузера как повторяемое.

ЧТО РАСХОДИТСЯ СО СТАРЫМ ЭКРАНОМ И ПОЧЕМУ

  1. ПУСТАЯ КОРЗИНА БОЛЬШЕ НЕ НАПОЛНЯЕТСЯ САМА ПРИ ОТКРЫТИИ. В Streamlit она
     собиралась по истории молча, а от повторного наполнения (после «очистить»)
     спасала отметка в session_state. Здесь такой отметки нет и быть не должно, а
     запись в базу на обычном открытии страницы означала бы: обновил вкладку —
     корзина снова полна, очистил в одной вкладке — вернулась из другой. Ровно то,
     ради чего затевался переезд, это бы и сломало. Поэтому наполнение переехало
     туда, где оно и есть действие: новая корзина создаётся уже собранной, а
     пустую собирают две кнопки на её же экране, с датой и размером прошлой
     покупки в подписи — одно нажатие вместо ввода руками.

  2. ЖИВОЙ ОПРОС ЦЕН УШЁЛ В ФОН. Опрос всех сетей по всем позициям — это минуты
     (замер 15.09.2026: ≈7 с на позицию вхолодную). Держать столько открытым
     обычный HTTP-запрос нельзя: его оборвёт или браузер, или прокси, и человек
     увидит ошибку вместо цен. Поэтому нажатие заводит фоновую работу, страница
     показывает, какой товар спрашивается сейчас, и обновляется сама. Результат
     опроса при этом никуда не «кладётся на экран»: он сохраняется снимками цен
     в базу (как и раньше, см. _remember в прежнем экране), поэтому переживает и
     закрытую вкладку, и перезапуск сервера.

  3. «РАССЧИТАТЬ» БОЛЬШЕ НЕ ДЕРЖИТ ДВА СОСТОЯНИЯ. Без обновления цен расчёт лёгкий
     и считается прямо на экране «Результат» (app/web/screens/result.py) — туда и
     переводим. С обновлением цен тяжёлая часть — само обновление, и оно идёт той
     же фоновой работой, что и опрос: когда она кончится, «Результат» посчитает по
     свежим снимкам.

ЭКРАН ПЕРЕСОБРАН ПОД ТЕЛЕФОН (17.09.2026). Это главный экран ежедневного
пользования: человек стоит у полки и добавляет товары ОДНОЙ рукой. Отсюда четыре
решения, каждое против живой поломки.

  ДОБОР ПОДНЯТ НАВЕРХ, СПИСОК УШЁЛ ПОД НЕГО. Раньше порядок был «сначала то, что
  уже в корзине, потом добор», и на телефоне это значило: чтобы положить второй
  товар, прокрути мимо первого. На сороковой позиции добор оказывался за экран от
  начала. Теперь сверху то, чем работают каждую минуту, — частое и поиск, а список
  под ними, и он же виден в прилипшей строке итога.

  ЧАСТОЕ ПОКАЗЫВАЕТСЯ ДО ВСЯКОГО ПОИСКА. Человек покупает одно и то же, и пустое
  поле поиска — это вопрос там, где у нас уже есть ответ: история покупок лежит в
  базе (app/baskets.regular_purchases). Поэтому первое, что видно на экране, —
  то, что он брал не раз и чего сейчас в корзине нет, нажатием на каждое.

  ИТОГ ПРИЛИПАЕТ, А НЕ ПРИБИВАЕТСЯ. Сколько позиций, сколько примерно стоит и
  «Посчитать» — в строке, которая держится у нижнего края. Считаем по самой
  дешёвой цене каждой позиции и честно говорим, у скольких позиций цены нет:
  сумма известного, выданная за итог, в магазине не проверяется ничем.

  КОРЗИНА ПОМНИТ, ГДЕ ЕЁ ОСТАВИЛИ. В нижней панели телефона «Корзина» — это голый
  /basket, без номера, и раньше он открывал САМУЮ НОВУЮ корзину. Человек уходил на
  «Результат» из той, которую набирал, возвращался — и попадал в другую. Работа не
  терялась, но выглядело это ровно как потеря. Теперь голый адрес открывает ту, в
  которой человек последний раз что-то менял.

ЛОГИКИ ЗДЕСЬ НЕТ. Сборка из истории — app/baskets.py, цены — app/repo.py, расчёт —
app/service.py, каталог сетей — app/catalog. Экран только показывает то, что они
дали, и передаёт им нажатия.
"""
from __future__ import annotations

import logging
import threading
import time
from urllib.parse import quote

from flask import g, redirect, render_template, request

from app import repo
from app.web import auth
from app.web.views import SCREEN_BY_KEY

log = logging.getLogger(__name__)

PATH = SCREEN_BY_KEY["basket"].path

# Подсказка поиска отвечает данными, а не страницей, поэтому живёт своим адресом и
# в карту экранов (app/web/views.SCREENS) не попадает: в меню ей делать нечего.
SUGGEST_PATH = f"{PATH}/suggest"

# Какую корзину человек правил последней. Ключ в его же базе, а не в сессии: сессия
# не переживает закрытую вкладку, а «вернуться туда, где остановился» нужно как раз
# назавтра. Пишется только действием — см. _remember_open.
LAST_OPEN = "basket:last"

# Короче двух букв подсказка смысла не имеет: на «м» откликается половина каталога,
# и человек получает список, который не читает, вместо ответа.
SUGGEST_FROM = 2

# Неразрывный пробел: «1 234,56 ₽» не должно переноситься между разрядами и
# знаком валюты — на телефоне это видно сразу, сумма разваливается на две строки.
NBSP = " "


# ---------- числа и деньги ----------
# Живут здесь, а не в общем файле, потому что общего файла у нового интерфейса
# пока нет: прежние помощники (app/ui/helpers.py) тянут за собой streamlit, а он
# и есть то, от чего уезжаем. Корзина — первый экран своего раздела, «Результат»
# и «История» берут формат отсюда. Когда переезд кончится, это место переедет в
# app/web/format.py одним движением.
def rub(value) -> str:
    """1234.56 -> «1 234,56 ₽». Пусто -> «—», а не «0,00 ₽»: это разные ответы."""
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:,.2f}".replace(",", NBSP).replace(".", ",") + NBSP + "₽"


def num(value, digits: int = 3) -> str:
    """Число без хвостовых нулей: «2», а не «2,000» — в корзине это лишний шум."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.{digits}f}".rstrip("0").rstrip(".").replace(".", ",")


def pct(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f}".replace(".", ",") + NBSP + "%"
    except (TypeError, ValueError):
        return "—"


def unit_label(unit: str | None) -> str:
    return "кг" if (unit or "pcs") == "kg" else "шт"


def plural_items(count: int) -> str:
    """«1 позиция», «2 позиции», «5 позиций» — иначе в подписи видно машину."""
    tail = count % 100
    if 11 <= tail <= 14:
        return f"{count} позиций"
    last = count % 10
    word = "позиция" if last == 1 else "позиции" if 2 <= last <= 4 else "позиций"
    return f"{count} {word}"


# ---------- фоновая работа: опрос цен и обновление перед расчётом ----------
# Долгая работа привязана к паре «база человека + корзина», а не к сессии: в
# сессии её не удержать (она переживает закрытую вкладку), а на сервере — можно.
# Экран от этого не становится «с состоянием»: что показывать, по-прежнему решает
# адрес, а работа лишь дорисовывает к нему строку «идёт опрос».
_JOBS: dict[tuple[str, int], dict] = {}
_JOBS_LOCK = threading.Lock()

# Сколько держим законченную работу, чтобы человек успел увидеть её итог. Без
# срока строка «цены обновлены» висела бы вечно и через день врала бы о свежести.
KEEP_DONE_SEC = 900


def _job_key(basket_id: int) -> tuple[str, int]:
    from app import config
    return (config.db_path(), int(basket_id))


def job_of(basket_id: int) -> dict | None:
    """Что сейчас делается с этой корзиной. None — ничего."""
    key = _job_key(basket_id)
    with _JOBS_LOCK:
        job = _JOBS.get(key)
        if job and job["status"] != "running" and time.time() - job["finished"] > KEEP_DONE_SEC:
            _JOBS.pop(key, None)
            return None
        return dict(job) if job else None


def _start_job(basket_id: int, kind: str, items: list[dict]) -> None:
    """Завести фоновую работу, если такой ещё нет.

    ПОТОК НЕ УНАСЛЕДУЕТ БАЗУ ЧЕЛОВЕКА. Путь к ней лежит в contextvars (app/config.py),
    а новый поток в Python начинает с ПУСТОГО контекста — не с копии родительского.
    Значит фоновая работа без своего open_workspace писала бы цены в общую базу из
    config.yaml, а человек ждал бы их в своей и не дождался. Поэтому номер телефона
    передаётся в поток руками и рабочее место открывается там заново.
    """
    key = _job_key(basket_id)
    phone = getattr(g, "phone", None)
    if not phone:
        return
    with _JOBS_LOCK:
        running = _JOBS.get(key)
        if running and running["status"] == "running":
            return
        _JOBS[key] = {"kind": kind, "status": "running", "done": 0,
                      "total": max(1, len(items)), "what": "", "note": "",
                      "started": time.time(), "finished": 0.0}

    worker = threading.Thread(target=_run_job, args=(key, phone, int(basket_id), kind, items),
                              name=f"korzina-{kind}-{basket_id}", daemon=True)
    worker.start()


def _mark(key, **fields) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(key)
        if job:
            job.update(fields)


def _run_job(key, phone: str, basket_id: int, kind: str, items: list[dict]) -> None:
    from app import users

    try:
        users.open_workspace(phone)
        if kind == "ask":
            _ask_stores(key, items)
            note = "Цены обновлены по всем позициям."
        else:
            from app import service
            service.calculate(basket_id, True)   # нужны не варианты, а свежие снимки цен
            note = "Цены обновлены — можно смотреть результат."
        _mark(key, status="ok", note=note, finished=time.time())
    except Exception as exc:  # noqa: BLE001 — фоновая работа не должна ронять сервер
        log.exception("фоновая работа %s по корзине %s не удалась", kind, basket_id)
        _mark(key, status="failed", note=str(exc), finished=time.time())
    finally:
        # Снять базу с потока обязательно: потоки в пуле переиспользуются, и
        # оставленная чужая база — самая незаметная из всех возможных ошибок.
        try:
            users.deactivate()
        except Exception:  # noqa: BLE001
            log.exception("рабочее место не снялось с фонового потока")


def _ask_stores(key, items: list[dict]) -> None:
    """Спросить цены и наличие по всем позициям и сохранить найденное снимками.

    Идём по позициям, а не по магазинам: только так видно, какой товар
    спрашивается сейчас, — а без этого полоса ожидания неотличима от зависания.
    """
    from app import compare

    for n, item in enumerate(items, start=1):
        name = item.get("name") or ""
        _mark(key, done=n - 1, what=name)
        try:
            for offer in compare.compare_query(name, per_store=1):
                # Артикул сохраняем ДАЖЕ БЕЗ ЦЕНЫ: так устроен Дикси — его каталог
                # отдаёт идентификатор с адресом карточки, а цену прячет за защитой
                # сайта. Отбросив такой ответ, мы теряем не цену, а возможность
                # открыть товар в приложении сети.
                if offer.sku and _agrees_with_mapping(int(item["product_id"]), offer):
                    _remember(int(item["product_id"]), offer)
        except Exception:  # noqa: BLE001 — один товар не должен ронять весь опрос
            log.warning("«%s» спросить не удалось", name, exc_info=True)
        _mark(key, done=n, what=name)


def _remember(product_id: int, offer) -> None:
    """Запомнить найденный артикул сопоставлением и цену — снимком.

    БЕЗ ЭТОГО ШАГА КОРЗИНА НЕ УЕЗЖАЕТ В МАГАЗИН: ссылка на готовую корзину
    принимает не название, а идентификатор товара В ЭТОЙ СЕТИ. Заодно снимок цены
    переживает вкладку — иначе назавтра корзина снова оказывалась бы без цен.
    """
    store = repo.get_store(offer.store_code)
    if store is None:
        return
    try:
        sp_id = repo.upsert_store_product(store.id, str(offer.sku), offer.name or "",
                                          weight_g=offer.weight_g, unit=offer.unit, url=offer.url)
        repo.confirm_mapping(product_id, sp_id, confirmed=True)
        if offer.price is not None:
            repo.save_price(sp_id, float(offer.price),
                            price_per_kg=offer.per_unit if offer.unit == "kg" else None,
                            in_stock=bool(offer.in_stock))
    except Exception:  # noqa: BLE001 — не сохранили, но остальные позиции спросить можем
        log.warning("сопоставление %s в %s не сохранилось", product_id, offer.store_code,
                    exc_info=True)


def _agrees_with_mapping(product_id: int, offer) -> bool:
    """Верить ли находке поиска, если человек уже сказал, какой это товар в сети.

    Поиск берёт лучшее совпадение по названию и на общих названиях промахивается
    дорого: на «Икра лососевая копчёная 180 г» прилетала банка за 2 090 ₽. Там,
    где подтверждённое сопоставление есть и оно не совпало с находкой, находку
    выбрасываем — цену покажет снимок по подтверждённому товару.

    Исключение — артикул-заглушка из резервного CSV («vkusvill-strachatella-200»):
    сеть такого не знает, ссылка на корзину по нему не строится, и настоящая
    находка главнее.
    """
    store = repo.get_store(offer.store_code)
    if store is None:
        return True
    mapping = repo.confirmed_mapping(product_id, store.id)
    if not mapping:
        return True
    known = str(mapping.get("sku") or "")
    return known == str(offer.sku) or known.startswith(f"{offer.store_code}-")


# ---------- цены позиций ----------
def _price_matrix(items, stores) -> tuple[dict, dict]:
    """(product_id, store_code) -> стоимость позиции целиком; и сумма корзины по сети."""
    cell: dict[tuple[int, str], dict] = {}
    totals: dict[str, float] = {s.code: 0.0 for s in stores}
    from app import service

    for item in items:
        pid = int(item["product_id"])
        qty = float(item.get("qty") or 0)
        unit = item.get("unit") or "pcs"
        for store in stores:
            # Тот же расчёт, что у оптимизатора: цена килограмма не берётся из цены
            # фасовки, разные фасовки приводятся к весу эталона. Два расчёта цены
            # на двух экранах однажды разошлись бы, и строка корзины спорила бы с итогом.
            found = service.line_price(pid, store, qty, unit, item.get("weight_g"))
            if not found:
                continue
            value = found["value"]
            in_stock = found["in_stock"]
            cell[(pid, store.code)] = {"value": value, "in_stock": in_stock,
                                       "stale": found["stale"], "note": found["note"]}
            if in_stock:
                # Складывать цену отсутствующего товара — значит обещать корзину,
                # которую не соберут. Поэтому в сумму сети идёт только то, что есть.
                totals[store.code] += value
    return cell, {code: round(value, 2) for code, value in totals.items()}


def _prices_of(pid: int, stores, cell) -> list[dict]:
    """Цены одной позиции по сетям, готовые к показу.

    Пустая клетка и «нет в наличии» — разные ответы, и путать их нельзя. Пусто
    значит «не спрашивали», перечёркнутая цена — «спросили, и товара там сейчас
    нет». Второе для сборки корзины важнее цены: в такой магазин ехать незачем.
    """
    known = [(store, cell[(pid, store.code)]) for store in stores if (pid, store.code) in cell]
    available = [found["value"] for _, found in known if found["in_stock"]]
    best = min(available) if available else None
    return [{"code": store.code, "name": store.name, "value": found["value"],
             "in_stock": found["in_stock"],
             "best": best is not None and found["in_stock"] and abs(found["value"] - best) < 0.005}
            for store, found in known]


def _best_of(prices: list[dict]) -> dict | None:
    """Где эта позиция дешевле всего и насколько — то, что стоит в строке корзины.

    Разница считается со ВТОРЫМ по дешевизне, а не с самым дорогим: строка
    отвечает на вопрос «а если взять не там», и ответ на него — следующая цена,
    а не худшая из возможных.
    """
    order = sorted((p for p in prices if p["in_stock"]), key=lambda p: p["value"])
    if not order:
        return None
    first = order[0]
    return {"code": first["code"], "store": first["name"], "value": first["value"],
            "diff": round(order[1]["value"] - first["value"], 2) if len(order) > 1 else 0.0,
            "count": len(order)}


def _ready_in(product_id: int, matrix: dict, stores) -> int:
    """В скольких сетях товар уже опознан.

    Это и есть «готовность» товара: опознанный во всех сетях уезжает в магазин
    ссылкой, неопознанный придётся искать руками в каждой.
    """
    return sum(1 for store in stores if matrix.get((product_id, store.id)))


# ---------- сборка страницы ----------
def _pick(baskets: list[dict]) -> dict | None:
    """Какая корзина открыта. Сначала адрес, и только если его нет — память.

    АДРЕС ГЛАВНЕЕ ВСЕГДА: на корзину дают ссылку, её открывают двумя вкладками
    рядом, к ней возвращаются кнопкой «назад». Память отвечает ровно на один
    вопрос — какую корзину показать, когда номера не спросили вовсе. А спрашивают
    так с телефона постоянно: в нижней панели «Корзина» — это голый /basket.

    ЧЕГО ЗДЕСЬ НЕТ. Записи в память при показе страницы. Открытие вкладки не
    должно менять ничего, иначе две вкладки рядом начнут переставлять «последнюю»
    корзину друг у друга, и голый адрес станет непредсказуемым. Память заводит
    только действие человека — _remember_open в _act.
    """
    asked = request.args.get("id", type=int)
    if asked:
        return next((b for b in baskets if int(b["id"]) == asked), None)
    try:
        last = int(repo.get_setting(LAST_OPEN) or 0)
    except (TypeError, ValueError):
        last = 0
    if last:
        # Корзину могли удалить после того, как её запомнили: тогда молча
        # открываем свежую, а не показываем пустой экран «корзины нет».
        remembered = next((b for b in baskets if int(b["id"]) == last), None)
        if remembered is not None:
            return remembered
    return baskets[0] if baskets else None


def _remember_open(basket_id: int) -> None:
    """Запомнить, какую корзину человек правит. Зовётся только из действия."""
    try:
        repo.set_setting(LAST_OPEN, str(int(basket_id)))
    except Exception:  # noqa: BLE001 — не запомнили, но действие сделано
        log.warning("не записалось, какая корзина открыта", exc_info=True)


def _approx(rows: list[dict]) -> dict:
    """Сколько примерно стоит набранное — то, что стоит в прилипшей строке итога.

    СЧИТАЕМ ПО САМОЙ ДЕШЁВОЙ ЦЕНЕ КАЖДОЙ ПОЗИЦИИ, а не по одному магазину: это и
    есть обещание приложения — берём там, где дешевле. Сумма одной сети врала бы в
    другую сторону и расходилась бы с числом на экране «Результат».

    ПОЧЕМУ РЯДОМ ВСЕГДА СТОИТ, СКОЛЬКО ПОЗИЦИЙ БЕЗ ЦЕН. Сложить известное и выдать
    это за итог — худший вид вранья: в магазине его нечем проверить, человек
    обнаружит разницу только на кассе. Поэтому «≈», и рядом прямо сказано, сколько
    позиций в эту сумму не вошло.
    """
    total = 0.0
    priced = 0
    for row in rows:
        best = row.get("best")
        if best:
            total += float(best["value"])
            priced += 1
    return {"total": round(total, 2), "priced": priced,
            "blind": len(rows) - priced, "count": len(rows)}


def _peek_usual() -> tuple[str | None, int]:
    """Дата и размер последней покупки — чтобы предложение было не абстрактным."""
    try:
        from app import baskets as build
        date, rows = build.last_purchase_items()
        return date, len(rows)
    except Exception:  # noqa: BLE001 — истории может не быть вовсе
        return None, 0


def _catalog_hits(query: str, stores_n: int) -> list[dict]:
    """Единый каталог сетей — только по запросу.

    Товар оттуда уже сопоставлен между сетями: положить его значит сразу получить
    эталон и артикулы везде, где он есть. Без слова в поиске список был бы
    каталогом целиком, поэтому пустой запрос сюда не доходит.
    """
    if not query.strip():
        return []
    try:
        from app.catalog import store as catalog_store
        found = catalog_store.search_items(query, limit=8)
    except Exception:  # noqa: BLE001 — каталог не повод ронять корзину
        return []
    out = []
    for item in found:
        small = " · ".join(x for x in (f"{int(item['weight_g'])} г" if item.get("weight_g") else "",
                                       item.get("brand") or "") if x)
        out.append({"id": item["id"], "name": item["name"], "small": small,
                    "chains_n": item.get("chains_n") or 0, "stores_n": stores_n,
                    "chains": (item.get("chain_codes") or "").replace(",", ", ")})
    return out


def _regulars(basket_id: int) -> list[dict]:
    """Частое — то, что человек брал не раз и чего сейчас в корзине нет.

    ЭТО ПЕРВОЕ, ЧТО ВИДНО НА ЭКРАНЕ, и потому порядок здесь не случайный: сверху
    то, что встречалось в наибольшем числе покупок. Без сортировки список шёл в
    порядке, в котором строки легли в базу, — то есть по сути случайно, и самое
    нужное оказывалось десятым.

    Забытый товар стоит дороже любой разницы в ценах: за ним придётся идти
    отдельно, и эта поездка съедает всю экономию расчёта.
    """
    try:
        from app import baskets as build
        found = build.missing_regulars(basket_id) or []
    except Exception:  # noqa: BLE001 — истории может не быть вовсе
        return []
    found.sort(key=lambda r: (-int(r.get("times") or 0), str(r.get("name") or "")))
    return found[:12]


def page():
    if request.method == "POST":
        return _act()

    baskets = repo.list_baskets()
    basket = _pick(baskets)
    query = (request.args.get("q") or "").strip()
    stores = repo.list_stores()

    if basket is None:
        # Пустое состояние — тоже экран. Он обязан объяснить, что сделать, и
        # предложить это одним нажатием, а не отправить человека искать кнопку.
        date, count = _peek_usual()
        return render_template("basket.html", screen=SCREEN_BY_KEY["basket"], basket=None,
                               baskets=baskets, usual={"date": date, "count": count},
                               receipts_path=SCREEN_BY_KEY["receipts"].path,
                               plural_items=plural_items, rub=rub, num=num, pct=pct,
                               unit_label=unit_label, missing=request.args.get("id", type=int))

    basket_id = int(basket["id"])
    items = repo.basket_items(basket_id)
    cell, totals = _price_matrix(items, stores)

    rows = []
    for item in items:
        pid = int(item["product_id"])
        prices = _prices_of(pid, stores, cell)
        rows.append({"pid": pid, "name": item.get("name"), "unit": item.get("unit") or "pcs",
                     "qty": float(item.get("qty") or 0), "prices": prices,
                     "best": _best_of(prices)})

    in_basket = {int(i["product_id"]) for i in items}
    matrix = repo.mapping_matrix()
    suggestions = [p for p in (repo.list_products() or [])
                   if p.id not in in_basket
                   and (not query or query.lower() in (p.name or "").lower())]
    # Сверху — товары, известные наибольшему числу сетей: их корзина уедет в
    # магазин ссылкой, а не превратится в список для ручного поиска.
    suggestions.sort(key=lambda p: (-_ready_in(p.id, matrix, stores), p.name or ""))

    date, count = _peek_usual()
    return render_template(
        "basket.html",
        screen=SCREEN_BY_KEY["basket"],
        basket=basket, baskets=baskets, items=rows, query=query,
        totals=[{"code": s.code, "name": s.name, "value": totals[s.code]}
                for s in stores if totals.get(s.code)],
        stores_n=len(stores),
        suggestions=[{"id": p.id, "name": p.name, "brand": p.brand,
                      "unit": unit_label(p.unit), "ready": _ready_in(p.id, matrix, stores)}
                     for p in suggestions[:8]],
        suggestions_n=len(suggestions),
        catalog=_catalog_hits(query, len(stores)),
        regulars=_regulars(basket_id),
        approx=_approx(rows),
        usual={"date": date, "count": count},
        job=job_of(basket_id),
        saved=repo.list_variants(basket_id),
        result_path=SCREEN_BY_KEY["result"].path,
        receipts_path=SCREEN_BY_KEY["receipts"].path,
        plural_items=plural_items, rub=rub, num=num, pct=pct, unit_label=unit_label,
    )


# ---------- подсказка по мере набора ----------
def suggest():
    """Что показать под полем поиска, пока человек ещё набирает. Отвечает данными.

    ПОЧЕМУ ЭТО ОТДЕЛЬНЫЙ АДРЕС, А НЕ ЧАСТЬ СТРАНИЦЫ. Подсказка нужна на каждую
    букву, а страница корзины — это список позиций, цены по сетям и частое: гонять
    её целиком по мобильной сети на каждое нажатие клавиши нельзя, человек стоит у
    полки и ждёт. Здесь уезжает десяток строк.

    ПОЧЕМУ СВОИ ТОВАРЫ И КАТАЛОГ СЕТЕЙ — ОДНИМ СПИСКОМ. Человеку всё равно, откуда
    товар взялся: он ищет «молоко». Разница не пропадает — у строки из каталога
    другое действие (catalog: вместо add:) и сказано, в скольких сетях он известен.

    ПОИСК БЕЗ СКРИПТА НЕ ЛОМАЕТСЯ. Это надстройка: обычная форма с кнопкой «Найти»
    на экране осталась и работает сама по себе. Выключенный JavaScript, старый
    браузер или оборванная сеть отнимают подсказку, а не поиск.
    """
    query = (request.args.get("q") or "").strip()
    basket_id = request.args.get("id", type=int)
    if len(query) < SUGGEST_FROM:
        return {"q": query, "items": []}

    stores = repo.list_stores()
    matrix = repo.mapping_matrix()
    # Того, что уже лежит в корзине, в подсказке быть не должно: нажатие на него
    # молча переставило бы количество в единицу, и человек потерял бы набранное.
    in_basket = {int(i["product_id"]) for i in repo.basket_items(basket_id)} if basket_id else set()

    low = query.lower()
    mine = [p for p in (repo.list_products() or [])
            if p.id not in in_basket and low in (p.name or "").lower()]
    mine.sort(key=lambda p: (-_ready_in(p.id, matrix, stores), p.name or ""))

    items = [{"do": f"add:{p.id}", "name": p.name, "mine": True,
              "small": " · ".join(x for x in (unit_label(p.unit), p.brand or "") if x),
              "where": f"в {_ready_in(p.id, matrix, stores)} из {len(stores)}"}
             for p in mine[:6]]
    for hit in _catalog_hits(query, len(stores))[:6]:
        items.append({"do": f"catalog:{hit['id']}", "name": hit["name"], "mine": False,
                      "small": hit["small"],
                      "where": f"в {hit['chains_n']} из {hit['stores_n']}"})
    # Запрос возвращаем обратно, чтобы браузер выбросил ответ, опоздавший к уже
    # набранному слову: ответы приходят не в том порядке, в котором их спросили.
    return {"q": query, "items": items[:10]}


def install(flask_app) -> None:
    """Повесить подсказку поиска. Сам экран вешает карта страниц (app/web/views)."""
    flask_app.add_url_rule(SUGGEST_PATH, endpoint="screen.basket_suggest",
                           view_func=auth.needs_phone(suggest))


# ---------- действия ----------
def _back(basket_id: int | None, query: str = "") -> str:
    """Куда вернуть после действия. Всегда на адрес корзины — и с тем же поиском.

    Перевод обязателен: без него нажатие остаётся в истории браузера повторяемым,
    и обновление страницы кладёт товар в корзину второй раз.
    """
    parts = []
    if basket_id:
        parts.append(f"id={basket_id}")
    if query:
        parts.append(f"q={quote(query)}")
    return PATH + ("?" + "&".join(parts) if parts else "")


def _apply_quantities(basket_id: int, items) -> None:
    """Сохранить количества, набранные в форме.

    Делается при ЛЮБОМ нажатии, а не только при «Сохранить»: человек правит
    корзину по строке и вправе ожидать, что набранное не пропадёт из-за того,
    что он нажал «убрать» или «＋» раньше сохранения.
    """
    for item in items:
        pid = int(item["product_id"])
        typed = request.form.get(f"qty:{pid}")
        if typed is None:
            continue
        try:
            qty = float(str(typed).replace(",", "."))
        except ValueError:
            continue
        if abs(qty - float(item.get("qty") or 0)) > 1e-9:
            repo.set_basket_item(basket_id, pid, max(0.0, qty))


def _fill_from_history(basket_id: int, kind: str) -> None:
    """Наполнить ЭТУ корзину по истории покупок.

    Сборка (app/baskets.py) заводит свою корзину, а наполняем мы текущую — за
    собой убираем, иначе список корзин копит мусорную запись на каждое нажатие.
    """
    from app import baskets as build

    made = build.build_from_history(kind)
    source = int(made.get("basket_id") or 0)
    if not source:
        return
    for row in repo.basket_items(source):
        repo.set_basket_item(basket_id, int(row["product_id"]), float(row["qty"]))
    if source != basket_id:
        repo.delete_basket(source)


def _act():
    """Разбор нажатия. Все кнопки живут в одной форме и различаются своим do."""
    do = request.form.get("do") or ""
    query = (request.form.get("q") or "").strip()
    basket_id = request.form.get("basket", type=int)
    what, _, target = do.partition(":")

    if what == "new":
        name = (request.form.get("name") or "").strip()
        kind = target
        basket_id = repo.create_basket(name or f"Корзина от {time.strftime('%d.%m.%Y')}",
                                       source=kind or "manual")
        if kind in ("history", "average"):
            # Новая корзина создаётся уже собранной: это единственное место, где
            # наполнение по истории — действие человека, а не побочный эффект
            # открытия страницы.
            _fill_from_history(basket_id, kind)
        _remember_open(basket_id)
        return redirect(_back(basket_id))

    if not basket_id:
        return redirect(_back(None, query))

    items = repo.basket_items(basket_id)

    # СНАЧАЛА СОХРАНИТЬ НАБРАННОЕ, ПОТОМ РАЗБИРАТЬ НАЖАТИЕ. Все кнопки экрана —
    # и «＋» в подсказке поиска, и «Посчитать» в прилипшей строке итога — шлют одну
    # и ту же форму, в которой едут количества. Раньше их сохраняли только «Сохранить»,
    # «убрать» и «±», а «Рассчитать» и «Узнать цены» — нет: человек правил количество,
    # нажимал «Посчитать» и получал расчёт по СТАРОМУ числу, ничего об этом не узнав.
    # Порядок важен: плюс и минус ниже всё равно считают от набранного в поле, а не
    # от того, что было в базе, поэтому записать заранее безопасно.
    _apply_quantities(basket_id, items)
    _remember_open(basket_id)

    if what in ("save", "drop", "plus", "minus", "clear"):
        if what == "drop" and target.isdigit():
            repo.set_basket_item(basket_id, int(target), 0.0)
        elif what in ("plus", "minus") and target.isdigit():
            pid = int(target)
            row = next((i for i in items if int(i["product_id"]) == pid), None)
            if row is not None:
                step = 0.1 if (row.get("unit") or "pcs") == "kg" else 1.0
                typed = request.form.get(f"qty:{pid}")
                try:
                    now = float(str(typed).replace(",", ".")) if typed else float(row.get("qty") or 0)
                except ValueError:
                    now = float(row.get("qty") or 0)
                repo.set_basket_item(basket_id, pid,
                                     max(0.0, round(now + (step if what == "plus" else -step), 3)))
        elif what == "clear":
            repo.clear_basket(basket_id)
        return redirect(_back(basket_id, query))

    if what == "add" and target.isdigit():
        repo.set_basket_item(basket_id, int(target), 1.0)
        return redirect(_back(basket_id, query))

    if what == "catalog" and target.isdigit():
        try:
            from app.catalog.refresh import adopt
            repo.set_basket_item(basket_id, adopt(int(target)), 1.0)
        except Exception:  # noqa: BLE001 — каталог не повод ронять корзину
            log.warning("товар %s из каталога взять не вышло", target, exc_info=True)
        return redirect(_back(basket_id, query))

    if what == "fill" and target in ("history", "average"):
        _fill_from_history(basket_id, target)
        return redirect(_back(basket_id, query))

    if what == "ask":
        _start_job(basket_id, "ask", items)
        return redirect(_back(basket_id, query))

    if what == "fresh" or (what == "calc" and request.form.get("fresh")):
        # Тяжёлое здесь — обновление цен, а не сам расчёт. Уводим его в фон и
        # оставляем человека на корзине, где видно, что происходит.
        #
        # ПОЧЕМУ ОТДЕЛЬНОЕ НАЖАТИЕ, А НЕ ОТМЕТКА. Отметка «сначала обновить цены»
        # жила в той же форме, что и «Посчитать», и после переезда кнопки итога в
        # прилипшую строку забытая галочка превращала бы мгновенный расчёт в
        # ожидание на минуты — молча и без объяснения. Старый вид (calc + fresh)
        # остаётся понятным: ссылку на действие могли сохранить.
        _start_job(basket_id, "fresh", items)
        return redirect(_back(basket_id, query))

    if what == "calc":
        return redirect(f"{SCREEN_BY_KEY['result'].path}?basket={basket_id}")

    return redirect(_back(basket_id, query))


# Flask берёт список методов у самой view-функции, если ему не сказали иначе, а
# functools.wraps в auth.needs_phone переносит атрибуты на обёртку. Благодаря
# этому действия экрана живут на его же адресе и ничего не добавляют в карту
# страниц: POST /basket — это тот же /basket, просто с нажатой кнопкой.
page.methods = ("GET", "POST")

__all__ = ["page", "install", "suggest", "SUGGEST_PATH",
           "rub", "num", "pct", "unit_label", "plural_items"]
