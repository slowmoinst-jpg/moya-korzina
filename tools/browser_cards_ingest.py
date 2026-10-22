"""Заливка карточек из tools/browser_cards.py в единую базу боевого сервера.

Карточки кладутся ТОЙ ЖЕ дверью, что и с телефона, — phonecart.accept_cards
(app/phonecart.py): артикул сети берётся из адреса карточки (app/catalog/fromurl.py),
новые товары заводит refresh.learn_device_rows, а цены знакомых строк обновляет
store.set_prices. Поэтому повторный сбор по тем же страницам — это обновление цен,
а не дубли: сколько строк завелось, видно в ответе полем learned.

ЗАПУСК.
    python tools/browser_cards_ingest.py data/browser_cards/cards_samokat.json
    python tools/browser_cards_ingest.py data/browser_cards
    python tools/browser_cards_ingest.py data/browser_cards --match

--match после заливки запускает сопоставление (refresh.match_all) в ОТДЕЛЬНОМ
контейнере: выкладка перезапускает korzina-jobs и убивает exec-прогон (замер
19.10.2026: exec_die 137 посреди сопоставления). Отдельный контейнер выкладку
переживает; имя, том и образ настраиваются аргументами.

Сервер должен быть доступен по ssh-псевдониму (по умолчанию korzina), в нём —
контейнер приложения с общим томом данных.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SERVER_SCRIPT = '''"""Карточки витрины -> единая база (app.phonecart.accept_cards), stdin JSON."""
import json
import sys

from app import phonecart

body = json.load(sys.stdin)
print(json.dumps(phonecart.accept_cards(body), ensure_ascii=False))
'''

CHAIN_BY_FILE = {"cards_samokat.json": "samokat", "cards_pyaterochka.json": "pyaterochka"}
REMOTE_SCRIPT = "/tmp/browser_cards_ingest.py"


def ssh(host: str, command: str, payload: bytes | None = None) -> subprocess.CompletedProcess:
    """ssh с ОДНОЙ строкой удалённой команды — иначе кавычки sh -c теряются."""
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", host, command],
        input=payload, capture_output=True, timeout=300,
    )


def install_server_script(host: str, container: str) -> None:
    result = ssh(host, f'docker exec -i {container} sh -c "cat > {REMOTE_SCRIPT}"',
                 SERVER_SCRIPT.encode("utf-8"))
    if result.returncode != 0:
        raise SystemExit(f"не установил скрипт в контейнер: {result.stderr.decode('utf-8', 'replace')}")
    check = ssh(host, f'docker exec {container} sh -c "wc -c {REMOTE_SCRIPT}"')
    if not check.stdout.strip():
        raise SystemExit("скрипт в контейнер не лёг")
    print("серверный скрипт установлен:", check.stdout.decode("utf-8", "replace").strip(), flush=True)


def ingest_file(host: str, container: str, chain: str, cards: list[dict], batch: int) -> dict:
    totals = {"got": 0, "with_sku": 0, "learned": 0, "stocked": 0, "priced": 0,
              "repriced": 0, "no_sku": 0, "no_name": 0}
    batches = [cards[i:i + batch] for i in range(0, len(cards), batch)]
    for index, part in enumerate(batches, 1):
        body = json.dumps({"store": chain, "cards": part}, ensure_ascii=False).encode("utf-8")
        result = ssh(host, f'docker exec -i -e PYTHONPATH=/app {container} python {REMOTE_SCRIPT} -',
                     body)
        if result.returncode != 0:
            print(f"  пачка {index}/{len(batches)}: ОШИБКА "
                  f"{result.stderr.decode('utf-8', 'replace')[:300]}", flush=True)
            continue
        line = result.stdout.decode("utf-8", "replace").strip().splitlines()
        if not line:
            print(f"  пачка {index}/{len(batches)}: пустой ответ", flush=True)
            continue
        try:
            data = json.loads(line[-1])
        except json.JSONDecodeError:
            print(f"  пачка {index}/{len(batches)}: не разобрал ответ {line[-1][:200]}", flush=True)
            continue
        for key in totals:
            totals[key] += int(data.get(key, 0))
        print(f"  пачка {index}/{len(batches)}: learned={data.get('learned')} "
              f"repriced={data.get('repriced')}", flush=True)
    return totals


def start_match(host: str, container: str, volume: str, image: str, name: str) -> None:
    ssh(host, f"docker rm -f {name} 2>/dev/null || true")
    command = (f'docker run -d --name {name} -v {volume}:/app/data {image} '
               f'sh -c "python -u -m app.catalog.worker --match > /tmp/match.log 2>&1"')
    result = ssh(host, command)
    if result.returncode != 0:
        raise SystemExit(f"сопоставление не запустилось: {result.stderr.decode('utf-8', 'replace')}")
    print(f"\nсопоставление запущено в отдельном контейнере {name} "
          f"(журнал: docker exec {name} tail -5 /tmp/match.log; обычно 12–15 мин)", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Заливка карточек витрин в единую базу")
    parser.add_argument("paths", nargs="+", help="файл cards_<сеть>.json или каталог с ними")
    parser.add_argument("--host", default="korzina", help="ssh-псевдоним сервера")
    parser.add_argument("--container", default="korzina-jobs", help="контейнер приложения")
    parser.add_argument("--batch", type=int, default=150, help="карточек в запросе (CARDS_LIMIT)")
    parser.add_argument("--match", action="store_true", help="после заливки запустить сопоставление")
    parser.add_argument("--volume", default="korzina-data", help="том данных для контейнера сопоставления")
    parser.add_argument("--image", default="korzina:latest", help="образ для контейнера сопоставления")
    parser.add_argument("--match-name", default="browser-match", help="имя контейнера сопоставления")
    args = parser.parse_args(argv)

    files: list[Path] = []
    for raw in args.paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(path.glob("cards_*.json")))
        else:
            files.append(path)
    if not files:
        raise SystemExit("файлов с карточками не найдено")

    install_server_script(args.host, args.container)
    grand: dict[str, dict] = {}
    for path in files:
        chain = CHAIN_BY_FILE.get(path.name)
        if not chain:
            raise SystemExit(f"по имени {path.name} не понять сеть (ожидаются cards_samokat.json "
                             f"или cards_pyaterochka.json)")
        cards = json.loads(path.read_text(encoding="utf-8"))
        print(f"== {chain}: {path.name}, карточек {len(cards)} ==", flush=True)
        grand[chain] = ingest_file(args.host, args.container, chain, cards, args.batch)
        print(f"== {chain}: {json.dumps(grand[chain], ensure_ascii=False)}", flush=True)

    if args.match:
        start_match(args.host, args.container, args.volume, args.image, args.match_name)
    print("\nГОТОВО", json.dumps(grand, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
