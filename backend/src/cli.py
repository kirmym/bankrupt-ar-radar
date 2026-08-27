"""CLI — управление радаром."""
from __future__ import annotations

import asyncio
import logging
import sys

import typer
from rich.console import Console
from rich.table import Table

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
    console.print(f"✓ Backend OK (env={settings.app_env})", style="green")


@app.command()
def ingest() -> None:
    """Запустить ingest ЕФРСБ."""
    from src.workers.ingest_worker import run_ingest

    async def _run() -> None:
        count = await run_ingest()
        console.print(f"✓ Ingest: {count} lots", style="green")

    asyncio.run(_run())


@app.command()
def enrich() -> None:
    """Запустить enrich дебиторов."""
    from src.workers.enrich_worker import run_enrich

    async def _run() -> None:
        count = await run_enrich()
        console.print(f"✓ Enrich: {count} debtors", style="green")

    asyncio.run(_run())


@app.command()
def score() -> None:
    """Пересчитать скоринг всех лотов."""
    from src.workers.score_worker import run_rescore

    async def _run() -> None:
        count = await run_rescore()
        console.print(f"✓ Score: {count} lots", style="green")

    asyncio.run(_run())


@app.command()
def init_db() -> None:
    """Создать таблицы (для dev)."""
    from src.database import Base, engine
    from src.models import entities  # noqa: F401

    async def _run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        console.print("✓ Tables created", style="green")

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
