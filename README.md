# Bankrupt AR Radar

**Радар прибыльной дебиторской задолженности на торгах по банкротству РФ.**

Получает публичные предложения из бесплатного публичного JSON ЦДТ, извлекает дебитора из текста лота, обогащает данными из открытых реестров, считает EV (ожидаемую прибыль) и шлёт алерты в Telegram. ЕФРСБ остаётся дополнительным источником и не блокирует импорт при своей недоступности.

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
   | `BOT_PUBLIC` | `false` (включать только при сознательно публичном боте) |
   | `TELEGRAM_BOT_TOKEN` | токен от @BotFather (для алертов) |
   | `TELEGRAM_CHAT_IDS` | ваш chat_id |
   | `API_AUTH_TOKEN` | секрет `X-API-Key` для feedback и диагностики |
   | `CLOAKBROWSER_CDP_URL` | CDP endpoint запущенного профиля CloakBrowser для challenge fallback |
   | `CLOAKBROWSER_PROXY_URL` | HTTP(S)/SOCKS proxy профиля CloakBrowser; задаётся до запуска профиля |
   | `CLOAKBROWSER_WAIT_SECONDS` | время ожидания ручного прохождения проверки |
   | `SOURCE_PROXY_URL` | необязательный HTTP(S)-прокси только для публичных source-запросов; Telegram/OpenAI не проксируются |
   | `FREE_API_SOURCES` | только документально подтверждённые бесплатные production API; сейчас оставлять пустым |
   | `INGEST_SOURCES` | источники первичного импорта через запятую; по умолчанию `cdt`, опционально `cdt,efrsb` |
   | `CDT_INGEST_MAX_ITEMS` | лимит карточек ЦДТ за один запуск; по умолчанию `250` |

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
pwsh ./scripts/dev-up.ps1

# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
Copy-Item ../.env.example ../.env -ErrorAction SilentlyContinue   # PowerShell; существующий файл сначала проверьте вручную
alembic upgrade head
uvicorn src.api.main:app --reload --port 8000
# фоновые воркеры стартуют сами внутри API-процесса

# 3. Веб (dev-режим с HMR)
cd ../web
npm install
npm run dev            # http://localhost:5173 (проксирует /api на :8000)

# Прод-сборка фронта через Vite, затем раздача статики из FastAPI:
# npm run build && cd .. && docker compose up --build app

# Полный smoke-тест Docker + PostgreSQL + API:
pwsh ./scripts/smoke.ps1
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
│   └── tests/                # pytest regression suite
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
ar-radar ingest    # однократный ingest настроенных источников
ar-radar enrich    # обогатить дебиторов
ar-radar score     # пересчитать скоринг
ar-radar init-db   # создать таблицы (dev, без alembic)
```

## API

| Endpoint | Метод | Описание |
|---|---|---|
| `/health` | GET | Проверка |
| `/api/v1/lots` | GET | Лента лотов с фильтрами (в production с `X-API-Key`) |
| `/api/v1/lots/{id}` | GET | Карточка лота с `source_refs`, source checks и tri-state фактами (в production с `X-API-Key`) |
| `/api/v1/lots/{id}/debtor` | PUT | Ручная привязка ИНН дебитора (с `X-API-Key`) |
| `/api/v1/documents/{id}/proposal/apply` | POST | Применить подтвержденное предложение фактов (с `X-API-Key`) |
| `/api/v1/stats` | GET | Дашборд-агрегаты (в production с `X-API-Key`) |
| `/api/v1/ingest/status` | GET | Последний запуск ingest и checkpoint (с `X-API-Key`) |
| `/api/v1/workers/status` | GET | Состояние фоновых worker-циклов (с `X-API-Key`) |
| `/api/v1/feedback` | POST | Действие пользователя (watch/reject/bought) |
| `/docs` | GET | Swagger UI |

Фильтры `/api/v1/lots`: `score_class` (A/B/C/D), `min_ev`/`max_ev`, `debtor_inn`, `search`, `trade_status`, `deadline_before`, `etp_name`, `has_debtor`, `has_court`, `price_status`, `page`/`page_size`.

## Telegram-алерты

При заполненных `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_IDS` alert-воркер раз в 30 минут проверяет лоты класса A/B с EV > 0 и шлёт в Telegram. Дедупликация хранится в `alerts_state` отдельно для каждого получателя; лоты с будущим интервалом, стоп-фактором или последним действием `reject`/`bought` не отправляются. Внешнему Telegram API передаются финансовые показатели лота, без имени и ИНН дебитора.

Интерактивный бот (`/top`, `/start`) — опциональный отдельный сервис из `bot/` (можно задеплоить вторым сервисом с тем же репо, root `bot/Dockerfile`).

## CloakBrowser fallback

Обычный HTTP-парсер используется первым. При `401`, `403`, `429`, сетевой ошибке или challenge ЕФРСБ и поддержанные ЭТП повторяются через уже запущенный профиль CloakBrowser по CDP. Профиль должен быть доступен воркеру и может потребовать ручного прохождения капчи. Проект не решает капчи автоматически и не подменяет домены или allowlist источников. HTTP-статусы 4xx/5xx и страницы `404` не маскируются под ошибку разметки. В Docker-образе клиент `playwright` устанавливается автоматически; при локальном запуске его нужно установить в окружение воркера отдельно.

Для локального автозапуска CloakBrowser (Windows Task Scheduler):

```powershell
pwsh ./scripts/register-cloakbrowser-task.ps1
# удалить задачу:
Unregister-ScheduledTask -TaskName BankruptAR-CloakBrowser -Confirm:$false
```

## Источники данных

Подробная политика транспорта, результаты живой проверки и варианты размещения collector: [docs/source-access-strategy.md](docs/source-access-strategy.md).

В карточке лота каждый внешний URL находится в `trade.source_refs`, а проверки
ЕГРЮЛ/ФССП/КАД — в `debtor_party.source_checks`. Юридические признаки имеют
значения `true`, `false` или `null` (`null` отображается как «не проверено» и
попадает в typed gap скоринга).

| Источник | Что | Статус |
|---|---|---|
| ЦДТ (`torgi.cdtrf.ru`) | активные публичные предложения | ✅ основной seed: бесплатный публичный JSON списка и карточек; текстовая перепроверка лотов |
| ЕФРСБ (legacy публичный HTML) | торги публичного предложения | ⚠️ дополнительный источник; парсер и CloakBrowser fallback готовы, текущий маршрут получает 401 |
| ЕГРЮЛ/ЕГРИП | точная идентификация, статус, директор, ОГРН | ✅ бесплатный публичный поиск ФНС; при наличии токена скачивается официальная PDF-выписка, из неё извлекаются недостоверность и предстоящее исключение; CAPTCHA/challenge — через CloakBrowser |
| ГИР БО (bo.nalog.ru) | выручка, чистые активы | 🚧 v0 |
| КАД (kad.arbitr.ru) | дела, банкротство дебитора | ⚠️ публичный HTML/CloakBrowser; документированного бесплатного production API не найдено |
| ФССП (fssp.gov.ru/iss/ip) | исполнительные производства | ⚠️ публичная форма/CloakBrowser; документированного бесплатного production API не найдено |
| Другие ЭТП: Сбербанк-АСТ, МЭТС, Альфалот, Фабрикант | цена, deadline, файлы | 🚧 следующие прямые адаптеры |
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

Подробный исполнимый план до полноценного MVP: [docs/mvp-roadmap.md](docs/mvp-roadmap.md).

- Этап 3 — калибровка скоринга на реальных взысканиях
- Прямые адаптеры крупнейших ЭТП без бесплатного API
- ЕФРСБ: публичный parser/CloakBrowser; REST только вне бесплатного MVP
- Больше ЭТП-адаптеров (Фабрикант)
- LLM-сценарии: шаблон письма АУ, прогноз дисконта

## Лицензия

MIT
