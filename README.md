# Bankrupt AR Radar

**Радар прибыльной дебиторской задолженности на торгах по банкротству РФ.**

Мониторит публичные предложения на ЕФРСБ, извлекает дебитора из текста лота, обогащает данными из открытых реестров, считает EV (ожидаемую прибыль) и шлёт алерты в Telegram.

- 🟢 **Класс A** — высокая вероятность взыскания, EV > 500 000 ₽
- 🟡 **Класс B** — средняя вероятность, EV > 100 000 ₽
- 🟠 **Класс C** — низкая, EV > 0 (только watchlist)
- 🔴 **Класс D** — стоп-фактор, покупать запрещено

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

## Что сделано (этап 0–2)

- [x] **Этап 0** — каркас, Docker, модели, Alembic, CI-lint
- [x] **Этап 1** — ingest ЕФРСБ, ИНН-извлечение, скоринг v1, веб + Telegram
- [x] **Этап 2** — ЭТП-адаптеры (ЦДТ, Сбербанк-АСТ), PDF/файлы, LLM-извлечение фактов
- [ ] **Этап 3** — фидбек взысканий, калибровка скоринга, REST ЕФРСБ

## Стек

| Слой | Технологии |
|---|---|
| API | FastAPI 0.115 + Pydantic v2 (Python 3.12) |
| ORM / DB | SQLAlchemy 2.0 async + PostgreSQL 16 + Alembic |
| Очереди | ARQ + Redis 7 |
| Парсинг | httpx + selectolax + beautifulsoup4 |
| Скоринг | чистые функции в `backend/src/scoring/v1.py` |
| LLM (опц.) | OpenAI (gpt-4o-mini) |
| Бот | aiogram 3 |
| Фронтенд | React 18 + Vite + Tailwind CSS 3 + TypeScript |
| Инфра | Docker Compose (postgres, redis, backend, 3 workers, bot, nginx) |

## Структура репозитория

```
bankrupt-ar-radar/
├── backend/                  # Python 3.12
│   ├── src/
│   │   ├── api/              # FastAPI
│   │   ├── connectors/       # Парсеры ЕФРСБ, ЭТП, файлов, LLM
│   │   ├── models/           # SQLAlchemy ORM
│   │   ├── schemas/          # Pydantic
│   │   ├── scoring/          # Чистые функции EV
│   │   ├── workers/          # ingest / enrich / score / files
│   │   ├── cli.py            # Typer CLI
│   │   ├── config.py
│   │   └── database.py
│   ├── alembic/              # миграции
│   ├── tests/                # pytest
│   └── pyproject.toml
├── bot/                      # Telegram aiogram
│   └── src/
│       ├── bot/main.py
│       └── config.py
├── web/                      # React 18 + Vite
│   └── src/
│       ├── pages/            # Dashboard, LotList, LotDetail
│       ├── api.ts
│       └── utils.ts
├── docs/                     # доменная документация
│   ├── plan-bankrupt-ar-radar.md
│   └── likvidnaya-debitorskaya-zadolzhennost.md
├── docker-compose.yml
├── Dockerfile.backend
├── Dockerfile.bot
├── nginx.conf
├── Makefile
└── README.md
```

## Быстрый старт (Docker)

```bash
git clone https://github.com/kirmym/bankrupt-ar-radar.git
cd bankrupt-ar-radar

# 1. Заполнить переменные окружения
cp .env.example .env
$EDITOR .env

# 2. Поднять всё одной командой
docker compose up -d

# 3. Применить миграции
docker compose exec backend alembic upgrade head

# 4. Открыть
#   - API:        http://localhost:8000
#   - API docs:   http://localhost:8000/docs
#   - Web:        http://localhost:5173  (через Vite dev)
#   - Postgres:   localhost:5432 (postgres/postgres)
#   - Redis:      localhost:6379
```

## Быстрый старт (без Docker)

```bash
# 1. Поднять инфраструктуру
docker compose up -d postgres redis

# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn src.api.main:app --reload --port 8000

# 3. Workers (в отдельных терминалах)
python -m src.workers.ingest_worker
python -m src.workers.enrich_worker
python -m src.workers.score_worker
python -m src.workers.files_worker

# 4. Web
cd ../web
npm install
npm run dev

# 5. Bot
cd ../bot
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m src.bot.main
```

## CLI

```bash
# Проверить здоровье
ar-radar health

# Однократно запустить ingest
ar-radar ingest

# Обогатить дебиторов
ar-radar enrich

# Пересчитать скоринг
ar-radar score

# Инициализировать БД (только dev)
ar-radar init-db
```

## API

| Endpoint | Метод | Описание |
|---|---|---|
| `/health` | GET | Проверка |
| `/api/v1/lots` | GET | Лента лотов с фильтрами |
| `/api/v1/lots/{id}` | GET | Карточка лота |
| `/api/v1/stats` | GET | Дашборд-агрегаты |
| `/api/v1/feedback` | POST | Действие пользователя (watch/reject/bought) |
| `/docs` | GET | Swagger UI |

Фильтры `/api/v1/lots`:
- `score_class` (A/B/C/D)
- `min_ev`, `max_ev` (₽)
- `debtor_inn`
- `trade_status`, `trade_kind`
- `active_after`, `deadline_before`

## Telegram-бот

Команды:

- `/start` — приветствие
- `/top` — топ-10 лотов по EV
- `/help` — справка

Чтобы получать **срочные алерты** (класс A/B с EV > 0), добавьте chat_id в `TELEGRAM_CHAT_IDS` (через запятую).

## Скоринг (v1)

Логика — [`backend/src/scoring/v1.py`](backend/src/scoring/v1.py).

Вход: `ScoreInput(lot_id, current_price, nominal_claimed, debtor, claims)`.
Выход: `ScoreResult(class, ev, ev_low, ev_high, max_bid, scenario, stop_factors, gaps)`.

Сценарий выбирается по фактам:
- `enforcement` — есть ИЛ
- `court` — есть решение суда
- `negotiation` — только договор
- `debtor_bankruptcy` — банкротство дебитора
- `subsidiary` — расчёт на КДЛ

Стоп-факторы → всегда класс D:
- `no_debtor_inn`, `debtor_excluded`, `debtor_liquidation`
- `limitations_expired`, `il_present_expired`
- `personal_claim`, `assignment_forbidden`
- `bundle_no_detail`, `no_source_of_funds`

## Источники данных

| Источник | Что | Статус |
|---|---|---|
| ЕФРСБ (HTML / REST) | торги публичного предложения | ✅ парсер; 🔜 REST при договоре |
| ЕГРЮЛ/ЕГРИП (egrul.nic.ru) | статус, директор, ОГРН | ✅ |
| ГИР БО (bo.nalog.ru) | выручка, чистые активы, деньги | 🚧 v0 (лучше REST ФНС, платно) |
| КАД (kad.arbitr.ru) | дела, банкротство дебитора | ✅ |
| ФССП (api-ip.fssprus.ru) | исполнительные производства | ✅ |
| ЭТП ЦДТ (elektortorgi.ru) | текущая цена, deadline, файлы | ✅ (этап 2) |
| ЭТП Сбербанк-АСТ | то же | ✅ (этап 2) |
| OpenAI (gpt-4o-mini) | LLM-факты из PDF | ✅ (этап 2, опц.) |

## Тесты

```bash
cd backend
pytest                                    # все
pytest tests/test_scoring.py -v           # скоринг
pytest tests/test_efrsb.py -v             # ИНН-экстрактор
pytest tests/test_files.py -v             # парсер файлов
```

Покрытие: скоринг (12 кейсов), ИНН (10), факты из файлов (11).

## Дисклеймер

Материал носит **справочный** характер. Ничего на сайте не является юридической консультацией или гарантией прибыли. Покупка прав требования на торгах по банкротству — рискованная операция; due diligence по конкретному лоту остаётся за специалистом.

## Дорожная карта

Сейчас в работе:

- Этап 3 — калибровка скоринга на реальных взысканиях
- REST ЕФРСБ (после подписания договора)
- Уведомления в Telegram по cron (каждые 15 мин) с дедупликацией
- Больше ЭТП-адаптеров (Фабрикант, Аукционный тендерный центр)
- LLM-сценарии: шаблон письма АУ, прогноз «какой дисконт предложат»

## Лицензия

MIT
