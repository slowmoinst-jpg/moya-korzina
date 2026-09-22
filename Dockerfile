# «Моя корзина» — образ для своего сервера. Выбор площадки закрыт решением
# владельца 17.09.2026: приложение живёт на выделенном сервере в Timeweb,
# сравнивать хостинги больше не нужно (DEPLOY.md, «Где живёт приложение»).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Chromium для окна магазина (app/shopbrowser). Ставится ОТДЕЛЬНЫМ слоем и до
# копирования кода нарочно: сам браузер с его системными библиотеками весит под
# гигабайт и качается минуты, а меняется раз в полгода — попади он в один слой с
# app/, каждая выкладка тянула бы его заново.
#
# --with-deps ставит системные библиотеки, без которых Chromium не запускается
# вовсе (libnss3, libatk, шрифты). Ставим ровно один браузер: firefox и webkit нам
# не нужны, а весят столько же.
#
# xvfb — виртуальный экран для окна магазина. Поднимает его tools/entrypoint.sh
# напрямую, а не обёрткой xvfb-run: та требует ещё и xauth, а с ним зависает на
# ожидании экрана, не дойдя до Python (проверено на выкладке 17.09.2026).
RUN playwright install --with-deps chromium \
    && apt-get update \
    && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY tools ./tools
COPY data/fallback_prices.csv data/seed_products.csv data/seed_offers.csv data/seed_receipt.csv ./data/
COPY config.yaml README.md ./
# Собранные закладки: «Забрать все чеки» отдаёт экран «Мои чеки» (без неё он пишет
# «Закладка не собрана»), «Забрать цены» — инструмент для витрин Пятёрочки и
# Самоката, которые наш сервер не видит вовсе (разбор — docs/grab-prices.src.js).
# Остальное из docs образу не нужно.
COPY docs/grab.min.txt docs/grab-prices.min.txt docs/hand.min.txt ./docs/

# База и кэш цен живут здесь. На хостинге с эфемерным диском
# подключите том именно сюда, иначе данные исчезнут при передеплое.
VOLUME ["/app/data"]

EXPOSE 8501

# Интерфейс — обычное веб-приложение (app/web, Flask + waitress). Порт читает
# сам сервер из PORT; 8501 оставлен тем же, что слушал Streamlit, чтобы
# tools/server-deploy.sh и проброс наружу не пришлось трогать.
# Входной скрипт поднимает виртуальный экран и передаёт управление приложению.
# Экран нужен, чтобы Chromium шёл обычным режимом, а не headless: тот сообщает
# странице «за мной никто не смотрит», тогда как за ней смотрит человек — она
# показана ему в экране «Кабинет» (разбор — в app/shopbrowser/driver._headless).
# Не поднялся экран — приложение всё равно стартует и уходит в headless само.
# На машине разработчика под Windows входной скрипт не участвует вовсе.
ENTRYPOINT ["/bin/sh", "/app/tools/entrypoint.sh"]
CMD ["python", "-m", "app.web.server"]
