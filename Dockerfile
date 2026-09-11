# Оптимизатор продуктовой корзины — образ для любого контейнерного хостинга
# (Render, Railway, Fly.io, Yandex Cloud Serverless Containers, свой VPS).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tools ./tools
COPY data/fallback_prices.csv data/seed_products.csv data/seed_offers.csv data/seed_receipt.csv ./data/
COPY config.yaml README.md ./
COPY .streamlit ./.streamlit

# База и кэш цен живут здесь. На хостинге с эфемерным диском
# подключите том именно сюда, иначе данные исчезнут при передеплое.
VOLUME ["/app/data"]

EXPOSE 8501

# PORT задаёт хостинг (Render, Railway); локально — 8501.
CMD ["sh", "-c", "streamlit run app/ui/main.py --server.port=${PORT:-8501} --server.address=0.0.0.0"]
