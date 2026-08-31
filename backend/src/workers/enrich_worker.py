"""Enrich worker — обогащение дебитора из ЕГРЮЛ/ФССП/КАД."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update

from src.config import get_settings
from src.connectors.enrich import SourceOutcome, enrich_party
from src.database import async_session_factory
from src.models.entities import Claim, Lot, Party, PartySourceCheck
from src.models.enums import PartyRole

logger = logging.getLogger(__name__)
settings = get_settings()

SOURCE_URLS = {
    "egrul": "https://egrul.nalog.ru/",
    "fssp": "https://fssp.gov.ru/",
    "kad": "https://kad.arbitr.ru/",
}


async def record_source_checks(
    session,
    party: Party,
    statuses: Mapping[str, SourceOutcome | bool],
    checked_at: datetime,
) -> None:
    """Persist one explicit result per registry instead of overloading a flag."""
    for source, raw_outcome in statuses.items():
        outcome = (
            raw_outcome
            if isinstance(raw_outcome, SourceOutcome)
            else SourceOutcome(
                ok=bool(raw_outcome),
                state="success" if raw_outcome else "unavailable",
            )
        )
        ok = outcome.ok
        check = (
            await session.execute(
                select(PartySourceCheck).where(
                    PartySourceCheck.party_id == party.id,
                    PartySourceCheck.source == source,
                )
            )
        ).scalar_one_or_none()
        if check is None:
            check = PartySourceCheck(
                party_id=party.id,
                source=source,
                failures=0,
            )
            session.add(check)
        check.status = "success" if ok else outcome.state
        check.checked_at = checked_at
        check.source_url = SOURCE_URLS.get(source)
        if ok:
            check.failures = 0
            check.next_retry_at = None
            check.last_error = None
        else:
            check.failures = int(check.failures or 0) + 1
            check.next_retry_at = enrich_retry_at(
                checked_at, check.failures, settings.enrich_max_attempts
            )
            check.last_error = (
                outcome.error or "source returned no verified result"
            )[:500]
        check.evidence = outcome.evidence


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
                    Party.source_checks.any(
                        and_(
                            PartySourceCheck.source == "egrul",
                            or_(
                                PartySourceCheck.checked_at.is_(None),
                                PartySourceCheck.next_retry_at <= now,
                            ),
                        )
                    ),
                    Party.source_checks.any(
                        and_(
                            PartySourceCheck.source.in_(("fssp", "kad")),
                            or_(
                                PartySourceCheck.checked_at.is_(None),
                                PartySourceCheck.next_retry_at <= now,
                            ),
                        )
                    ),
                    ~Party.source_checks.any(
                        PartySourceCheck.source.in_(("egrul", "fssp", "kad"))
                    ),
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
                outcomes = await enrich_party(debtor, session)
                statuses = {source: outcome.ok for source, outcome in outcomes.items()}
                await record_source_checks(session, debtor, outcomes, attempt_at)
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
                # The public FNS search can verify an exact organization row
                # without returning lifecycle status. That identity evidence
                # is sufficient to refresh the timestamp; scoring exposes an
                # unknown status as a gap and still blocks stale identities.
                egrul_verified = bool(statuses.get("egrul", False))
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
                        + ", ".join(
                            f"{source}={outcome.state}"
                            for source, outcome in outcomes.items()
                        )
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
        # A registry outage or challenge is an expected source result, not a
        # worker crash: the per-source rows above keep it visible as a typed
        # ``unavailable`` check and the next retry remains scheduled.
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
