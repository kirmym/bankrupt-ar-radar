# ── Stage 1: сборка фронтенда ────────────────────────────────────────────────
FROM node:20-alpine AS webbuilder

WORKDIR /web
COPY web/package*.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

# ── Stage 2: backend + статика ───────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:0.12.7 AS uv

FROM python:3.12-slim

WORKDIR /app

# OCR runtime for scanned PDF evidence (bounded by the Python parser).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /bin/

# Создаём непривилегированного пользователя до установки Python-пакетов.
# Все зафиксированные зависимости поставляются manylinux-wheel'ами, поэтому
# компилятор и libpq-dev в runtime-образе не нужны.
RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /home/app \
    && chown app:app /app /home/app
ENV HOME=/home/app \
    UV_CACHE_DIR=/home/app/.cache/uv
USER app

# Lockfile is part of the production artifact: CI and deployed code receive
# the same dependency graph.
COPY --chown=app:app backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=app:app backend/src ./src
RUN uv sync --frozen --no-dev

# Остальное (миграции, тесты)
COPY --chown=app:app backend/alembic ./alembic
COPY --chown=app:app backend/alembic.ini ./
COPY --chown=app:app backend/tests ./tests

# Собранный фронтенд внутрь backend-контейнера
COPY --chown=app:app --from=webbuilder /web/dist /app/web/dist

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/app/.venv/bin:$PATH \
    WEB_DIST_DIR=/app/web/dist

EXPOSE 8000

# Alembic itself obtains a PostgreSQL advisory lock, so this remains safe
# through an overlapping rolling deployment.
CMD ["sh", "-c", "alembic upgrade head && uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
