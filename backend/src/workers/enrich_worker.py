"""Enrich worker — обогащение дебитора из ЕГРЮЛ/ФССП/КАД."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from src.config import get_settings
from src.connectors.enrich import enrich_party
from src.database import async_session_factory
from src.models.entities import Party
from src.models.enums import PartyRole

logger = logging.getLogger(__name__)
settings = get_settings()


async def run_enrich(batch_size: int = 50) -> int:
    """Обогащает партии дебиторов, которых давно не обогащали."""
    logger.info("enrich: starting")

    async with async_session_factory() as session:
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
                debtor.source_as_of = datetime.now(UTC)
                await session.commit()
                logger.info("enrich: %s done", debtor.inn)
            except Exception:
                logger.exception("enrich: failed for %s", debtor.inn)
                await session.rollback()

        logger.info("enrich: %d debtors processed", len(debtors))
        return len(debtors)


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
