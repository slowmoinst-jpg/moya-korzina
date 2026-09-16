#!/usr/bin/env bash
# Серверная часть выкладки «Моей корзины». Запускает deploy.ps1 по ssh после того,
# как распакует исходники в /opt/korzina/src. Собирает образ, при удачной сборке меняет
# контейнер и ждёт, пока приложение ответит. Данные живут в томе korzina-data и
# передеплой их не трогает. Если сборка не удалась, старый контейнер продолжает работать.
set -euo pipefail

ROOT=/opt/korzina
SRC=$ROOT/src
IMAGE=korzina:latest
NAME=korzina
VOLUME=korzina-data
PORT=${PORT:-80}

probe() {
    if command -v curl >/dev/null 2>&1; then
        curl -sf -m 3 "$1" >/dev/null 2>&1
    else
        wget -q -T 3 -O /dev/null "$1" 2>/dev/null
    fi
}

cd "$SRC"
echo "== сборка образа =="
if ! docker build -t "$IMAGE" . > "$ROOT/build.log" 2>&1; then
    tail -40 "$ROOT/build.log"
    echo "сборка не удалась, контейнер не тронут" >&2
    exit 1
fi
tail -2 "$ROOT/build.log"

echo "== замена контейнера =="
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped \
    -p "$PORT:8501" -v "$VOLUME:/app/data" "$IMAGE" >/dev/null

echo "== ожидание приложения =="
for i in $(seq 1 30); do
    if probe "http://127.0.0.1:$PORT/_stcore/health"; then
        echo "приложение ответило через $i с"
        docker image prune -f >/dev/null
        docker ps --filter "name=$NAME" --format "{{.Names}} {{.Status}} {{.Ports}}"
        exit 0
    fi
    sleep 1
done
echo "приложение не ответило за 30 с, последние строки журнала:" >&2
docker logs --tail 30 "$NAME" >&2
exit 1
