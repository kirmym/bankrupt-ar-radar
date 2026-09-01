"""CLI — управление радаром."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime

import typer
from rich.console import Console

from src.config import get_settings

app = typer.Typer(
    name="ar-radar",
    help="Радар прибыльной дебиторской задолженности",
    no_args_is_help=True,
)

console = Console()
settings = get_settings()


@app.command()
def health() -> None:
    """Проверить здоровье системы."""
    console.print(f"OK Backend (env={settings.app_env})", style="green")


@app.command()
def ingest() -> None:
    """Запустить ingest настроенных публичных источников."""
    from src.workers.ingest_worker import run_ingest

    async def _run() -> None:
        count = await run_ingest()
        console.print(f"OK Ingest: {count} lots", style="green")

    asyncio.run(_run())


@app.command()
def enrich() -> None:
    """Запустить enrich дебиторов."""
    from src.workers.enrich_worker import run_enrich

    async def _run() -> None:
        count = await run_enrich()
        console.print(f"OK Enrich: {count} debtors", style="green")

    asyncio.run(_run())


@app.command()
def score() -> None:
    """Пересчитать скоринг всех лотов."""
    from src.workers.score_worker import run_rescore

    async def _run() -> None:
        count = await run_rescore()
        console.print(f"OK Score: {count} lots", style="green")

    asyncio.run(_run())


@app.command("files")
def files() -> None:
    """Download and parse one bounded batch of public lot documents."""
    from src.workers.files_worker import run_files

    async def _run() -> None:
        count = await run_files(batch_size=settings.files_batch_size)
        console.print(f"OK Files: {count} documents completed/reused", style="green")

    asyncio.run(_run())


@app.command("prototype-run")
def prototype_run() -> None:
    """Run one bounded end-to-end cycle and print a machine-readable report."""
    async def _run() -> None:
        from sqlalchemy import func, select

        from src.api.main import _participation_clause, _ready_clause, _review_clause
        from src.database import async_session_factory
        from src.models.entities import Document, ImportRun, Lot, Trade
        from src.workers.enrich_worker import run_enrich
        from src.workers.files_worker import run_files
        from src.workers.ingest_worker import run_ingest
        from src.workers.score_worker import run_rescore

        report: dict[str, object] = {}
        report["ingest"] = await run_ingest()
        file_runs = 0
        for _ in range(max(1, int(settings.prototype_files_cycles))):
            file_runs += await run_files(batch_size=settings.files_batch_size)
        report["files_completed"] = file_runs
        report["enrich"] = await run_enrich()
        report["score"] = await run_rescore()

        now = datetime.now(UTC)
        async with async_session_factory() as session:
            active_base = (
                select(func.count(Lot.id))
                .join(Trade, Lot.trade_id == Trade.id)
                .where(Lot.is_receivable == True)  # noqa: E712
                .where(_participation_clause(now))
            )
            report["active_lots"] = int(await session.scalar(active_base) or 0)
            report["review_candidates"] = int(
                await session.scalar(active_base.where(_review_clause(now))) or 0
            )
            report["ready_recommendations"] = int(
                await session.scalar(active_base.where(_ready_clause(now))) or 0
            )
            document_counts = (
                await session.execute(
                    select(
                        func.count(Document.id),
                        func.count(Document.id).filter(Document.processing_status == "completed"),
                        func.count(Document.id).filter(Document.processing_status == "pending"),
                        func.count(Document.id).filter(Document.processing_status == "needs_review"),
                        func.count(Document.id).filter(Document.processing_status == "retrying"),
                    )
                )
            ).one()
            report["documents"] = {
                "total": int(document_counts[0]),
                "completed": int(document_counts[1]),
                "pending": int(document_counts[2]),
                "needs_review": int(document_counts[3]),
                "retrying": int(document_counts[4]),
            }
            report["source_status"] = (
                await session.scalar(
                    select(ImportRun.status)
                    .where(ImportRun.source == settings.primary_ingest_source)
                    .order_by(ImportRun.started_at.desc())
                    .limit(1)
                )
                or "unknown"
            )
            rows = (
                await session.execute(
                    select(Lot.id, Lot.title, Lot.score_class, Lot.score_ev, Trade.applications_to)
                    .join(Trade, Lot.trade_id == Trade.id)
                    .where(Lot.is_receivable == True)  # noqa: E712
                    .where(_participation_clause(now))
                    .where(_review_clause(now))
                    .order_by(Trade.applications_to.asc(), Lot.score_ev.desc())
                    .limit(10)
                )
            ).all()
            report["review_top10"] = [
                {
                    "lot_id": row[0],
                    "title": row[1],
                    "score_class": row[2],
                    "score_ev": row[3],
                    "applications_to": row[4],
                }
                for row in rows
            ]
        console.print_json(json.dumps(report, default=str, ensure_ascii=False))

    asyncio.run(_run())


@app.command()
def etp_refresh() -> None:
    """Обновить цену и статус лотов с поддержанных ЭТП."""
    from src.workers.etp_worker import run_etp_refresh

    async def _run() -> None:
        count = await run_etp_refresh()
        console.print(f"OK ETP refresh: {count} lots", style="green")

    asyncio.run(_run())


@app.command()
def workers() -> None:
    """Запустить singleton-планировщик фоновых воркеров."""
    from src.runtime import run_worker_scheduler

    asyncio.run(run_worker_scheduler())


@app.command()
def init_db() -> None:
    """Создать таблицы (для dev)."""
    from src.database import Base, engine
    from src.models import entities  # noqa: F401

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        console.print("OK Tables created", style="green")

    asyncio.run(_run())


def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    app()
    sys.exit(0)


if __name__ == "__main__":
    main()
