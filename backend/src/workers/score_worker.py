"""Score worker — пересчёт EV/класса при обновлении данных."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.config import get_settings
from src.database import async_session_factory
from src.models.entities import Claim, Lot, ScoreSnapshot
from src.models.enums import Gap, PriceScheduleStatus
from src.schemas.lot import ClaimSchema, DebtorPartySchema
from src.scoring.v1 import ScoreInput, _claim_rank, compute_ev_and_class

logger = logging.getLogger(__name__)
settings = get_settings()


async def score_lot(lot_id: int, session) -> ScoreSnapshot | None:
    """Пересчитывает скоринг одного лота и сохраняет снимок."""
    stmt = (
        select(Lot)
        .where(Lot.id == lot_id)
        .options(
            selectinload(Lot.claims).selectinload(Claim.debtor_party),
            selectinload(Lot.claims).selectinload(Claim.guarantor_party),
        )
    )
    result = await session.execute(stmt)
    lot = result.scalar_one_or_none()
    if not lot:
        return None

    # Выбираем представительное требование детерминированно. Если в корзине
    # несколько дебиторов, скоринг добавит стоп-фактор MULTIPLE_DEBTORS.
    claim_schemas = [
        ClaimSchema.model_validate(c, from_attributes=True) for c in lot.claims
    ]
    primary_claim = max(claim_schemas, key=_claim_rank, default=None)
    debtor: DebtorPartySchema | None = None
    claim_debtors = [
        claim.debtor_party
        for claim in claim_schemas
        if claim.debtor_party is not None and claim.debtor_party.inn
    ]
    debtor_inns = {claim.inn for claim in claim_debtors if claim.inn}
    if len(debtor_inns) == 1:
        debtor = claim_debtors[0]
    elif primary_claim and primary_claim.debtor_party and not debtor_inns:
        debtor = primary_claim.debtor_party

    inp = ScoreInput(
        lot_id=lot.id,
        start_price=lot.start_price,
        current_price=lot.current_price,
        current_price_confirmed=(
            lot.current_price is not None
            and lot.price_schedule_status
            not in {PriceScheduleStatus.UNPARSED.value, PriceScheduleStatus.EXPIRED.value}
        ),
        cutoff_price=lot.cutoff_price,
        nominal_claimed=lot.nominal_claimed,
        is_bundle=lot.bundle_flag,
        description_text=lot.description_text,
        debtor=debtor,
        claims=claim_schemas,
        gaps=(
            [Gap.SCHEDULE_UNPARSED]
            if lot.price_schedule_status == PriceScheduleStatus.UNPARSED.value
            else []
        ),
    )

    result = compute_ev_and_class(inp)

    # Сохраняем снимок
    scored_at = datetime.now(UTC)
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
        scored_at=scored_at,
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
    # Keep this timestamp identical to ``updated_at``.  Alerts may send only
    # scores that were calculated after the last input mutation.
    lot.score_updated_at = scored_at
    lot.updated_at = scored_at

    await session.commit()
    return snapshot


async def run_rescore() -> int:
    """Пересчитывает все лоты, у которых поменялся nominal_claimed/debtor."""
    logger.info("score: starting")

    async with async_session_factory() as session:
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
