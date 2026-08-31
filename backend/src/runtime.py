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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from src.config import get_settings
from src.database import engine

logger = logging.getLogger(__name__)
settings = get_settings()

# In-memory worker telemetry is intentionally process-local: the PostgreSQL
# advisory lock already selects one scheduler leader, and this endpoint is a
# liveness/diagnostic view rather than durable business data.
_WORKER_STATES: dict[str, dict[str, Any]] = {}


def worker_status_snapshot() -> dict[str, dict[str, Any]]:
    """Return a copy of the current scheduler state without live references."""
    return {name: dict(state) for name, state in _WORKER_STATES.items()}


def _mark_worker(name: str, **values: Any) -> None:
    state = _WORKER_STATES.setdefault(
        name,
        {
            "status": "starting",
            "consecutive_failures": 0,
        },
    )
    state.update(values)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _loop(
    name: str,
    coro_fn: Callable[[], Awaitable[None]],
    interval_minutes: float,
    startup_gate: asyncio.Event | None = None,
    wake_event: asyncio.Event | None = None,
) -> None:
    """Бесконечный цикл воркера: запустить, подождать, повторить."""
    delay_sec = max(60.0, interval_minutes * 60)
    _mark_worker(name, status="waiting", interval_minutes=interval_minutes)
    if startup_gate is not None:
        await startup_gate.wait()
    logger.info("runtime: %s loop started (interval %.0f min)", name, interval_minutes)
    while True:
        started = time.monotonic()
        if wake_event is not None:
            # Clear before entering the worker so a signal received while the
            # worker is running is retained and wakes the next iteration.
            wake_event.clear()
        _mark_worker(name, status="running", last_started_at=_utc_now_iso())
        try:
            await coro_fn()
        except asyncio.CancelledError:
            _mark_worker(name, status="stopped", last_finished_at=_utc_now_iso())
            logger.info("runtime: %s loop cancelled", name)
            raise
        except Exception:
            _mark_worker(
                name,
                status="error",
                last_finished_at=_utc_now_iso(),
                last_error="worker iteration failed",
                consecutive_failures=int(_WORKER_STATES[name].get("consecutive_failures", 0)) + 1,
            )
            logger.exception("runtime: %s iteration failed", name)
        else:
            _mark_worker(
                name,
                status="waiting",
                last_finished_at=_utc_now_iso(),
                last_error=None,
                consecutive_failures=0,
            )
        elapsed = time.monotonic() - started
        _mark_worker(name, last_duration_ms=int(elapsed * 1000))
        sleep_for = max(5.0, delay_sec - elapsed)
        if wake_event is None:
            await asyncio.sleep(sleep_for)
            continue
        try:
            await asyncio.wait_for(wake_event.wait(), timeout=sleep_for)
        except TimeoutError:
            pass


async def _ingest_once(
    startup_gate: asyncio.Event | None = None,
    score_requested: asyncio.Event | None = None,
) -> None:
    from src.workers.ingest_worker import run_ingest

    await run_ingest()
    # Do not release dependent workers after a failed first import.  They
    # would otherwise score and alert stale data before source access recovers.
    if startup_gate is not None:
        startup_gate.set()
    if score_requested is not None:
        score_requested.set()


async def _enrich_once(
    ready_gate: asyncio.Event | None = None,
    score_requested: asyncio.Event | None = None,
) -> None:
    from src.workers.enrich_worker import run_enrich

    try:
        await run_enrich()
    finally:
        # A failed enrichment is represented by gaps and retry metadata. It
        # must not deadlock scoring forever; the scorer can safely produce a
        # conservative result and the alert query rejects stale/blocked lots.
        if ready_gate is not None:
            ready_gate.set()
        if score_requested is not None:
            score_requested.set()


async def _score_once(
    ready_gate: asyncio.Event | None = None,
    alert_requested: asyncio.Event | None = None,
) -> None:
    from src.workers.score_worker import run_rescore

    try:
        await run_rescore()
    finally:
        if ready_gate is not None:
            ready_gate.set()
        if alert_requested is not None:
            alert_requested.set()


async def _files_once() -> None:
    from src.workers.files_worker import run_files

    await run_files()


async def _etp_once() -> None:
    from src.workers.etp_worker import run_etp_refresh

    await run_etp_refresh()


async def _alerts_once() -> None:
    from src.workers.alert_worker import run_alerts

    await run_alerts()


async def _maintenance_once() -> None:
    from src.workers.maintenance_worker import run_retention

    await run_retention()


def _create_worker_tasks() -> list[asyncio.Task]:
    """Create the worker loops owned by the current scheduler leader."""
    tasks: list[asyncio.Task] = []

    ingest_ready = asyncio.Event()
    enrich_ready = asyncio.Event()
    score_ready = asyncio.Event()
    score_requested = asyncio.Event()
    alert_requested = asyncio.Event()

    async def ingest_once() -> None:
        await _ingest_once(ingest_ready, score_requested)

    tasks.append(asyncio.create_task(_loop("ingest", ingest_once, settings.ingest_interval_minutes)))
    async def enrich_once() -> None:
        await _enrich_once(enrich_ready, score_requested)

    async def score_once() -> None:
        await _score_once(score_ready, alert_requested)

    tasks.append(
        asyncio.create_task(
            _loop("enrich", enrich_once, settings.enrich_interval_minutes, ingest_ready)
        )
    )
    tasks.append(
        asyncio.create_task(
            _loop(
                "score",
                score_once,
                settings.score_interval_minutes,
                enrich_ready,
                score_requested,
            )
        )
    )
    tasks.append(asyncio.create_task(_loop("files", _files_once, 15, ingest_ready)))
    tasks.append(
        asyncio.create_task(_loop("etp", _etp_once, settings.etp_interval_minutes, ingest_ready))
    )
    tasks.append(
        asyncio.create_task(
            _loop(
                "alerts",
                _alerts_once,
                settings.alert_interval_minutes,
                score_ready,
                alert_requested,
            )
        )
    )
    tasks.append(asyncio.create_task(_loop("retention", _maintenance_once, 24 * 60)))

    return tasks


async def run_worker_scheduler() -> None:
    """Run worker loops only while this process owns the PostgreSQL leader lock."""
    key = int(settings.worker_leader_lock_key)
    retry_delay_seconds = 30
    while True:
        tasks: list[asyncio.Task] = []
        try:
            async with engine.connect() as connection:
                acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
                if not acquired:
                    logger.info("runtime: scheduler leader lock is held by another instance")
                    await asyncio.sleep(retry_delay_seconds)
                    continue
                logger.info("runtime: scheduler leader lock acquired")
                try:
                    tasks = _create_worker_tasks()
                    await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        task.cancel()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                    try:
                        await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    except Exception:
                        logger.exception("runtime: failed to release scheduler leader lock")
        except asyncio.CancelledError:
            logger.info("runtime: scheduler cancelled")
            raise
        except Exception:
            logger.exception("runtime: scheduler leader connection failed")
            await asyncio.sleep(retry_delay_seconds)


def start_background_tasks() -> list[asyncio.Task]:
    """Start one scheduler task; it elects a PostgreSQL-backed leader."""
    return [asyncio.create_task(run_worker_scheduler())]


async def stop_background_tasks(tasks: list[asyncio.Task]) -> None:
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("runtime: background tasks stopped")
