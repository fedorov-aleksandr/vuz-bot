FROM python:3.12-slim

WORKDIR /app

# Устанавливаем компилятор и заголовочные файлы, необходимые для сборки aiohttp
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

RUN mkdir -p /app/logs && chmod +x /app/start.sh || true

ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=10s --retries=3 CMD test -s /tmp/bot.pid && kill -0 $(cat /tmp/bot.pid) || exit 1

CMD ["/app/start.sh"]
