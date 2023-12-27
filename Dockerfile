# A Telegram Bot to track the GAS price through Etherscan
FROM docker.io/bitnami/python:3.11

LABEL org.opencontainers.image.authors="F." \
    org.opencontainers.image.vendor="hyp3rd" \
    org.opencontainers.image.description="A Telegram bot to track the GAS price through Etherscan" \
    org.opencontainers.image.source="https://github.com/hyp3rd/telegram-gas-tracker/" \
    org.opencontainers.image.title="Telegram GAS Tracker"

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --upgrade -r /app/requirements.txt

ENV TELEGRAM_TOKEN=""
ENV ETHERSCAN_API_KEY=""

EXPOSE 8000

HEALTHCHECK --interval=15m --timeout=60s --retries=10 \
    CMD wget --spider --no-verbose http://localhost:8000/health || exit 1

CMD ["python", "/app/bot.py"]
