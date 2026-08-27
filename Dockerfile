# ── Stage 1: сборка фронтенда ────────────────────────────────────────────────
FROM node:20-alpine AS webbuilder

WORKDIR /web
COPY web/package*.json ./
RUN npm ci || npm install

COPY web/ ./
RUN npm run build

# ── Stage 2: backend + статика ───────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Сначала зависимости (кэшируемый слой)
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir .

# Код backend
COPY backend/ .

# Собранный фронтенд внутрь backend-контейнера
COPY --from=webbuilder /web/dist /app/web/dist

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WEB_DIST_DIR=/app/web/dist

EXPOSE 8000

# Миграции при старте, затем API+воркеры одним процессом
CMD ["sh", "-c", "alembic upgrade head && uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
