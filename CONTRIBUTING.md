# Contributing

## Code style

- Python: `ruff check` + `ruff format`, `mypy --strict` в CI
- TypeScript: ESLint + Prettier
- Коммиты: Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`)

## Локальная разработка

```bash
# pre-commit
pre-commit install

# backend
cd backend
uv sync --frozen
pytest

# web
cd web
npm install
npm run lint
```

## Структура

- `backend/src/scoring/` — **чистые функции** без I/O, без SQLAlchemy. Так их легко тестировать и калибровать.
- `backend/src/connectors/` — адаптеры к внешним источникам. Каждый изолирован.
- `backend/src/workers/` — воркеры с бизнес-логикой (ingest, enrich, score, files).
- `backend/src/api/` — FastAPI-роуты. Только сериализация + dependency injection.
- `backend/src/models/` — SQLAlchemy ORM. **Не** использовать вне API/workers.

## При добавлении нового источника

1. Создать адаптер в `backend/src/connectors/`.
2. Реализовать `fetch` / `parse` → `canonical DTO`.
3. В `ingest_worker` — вызвать адаптер, разложить в `Trade`/`Lot`/`Claim`/`Party`.
4. Добавить в `docker-compose.yml` отдельный воркер при необходимости.

## При добавлении нового скоринга

1. Новый файл в `backend/src/scoring/v2.py` (иммутабельный API).
2. Версия в `model_version` (например `v2.0`).
3. Существующий `v1.py` **не трогать** — для калибровки.
4. Тесты: `tests/test_scoring_v2.py`.
