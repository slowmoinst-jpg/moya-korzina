"""Проверка здоровья единой базы: наполняется ли она и не сломалось ли что-то.

ЗАЧЕМ. Цель проекта — единая база всех сетей, которая формируется на постоянной основе.
Смотреть на неё руками каждый день человек не станет, а обход умеет «пройти успешно» и
ничего при этом не собрать: у Пятёрочки и Самоката статус был `blocked` при нуле строк, и
понять это можно было только по нулю.

Что проверяется:

  - у сети НЕТ строк, хотя обход числится успешным — это не «мало товаров», это поломка;
  - строк у сети стало меньше на 10 % против прошлого снимка — либо сеть правда убрала
    товары, либо обход в этот раз собрал половину;
  - сеть не обходилась дольше двух суток — «постоянная основа» перестала быть постоянной;
  - доля строк с ценой упала больше чем на 10 пунктов;
  - сравнимых по цене товаров стало меньше на 10 % — ради них база и строится;
  - сравнимых много, а свежих (моложе prices.fresh_hours) — ни одного: приложению
    сравнивать нечего, хотя в базе цены есть;
  - цену видели, а цену стёрли (price_seen без price);
  - цена стареет и не обновляется: виток свежести цен встал;
  - остаток перечня сети не уменьшается: разбор всех товаров стоит;
  - у сети месяц нет ни одной новой строки — перечень застыл (источник), а не поломка:
    у Vprok карта сайта не менялась с 2024 года (замер 20.10.2026), и без этой оговорки
    ноль новых строк выглядел бы сломанным обходом;
  - ночь стала длиннее половины суток — обход перестаёт успевать до дня.

    python -m app.catalog.checkup            показать и сказать, всё ли в порядке
    python -m app.catalog.checkup --save     ещё и запомнить снимок для следующего раза
    python -m app.catalog.checkup --json     машинный вывод

Код возврата: 0 — замечаний нет, 1 — есть (они напечатаны). Так это можно ставить в
расписание и замечать поломку в ту же ночь, а не через неделю.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta

from app.catalog import store

# Снимок прошлой проверки лежит в самой базе: отдельного файла не заводим, чтобы он не
# разъехался с базой при переносах тома.
STATE_KEY = "checkup"

# Пороги. Ниже — шум, выше — повод посмотреть руками.
DROP_PCT = 10.0          # насколько могут упасть строки/сравнимые, прежде чем это тревога
COVERAGE_DROP = 10.0     # насколько может упасть доля строк с ценой, в пунктах
STALE_HOURS = 48.0       # сколько часов сеть может не обходиться
# Сколько телефон может молчать, прежде чем это станет замечанием. Неделя: человек может
# и не ходить в магазин столько, но дальше база по телефонным сетям стоит на месте.
DEVICE_SILENT_HOURS = 24.0 * 7
# Свежесть цены. Сборщики читают карточку только при первом знакомстве с артикулом, и
# цену перечитывает отдельный виток (store.stale_price_skus) — не больше своего потолка
# карточек за ночь, поэтому доля старых цен падает МЕДЛЕННО, и это нормально. Замечание
# появляется только тогда, когда она не падает вовсе: виток встал, и база показывает
# цену недельной давности как настоящую.
PRICE_STALE_DAYS = 7.0
PRICE_STALE_SHARE = 50.0  # доля старых цен, выше которой это уже не «идёт виток», а беда
# Сколько сравнимых товаров должно быть в базе, чтобы «свежих ни одного» стало замечанием.
# На пустой базе ноль свежих — не поломка, а начало работы.
FRESH_COMPARABLE_FLOOR = 100
# Сколько часов после обхода замечание «обход не записал действующих цен» ещё уместно.
# Ночной обход кончается около 11:30 UTC и проверка идёт сразу за ним; вечером те же числа
# значат норму, а не поломку.
SWEEP_GRACE_HOURS = 3.0
# Сколько часов может длиться ночь целиком, прежде чем это станет замечанием. Замер
# 20.10.2026: 8 ч (03:30 → 11:32 UTC) при сумме времён сетей 6,3 ч. Половина суток — уже
# не «постоянная основа», а догоняющая: следующая ночь начнётся, не дождавшись этой.
NIGHT_LONG_HOURS = 12.0
# Разрыв между прогонами, после которого это уже другая пачка, а не та же ночь. Обход идёт
# по очереди, между сетями бывает сопоставление на 10–15 минут; ручной прогон днём
# отстоит от ночи на часы (замер 20.10.2026: 03:30–11:32, потом проверка Самоката в 17:59).
NIGHT_GAP_MIN = 60.0
# Сколько часов тишины внутри «идущего» обхода считать обрывом. Потолок одной сети — 180 мин,
# поэтому порог выше: иначе нормальный METRO (два с лишним часа) выглядел бы поломкой.
SWEEP_STALL_HOURS = 3.5
# Доля строк без единого товара, выше которой это уже не остаток, а застой сопоставления.
# Замер 20.10.2026: 26 из 308 670 — 0,01 %, то есть норма на два порядка ниже порога.
UNMATCHED_SHARE = 1.0
# И сколько строк должно быть не сопоставлено, чтобы об этом вообще говорить. На крошечной
# базе (свежая установка, тесты) одна строка — это 100 %, но поломкой не является.
UNMATCHED_FLOOR = 200


def snapshot() -> dict:
    """Что есть в базе сейчас: по сетям и общими числами."""
    store.init()
    conn = store.connect()
    chains: dict[str, dict] = {}
    # Цена без даты (price_seen) считается по дате первого знакомства: у строк,
    # записанных до появления колонки, другой даты нет.
    stale_before = (datetime.now() - timedelta(days=PRICE_STALE_DAYS)).isoformat(
        timespec="microseconds")
    week_before = (datetime.now() - timedelta(days=7)).isoformat(timespec="microseconds")
    for row in conn.execute(
            "SELECT chain, COUNT(*) AS products,"
            " SUM(price IS NOT NULL) AS priced, SUM(in_stock IS NOT NULL) AS stocked,"
            " SUM(CASE WHEN price IS NOT NULL"
            "     AND COALESCE(price_seen, first_seen) <= ? THEN 1 ELSE 0 END) AS stale_week,"
            # РОСТ БАЗЫ. Цель говорит «наполнение для всех магазинов» — и проверить это
            # можно только по каждой сети отдельно. Замер 20.10.2026: у Vprok последняя
            # новая строка 20.09, и это не наша недосборка — в его карте сайта все даты
            # lastmod из 2024 года, источник застыл. Без этих чисел такой факт выглядел бы
            # поломкой обхода.
            " SUM(CASE WHEN first_seen >= ? THEN 1 ELSE 0 END) AS new_week,"
            " MAX(first_seen) AS last_new"
            " FROM chain_products WHERE active=1 GROUP BY chain",
            (stale_before, week_before,)):
        chains[row["chain"]] = {"products": int(row["products"]),
                                "priced": int(row["priced"] or 0),
                                "stocked": int(row["stocked"] or 0),
                                "stale_week": int(row["stale_week"] or 0),
                                "new_week": int(row["new_week"] or 0),
                                "last_new": row["last_new"]}
    # Сеть, которую обходили, но у которой нет строк, должна быть видна: пустой словарь
    # chains её не покажет, а это и есть самая опасная поломка.
    for row in conn.execute("SELECT chain, status, started_at, finished_at FROM crawl_runs"
                            " ORDER BY id DESC LIMIT 40"):
        chains.setdefault(row["chain"], {"products": 0, "priced": 0, "stocked": 0,
                                         "stale_week": 0, "new_week": 0, "last_new": None})
        info = chains[row["chain"]]
        if "status" not in info:
            info["status"] = row["status"]
            info["finished_at"] = row["finished_at"] or row["started_at"]
    comparable = conn.execute(
        "SELECT COUNT(*) FROM (SELECT item_id FROM chain_products WHERE active=1"
        " AND item_id IS NOT NULL AND price IS NOT NULL"
        " GROUP BY item_id HAVING COUNT(DISTINCT chain) >= 2)").fetchone()[0]
    # СРАВНИМЫХ ДВА РАЗНЫХ ЧИСЛА, и второе честнее. `comparable` считает цену в базе,
    # какой бы старой она ни была. Приложение же верит только цене моложе
    # prices.fresh_hours (app/freshness.py, 6 ч) — всё, что старше, оно НЕ показывает вовсе.
    # Замер 20.10.2026 в 18:15 MSK: в базе сравнимых 7 111, а приложение могло взять 190 —
    # обход прошёл утром, и его цены к вечеру просрочены. Одно число без второго выглядело
    # бы успехом там, где выбора у человека нет.
    hours = _fresh_hours()
    fresh_since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="microseconds")
    comparable_fresh = conn.execute(
        "SELECT COUNT(*) FROM (SELECT item_id FROM chain_products WHERE active=1"
        " AND item_id IS NOT NULL AND price IS NOT NULL"
        " AND COALESCE(price_seen, first_seen) > ?"
        " GROUP BY item_id HAVING COUNT(DISTINCT chain) >= 2)", (fresh_since,)).fetchone()[0]
    # ЦЕНУ ВИДЕЛИ, А ЦЕНЫ НЕТ. price_seen ставится тогда, когда источник цену назвал;
    # если при этом price пуст, кто-то стёр значение, не тронув отметку. Замер 20.10.2026:
    # у Самоката price_seen стоит у 1 494 строк, а цены остались у 884 — 610 стёрли.
    gaps = {row["chain"]: {"gap": int(row["gap"]), "products": int(row["products"])}
            for row in conn.execute(
                "SELECT chain, COUNT(*) AS gap,"
                " (SELECT COUNT(*) FROM chain_products p2 WHERE p2.chain=p.chain AND p2.active=1)"
                " AS products"
                " FROM chain_products p WHERE active=1 AND price IS NULL AND price_seen IS NOT NULL"
                " GROUP BY chain")}
    counts = store.item_count()
    # НЕ СОПОСТАВЛЕНО. Строка без единого товара не участвует в сравнении сетей — то есть
    # собрана, но бесполезна. Замер 20.10.2026: 26 строк из 308 670 (0,01 %), и все они
    # пришли карточками с витрины, где имя склеилось с наклейкой и ценой. Пока это число
    # видно, застой сопоставления (match_all перестал ходить) заметен в тот же вечер.
    unmatched = store.unmatched_count()
    products_total = sum(int(info.get("products") or 0) for info in chains.values())
    # СКОЛЬКО СРАВНЕНИЙ ЖДЁТ ЦЕНЫ СЕТИ БЕЗ ЦЕН. У Перекрёстка, Монетки и Vprok цен нет
    # вовсе; часть мультисетевых товаров ждёт только их. Замер 20.10.2026: Перекрёсток
    # +4 399 сравнимых (7 111 → 11 510, +62 %), Монетка +716, Vprok +247. Для владельца это
    # цена одного действия: пройти проверку Перекрёстка в окне магазина — и число сдвинется.
    waiting = _waiting_comparisons(conn)
    # СКОЛЬКО ИДЁТ НОЧЬ ЦЕЛИКОМ. Обходы идут по очереди, и рост одной сети сдвигает
    # остальные: замер 20.10.2026 — 03:30 → 11:32 UTC, восемь часов при сумме времён сетей
    # 6,3 ч (разница — сопоставление между сетями). С 21.10 к этому прибавляется виток
    # свежести цен (Лента 2 000 карточек ≈ 33 мин, ВкусВилл 1 500 ≈ 25 мин). Без этого
    # числа «ночь выросла» замечают только тогда, когда она наехала на день.
    night = _night(conn)
    return {
        "at": store.now(),
        "chains": chains,
        "items": int(counts.get("items") or 0),
        "multi": int(counts.get("multi") or 0),
        "unmatched": int(unmatched or 0),
        "products_total": int(products_total or 0),
        "waiting": waiting,
        "comparable": int(comparable or 0),
        "comparable_fresh": int(comparable_fresh or 0),
        "fresh_hours": hours,
        "misses": store.miss_counts(),
        "price_gaps": gaps,
        # Что и когда приносил телефон: Пятёрочка и Самокат живут только им, и без этой
        # отметки «а телефон-то ещё работает?» не ответить (store.mark_device).
        "device": store.device_marks(),
        # Сколько НОВЫХ строк принёс телефон за сутки: его вклад в единую базу иначе
        # нигде не виден, а у Пятёрочки и Самоката он единственный (store.phone_new_rows).
        "phone_new": store.phone_new_rows(),
        # Сколько разобрано из перечня сети (store.mark_map): «все товары» — пункт цели,
        # и без этого числа 37 тыс. строк Ленты ничего не говорят о том, половина это или всё.
        "maps": store.map_marks(),
        # Когда обход сетей закончился: по нему видно, свежие ли ещё цены и уместно ли
        # замечание «обход не записал ни одной действующей цены».
        "sweep_at": store.sweep_at(),
        # Сколько шла последняя ночь и кто в ней был самым долгим.
        "night": night,
        # Отметка обхода и последняя сеть, которую он трогал: по ним видно оборванную ночь,
        # даже если процесс убили и `finally` не сработал.
        "sweep_state": store.sweep_state(),
        "sweep_step": store.sweep_step(),
    }


def _waiting_comparisons(conn) -> dict[str, int]:
    """Сколько НОВЫХ сравнимых товаров появится, если у сети без цен появятся цены.

    Считаем строго: товар, у которого у этой сети цены нет, а у ДРУГИХ сетей цена есть
    ровно у одной (двух уже достаточно для сравнения). Товары, которые и так сравнимы,
    в счёт не идут — иначе число обещало бы больше, чем даст.

    Сети без цен: Перекрёсток и Монетка (витрина серверу не отдаёт, дорога — телефон или
    пройденная проверка) и Vprok (перечень застыл с 2024 года).
    """
    out: dict[str, int] = {}
    for chain in ("perekrestok", "monetka", "vprok"):
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM ("
                " SELECT cp.item_id FROM chain_products cp"
                "  WHERE cp.active=1 AND cp.price IS NOT NULL AND cp.chain <> ?"
                "   AND cp.item_id IN (SELECT item_id FROM chain_products"
                "                        WHERE active=1 AND chain=? AND price IS NULL)"
                "  GROUP BY cp.item_id HAVING COUNT(DISTINCT cp.chain) = 1)", (chain, chain)
            ).fetchone()
        except Exception:  # noqa: BLE001 — проверка базы не должна падать из-за счёта
            continue
        if row and row[0]:
            out[chain] = int(row[0])
    return out


def _night(conn) -> dict:
    """Последняя ночь: самая длинная непрерывная цепочка обходов за день.

    Ночь — это пачка прогонов, идущих друг за другом: плановый обход начинается в 03:30 UTC
    и кончается до полуночи. Считать «от первого до последнего прогона за день» нельзя:
    ручной прогон одной сети днём (проверка сборщика, замер) растянул бы ночь на весь день —
    замер 20.10.2026 показал 14,5 ч вместо 8 ч и дал бы ложную тревогу. Поэтому делим день
    на цепочки с разрывом больше NIGHT_GAP_MIN и берём самую длинную.
    """
    row = conn.execute("SELECT MAX(substr(started_at, 1, 10)) AS day FROM crawl_runs"
                       " WHERE finished_at IS NOT NULL").fetchone()
    day = (row["day"] if row else None) or None
    if not day:
        return {}
    runs = []
    for run in conn.execute(
            "SELECT chain, started_at, finished_at FROM crawl_runs"
            " WHERE substr(started_at, 1, 10)=? AND finished_at IS NOT NULL"
            " ORDER BY started_at", (day,)):
        try:
            began = datetime.fromisoformat(str(run["started_at"]))
            ended = datetime.fromisoformat(str(run["finished_at"]))
        except ValueError:
            continue
        runs.append((str(run["chain"]), began, ended, (ended - began).total_seconds() / 60.0))
    if not runs:
        return {}
    runs.sort(key=lambda r: r[1])
    chains_of: list[list[tuple]] = [[runs[0]]]
    for run in runs[1:]:
        if (run[1] - chains_of[-1][-1][2]).total_seconds() / 60.0 > NIGHT_GAP_MIN:
            chains_of.append([run])
        else:
            chains_of[-1].append(run)
    night = max(chains_of, key=len)
    began = min(r[1] for r in night)
    ended = max(r[2] for r in night)
    longest = max(night, key=lambda r: r[3])
    return {"day": day, "from": began.isoformat(timespec="minutes"),
            "to": ended.isoformat(timespec="minutes"),
            "hours": round((ended - began).total_seconds() / 3600.0, 2),
            "chains": len(night),
            "longest": {"chain": longest[0], "minutes": round(longest[3])}}


def _share(info: dict) -> float:
    products = info.get("products") or 0
    return (info.get("priced") or 0) * 100.0 / products if products else 0.0


def _fresh_hours() -> float:
    """Сколько часов приложение верит цене (app/freshness.py). 6 ч, если не задано."""
    try:
        from app import freshness

        return float(freshness.max_age_hours())
    except Exception:  # noqa: BLE001 — проверка базы не должна падать из-за настройки
        return 6.0


def complaints(now_snap: dict, before: dict | None, hours: float = STALE_HOURS) -> list[str]:
    """Что не так с базой. Пустой список — всё в порядке."""
    out: list[str] = []
    chains = now_snap.get("chains") or {}
    old_chains = (before or {}).get("chains") or {}

    for chain, info in sorted(chains.items()):
        products = int(info.get("products") or 0)
        status = str(info.get("status") or "")
        if products == 0 and status in ("ok", "partial"):
            out.append(f"{chain}: обход {status}, а строк ноль — база по этой сети не наполняется")
        old = old_chains.get(chain)
        if old and int(old.get("products") or 0) > 0 and products > 0:
            drop = (int(old["products"]) - products) * 100.0 / int(old["products"])
            if drop > DROP_PCT:
                out.append(f"{chain}: строк стало меньше на {drop:.0f} % "
                           f"({old['products']} → {products})")
            was, now_share = _share(old), _share(info)
            if was - now_share > COVERAGE_DROP:
                out.append(f"{chain}: доля строк с ценой упала на {was - now_share:.0f} пункта "
                           f"({was:.0f} % → {now_share:.0f} %)")
        finished = info.get("finished_at")
        if finished:
            try:
                age = (datetime.now() - datetime.fromisoformat(str(finished))).total_seconds() / 3600.0
                if age > hours:
                    out.append(f"{chain}: обход не был {age:.0f} ч (последний {finished})")
            except ValueError:
                pass

    old_comparable = int((before or {}).get("comparable") or 0)
    now_comparable = int(now_snap.get("comparable") or 0)
    if old_comparable > 0 and now_comparable < old_comparable:
        drop = (old_comparable - now_comparable) * 100.0 / old_comparable
        if drop > DROP_PCT:
            out.append(f"сравнимых по цене товаров стало меньше на {drop:.0f} % "
                       f"({old_comparable} → {now_comparable})")

    # СРАВНИМО В БАЗЕ, НО НЕ ДЛЯ ПРИЛОЖЕНИЯ. Замер 20.10.2026 18:15 MSK: в базе 7 111
    # сравнимых товаров, а приложение могло взять 190 — цены утреннего обхода к вечеру
    # просрочены (6 ч). Для середины дня это норма, поэтому замечание ставится ТОЛЬКО сразу
    # после обхода (в пределах SWEEP_GRACE_HOURS): тогда «свежих ни одного» значит, что
    # обход только что прошёл и не записал действующих цен. Вечерний ручной запуск проверки
    # иначе ругался бы каждый раз на нормальный ход вещей.
    comparable_fresh = now_snap.get("comparable_fresh")
    sweep_age = _age_hours(now_snap.get("sweep_at"))
    if isinstance(comparable_fresh, int) and now_comparable > FRESH_COMPARABLE_FLOOR \
            and comparable_fresh == 0 \
            and sweep_age is not None and sweep_age <= SWEEP_GRACE_HOURS:
        out.append(f"в базе {now_comparable} сравнимых по цене товаров, а свежих "
                   f"(моложе {now_snap.get('fresh_hours')} ч) — ни одного: обход не записал "
                   "действующих цен, и приложению сравнивать нечего")

    # ЦЕНУ ВИДЕЛИ, А ЦЕНЫ НЕТ — след чужой записи, а не свойство данных. Замер 20.10.2026:
    # у Самоката price_seen стоял у 1 494 строк, а цена осталась у 884. Такое надо не
    # «просто заметить через долю цен», а назвать прямо: кто-то стёр значения, не тронув
    # отметку, и товар выпал из сравнения.
    for chain, info in sorted((now_snap.get("price_gaps") or {}).items()):
        gap = int(info.get("gap") or 0)
        if gap > 0:
            out.append(f"{chain}: у {gap} строк цену видели (price_seen), а цены нет — "
                       "значения стёрты, товар выпал из сравнения")

    # ЦЕНА СТАРЕЕТ И НЕ ОБНОВЛЯЕТСЯ. Замер 20.10.2026: у ВкусВилла 3 132 цены из 3 305
    # были старше недели, и проверка восьми живых карточек показала 3 расхождения больше
    # процента (худшее — 129 ₽ в базе против 167 ₽ на витрине). Лечится витком свежести
    # (store.stale_price_skus): за ночь сеть перечитывает свой потолок карточек, поэтому
    # доля старых цен падает медленно — это норма, а не замечание. Замечание — когда она
    # НЕ падает: виток встал (потолок обнулили, сеть перестала обходиться, карточки
    # замолчали), и база продолжает выдавать цену недельной давности за настоящую.
    for chain, info in sorted(chains.items()):
        priced = int(info.get("priced") or 0)
        stale = int(info.get("stale_week") or 0)
        if not priced or stale * 100.0 / priced <= PRICE_STALE_SHARE:
            continue
        old = old_chains.get(chain) or {}
        was_stale = old.get("stale_week")
        if isinstance(was_stale, int) and stale >= was_stale:
            out.append(f"{chain}: {stale} цен старше недели (из {priced}) и их число "
                       f"не уменьшилось ({was_stale} в прошлый раз) — виток свежести цен "
                       "не идёт, сравнение опирается на устаревшие цены")

    # ТЕЛЕФОН МОЛЧИТ ДОЛЬШЕ НЕДЕЛИ. Пятёрочка и Самокат живут только им: если отметка
    # есть, но старая, — база по этим сетям стоит на месте, и об этом надо сказать.
    for chain, mark in sorted((now_snap.get("device") or {}).items()):
        age = _age_hours(mark.get("at"))
        if age is not None and age > DEVICE_SILENT_HOURS:
            out.append(f"{chain}: телефон не присылал {age / 24:.0f} дн — "
                       "база по этой сети не обновляется")

    # ПЕРЕЧЕНЬ НЕ РАЗБИРАЕТСЯ. «Все товары разобраны» — пункт цели, и проверять его надо
    # числом: сколько артикулов в перечне сети и сколько ещё ждёт разбора (store.mark_map).
    # Замечание только когда разбор стоит: остаток есть, а новых строк за обход не пришло.
    for chain, mark in sorted((now_snap.get("maps") or {}).items()):
        left = int(mark.get("left") or 0)
        added = int(mark.get("added") or 0)
        total = int(mark.get("total") or 0)
        if left > 0 and added == 0:
            out.append(f"{chain}: разобрано {total - left} из {total} артикулов перечня, "
                       f"осталось {left}, а за обход не пришло ни одного нового — разбор стоит")

    # СОПОСТАВЛЕНИЕ НЕ ДОГОНЯЕТ ОБХОД. Строка без единого товара в сравнении сетей не
    # участвует: обход её собрал, а пользы нет. Замер 20.10.2026 — 26 строк из 308 670
    # (0,01 %), это норма. Тревога — когда доля перевалит за процент: значит match_all
    # перестал ходить или имена приезжают такими, что сопоставить нельзя.
    unmatched = int(now_snap.get("unmatched") or 0)
    total_rows = int(now_snap.get("products_total") or 0)
    if total_rows and unmatched > UNMATCHED_FLOOR \
            and unmatched * 100.0 / total_rows > UNMATCHED_SHARE:
        out.append(f"не сопоставлено {unmatched} строк из {total_rows} "
                   f"({unmatched * 100.0 / total_rows:.1f} %) — сопоставление не догоняет обход")

    # НОЧЬ СТАЛА СЛИШКОМ ДОЛГОЙ. Обходы идут по очереди, и если ночь перевалит за сутки
    # наполовину, следующая начнётся, не дождавшись конца этой: «постоянная основа»
    # превратится в догоняющую. Замер 20.10.2026 — 8 ч, это норма.
    night = now_snap.get("night") or {}
    hours = night.get("hours")
    if isinstance(hours, (int, float)) and hours > NIGHT_LONG_HOURS:
        longest = night.get("longest") or {}
        out.append(f"ночь {night.get('day')} заняла {hours:.1f} ч при потолке "
                   f"{NIGHT_LONG_HOURS:.0f}: дольше всех {longest.get('chain')} "
                   f"{longest.get('minutes')} мин — обход перестаёт успевать до дня")

    # ОБХОД ОБОРВАН, А ОТМЕТКА ОСТАЛАСЬ. Признаков два, и оба нужны:
    #   - отметка «идёт обход» старше 12 ч — процесс умер и `finally` не сработал;
    #   - отметка ещё свежая, но последняя сеть трогалась больше SWEEP_STALL_HOURS назад —
    #     «идёт», а движения нет. Без второго признака обрыв в 04:00 и проверка в 08:00
    #     молчали бы: 12-часовой порог ещё не наступил.
    # Порог 3,5 ч выше потолка одной сети (180 мин) — иначе тревога срабатывала бы на
    # нормальном METRO, который идёт два с лишним часа.
    sweeps = now_snap.get("sweep_state")
    step = now_snap.get("sweep_step") or {}
    step_age = _age_hours(step.get("at"))
    stalled = sweeps == "stale" or (sweeps == "running" and step_age is not None
                                    and step_age > SWEEP_STALL_HOURS)
    if stalled:
        chain = str(step.get("chain") or "")
        why = (f"отметке больше 12 ч" if sweeps == "stale"
               else f"последняя сеть трогалась {step_age:.1f} ч назад")
        out.append(f"обход помечен идущим, но {why} — прошлая ночь оборвана"
                   + (f" на сети {chain}" if chain else ""))
    return out


def save(snap: dict) -> None:
    store.init()
    with store.connect() as conn:
        conn.execute("INSERT INTO catalog_state (key, value, at) VALUES (?,?,?)"
                     " ON CONFLICT(key) DO UPDATE SET value=excluded.value, at=excluded.at",
                     (STATE_KEY, json.dumps(snap, ensure_ascii=False), snap["at"]))
        conn.commit()


def previous() -> dict | None:
    store.init()
    with store.connect() as conn:
        row = conn.execute("SELECT value FROM catalog_state WHERE key=?", (STATE_KEY,)).fetchone()
    if not row:
        return None
    try:
        value = json.loads(row["value"])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _age_hours(stamp) -> float | None:
    """Сколько часов назад была отметка. None — разобрать не удалось."""
    try:
        return (datetime.now() - datetime.fromisoformat(str(stamp))).total_seconds() / 3600.0
    except (TypeError, ValueError):
        return None


def render(snap: dict, notes: list[str]) -> str:
    """Человеческий отчёт: что есть по сетям и что не так."""
    lines = [f"единая база на {snap['at'][:19]}:",
             f"  товаров единых {snap['items']}, мультисетевых {snap['multi']},"
             f" сравнимых по цене {snap['comparable']},"
             f" без единого товара {snap.get('unmatched', 0)}"]
    if "comparable_fresh" in snap:
        lines.append(f"  из них приложение может взять {snap['comparable_fresh']}:"
                     f" цене старше {snap.get('fresh_hours')} ч оно не верит")
    waiting = snap.get("waiting") or {}
    if waiting:
        names = {"perekrestok": "Перекрёсток", "monetka": "Монетка", "vprok": "Vprok"}
        parts = [f"{names.get(code, code)} +{n}" for code, n in
                 sorted(waiting.items(), key=lambda kv: -kv[1])]
        lines.append("  ждут цены сети (новых сравнимых): " + ", ".join(parts))
    night = snap.get("night") or {}
    if night:
        longest = night.get("longest") or {}
        lines.append(f"  ночь {night.get('day')}: {str(night.get('from'))[11:16]} → "
                     f"{str(night.get('to'))[11:16]} = {night.get('hours')} ч,"
                     f" сетей {night.get('chains')}, дольше всех {longest.get('chain')}"
                     f" {longest.get('minutes')} мин")
    lines.append("  сеть          строк   с ценой   с наличием   цена старше недели")
    for chain, info in sorted(snap["chains"].items()):
        lines.append(f"  {chain:<12} {info.get('products', 0):>7}   {info.get('priced', 0):>7}"
                     f"   {info.get('stocked', 0):>9}"
                     f"   {info.get('stale_week', 0):>17}")
    device = snap.get("device") or {}
    if device:
        lines.append("")
        lines.append("  телефон приносил (последнее):")
        for chain, mark in sorted(device.items()):
            kind = {"cards": "карточки", "report": "отчёт о корзине"}.get(str(mark.get("kind")), str(mark.get("kind")))
            age = _age_hours(mark.get("at"))
            when = f"{age:.0f} ч назад" if age is not None else str(mark.get("at") or "")
            lines.append(f"  {chain:<12} {kind}, {int(mark.get('got') or 0)} шт, {when}")
    phone_new = snap.get("phone_new") or {}
    if phone_new:
        brought = ", ".join(f"{chain} {count}" for chain, count in sorted(phone_new.items()))
        lines.append(f"  новых строк за сутки принёс телефон: {brought}")
    maps = snap.get("maps") or {}
    if maps:
        lines.append("")
        lines.append("  разбор перечня сетей:")
        for chain, mark in sorted(maps.items()):
            total = int(mark.get("total") or 0)
            left = int(mark.get("left") or 0)
            age = _age_hours(mark.get("at"))
            when = f"{age:.0f} ч назад" if age is not None else str(mark.get("at") or "")
            lines.append(f"  {chain:<12} разобрано {total - left} из {total},"
                         f" осталось {left}  ({when})")

    # РОСТ БАЗЫ ПО СЕТЯМ. «Наполнение для всех магазинов» проверяется только так: у сети
    # может быть много строк и ноль новых месяц — и это либо застывший источник, либо
    # сломанный обход, а различить их без такого списка нельзя.
    growth = [(chain, info) for chain, info in snap["chains"].items()]
    if growth:
        lines.append("")
        lines.append("  рост базы:")
        for chain, info in sorted(growth, key=lambda kv: -int(kv[1].get("new_week") or 0)):
            new = int(info.get("new_week") or 0)
            age = _age_hours(info.get("last_new"))
            when = f"{age / 24:.0f} дн назад" if age is not None else "нет строк"
            note = "  ← перечень не менялся месяц: источник застыл" \
                if age is not None and age > 24 * 30 else ""
            lines.append(f"  {chain:<12} +{new:<6} за неделю, последняя новая {when}{note}")
    lines.append("")
    if notes:
        lines.append("ЗАМЕЧАНИЯ:")
        lines.extend(f"  - {note}" for note in notes)
    else:
        lines.append("замечаний нет")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка здоровья единой базы")
    parser.add_argument("--save", action="store_true", help="запомнить снимок для следующей проверки")
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    args = parser.parse_args(argv)

    snap = snapshot()
    before = previous()
    notes = complaints(snap, before)
    if args.json:
        print(json.dumps({"snapshot": snap, "complaints": notes}, ensure_ascii=False))
    else:
        print(render(snap, notes))
    if args.save:
        save(snap)
    return 1 if notes else 0


if __name__ == "__main__":
    raise SystemExit(main())
