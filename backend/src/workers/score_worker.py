"""Score worker — пересчёт EV/класса при обновлении данных."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.entities import Claim, Lot, Party, ScoreSnapshot
from src.models.enums import PartyRole
from src.schemas.lot import ClaimSchema, DebtorPartySchema
from src.scoring.v1 import ScoreInput, compute_ev_and_class

logger = logging.getLogger(__name__)
settings = get_settings()

engine = create_async_engine(settings.database_url)
Session = async_sessionmaker(engine, expire_on_commit=False)


async def score_lot(lot_id: int, session) -> ScoreSnapshot | None:
    """Пересчитывает скоринг одного лота и сохраняет снимок."""
    stmt = select(Lot).where(Lot.id == lot_id)
    result = await session.execute(stmt)
    lot = result.scalar_one_or_none()
    if not lot:
        return None

    # Дебитор
    debtor: DebtorPartySchema | None = None
    if lot.claims:
        claim = lot.claims[0]
        if claim.debtor_party:
            p = claim.debtor_party
            debtor = DebtorPartySchema.model_validate(p)

    # Claims
    claim_schemas = []
    for c in lot.claims:
        if c.debtor_party:
            c.debtor_party = c.debtor_party  # load
        claim_schemas.append(ClaimSchema.model_validate(c))

    inp = ScoreInput(
        lot_id=lot.id,
        start_price=lot.start_price,
        current_price=lot.current_price,
        cutoff_price=lot.cutoff_price,
        nominal_claimed=lot.nominal_claimed,
        is_bundle=lot.bundle_flag,
        description_text=lot.description_text,
        debtor=debtor,
        claims=claim_schemas,
    )

    result = compute_ev_and_class(inp)

    # Сохраняем снимок
    snapshot = ScoreSnapshot(
        lot_id=lot.id,
        score_class=result.score_class.value,
        ev=result.ev,
        ev_low=result.ev_low,
        ev_high=result.ev_high,
        max_bid=result.max_bid,
        scenario=result.scenario.value,
        stop_factors=result.stop_factors,
        gaps=result.gaps,
        model_version=result.version,
        scored_at=datetime.now(timezone.utc),
    )
    session.add(snapshot)

    # Обновляем поля на лоте
    lot.score_class = result.score_class.value
    lot.score_ev = result.ev
    lot.score_ev_low = result.ev_low
    lot.score_ev_high = result.ev_high
    lot.score_scenario = result.scenario.value
    lot.score_stop_factors = [s.value for s in result.stop_factors]
    lot.score_gaps = [g.value for g in result.gaps]
    lot.score_max_bid = result.max_bid
    lot.score_version = result.version

    await session.commit()
    return snapshot


async def run_rescore() -> int:
    """Пересчитывает все лоты, у которых поменялся nominal_claimed/debtor."""
    logger.info("score: starting")

    async with Session() as session:
        stmt = select(Lot.id).where(Lot.is_receivable == True)  # noqa: E712
        result = await session.execute(stmt)
        lot_ids = result.scalars().all()

        count = 0
        for lot_id in lot_ids:
            try:
                snap = await score_lot(lot_id, session)
                if snap:
                    count += 1
            except Exception:
                logger.exception("score: failed for lot %d", lot_id)
                await session.rollback()

        logger.info("score: %d lots scored", count)
        return count


async def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    while True:
        try:
            await run_rescore()
        except Exception:
            logger.exception("score: failed")
        await asyncio.sleep(settings.score_interval_minutes * 60)


if __name__ == "__main__":
    asyncio.run(main())
