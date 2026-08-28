# Bankrupt AR Radar

**Радар прибыльной дебиторской задолженности на торгах по банкротству РФ.**

Мониторит публичные предложения на ЕФРСБ, извлекает дебитора из текста лота, обогащает данными из открытых реестров, считает EV (ожидаемую прибыль) и шлёт алерты в Telegram.

- 🟢 **Класс A** — высокая вероятность взыскания, EV > 500 000 ₽
- 🟡 **Класс B** — средняя вероятность, EV > 100 000 ₽
- 🟠 **Класс C** — низкая, EV > 0 (только watchlist)
- 🔴 **Класс D** — стоп-фактор, покупать запрещено

## Деплой на Railway (основной способ)

Весь стек — **один сервис**: FastAPI отдаёт API, собранный фронтенд и крутит фоновые воркеры (ingest/enrich/score/files/alerts) в одном процессе. Redis не нужен.

### Пошагово

1. Зайдите на [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo** → выберите `bankrupt-ar-radar`.
2. Railway соберёт Docker-образ из `Dockerfile` (фронтенд собирается внутри образа) и запустит `railway.json`.
3. В сервисе нажмите **+ New → Database → PostgreSQL** — Railway сам создаст БД.
4. Свяжите БД с сервисом: в переменных сервиса добавьте ссылку на `DATABASE_URL` из плагина Postgres (кнопка `${{Postgres.DATABASE_URL}}`).
5. Допишите переменные окружения:

   | Переменная | Значение |
   |---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
   | `APP_ENV` | `production` |
   | `ENABLE_WORKERS` | `true` |
   | `ENABLE_BOT` | `false` |
   | `TELEGRAM_BOT_TOKEN` | токен от @BotFather (для алертов) |
   | `TELEGRAM_CHAT_IDS` | ваш chat_id |
   | `API_AUTH_TOKEN` | секрет `X-API-Key` для feedback и диагностики |
   | `CLOAKBROWSER_CDP_URL` | CDP endpoint запущенного профиля CloakBrowser для challenge fallback |
   | `CLOAKBROWSER_WAIT_SECONDS` | время ожидания ручного прохождения проверки |
   | `FREE_API_SOURCES` | только подтверждённые бесплатные API, например `fssp`; пусто отключает API |

6. **Settings → Networking → Generate Domain** — получите публичный URL.
7. Пуш в `main` → авто-редеплой.

### Проверка после деплоя

- `https://<ваш-домен>/health` → `{"status": "ok"}`
- `https://<ваш-домен>/ready` → проверка доступности PostgreSQL
- `https://<ваш-домен>/` → веб-дашборд
- `https://<ваш-домен>/docs` → Swagger API

### Важно: геоблокировка реестров

Railway — зарубежный хостинг. Российские госреестры (kad.arbitr.ru, egrul.nalog.ru, fssp.gov.ru) могут блокировать иностранные IP. Enrich-воркер спроектирован отказоустойчиво: недоступность источника не ломает радар, в карточке появится пробел (`gaps`). Если геоблок подтвердится — переносим на RU-VPS (docker-compose из репо поднимается как есть) либо добавляем HTTP-прокси.

## Локальный запуск

```bash
# 1. Инфраструктура (только Postgres)
docker compose up -d postgres

# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp ../.env.example ../.env   # при необходимости поправить DATABASE_URL
alembic upgrade head
uvicorn src.api.main:app --reload --port 8000
# фоновые воркеры стартуют сами внутри API-процесса

# 3. Веб (dev-режим с HMR)
cd ../web
npm install
npm run dev            # http://localhost:5173 (проксирует /api на :8000)

# Прод-сборка фронта без Vite (статика из FastAPI):
# npm run build && cd .. && docker compose up --build app
```

## Доменная модель

Подробно — в [`docs/plan-bankrupt-ar-radar.md`](docs/plan-bankrupt-ar-radar.md).
Что считаем ликвидным — в [`docs/likvidnaya-debitorskaya-zadolzhennost.md`](docs/likvidnaya-debitorskaya-zadolzhennost.md).

Короткая суть:

```text
EV = номинал × доля_взыскания × P(успех)
     − цена_текущего_интервала
     − расходы_взыскания
     − стоимость_времени
```

Три фильтра до ставки: **(А) юридическая сила требования**, **(Б) источник денег у дебитора**, **(В) экономика после расходов**.

## Стек

| Слой | Технологии |
|---|---|
| API | FastAPI + Pydantic v2 (Python 3.12) |
| ORM / DB | SQLAlchemy 2.0 async + PostgreSQL 16 + Alembic |
| Парсинг | httpx + selectolax |
| Скоринг | чистые функции в `backend/src/scoring/v1.py` |
| LLM (опц.) | OpenAI (gpt-4o-mini) |
| Бот | aiogram 3 (опциональный отдельный сервис `bot/`) |
| Фронтенд | React 18 + Vite + Tailwind + TypeScript |
| Деплой | Docker + Railway (`railway.json`), один сервис |

## Структура репозитория

```
bankrupt-ar-radar/
├── backend/                  # Python 3.12
│   ├── src/
│   │   ├── api/              # FastAPI: REST + SPA-статика + lifespan
│   │   ├── connectors/       # ЕФРСБ, ЭТП (ЦДТ, Сбер-АСТ), файлы, LLM
│   │   ├── models/           # SQLAlchemy ORM + enum'ы
│   │   ├── schemas/          # Pydantic
│   │   ├── scoring/          # Чистые функции EV
│   │   ├── workers/          # ingest / enrich / score / files / alerts
│   │   ├── runtime.py        # фоновые задачи одного процесса
│   │   ├── telegram.py       # алерты через Bot API (httpx)
│   │   ├── cli.py            # Typer CLI
│   │   ├── config.py
│   │   └── database.py
│   ├── alembic/              # миграции
│   └── tests/                # pytest (38 кейсов)
├── bot/                      # интерактивный бот (опциональный сервис)
├── web/                      # React 18 + Vite
├── docs/                     # доменная документация
├── Dockerfile                # web build внутри → один образ
├── railway.json              # деплой Railway
├── docker-compose.yml        # локально: app + postgres
└── Makefile
```

## CLI

```bash
cd backend
ar-radar health    # проверка конфига
ar-radar ingest    # однократный ingest ЕФРСБ
ar-radar enrich    # обогатить дебиторов
ar-radar score     # пересчитать скоринг
ar-radar init-db   # создать таблицы (dev, без alembic)
```

## API

| Endpoint | Метод | Описание |
|---|---|---|
| `/health` | GET | Проверка |
| `/api/v1/lots` | GET | Лента лотов с фильтрами |
| `/api/v1/lots/{id}` | GET | Карточка лота |
| `/api/v1/stats` | GET | Дашборд-агрегаты |
| `/api/v1/ingest/status` | GET | Последний запуск ingest и checkpoint (с `X-API-Key`) |
| `/api/v1/feedback` | POST | Действие пользователя (watch/reject/bought) |
| `/docs` | GET | Swagger UI |

Фильтры `/api/v1/lots`: `score_class` (A/B/C/D), `min_ev`/`max_ev`, `debtor_inn`, `search`, `trade_status`, `deadline_before`, `page`/`page_size`.

## Telegram-алерты

При заполненных `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_IDS` alert-воркер раз в 30 минут проверяет лоты класса A/B с EV > 0 и шлёт в Telegram (дедупликация — один лот не чаще раза в сутки, состояние в таблице `alerts_state`). Внешнему Telegram API передаются финансовые показатели лота, без имени и ИНН дебитора.

Интерактивный бот (`/top`, `/start`) — опциональный отдельный сервис из `bot/` (можно задеплоить вторым сервисом с тем же репо, root `bot/Dockerfile`).

## CloakBrowser fallback

Обычный HTTP-парсер используется первым. При `401`, `403` или `429` ЕФРСБ и поддержанные ЭТП помечаются как challenge и, если задан `CLOAKBROWSER_CDP_URL`, повторяются через уже запущенный профиль CloakBrowser по CDP. Профиль должен быть доступен воркеру и может потребовать ручного прохождения капчи. Проект не решает капчи автоматически и не подменяет домены или allowlist источников. Для включения установите опциональную зависимость `playwright` в окружении воркера и задайте endpoint CDP.

## Источники данных

| Источник | Что | Статус |
|---|---|---|
| ЕФРСБ (публичный HTML) | торги публичного предложения | ✅ разрешённый парсер; API не требуется; CloakBrowser fallback при challenge |
| ЕГРЮЛ/ЕГРИП | статус, директор, ОГРН | ⚙️ только при явном разрешении в `FREE_API_SOURCES` |
| ГИР БО (bo.nalog.ru) | выручка, чистые активы | 🚧 v0 |
| КАД (kad.arbitr.ru) | дела, банкротство дебитора | ⚙️ только при явном разрешении в `FREE_API_SOURCES` |
| ФССП (api-ip.fssprus.ru) | исполнительные производства | ⚙️ только при явном разрешении в `FREE_API_SOURCES` |
| ЭТП ЦДТ, Сбербанк-АСТ | цена, deadline, файлы | ✅ (этап 2) |
| OpenAI | LLM-факты из PDF | ✅ (опция) |

## Тесты

```bash
cd backend
pytest        # backend regression suite
ruff check src/ tests/
```

## Дисклеймер

Материал носит **справочный** характер. Не является юридической консультацией или гарантией прибыли. Покупка прав требования на торгах по банкротству — рискованная операция; due diligence по конкретному лоту остаётся за специалистом.

## Дорожная карта

- Этап 3 — калибровка скоринга на реальных взысканиях
- REST ЕФРСБ (после подписания договора)
- Больше ЭТП-адаптеров (Фабрикант)
- LLM-сценарии: шаблон письма АУ, прогноз дисконта

## Лицензия

MIT
