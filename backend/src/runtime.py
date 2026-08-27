"""Runtime одного процесса: фоновые задачи (воркеры) внутри API-процесса.

На Railway каждый сервис оплачивается отдельно, поэтому ingest/enrich/score/
files/alerts живут как asyncio-задачи в процессе uvicorn, а не отдельными
контейнерами. Локально можно запускать воркеры раздельно — модули остаются
самостоятельными.
"""
from __future__ import annotations

import asyncio
import logging
import time

from src.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


async def _loop(name: str, coro_fn, interval_minutes: float) -> None:
    """Бесконечный цикл воркера: запустить, подождать, повторить."""
    delay_sec = max(60.0, interval_minutes * 60)
    logger.info("runtime: %s loop started (interval %.0f min)", name, interval_minutes)
    while True:
        started = time.monotonic()
        try:
            await coro_fn()
        except asyncio.CancelledError:
            logger.info("runtime: %s loop cancelled", name)
            raise
        except Exception:
            logger.exception("runtime: %s iteration failed", name)
        elapsed = time.monotonic() - started
        sleep_for = max(5.0, delay_sec - elapsed)
        await asyncio.sleep(sleep_for)


async def _ingest_once() -> None:
    from src.workers.ingest_worker import run_ingest

    await run_ingest()


async def _enrich_once() -> None:
    from src.workers.enrich_worker import run_enrich

    await run_enrich()


async def _score_once() -> None:
    from src.workers.score_worker import run_rescore

    await run_rescore()


async def _files_once() -> None:
    from src.workers.files_worker import run_files

    await run_files()


async def _alerts_once() -> None:
    from src.workers.alert_worker import run_alerts

    await run_alerts()


def start_background_tasks() -> list[asyncio.Task]:
    """Создаёт фоновые задачи согласно флагам настроек."""
    tasks: list[asyncio.Task] = []

    tasks.append(asyncio.create_task(_loop("ingest", _ingest_once, settings.ingest_interval_minutes)))
    tasks.append(asyncio.create_task(_loop("enrich", _enrich_once, settings.enrich_interval_minutes)))
    tasks.append(asyncio.create_task(_loop("score", _score_once, settings.score_interval_minutes)))
    tasks.append(asyncio.create_task(_loop("files", _files_once, 15)))
    tasks.append(asyncio.create_task(_loop("alerts", _alerts_once, settings.alert_interval_minutes)))

    return tasks


async def stop_background_tasks(tasks: list[asyncio.Task]) -> None:
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("runtime: background tasks stopped")
