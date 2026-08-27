# Bankrupt AR Radar

**Радар прибыльной дебиторской задолженности на торгах по банкротству РФ.**

Мониторит публичные предложения на ЕФРСБ, извлекает дебитора из текста лота, обогащает данными из открытых реестров, считает EV (ожидаемую прибыль) и шлёт алерты в Telegram.

## Что делает

- Собирает лоты **публичного предложения** (публички) по банкротству с ЕФРСБ
- Классифицирует только **права требования / дебиторскую задолженность**
- Извлекает ИНН дебитора из описания лота и файлов
- Обогащает карточку дебитора: ЕГРЮЛ, ГИР БО, КАД, ФССП, ЕФРСБ
- Скорит лот по формуле EV, классу A–D, стоп-факторам
- Показывает ленту + карточку в вебе
- Шлёт срочные алерты в Telegram

## Стек

| Компонент | Технологии |
|---|---|
| API | FastAPI (Python 3.12) |
| Workers | ARQ (Redis) |
| База | PostgreSQL 16 |
| Очередь | Redis |
| Фронтенд | React 18 + Vite + TypeScript |
| Телеграм | aiogram 3 |
| Парсеры | httpx + BeautifulSoup + lxml |
| LLM (этап 2) | OpenAI / Claude API |

## Этапы поставки

- [x] **Этап 0** — каркас, Docker, модели, CI
- [ ] **Этап 1** — ingest ЕФРСБ, ИНН-извлечение, скоринг v1, веб + Telegram
- [ ] **Этап 2** — ЭТП-адаптеры, PDF/OCR, LLM → факты, сценарии взыскания
- [ ] **Этап 3** — фидбек взысканий, калибровка скоринга

См. [`plan-bankrupt-ar-radar.md`](docs/plan-bankrupt-ar-radar.md) и
[`likvidnaya-debitorskaya-zadolzhennost.md`](docs/likvidnaya-debitorskaya-zadolzhennost.md).

## Быстрый старт

```bash
# 1. Клонировать
git clone https://github.com/kirmym/bankrupt-ar-radar.git
cd bankrupt-ar-radar

# 2. Переменные окружения
cp .env.example .env
# Заполнить .env (токены, пароли, Telegram bot token)

# 3. Поднять инфраструктуру
docker compose up -d postgres redis

# 4. Backend
cd backend
python -m venv .venv && source .venv/bin/activate  # или .venv\Scripts\activate на Windows
pip install -e .
alembic upgrade head
python -m src.api.main &
cd ..

# 5. Worker
cd backend && python -m src.workers.enrich_worker &
cd ..

# 6. Frontend
cd web && npm install && npm run dev &
cd ..

# 7. Telegram bot
cd bot && python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m src.bot.main &
```

Или одной командой:

```bash
docker compose up --build
```

## Архитектура

```
ЕФРСБ / ЭТП / файлы лота
        ↓
 нормализация лота  →  извлечение дебитора (ИНН)
        ↓
 ЕГРЮЛ, ГИР БО, КАД, ФССП, ЕФРСБ по дебитору
        ↓
 скоринг EV  →  PostgreSQL  →  веб + Telegram
```

Слои в коде:

- `backend/src/connectors/` — адаптеры на источники
- `backend/src/workers/` — очереди ingest / enrich / файлы
- `backend/src/scoring/` — чистые функции скоринга
- `backend/src/api/` — FastAPI endpoints
- `backend/src/models/` — Pydantic-модели, SQLAlchemy-сущности
- `web/` — React-фронтенд
- `bot/` — Telegram-бот

## Дисклеймер

Материал носит справочный характер. Ничего на сайте не является юридической консультацией или гарантией прибыли. Due diligence по конкретному лоту — задача специалиста.

## Лицензия

MIT
