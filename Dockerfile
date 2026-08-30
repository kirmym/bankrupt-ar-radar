# ── Stage 1: сборка фронтенда ────────────────────────────────────────────────
FROM node:20-alpine AS webbuilder

WORKDIR /web
COPY web/package*.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

# ── Stage 2: backend + статика ───────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:0.6.5 AS uv

FROM python:3.12-slim

WORKDIR /app

COPY --from=uv /uv /uvx /bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Lockfile is part of the production artifact: CI and deployed code receive
# the same dependency graph.
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/src ./src
RUN uv sync --frozen --no-dev

# Не запускать API от root внутри контейнера.
RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app
USER app

# Остальное (миграции, тесты)
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY backend/tests ./tests

# Собранный фронтенд внутрь backend-контейнера
COPY --from=webbuilder /web/dist /app/web/dist

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/app/.venv/bin:$PATH \
    WEB_DIST_DIR=/app/web/dist

EXPOSE 8000

# Alembic itself obtains a PostgreSQL advisory lock, so this remains safe
# through an overlapping rolling deployment.
CMD ["sh", "-c", "alembic upgrade head && uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
