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
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

from app import api, config
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


def run_all(only: str | None = None, fresh: float | None = None) -> list[dict]:
    """Обойти сети по очереди, потом сопоставить. Одна упавшая сеть не мешает остальным.

    Обход идёт ПО ТОЧКАМ ЛЮДЕЙ (app/places.py): магазины подбираются к адресам
    рабочих мест, а не берутся списком «все магазины сети». Решение владельца
    16.09.2026 — так и объём работы, и содержимое каталога определяет тот, кто им
    пользуется. У сетей без точек (ВкусВилл, Дикси) обход прежний: их каталог один
    на страну, и делить его нечего.

    `fresh` передаёт только ПЛАНОВЫЙ обход: сеть, обойденная без ошибок за
    последние столько часов, пропускается — второй проход по ней за вечер нагрузил
    бы источник и не дал бы новых цен. Ручной `--once` его не передаёт: человек
    попросил обойти сейчас, значит сейчас.
    """
    results: list[dict] = []
    summary: dict | None = None
    with one_crawler():
        # Свежесть спрашиваем ПОСЛЕ замка: плановый обход мог прождать внеочередной
        # несколько часов, и спроси он раньше — счёл бы несвежим только что обойденное.
        recent = store.fresh_chains(fresh) if fresh else {}
        for code in chains():
            if only and code != only:
                continue
            if code in recent:
                log.info("%s: обойдена %s — плановый обход её пропускает", code,
                         recent[code][:16].replace("T", " "))
                continue
            spots = places.points(code)
            if code in places.BY_POINT and not spots:
                log.info("%s: точек по адресам рабочих мест нет — иду по запасной из config", code)
            _remember(spots)
            try:
                crawler = make(code, spots)
            except KeyError as exc:
                log.warning("%s", exc)
                continue
            where = ", ".join(str(p) for p in spots) or "запасная точка из config.yaml"
            log.info("%s: обход начат, точки: %s", code, where)
            result = refresh.run_chain(crawler, progress=lambda m, c=code: log.info("%s: %s", c, m))
            results.append(result)
            # Сопоставляем после КАЖДОЙ сети, а не только в конце: первый обход длится часы
            # (карточки Ленты и ВкусВилла по одной в секунду), и без этого каталог в
            # интерфейсе был бы пустым до самого конца, хотя Магнит и Дикси уже собраны.
            # И выкладка обрывает обход — сопоставление в конце не случилось бы вовсе.
            #
            # Но ТОЛЬКО если обход сети что-то поменял в составе: сопоставление стоит
            # десять минут (журнал 22.09.2026: 09:52 — 10:02), а цена и наличие на
            # единые товары не влияют — они не ключи. Обход без новых, изменённых и
            # пропавших строк пересчитал бы ровно тот же каталог.
            if _changed(result):
                summary = refresh.match_all(progress=lambda m: log.info("сопоставление: %s", m))
    if summary is not None:
        results.append({"chain": "match", **summary})
    return results


def _changed(result: dict) -> bool:
    """Поменял ли обход сети состав каталога — то, от чего зависит сопоставление."""
    return bool(result.get("added") or result.get("updated") or result.get("gone"))


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
        if any(_changed(r) for r in results):
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
    revived = store.revive_stuck()
    if revived:
        log.info("возвращено в очередь после перезапуска: %d адрес(ов)", revived)
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
