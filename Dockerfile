FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    STATE_PATH=/app/data/.freelance_ua_seen.json

WORKDIR /app

COPY freelance_ua_notifier.py /app/freelance_ua_notifier.py
COPY config.example.json /app/config.example.json

RUN mkdir -p /app/data

CMD ["python", "/app/freelance_ua_notifier.py", "--config", "/app/config.example.json"]
