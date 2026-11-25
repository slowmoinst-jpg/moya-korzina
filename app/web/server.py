"""Запуск веб-приложения. Один вход и для разработки, и для боевого сервера.

    python -m app.web.server                 — как в бою, через waitress
    python -m app.web.server --debug         — с перезагрузкой при правке файлов

ПОЧЕМУ WAITRESS, А НЕ ВСТРОЕННЫЙ СЕРВЕР FLASK. Встроенный сам себя называет
непригодным для боя, и это не формальность: он однопоточный по умолчанию и не
переживает медленного клиента. Из боевых серверов waitress единственный работает
и на Linux, где стоит сервер, и на Windows, где идёт разработка (gunicorn на
Windows не запускается вовсе). Одна команда на обеих машинах — значит меньше
шансов, что «у меня работало».

ПОТОКИ И ЧУЖИЕ БАЗЫ. waitress держит пул потоков, а путь к базе человека живёт в
contextvars, привязанных к потоку. Поэтому база снимается в конце КАЖДОГО запроса
(app/web/auth.py, teardown_request): иначе поток, освободившийся после одного
человека, отдал бы его базу следующему. Число потоков держим скромным — база
SQLite всё равно пишется по одному.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time

log = logging.getLogger(__name__)

DEFAULT_PORT = 8501          # тот же порт, что слушал Streamlit: Dockerfile и
                             # tools/server-deploy.sh пробрасывают именно его
THREADS = 8
# Квант переключения потоков вместо 5 мс по умолчанию. Наряд телефону — сотни коротких запросов к базе,
# после каждого поток заново ждёт GIL, и рядом со счётным потоком (расчёт «Где дешевле») ждал по 5 мс.
# Замер 13.11.2026 на сервере, шесть нарядов разом рядом со счётом: 5 мс — больше 8 с, 0,2 мс — 1,1 с,
# без счёта — 0,45 с. Счётному потоку это стоит до 10 % скорости и до 0,13 ядра сверху. Только Linux:
# на Windows ожидание GIL считается целыми миллисекундами, и 0,2 мс там — холостое кручение ядер.
SWITCH_INTERVAL_SEC = 0.0002
# Страницы единого каталога раз в минуту трогаем, чтобы ядро реже выгружало их из памяти сервера. Опознание
# товара для наряда (catalog.refresh.resolve) — полный проход по таблице каталога: 0,1 с, пока её страницы в
# памяти, и 2–6 с, когда их читать с диска. Замер 13.11.2026: через 8 минут после сборки APK на том же сервере
# первые наряды пяти сетей шли 5,3–7,3 с вместо 0,1–1,7 с; с выгруженным файлом каталога первый вопрос —
# 5,95 с, следующие — 0,09 с. Выгрузить страницы ядро может и между проходами (сборка образа или APK на
# сервере): тогда их вернёт ближайший проход, а не вопрос человека, если тот придёт позже прохода.
# shortcut: прогрев вместо индекса слов; появится индекс у store.search_items — прогрев убрать.
CATALOG_WARM_SEC = 60
CATALOG_SLOW_SEC = 1.0       # проход дольше пишем в журнал: обычно это значит, что страниц в памяти не было


def warm_catalog(say: bool = False) -> None:
    """Один проход по таблице единого каталога — тем же поиском, каким наряд опознаёт товар."""
    try:
        from app.catalog import store

        if not os.path.exists(store.path()):
            # Каталога ещё нет: пустым его не заводим, это дело обхода (store.init).
            if say:
                log.info("прогрев единого каталога ждёт: файла каталога ещё нет")
            return
        began = time.monotonic()
        store.search_items("молоко", limit=1)
    except Exception:  # noqa: BLE001 — прогрев не повод ронять сервер
        log.warning("единый каталог не прогрелся", exc_info=True)
        return
    took = time.monotonic() - began
    if say or took >= CATALOG_SLOW_SEC:
        log.info("прогрев единого каталога занял %.1f с", took)


def _keep_catalog_warm() -> None:
    warm_catalog(say=True)   # первый проход в журнал всегда: по нему видно, что прогрев запущен
    while True:
        time.sleep(CATALOG_WARM_SEC)
        warm_catalog()


def port() -> int:
    """Порт из окружения, как того требует Dockerfile (он читает PORT)."""
    raw = os.getenv("PORT") or ""
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_PORT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Веб-приложение «Моя корзина»")
    parser.add_argument("--port", type=int, default=port())
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--debug", action="store_true",
                        help="встроенный сервер Flask с перезагрузкой — только для разработки")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    from app.web import create_app

    if sys.platform != "win32":
        sys.setswitchinterval(SWITCH_INTERVAL_SEC)
    application = create_app()
    if args.debug:
        application.run(host=args.host, port=args.port, debug=True)
        return 0

    from waitress import serve
    from app.fns import background as receipt_jobs

    # Доверие обратному прокси включается только когда он есть. Пустое значение
    # передавать нельзя: waitress разбирает список заголовков сразу и падает на
    # None — проверено, сервер не поднимается вовсе.
    behind = os.getenv("TRUSTED_PROXY") or ""
    extra = {"trusted_proxy": behind,
             "trusted_proxy_headers": {"x-forwarded-for", "x-forwarded-proto"}} if behind else {}

    threading.Thread(target=_keep_catalog_warm, name="catalog-warm", daemon=True).start()
    threading.Thread(target=receipt_jobs.serve, name="receipt-schedule", daemon=True).start()
    log.info("интерфейс слушает %s:%d", args.host, args.port)
    serve(application, host=args.host, port=args.port, threads=THREADS, **extra)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
