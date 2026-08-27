.PHONY: help install dev prod migrate db-reset test lint clean logs

help:
	@echo "Bankrupt AR Radar — Make targets"
	@echo "  install      — pip install + npm install"
	@echo "  dev          — запуск dev-окружения (app + postgres)"
	@echo "  prod         — docker compose up --build"
	@echo "  migrate      — alembic upgrade head"
	@echo "  db-reset     — alembic downgrade base && alembic upgrade head"
	@echo "  test         — pytest"
	@echo "  lint         — ruff check"
	@echo "  clean        — remove venv/node_modules/pycache"
	@echo "  logs         — docker compose logs -f app"

install:
	cd backend && pip install -e ".[dev]"
	cd web && npm install

migrate:
	cd backend && alembic upgrade head

db-reset:
	cd backend && alembic downgrade base && alembic upgrade head

test:
	cd backend && python -m pytest

lint:
	cd backend && ruff check src/ tests/

clean:
	find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find backend -name "*.pyc" -delete 2>/dev/null || true
	find web -type d -name node_modules -exec rm -rf {} + 2>/dev/null || true
	find web -type d -name dist -exec rm -rf {} + 2>/dev/null || true
	find web -type d -name .vite -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.venv bot/.venv 2>/dev/null || true

logs:
	docker compose logs -f app

# Локальный запуск: один контейнер (API + воркеры + статика) + Postgres
dev:
	docker compose up --build -d
	docker compose logs -f app

prod:
	docker compose -f docker-compose.yml up --build -d
