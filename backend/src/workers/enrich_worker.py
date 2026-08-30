"""Enrich worker — обогащение дебитора из ЕГРЮЛ/ФССП/КАД."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update

from src.config import get_settings
from src.connectors.enrich import enrich_party
from src.database import async_session_factory
from src.models.entities import Claim, Lot, Party
from src.models.enums import PartyRole

logger = logging.getLogger(__name__)
settings = get_settings()


def enrich_retry_at(
    now: datetime, failures: int, max_attempts: int | None = None
) -> datetime:
    """Return a bounded retry time without starving later debtors."""
    if max_attempts is not None and failures >= max(1, max_attempts):
        return now + timedelta(days=7)
    minutes = min(15 * (2 ** min(max(failures - 1, 0), 7)), 24 * 60)
    return now + timedelta(minutes=minutes)


async def run_enrich(batch_size: int = 50) -> int:
    """Обогащает партии дебиторов, которых давно не обогащали."""
    logger.info("enrich: starting")

    async with async_session_factory() as session:
        now = datetime.now(UTC)
        # A failed first page must not permanently block the rest of the
        # backlog.  ``enrich_next_retry_at`` is advanced after every attempt.
        stmt = (
            select(Party)
            .where(Party.role == PartyRole.DEBTOR.value)
            .where(Party.inn.isnot(None))
            .where(
                or_(
                    Party.source_as_of.is_(None),
                    Party.source_as_of < now - timedelta(days=1),
                )
            )
            .where(
                or_(
                    Party.enrich_next_retry_at.is_(None),
                    Party.enrich_next_retry_at <= now,
                )
            )
            .order_by(
                Party.enrich_next_retry_at.asc().nulls_first(),
                Party.source_as_of.asc().nulls_first(),
                Party.id.asc(),
            )
            .limit(batch_size)
        )
        result = await session.execute(stmt)
        debtors = result.scalars().all()

        succeeded = 0
        failed = 0
        for debtor in debtors:
            debtor_id = debtor.id
            attempt_at = datetime.now(UTC)
            try:
                statuses = await enrich_party(debtor, session)
                if any(statuses.values()):
                    # Network calls happen outside a row lock.  Once a source
                    # returns, invalidate linked scores even when EGRUL itself
                    # needs a retry; FSSP/KAD are score inputs too.
                    await session.execute(
                        update(Lot)
                        .where(Lot.claims.any(Claim.debtor_party_id == debtor.id))
                        .values(updated_at=attempt_at)
                    )
                # One timestamp is used by scoring as the identity/status
                # verification timestamp. FSSP/KAD results must not make a
                # stale EGRUL status look fresh.
                egrul_verified = bool(statuses.get("egrul", False)) and (
                    debtor.status is not None or len(debtor.inn or "") == 12
                )
                if egrul_verified:
                    updated_at = attempt_at
                    debtor.source_as_of = updated_at
                    debtor.enrich_attempted_at = updated_at
                    debtor.enrich_next_retry_at = None
                    debtor.enrich_failures = 0
                    debtor.enrich_last_error = None
                    await session.commit()
                    logger.info("enrich: %s done (%s)", debtor.inn, statuses)
                    succeeded += 1
                else:
                    failures = int(debtor.enrich_failures or 0) + 1
                    debtor.enrich_attempted_at = attempt_at
                    debtor.enrich_failures = failures
                    debtor.enrich_next_retry_at = enrich_retry_at(
                        attempt_at, failures, settings.enrich_max_attempts
                    )
                    debtor.enrich_last_error = (
                        "EGRUL verification failed; "
                        + ", ".join(f"{source}={ok}" for source, ok in statuses.items())
                    )[:500]
                    logger.warning("enrich: %s deferred (%s)", debtor.inn, statuses)
                    failed += 1
                await session.commit()
            except Exception:
                logger.exception("enrich: failed for %s", debtor.inn)
                await session.rollback()
                retried = await session.get(Party, debtor_id, with_for_update={"skip_locked": True})
                if retried is not None:
                    failures = int(retried.enrich_failures or 0) + 1
                    retried.enrich_attempted_at = attempt_at
                    retried.enrich_failures = failures
                    retried.enrich_next_retry_at = enrich_retry_at(
                        attempt_at, failures, settings.enrich_max_attempts
                    )
                    retried.enrich_last_error = "unexpected worker exception"
                    await session.commit()
                failed += 1

        logger.info(
            "enrich: selected=%d succeeded=%d deferred=%d", len(debtors), succeeded, failed
        )
        if debtors and failed == len(debtors):
            raise RuntimeError("enrich: all selected debtors failed")
        return succeeded


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
