"""Optional retention for operational history tables."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from src.config import get_settings
from src.database import async_session_factory
from src.models.entities import AlertState, ImportRun, ScoreSnapshot
from src.models.enums import AlertDeliveryStatus

logger = logging.getLogger(__name__)
settings = get_settings()


async def run_retention() -> dict[str, int]:
    """Prune operational history when retention is explicitly enabled.

    Raw source snapshots are intentionally not deleted here: their retention
    needs an archival/export decision because they are audit evidence.
    """
    if settings.retention_days <= 0:
        return {"score_snapshots": 0, "alerts": 0, "import_runs": 0}

    now = datetime.now(UTC)
    score_cutoff = now - timedelta(days=max(1, settings.score_snapshot_retention_days))
    alert_cutoff = now - timedelta(days=max(1, settings.alert_retention_days))
    import_cutoff = now - timedelta(days=max(1, settings.import_run_retention_days))
    async with async_session_factory() as session:
        score_result = await session.execute(
            delete(ScoreSnapshot).where(ScoreSnapshot.scored_at < score_cutoff)
        )
        alert_result = await session.execute(
            delete(AlertState).where(
                AlertState.alerted_at < alert_cutoff,
                AlertState.status == AlertDeliveryStatus.SENT.value,
            )
        )
        import_result = await session.execute(
            delete(ImportRun).where(ImportRun.finished_at.is_not(None), ImportRun.finished_at < import_cutoff)
        )
        await session.commit()
    result = {
        "score_snapshots": int(getattr(score_result, "rowcount", 0) or 0),
        "alerts": int(getattr(alert_result, "rowcount", 0) or 0),
        "import_runs": int(getattr(import_result, "rowcount", 0) or 0),
    }
    logger.info("retention: %s", result)
    return result
