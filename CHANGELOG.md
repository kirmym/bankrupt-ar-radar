# Changelog

## 0.1.0 (2026-08-27) — Этапы 0–2

### Этап 0 — каркас
- Docker Compose: postgres 16, redis 7, backend, 3 workers, bot, nginx
- Backend: FastAPI 0.115, SQLAlchemy 2.0 async, Pydantic v2, Alembic
- Telegram-бот: aiogram 3 (команды /start /top /help)
- Web: React 18 + Vite + Tailwind, страницы Dashboard, LotList, LotDetail
- 9 таблиц в БД: trades, lots, claims, parties, price_intervals,
  documents, score_snapshots, raw_snapshots, user_feedbacks
- Makefile, .pre-commit-config, .env.example

### Этап 1 — ingest + скоринг
- Парсер ЕФРСБ (HTML + ИНН-экстрактор)
- Классификатор ДЗ (коды + ключевые слова)
- Скоринг v1: EV-формула, класс A–D, max_bid, 12 стоп-факторов
- Enrich-воркер (ЕГРЮЛ, ФССП, КАД)
- Тесты: 22 кейса (scoring + efrsb)

### Этап 2 — ЭТП и файлы
- EtpAdapter (abstract) + CdtAdapter (elektortorgi.ru) +
  SberbankAdapter (sberbank-ast.ru)
- Парсер файлов: PDF/DOCX/HTML, regex-извлечение ИНН/ОГРН/дат/сумм
- LLM-извлечение фактов (OpenAI, fallback на regex)
- files_worker: скачивание, sha256, JSONB-факты
- Тесты: 11 кейсов (files)

## 0.0.1 (2026-08-27) — init

- Начальная структура репозитория
