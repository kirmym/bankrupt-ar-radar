# Changelog

## 0.2.1 (2026-08-28) — ingest reliability

- Parsed public-offer price intervals and persisted the current step for scoring and alerts.
- Made debtor INN extraction deterministic and role-aware.
- Added optional CloakBrowser CDP fallback for source pages, ETP file listings, and challenged documents.
- Invalid PDF payloads now fall back cleanly instead of breaking the files worker.

## 0.2.0 (2026-08-27) — Railway single-process deploy

### Деплой
- Один сервис вместо восьми: API + SPA-статика + воркеры (ingest/enrich/
  score/files/alerts) в процессе FastAPI через lifespan
- Root `Dockerfile`: multi-stage, сборка Vite-фронта внутри образа,
  статика раздаётся из FastAPI (`WEB_DIST_DIR`)
- `railway.json`: Dockerfile builder, healthcheck `/health`, миграции
  при старте, `uvicorn --port $PORT`
- Redis удалён из стека (очередь реально не использовалась)
- `docker-compose.yml` упрощён до `app` + `postgres`

### Backend
- `config.async_database_url`: postgres:// → postgresql+asyncpg://
  (Railway отдаёт postgres://)
- Флаги `ENABLE_WORKERS` / `ENABLE_BOT`, `WEB_DIST_DIR`
- `runtime.py`: воркеры как asyncio-задачи с общим интервалом-лупом
- `telegram.py`: алерты через Bot API напрямую (httpx, без aiogram)
- `alert_worker.py`: алерты по классам A/B с EV > 0, дедупликация
  в новой таблице `alerts_state` (миграция 0002)
- API: `list_lots` переведён на явные Query-параметры + selectinload
  (исправлен lazy-loading в async-сессии), `get_lot` грузит все связи,
  SPA-fallback не перехватывает /api, /docs, /health
- Исправлен баг enrich_worker (`debters` → `debtors`), общий engine
  из `src.database` во всех воркерах
- `ClaimSchema`: добавлены `counterclaim_risk`, `personal_claim`,
  `guarantor_party` (использовались скорингом, но отсутствовали в схеме)
- Расширены regex-паттерны фактов («решением арбитражного суда»,
  «без согласия должника»)

### Frontend
- Исправлены TS-ошибки сборки: `vite-env.d.ts`, default-exports страниц
- `npm run build` — зелёный

### Качество
- ruff: 0 ошибок (lint-секция перенесена в `tool.ruff.lint`)
- pytest: 38/38 зелёные

## 0.1.0 (2026-08-27) — Этапы 0–2

### Этап 0 — каркас
- Docker Compose: postgres 16, redis 7, backend, 3 workers, bot, nginx
- Backend: FastAPI, SQLAlchemy 2.0 async, Pydantic v2, Alembic
- Telegram-бот: aiogram 3 (/start /top /help)
- Web: React 18 + Vite + Tailwind (Dashboard, LotList, LotDetail)
- 9 таблиц: trades, lots, claims, parties, price_intervals, documents,
  score_snapshots, raw_snapshots, user_feedbacks

### Этап 1 — ingest + скоринг
- Парсер ЕФРСБ (HTML + ИНН-экстрактор), классификатор ДЗ
- Скоринг v1: EV, класс A–D, max_bid, стоп-факторы
- Enrich (ЕГРЮЛ, ФССП, КАД), тесты scoring + efrsb

### Этап 2 — ЭТП и файлы
- EtpAdapter + CdtAdapter + SberbankAdapter
- Файлы: PDF/DOCX/HTML → текст → regex-факты → LLM (fallback)
- files_worker, тесты files

## 0.0.1 (2026-08-27) — init
