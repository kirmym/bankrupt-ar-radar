FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

COPY bot/pyproject.toml ./pyproject.toml
COPY bot/uv.lock ./uv.lock
COPY bot/src ./src
RUN uv sync --frozen --no-dev

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["uv", "run", "python", "-m", "src.bot.main"]
