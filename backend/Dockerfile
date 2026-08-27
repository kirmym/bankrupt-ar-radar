FROM python:3.12-slim

WORKDIR /app

# Build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

COPY backend/pyproject.toml backend/
RUN uv sync --frozen --no-install-project

COPY backend/ /app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
