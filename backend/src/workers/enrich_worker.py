"""Enrich worker — обогащение дебитора из ЕГРЮЛ/ФССП/КАД."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config import get_settings
from src.connectors.enrich import enrich_party
from src.models.entities import Party
from src.models.enums import PartyRole

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)
settings = get_settings()

engine = create_async_engine(settings.database_url)
Session = async_sessionmaker(engine, expire_on_commit=False)


async def run_enrich(batch_size: int = 50) -> int:
    """Обогащает партию дебиторов, которых давно не обогащали."""
    logger.info("enrich: starting")

    cutoff = datetime.now(timezone.utc)
    async with Session() as session:
        # Берём дебиторов, у которых source_as_of старше суток
        stmt = (
            select(Party)
            .where(Party.role == PartyRole.DEBTOR.value)
            .where(Party.inn.isnot(None))
            .order_by(Party.source_as_of.asc().nulls_first())
            .limit(batch_size)
        )
        result = await session.execute(stmt)
        debtors = result.scalars().all()

        for debtor in debtors:
            try:
                await enrich_party(debtor, session)
                debtor.source_as_of = datetime.now(timezone.utc)
                await session.commit()
                logger.info("enrich: %s done", debtor.inn)
            except Exception:
                logger.exception("enrich: failed for %s", debtor.inn)
                await session.rollback()

        logger.info("enrich: %d debtors processed", len(debtors))
        return len(debters)


async def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    while True:
        try:
            await run_enrich()
        except Exception:
            logger.exception("enrich: failed")
        await asyncio.sleep(settings.enrich_interval_minutes * 60)


if __name__ == "__main__":
    asyncio.run(main())
