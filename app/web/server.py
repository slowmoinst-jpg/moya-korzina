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

DEFAULT_PORT = 8501          # тот же порт, что слушал Streamlit: Dockerfile и
                             # tools/server-deploy.sh пробрасывают именно его
THREADS = 8


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

    application = create_app()
    if args.debug:
        application.run(host=args.host, port=args.port, debug=True)
        return 0

    from waitress import serve

    # Доверие обратному прокси включается только когда он есть. Пустое значение
    # передавать нельзя: waitress разбирает список заголовков сразу и падает на
    # None — проверено, сервер не поднимается вовсе.
    behind = os.getenv("TRUSTED_PROXY") or ""
    extra = {"trusted_proxy": behind,
             "trusted_proxy_headers": {"x-forwarded-for", "x-forwarded-proto"}} if behind else {}

    logging.getLogger(__name__).info("интерфейс слушает %s:%d", args.host, args.port)
    serve(application, host=args.host, port=args.port, threads=THREADS, **extra)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
