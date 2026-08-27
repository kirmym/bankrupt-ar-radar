.PHONY: help install dev prod migrate db-reset test lint clean logs

help:
	@echo "Bankrupt AR Radar — Make targets"
	@echo "  install      — pip install + npm install"
	@echo "  dev          — запуск dev-окружения (backend + redis + postgres)"
	@echo "  prod         — docker compose up"
	@echo "  migrate      — alembic upgrade head"
	@echo "  db-reset     — alembic downgrade base && alembic upgrade head"
	@echo "  test         — pytest"
	@echo "  lint         — ruff check && mypy"
	@echo "  clean        — remove .venv, node_modules, __pycache__"
	@echo "  logs-api     — docker logs -f api"
	@echo "  logs-worker  — docker logs -f worker_enrich"

install:
	cd backend && uv sync --frozen || uv sync
	cd web && npm install
	cd bot && uv sync --frozen || uv sync

migrate:
	cd backend && alembic upgrade head

db-reset:
	cd backend && alembic downgrade base && alembic upgrade head

test:
	cd backend && python -m pytest

lint:
	cd backend && ruff check src/ && ruff check tests/ && mypy src/

clean:
	find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find backend -name "*.pyc" -delete 2>/dev/null || true
	find web -type d -name node_modules -exec rm -rf {} + 2>/dev/null || true
	find web -type d -name dist -exec rm -rf {} + 2>/dev/null || true
	find web -type d -name .vite -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.venv bot/.venv 2>/dev/null || true

logs-api:
	docker compose logs -f api

logs-worker:
	docker compose logs -f worker_enrich worker_ingest worker_score

# Локальный запуск без Docker
dev:
	docker compose up -d postgres redis
	cd backend && alembic upgrade head
	cd backend && uvicorn src.api.main:app --reload --port 8000 &
	cd backend && python -m src.workers.ingest_worker &
	cd backend && python -m src.workers.enrich_worker &
	cd backend && python -m src.workers.score_worker &
	cd web && npm run dev

prod:
	docker compose -f docker-compose.yml up --build -d
