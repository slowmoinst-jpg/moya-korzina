"""Планировщик каталога: раз в сутки обойти сети и сопоставить товары.

Запуск в отдельном контейнере (см. tools/server-deploy.sh, контейнер korzina-jobs):
    python -m app.catalog.worker            — служба: обход в catalog.refresh_at, потом сон
    python -m app.catalog.worker --once     — один обход всех сетей и выход
    python -m app.catalog.worker --once --chain magnit   — одна сеть
    python -m app.catalog.worker --match    — только пересобрать единые товары
    python -m app.catalog.worker --api      — только приёмная дверь, без обхода

Почему отдельный процесс, а не поток внутри Streamlit. Обход длится десятки минут и
должен идти, когда в приложение никто не заходит; Streamlit живёт от запроса к запросу
и не место для долгих фоновых работ. Общая база каталога в WAL-режиме позволяет
процессам не мешать друг другу.

При старте службы обход запускается сразу, если каталог ещё пуст, — иначе первые сутки
после выкладки приложение стояло бы с пустым каталогом.

ЗДЕСЬ ЖЕ ЖИВЁТ ПРИЁМНАЯ ДВЕРЬ (app/api.py). Она прицеплена сюда не из экономии
контейнеров, а потому что это единственный процесс приложения, который работает
постоянно. Своего клиента у двери с 17.09.2026 нет — она писалась для
расширения-сборщика, которое владелец снял, — но живёт дальше под замком. Дверь занимает свой поток и с обходом не
пересекается — у них разные базы (каталог общий, дверь пишет в базу человека) и
общего состояния нет. Не открылась (занят порт, выключена настройкой) — служба
продолжает работать без неё: ночной обход важнее того, что к нему прицеплено.

И ДОЗОР ЦЕН (app/pricewatch.py) — по той же причине, в своём потоке. Ночной обход
держит основной поток часами, а цены корзин должны оставаться свежими всё это время.
Сеть у них общая, но темп общий тоже: слот сети лежит в data/pace и один на всех.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

from app import api, config, pricewatch
from app.catalog import refresh, store
from app import places
from app.catalog.crawlers import make

log = logging.getLogger("catalog")


QUEUE_POLL_SEC = 30.0          # как часто заглядывать в очередь адресов

# Сколько часов обход сети считается свежим для ПЛАНОВОГО обхода (config
# catalog.fresh_hours). Сутки между плановыми обходами больше этого срока, поэтому
# в обычную ночь не пропускается ничего; пропуск случается только после
# внеочередного обхода, пущенного вечером руками.
FRESH_HOURS = 12.0


def _lock_path() -> str:
    return os.path.join(os.path.dirname(store.path()), "crawl.lock")


@contextmanager
def one_crawler():
    """Сети обходит один процесс за раз, сколько бы обходов ни пустили.

    ЗАЧЕМ. Обход пускают с трёх сторон: плановый в korzina-jobs, внеочередной
    отдельным контейнером korzina-crawl (его не сносит выкладка) и руками
    `worker --once`. До 22.09.2026 они друг о друге не знали, и внеочередной обход,
    пущенный вечером, встретился бы с плановым в 03:30 на METRO: два потока в одну
    сеть — ровно то, от чего предостерегает рамка сбора (CLAUDE.md, «один поток на
    сеть»). Вдобавок сопоставление держит запись в каталог дольше, чем соседний
    процесс готов её ждать (store.connect, timeout 30 с).

    ПОЧЕМУ flock, А НЕ ОТМЕТКА В БАЗЕ. Отметку «идёт» пережил бы убитый процесс —
    обход, снесённый выкладкой, оставил бы её навсегда, и следующий ждал бы вечно.
    Замок ядра отпускается вместе с процессом сам. Том korzina-data у контейнеров
    общий, ядро одно, так что замок видят все.

    Второй обход ЖДЁТ первого, а не отказывается: плановый, упёршийся во
    внеочередной, дождётся его конца и дальше пропустит свежие сети (run_all,
    fresh_hours).

    На Windows fcntl нет, и замок ничего не делает: там идут тесты, а два обхода
    разом не запускают.
    """
    try:
        import fcntl
    except ImportError:
        fcntl = None
    if fcntl is None:
        yield
        return
    path = _lock_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.info("каталог обходит другой процесс — жду, пока он закончит")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            log.info("другой обход закончился, продолжаю")
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def fresh_hours() -> float:
    try:
        return float(config.get("catalog.fresh_hours") or FRESH_HOURS)
    except (TypeError, ValueError):
        return FRESH_HOURS


def chain_budget_min() -> float | None:
    """Потолок времени на одну сеть, минуты. Ноль и None — без потолка.

    Выше любой здоровой сети: ночь целиком (все одиннадцать по очереди) занимает пять
    часов, а самая долгая сеть — Лента с добором подробностей (~2,2 ч). Потолок нужен
    не для экономии, а чтобы одна застрявшая сеть не съела ночь остальным.
    """
    raw = config.get("catalog.chain_budget_min")
    try:
        value = float(raw or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def chains() -> list[str]:
    configured = config.get("catalog.chains")
    if isinstance(configured, list) and configured:
        return [str(c) for c in configured]
    # METRO и Перекрёсток добавлены 19.09.2026, Fix Price — 20.09.2026. METRO —
    # полноценный источник: цена по точке, остаток числом и штрихкоды одним ответом.
    # Fix Price отдаёт цены и наличие, но только нашему браузеру. Перекрёсток —
    # каталог без цен: у него открыта карта сайта и закрыто всё остальное, и нужен
    # он затем, чтобы опознавать строки его чеков.
    #
    # Порядок значим: обход последовательный и обрывается выкладкой, поэтому сети с
    # ценами идут первыми. Проверено 20.09.2026 — vprok и monetka стояли в хвосте и
    # не собрались ни разу.
    return ["magnit", "metro", "fixprice", "vkusvill", "lenta", "dixy",
            "perekrestok", "vprok", "monetka", "pyaterochka", "samokat"]


def lanes() -> int:
    """Сколько сетей вести ОДНОВРЕМЕННО. 1 — по очереди (как было до 20.10.2026).

    ЗАЧЕМ. Ночной обход шёл строго по очереди, и это не прихоть: рамка сбора требует
    «один поток НА СЕТЬ», но разные сети друг другу не мешают — у каждой свой магазин,
    свой адрес и свой темп. Замер 20.10.2026: ночь заняла 8 ч, из них 437 минут — это
    время самих сетей (METRO 135, Лента 166, Магнит 38, FixPrice 37, Дикси 35, ВкусВилл 26),
    и они ждали друг друга, хотя могли идти разом.

    ЧЕГО ПАРАЛЛЕЛИТЬ НЕЛЬЗЯ. Сети домашнего выхода (Дикси, Магнит, Пятёрочка, Самокат)
    ходят через туннель владельца: у него общий потолок 0.5 запроса в секунду и его
    домашний адрес. Два потока через один туннель удвоили бы нагрузку на этот адрес,
    поэтому они всегда идут одной полосой. Плюс сопоставление держит запись к базе одной
    транзакцией ~15 минут — поэтому при lanes>1 оно делается ОДИН раз в конце, а не
    между сетями.
    """
    try:
        value = int(config.get("catalog.lanes") or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(3, value))


def _split_lanes(codes: list[str], count: int,
                 minutes: dict[str, float] | None = None) -> list[list[str]]:
    """Разложить сети по полосам: домашний выход — одна полоса, остальные по времени.

    Порядок внутри полосы сохраняется (сети с ценами идут первыми, как в config).
    Раскладка остальных — «самая длинная в свободную полосу» (LPT): берём сети от долгой
    к короткой и кладём в ту полосу, где сейчас меньше всего минут. Без этого Лента
    (166 мин) стояла бы в одной полосе с METRO (135), а FixPrice (37) ждал бы в другой.
    Замеры берутся из журнала обходов (store.chain_minutes), неизвестная сеть считается
    средней — лучше так, чем поставить её в уже загруженную полосу наугад.
    """
    if count <= 1 or len(codes) <= 1:
        return [list(codes)]
    from app import homeexit

    minutes = minutes or {}
    home = [code for code in codes if homeexit.configured(code)]
    direct = [code for code in codes if code not in home]
    groups: list[list[str]] = [home] if home else []
    free = max(1, count - len(groups))
    buckets: list[list[str]] = [[] for _ in range(free)]
    loads = [0.0] * free
    known = [minutes.get(code) for code in direct if minutes.get(code)]
    fallback = sum(known) / len(known) if known else 10.0
    for code in sorted(direct, key=lambda item: -minutes.get(item, fallback)):
        index = loads.index(min(loads))
        buckets[index].append(code)
        loads[index] += minutes.get(code, fallback)
    order = {code: index for index, code in enumerate(codes)}
    groups.extend(sorted(bucket, key=lambda code: order[code]) for bucket in buckets if bucket)
    return groups


def _prepare(code: str) -> tuple[str, object] | None:
    """Подготовить сеть к обходу: точки, сборщик. None — сеть пропущена без обхода.

    ГОТОВИМ СЕТИ В ОДНОМ ПОТОКЕ, ДАЖЕ КОГДА ОБХОДИМ ПОЛОСАМИ (разбор 20.10.2026).
    Первый же параллельный прогон упал на импортах: две полосы одновременно тянули
    коннекторы, а они импортируют друг друга (`app.connectors.magnit` ↔ `app.connectors`),
    и один поток получал «partially initialized module». Хуже того, из-за сбоя Магнит
    остался без своей точки и мог уйти на запасной магазин из config — то есть в базу
    поехали бы цены чужого города. Подготовка (импорты, адреса, сборщик) — короткая и
    делается до старта полос; в потоках остаётся только сам обход, где импортов нет.
    """
    spots = places.points(code)
    if code in places.BY_POINT and not spots:
        log.info("%s: точек по адресам рабочих мест нет — иду по запасной из config", code)
    # Запоминаем точку КАЖДОГО адреса, а не только обходимые: два рабочих места у одного
    # магазина дают одну точку обхода, но цену своей точки должны найти оба.
    _remember(places.resolved(code) or spots)
    try:
        crawler = make(code, spots)
    except KeyError as exc:
        log.warning("%s", exc)
        return None
    where = ", ".join(str(p) for p in spots) or "запасная точка из config.yaml"
    log.info("%s: обход начат, точки: %s", code, where)
    return code, crawler


def _crawl_prepared(code: str, crawler, budget: float) -> dict:
    """Обойти уже подготовленную сеть. Тут нет импортов — только сеть и база."""
    # Пошаговая отметка: если процесс убьют посреди ночи, `finally` не сработает,
    # и без неё не узнать, на какой сети оборвалось (разбор 20.10.2026).
    store.mark_sweep_step(code)
    return refresh.run_chain(crawler, budget_min=budget,
                             progress=lambda m, c=code: log.info("%s: %s", c, m))


def _lane(group: list[tuple[str, object]], budget: float, results: list[dict],
          lock: threading.Lock) -> None:
    """Одна полоса: сети по очереди внутри себя. Падение сети не роняет полосу."""
    for code, crawler in group:
        try:
            result = _crawl_prepared(code, crawler, budget)
        except Exception:  # noqa: BLE001 — соседние полосы и сети должны дойти
            log.exception("%s: полоса упала на этой сети — иду к следующей", code)
            continue
        if result:
            with lock:
                results.append(result)


def run_all(only: str | None = None, fresh: float | None = None) -> list[dict]:
    """Обойти сети (полосами или по очереди), потом сопоставить. Одна сеть не мешает другим.

    Обход идёт ПО ТОЧКАМ ЛЮДЕЙ (app/places.py): магазины подбираются к адресам
    рабочих мест, а не берутся списком «все магазины сети». Решение владельца
    16.09.2026 — так и объём работы, и содержимое каталога определяет тот, кто им
    пользуется. У сетей без точек (ВкусВилл, Дикси) обход прежний: их каталог один
    на страну, и делить его нечего.

    `fresh` передаёт только ПЛАНОВЫЙ обход: сеть, обойденная без ошибок за
    последние столько часов, пропускается — второй проход по ней за вечер нагрузил
    бы источник и не дал бы новых цен. Ручной `--once` его не передаёт: человек
    попросил обойти сейчас, значит сейчас.

    У КАЖДОЙ СЕТИ ЕСТЬ ПОТОЛОК ВРЕМЕНИ (catalog.chain_budget_min). Обходы идут
    по очереди, и одна застрявшая сеть съедала бы всю ночь: следующие за ней до утра
    не начинались бы. Потолок выше любой здоровой сети (ночь целиком — пять часов),
    а упёршись в него, сеть останавливается и записывается неполной: пропавшими
    ничего не объявляется, следующей ночью обход продолжит с того же места.
    """
    results: list[dict] = []
    summary: dict | None = None
    budget = chain_budget_min()
    # Отметка «идёт обход целиком»: между сетями сторож выкладки видел «обходов нет»
    # (идёт сопоставление) и не мешал выкладке оборвать ночь (замер 20.10.2026).
    store.mark_sweep("running")
    outcome = "done"
    try:
        with one_crawler():
            # Свежесть спрашиваем ПОСЛЕ замка: плановый обход мог прождать внеочередной
            # несколько часов, и спроси он раньше — счёл бы несвежим только что обойденное.
            recent = store.fresh_chains(fresh) if fresh else {}
            todo: list[str] = []
            for code in chains():
                if only and code != only:
                    continue
                if code in recent:
                    log.info("%s: обойдена %s — плановый обход её пропускает", code,
                             recent[code][:16].replace("T", " "))
                    continue
                todo.append(code)
            count = lanes()
            if count <= 1 or len(todo) <= 1:
                for code in todo:
                    prepared = _prepare(code)
                    if not prepared:
                        continue
                    result = _crawl_prepared(prepared[0], prepared[1], budget)
                    results.append(result)
                    # Сопоставляем после КАЖДОЙ сети, а не только в конце: первый обход длится
                    # часы (карточки Ленты и ВкусВилла по одной в секунду), и без этого каталог
                    # в интерфейсе был бы пустым до самого конца, хотя Магнит и Дикси собраны.
                    # И выкладка обрывает обход — сопоставление в конце не случилось бы вовсе.
                    #
                    # Но ТОЛЬКО если обход сети что-то поменял в составе: сопоставление стоит
                    # десять минут (журнал 22.09.2026: 09:52 — 10:02), а цена и наличие на
                    # единые товары не влияют — они не ключи.
                    #
                    # СБОЙ СОПОСТАВЛЕНИЯ НЕ КОНЧАЕТ НОЧЬ (разбор 20.10.2026): падение шага
                    # писало бы в журнал и обход шёл бы к следующей сети.
                    if _changed(result) or _unmatched():
                        try:
                            summary = refresh.match_all(
                                progress=lambda m: log.info("сопоставление: %s", m))
                        except Exception:  # noqa: BLE001 — одна сеть не должна терять остальные
                            log.exception("сопоставление после %s упало — обход идёт дальше", code)
            else:
                # Сети готовим В ОДНОМ ПОТОКЕ: импорты и адреса, потом полосы (разбор 20.10.2026).
                prepared = [item for item in (_prepare(code) for code in todo) if item]
                by_code = {code: crawler for code, crawler in prepared}
                groups = [[(code, by_code[code]) for code in group]
                          for group in _split_lanes(list(by_code), count, store.chain_minutes())]
                log.info("полос обхода: %d — %s", len(groups),
                         " | ".join(",".join(code for code, _ in group) for group in groups))
                lock = threading.Lock()
                threads = [threading.Thread(target=_lane, args=(group, budget, results, lock),
                                            name=f"lane-{index}", daemon=False)
                           for index, group in enumerate(groups, start=1)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                order = {code: index for index, code in enumerate(chains())}
                results.sort(key=lambda row: order.get(row.get("chain"), 999))
                # Сопоставление — ОДИН раз в конце: при полосах оно одно на всех, а не
                # после каждой сети. Замер 21.10.2026: 580–620 с на 316 тыс. строк (в
                # основном кластеризация), запись при этом давно не держит базу: меняются
                # только изменившиеся строки, проба записи ждёт 0,6 с (было 15 минут).
                if any(_changed(result) for result in results) or _unmatched():
                    try:
                        summary = refresh.match_all(
                            progress=lambda m: log.info("сопоставление: %s", m))
                    except Exception:  # noqa: BLE001
                        log.exception("сопоставление после полос упало — ночь всё равно записана")
            # Связи товаров людей — после того как база обновилась (см. _link_places):
            # без них экран «Где дешевле» пишет «товар не связан с магазином».
            _link_places()
    except BaseException:
        outcome = "failed"
        raise
    finally:
        # Ночь обязана оставить след даже если упала: проверка базы и снятая отметка
        # «идёт обход» — единственный способ узнать об этом на следующее утро.
        _checkup_after_sweep(outcome)
    if summary is not None:
        results.append({"chain": "match", **summary})
    return results


def _checkup_after_sweep(state: str = "done") -> None:
    """Проверить базу после обхода: не просело ли наполнение.

    ЗАЧЕМ. Обход умеет «пройти успешно» и ничего не собрать (у Пятёрочки и Самоката так и
    было: blocked при нуле строк). Глазами это никто не проверяет — значит проверка должна
    идти сама и говорить в журнал. Снимок запоминается, чтобы следующая ночь сравнивалась
    с этой (app/catalog/checkup.py).

    `state` — чем кончился обход целиком: 'done' или 'failed'. Отметку ставим ЗДЕСЬ и в
    любом случае (её зовут из finally run_all): иначе упавшая ночь оставила бы «обход идёт»
    висеть до утра, а сторож выкладки до истечения срока считал бы контейнер занятым.
    """
    from app.catalog import checkup

    # Здесь же снимаем отметку «идёт обход»: это хвост run_all.
    store.mark_sweep(state)
    # Наблюдения приложения за ценами — в общую историю: у Пятёрочки и Самоката сервер
    # витрин не видит, и единственные настоящие цены по ним приносит телефон владельца
    # (замер 20.10.2026: 101 наблюдение по Пятёрочке, 86 артикулов).
    try:
        from app.catalog import observations

        moved = observations.mirror()
        total = sum(moved["chains"].values())
        if total:
            log.info("наблюдения приложения — в историю цен: %d", total)
    except Exception:  # noqa: BLE001 — перенос не должен ронять проверку
        log.exception("перенос наблюдений приложения не удался")
    try:
        snap = checkup.snapshot()
        notes = checkup.complaints(snap, checkup.previous())
        checkup.save(snap)
    except Exception:  # noqa: BLE001 — проверка не должна ронять обход
        log.exception("проверка базы после обхода не удалась")
        return
    for note in notes:
        log.warning("проверка базы: %s", note)
    if not notes:
        log.info("проверка базы: замечаний нет, сравнимым по цене товаров %d", snap["comparable"])


def _changed(result: dict) -> bool:
    """Поменял ли обход сети состав каталога — то, от чего зависит сопоставление."""
    return bool(result.get("added") or result.get("updated") or result.get("gone"))


def _link_places() -> None:
    """Поставить в фон связывание товаров людей с магазинами после обновления базы.

    ЗАЧЕМ (замер 21.10.2026). Экран «Где дешевле» по товару без связей пишет «товар не
    связан с магазином» — сравнивать не с чем. Связи создавала только кнопка в кабинете,
    то есть их наличие зависело от человека. Замер на корзине владельца: у молочки связи
    есть (три сети с ценой: METRO 136 ₽, Самокат 145 ₽, ВкусВилл 169 ₽), а у баранки и
    сушки — ни одной, хотя единый товар у обоих есть.

    ПОЧЕМУ В ФОН, А НЕ В НОЧЬ. Связывание — не мгновенное: на рабочем месте владельца
    1 159 товаров и проход занимает 537 с (9 минут), из них привязано 397, не найдено 735.
    Вставить это в конец обхода значило бы удлинить ночь на каждого человека. Поэтому
    зовём `link.link_soon` — он поднимает свой поток на рабочее место (по одному на
    человека, повторный вызов не плодит второй, база снимается в finally) и уважает
    `catalog.autolink`. Ночь заканчивается как обычно, а связи догоняют в ближайшие
    минуты — приложение увидит их без действий человека.
    """
    from app import users
    from app.catalog import link

    for phone in users.list_workspaces():
        if phone == users.DEMO:
            continue
        try:
            link.link_soon(phone)
        except Exception:  # noqa: BLE001 — одно место не должно ронять обход
            log.exception("связи товаров: %s не поставились в фон", phone[-4:])


def _unmatched() -> bool:
    """Остались ли строки без единого товара — например, после оборванного обхода."""
    try:
        return store.unmatched_count() > 0
    except Exception:  # noqa: BLE001 — не посчиталось: сопоставим по изменениям
        return False


def _remember(spots) -> None:
    """Точки к адресам — в каталог: по ним перенос товара берёт цену СВОЕЙ точки."""
    try:
        store.remember_points(spots)
    except Exception:  # noqa: BLE001 — запомнить не вышло, обход от этого не зависит
        log.warning("точки к адресам не запомнились", exc_info=True)


def _next_run(at: str) -> datetime:
    hour, minute = (int(x) for x in (at or "03:30").split(":")[:2])
    now = datetime.now()
    planned = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if planned <= now:
        planned += timedelta(days=1)
    return planned


def run_address(address: str) -> list[dict]:
    """Загрузить цены по одному адресу: подобрать точки и обойти их.

    Это работа по требованию: человек указал адрес и ждёт свои цены сегодня, а не
    в три часа ночи. Сети без точек (ВкусВилл, Дикси) сюда не идут — их каталог
    один на страну, и адрес его не меняет; они обновляются по расписанию.
    """
    results: list[dict] = []
    with one_crawler():
        for code in chains():
            spots = places.points_for(address, code)
            if not spots:
                continue
            _remember(spots)
            try:
                crawler = make(code, spots)
            except KeyError as exc:
                log.warning("%s", exc)
                continue
            log.info("%s: загрузка по адресу «%s», точка %s", code, address, spots[0])
            # ЧАСТИЧНЫЙ обход: одна точка сети, а не все. Пропавшими по нему ничего не
            # объявляется, и плановый обход сеть не пропустит (refresh.run_chain).
            results.append(refresh.run_chain(crawler, progress=lambda m, c=code: log.info("%s: %s", c, m),
                                             partial=True))
        if any(_changed(r) for r in results) or (results and _unmatched()):
            refresh.match_all(progress=lambda m: log.info("сопоставление: %s", m))
    return results


def drain_queue() -> int:
    """Разобрать очередь адресов до конца. Возвращает, сколько адресов обработано.

    По одному за раз и до опустошения: адресов в очереди обычно один-два, а обход
    каждого — минуты. Упавший адрес помечается failed и очередь не затыкает.
    """
    done = 0
    while True:
        job = store.take_address()
        if not job:
            return done
        address = job["address"]
        try:
            results = run_address(address)
            if not results:
                store.finish_address(job["id"], "done", "точек по этому адресу не нашлось")
            else:
                note = "; ".join(f"{r['chain']}: {r['status']}, {r['seen']}" for r in results)
                store.finish_address(job["id"], "done", note)
            done += 1
        except Exception as exc:  # noqa: BLE001 — один адрес не должен ронять службу
            log.exception("загрузка по адресу «%s» не удалась", address)
            store.finish_address(job["id"], "failed", f"{type(exc).__name__}: {exc}")
            done += 1                 # обработан — значит очередь сдвинулась, даже если неудачно
    return done


def serve() -> None:
    at = str(config.get("catalog.refresh_at") or "03:30")
    store.init()
    # Дверь открывается ПЕРВОЙ и в своём потоке: первый обход пустого каталога
    # длится часы, и расширение, постучавшееся в это время, не должно услышать
    # «нет такого адреса» только потому, что служба занята Магнитом.
    api.serve_in_background()
    # Дозор цен — тоже своим потоком: обход держит этот поток часами, а цены корзин
    # за это время протухли бы (app/freshness.py).
    pricewatch.serve_in_background()
    revived = store.revive_stuck()
    if revived:
        log.info("возвращено в очередь после перезапуска: %d адрес(ов)", revived)
    # Обходы, застигнутые перезапуском, закрываем: строка «running» навсегда — это
    # неправда о сети (замер 19.10.2026: 15 таких строк, старшие от 16.09.2026).
    stale = store.close_stale_runs()
    if stale:
        log.info("закрыто брошенных обходов после перезапуска: %d", stale)
    if not store.item_count()["items"]:
        log.info("каталог пуст — первый обход сразу")
        run_all()
    while True:
        planned = _next_run(at)
        log.info("следующий обход %s", planned.isoformat(timespec="minutes"))
        # Спим не до расписания одним куском, а короткими шагами: между ними
        # разбирается очередь адресов. Человек, указавший адрес в полдень, получит
        # свои цены через минуту, а не следующей ночью.
        while datetime.now() < planned:
            try:
                drain_queue()
            except Exception:  # noqa: BLE001
                log.exception("очередь адресов не разобралась, попробую на следующем шаге")
            left = (planned - datetime.now()).total_seconds()
            time.sleep(max(1.0, min(QUEUE_POLL_SEC, left)))
        try:
            run_all(fresh=fresh_hours())
        except Exception:  # noqa: BLE001 — служба не должна умирать от одного обхода
            log.exception("обход не удался, следующая попытка по расписанию")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    parser = argparse.ArgumentParser(description="Обход каталогов сетей и сопоставление")
    parser.add_argument("--once", action="store_true", help="один обход и выход")
    parser.add_argument("--chain", help="только эта сеть")
    parser.add_argument("--match", action="store_true", help="только сопоставление")
    parser.add_argument("--queue", action="store_true",
                        help="разобрать очередь адресов и выйти")
    parser.add_argument("--runs", action="store_true",
                        help="показать идущие обходы; код 3, если обход идёт (проверка перед выкладкой)")
    parser.add_argument("--address", help="загрузить цены по этому адресу и выйти")
    parser.add_argument("--api", action="store_true",
                        help="только приёмная дверь для расширения, без обхода")
    args = parser.parse_args(argv)
    if args.api:
        # Дверь одна, без обхода: так её проверяют у себя на машине и так её можно
        # поднять отдельным контейнером, если обход когда-нибудь переедет.
        api.serve()
        return 0
    if args.match:
        with one_crawler():
            print(refresh.match_all(progress=print))
        return 0
    if args.runs:
        # Дверь для выкладки: пока обход идёт, перезапуск контейнера его оборвёт.
        # Код возврата, а не только текст: так это читает deploy.ps1.
        live = store.running_runs()
        for row in live:
            print(f"{row['chain']} — обход идёт с {row['started_at']}")
        sweep = store.sweep_state()
        if sweep == "running":
            # Между сетями (идёт сопоставление) строки «running» нет, а обход идёт:
            # выкладка в этот момент так же оборвала бы ночь (замер 20.10.2026).
            print("идёт обход сетей целиком (сейчас, возможно, сопоставление)")
        if live or sweep == "running":
            print("обход идёт: выкладка оборвёт его, следующая попытка — по расписанию")
            return 3
        if sweep == "stale":
            print("отметка обхода старая — считаю, что его нет")
        print("обходов идёт: нет")
        return 0
    if args.address:
        for row in run_address(args.address):
            print(row)
        return 0
    if args.queue:
        print(f"адресов обработано: {drain_queue()}")
        return 0
    if args.once or args.chain:
        for row in run_all(args.chain):
            print(row)
        return 0
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
