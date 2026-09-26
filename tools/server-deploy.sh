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
# Приёмная дверь для расширения (app/api.py) живёт в контейнере korzina-jobs.
# Наружный номер можно сменить переменной, внутренний задан в config.yaml -> api.port
# и должен совпадать с правой половиной.
API_PORT=${API_PORT:-8765}

# Ключ языковой модели для запасного разбора вёрстки (app/connectors/smart_extract.py).
# Лежит ОТДЕЛЬНО от исходников и в репозиторий не попадает: выкладка перезаписывает
# $SRC целиком, и ключ, положенный туда, исчез бы при первой же следующей выкладке.
# Формат — строки KEY=значение, как понимает docker --env-file:
#     OPENROUTER_API_KEY=sk-or-v1-...
# Нет файла — приложение работает ровно как раньше, просто запасной разбор молчит и
# говорит в журнал, какой переменной ему не хватает.
ENV_FILE=$ROOT/llm.env
ENV_ARG=()
if [ -f "$ENV_FILE" ]; then
    ENV_ARG=(--env-file "$ENV_FILE")
    echo "ключ модели: $ENV_FILE подхвачен"
else
    echo "ключ модели: $ENV_FILE не найден, запасной разбор вёрстки будет молчать"
fi

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
    -p "$PORT:8501" -v "$VOLUME:/app/data" "${ENV_ARG[@]}" "$IMAGE" >/dev/null

# Планировщик каталога — второй контейнер из того же образа с тем же томом: раз в
# сутки обходит сети и сопоставляет товары (app/catalog/worker.py). Отдельно от
# приложения, чтобы часовой обход не жил внутри Streamlit. В нём же слушает приёмная
# дверь для расширения (app/api.py) — потому порт и открыт наружу: расширение живёт
# в браузере человека и достучаться должно снаружи. Замок у двери свой, секрет
# рабочего места; без него она не делает ни одной записи.
echo "== планировщик каталога и приёмная дверь =="
docker rm -f "$NAME-jobs" >/dev/null 2>&1 || true
docker run -d --name "$NAME-jobs" --restart unless-stopped \
    -p "$API_PORT:8765" -v "$VOLUME:/app/data" "${ENV_ARG[@]}" "$IMAGE" \
    python -m app.catalog.worker >/dev/null

# Мост к домашнему выходу (app/homeexit.py, tools/tunnel). Туннель с ноутбука
# владельца слушает только 127.0.0.1:1080 хоста, а контейнеры сидят в своей сети;
# мост слушает 172.17.0.1:1080 — адрес хоста в сети docker, из интернета он не
# виден — и перекладывает байты на туннель. Туннеля нет — мост просто закрывает
# соединение, и сборщик идёт без домашнего выхода.
echo "== мост к домашнему выходу =="
docker rm -f "$NAME-tunnel-relay" >/dev/null 2>&1 || true
docker run -d --name "$NAME-tunnel-relay" --restart unless-stopped --network host \
    --entrypoint python "$IMAGE" tools/tunnel/relay.py \
    --listen 172.17.0.1:1080 --to 127.0.0.1:1080 >/dev/null

echo "== ожидание приложения =="
for i in $(seq 1 30); do
    # Своя страница входа вместо прежнего /_stcore/health: интерфейс переехал
    # со Streamlit на app/web (Flask), и его проверки живости больше нет.
    if probe "http://127.0.0.1:$PORT/login"; then
        echo "приложение ответило через $i с"
        # Дверь проверяем отдельно и НЕ роняем из-за неё выкладку: приложение уже
        # работает, а расширение — вторая половина, которой пользуются не все.
        if probe "http://127.0.0.1:$API_PORT/api/health"; then
            echo "приёмная дверь отвечает на порту $API_PORT"
        else
            echo "приёмная дверь на порту $API_PORT молчит — смотрите docker logs $NAME-jobs"
        fi
        docker image prune -f >/dev/null
        docker ps --filter "name=$NAME" --format "{{.Names}} {{.Status}} {{.Ports}}"
        exit 0
    fi
    sleep 1
done
echo "приложение не ответило за 30 с, последние строки журнала:" >&2
docker logs --tail 30 "$NAME" >&2
exit 1
