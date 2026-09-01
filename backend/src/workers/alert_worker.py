"""Alert worker — Telegram-алерты по классам A/B с EV > 0.

Дедупликация: один и тот же лот не шлём чаще, чем раз в ALERT_DEDUPE_HOURS
(состояние в таблице alerts_state, без Redis).
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from src.config import get_settings
from src.database import async_session_factory
from src.models.entities import AlertState, ImportRun, Lot, SourceAlertState, Trade, UserFeedback
from src.models.enums import (
    PARTICIPABLE_TRADE_STATUSES,
    AlertDeliveryStatus,
    LotClass,
    PriceScheduleStatus,
)
from src.telegram import fmt_lot_message, send_message

logger = logging.getLogger(__name__)
settings = get_settings()
ZERO_LOT_ALERT_TYPE = "zero_lots"


def zero_lot_window_start(now: datetime, window_hours: int = 6) -> datetime:
    """Return a stable UTC bucket used to deduplicate source health alerts."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    seconds = max(1, int(window_hours)) * 3600
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=UTC)


def source_has_zero_lot_gap(
    runs: Sequence[object],
    now: datetime,
    window_hours: int = 6,
) -> bool:
    """Detect a six-hour source silence without confusing failures and zeroes.

    A recent successful run with at least one observed item suppresses the
    alert. If no such run exists, the alert waits until the first recorded
    attempt is at least ``window_hours`` old, avoiding startup noise.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    cutoff = now - timedelta(hours=max(1, int(window_hours)))
    timestamps: list[datetime] = []
    positive: list[datetime] = []
    for run in runs:
        finished = getattr(run, "finished_at", None)
        started = getattr(run, "started_at", None)
        timestamp = finished or started
        if not isinstance(timestamp, datetime):
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        timestamp = timestamp.astimezone(UTC)
        timestamps.append(timestamp)
        # A bounded import that is currently running may still produce lots;
        # do not announce a gap until that attempt has finished or failed.
        if getattr(run, "status", None) == "running" and timestamp >= cutoff:
            return False
        if (
            getattr(run, "status", None) == "finished"
            and int(getattr(run, "items_seen", 0) or 0) > 0
        ):
            positive.append(timestamp)
    if not timestamps:
        return False
    if any(timestamp >= cutoff for timestamp in positive):
        return False
    if positive:
        return max(positive) < cutoff
    return min(timestamps) <= cutoff


def build_zero_lot_alert_message(
    source: str,
    now: datetime,
    window_hours: int,
    latest_run: object | None,
) -> str:
    """Build a concise operator message without including source payloads."""
    status = str(getattr(latest_run, "status", "unknown")) if latest_run else "no_run"
    run_at = getattr(latest_run, "finished_at", None) or getattr(latest_run, "started_at", None)
    when = run_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") if isinstance(run_at, datetime) else "—"
    return (
        f"⚠️ Источник {source} не дал лотов за последние {max(1, int(window_hours))} ч. "
        f"Последний запуск: {status}, {when}. Проверьте доступность и диагностику источника."
    )


async def reserve_source_alert(
    session,
    source: str,
    alert_type: str,
    chat_id: str,
    window_start: datetime,
) -> int | None:
    """Reserve one source-health message with a durable unique key."""
    now = datetime.now(UTC)
    existing = await session.scalar(
        select(SourceAlertState)
        .where(
            SourceAlertState.source == source,
            SourceAlertState.alert_type == alert_type,
            SourceAlertState.chat_id == chat_id,
            SourceAlertState.window_start == window_start,
        )
        .with_for_update(skip_locked=True)
    )
    if existing is not None:
        if existing.status == "sent":
            return None
        if existing.lease_until is not None and existing.lease_until > now:
            return None
        existing.status = "sending"
        existing.alerted_at = now
        existing.lease_until = now + timedelta(minutes=5)
        existing.attempts = int(existing.attempts or 0) + 1
        existing.last_error = None
        await session.commit()
        return existing.id
    row = SourceAlertState(
        source=source,
        alert_type=alert_type,
        chat_id=chat_id,
        window_start=window_start,
        status="sending",
        alerted_at=now,
        lease_until=now + timedelta(minutes=5),
        attempts=1,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return None
    return row.id


async def finish_source_alert(
    session,
    delivery_id: int,
    delivered: bool,
    error: str | None = None,
) -> None:
    row = await session.get(SourceAlertState, delivery_id, with_for_update=True)
    if row is None:
        return
    row.lease_until = None
    if delivered:
        row.status = "sent"
        row.sent_at = datetime.now(UTC)
        row.last_error = None
    else:
        row.status = "failed"
        row.last_error = (error or "Telegram rejected source alert")[:500]
    await session.commit()


async def send_zero_lot_alert(
    session,
    source: str,
    chat_ids: Sequence[str],
    *,
    now: datetime,
    window_hours: int,
) -> int:
    """Send at most one health warning per source/chat/window."""
    runs = (
        await session.execute(
            select(ImportRun)
            .where(ImportRun.source == source)
            .order_by(ImportRun.started_at.desc())
            .limit(100)
        )
    ).scalars().all()
    if not source_has_zero_lot_gap(runs, now, window_hours):
        return 0
    latest_run = runs[0] if runs else None
    message = build_zero_lot_alert_message(source, now, window_hours, latest_run)
    window_start = zero_lot_window_start(now, window_hours)
    sent = 0
    for chat_id in chat_ids:
        delivery_id = await reserve_source_alert(
            session, source, ZERO_LOT_ALERT_TYPE, chat_id, window_start
        )
        if delivery_id is None:
            continue
        delivered = await send_message(message, chat_id=chat_id)
        await finish_source_alert(
            session,
            delivery_id,
            delivered,
            None if delivered else "Telegram API returned an unsuccessful response",
        )
        if delivered:
            sent += 1
    return sent


def alert_dedupe_key(lot: Lot, chat_id: str, dedupe_hours: int, now: datetime) -> str:
    """Create a stable idempotency key for one meaningful lot revision.

    ``score_updated_at`` is deliberately excluded: a routine recalculation
    must not send the same alert again. Price, deadline and score economics
    form the revision, so a real change can produce a new event immediately.
    """
    del dedupe_hours, now
    version = getattr(lot, "score_version", None) or "unversioned"
    interval_from = getattr(lot, "current_interval_from", None)
    interval_to = getattr(lot, "current_interval_to", None)
    revision_parts = (
        str(getattr(lot, "score_class", None) or ""),
        str(getattr(lot, "score_ev", None) or ""),
        str(getattr(lot, "score_max_bid", None) or ""),
        str(getattr(lot, "current_price", None) or ""),
        interval_from.isoformat() if isinstance(interval_from, datetime) else "",
        interval_to.isoformat() if isinstance(interval_to, datetime) else "",
        ",".join(sorted(str(value) for value in (getattr(lot, "score_stop_factors", None) or []))),
        ",".join(sorted(str(value) for value in (getattr(lot, "score_gaps", None) or []))),
    )
    revision = sha256("|".join(revision_parts).encode("utf-8")).hexdigest()[:24]
    return f"lot:{lot.id}:chat:{chat_id}:score:{version}:economics:{revision}"


def alert_interval_version(lot: Lot) -> str:
    """Return the persisted revision identifier shown in the outbox."""
    return alert_dedupe_key(lot, "_", 0, datetime.now(UTC)).rsplit(":", 1)[-1]


async def reserve_alert_delivery(session, lot: Lot, chat_id: str, dedupe_hours: int) -> int | None:
    """Atomically claim an outbox item before talking to Telegram.

    A database uniqueness constraint makes concurrent workers converge on one
    row.  A lease lets a later worker recover a process that died mid-delivery.
    """
    now = datetime.now(UTC)
    key = alert_dedupe_key(lot, chat_id, dedupe_hours, now)
    cutoff = now - timedelta(hours=max(0, dedupe_hours))
    existing = await session.scalar(
        select(AlertState)
        .where(AlertState.dedupe_key == key)
        .with_for_update(skip_locked=True)
    )
    if existing is not None:
        last_activity = existing.sent_at or existing.alerted_at
        if (
            existing.status == AlertDeliveryStatus.SENT.value
            and last_activity is not None
            and last_activity >= cutoff
        ):
            return None
        if existing.lease_until is not None and existing.lease_until > now:
            return None
        existing.dedupe_key = key
        existing.alerted_at = now
        existing.status = AlertDeliveryStatus.SENDING.value
        existing.lease_until = now + timedelta(minutes=5)
        existing.attempts = int(existing.attempts or 0) + 1
        existing.last_error = None
        existing.event_type = "candidate_eligible"
        existing.interval_version = alert_interval_version(lot)
        await session.commit()
        return existing.id

    row = AlertState(
        lot_id=lot.id,
        chat_id=chat_id,
        alerted_at=now,
        dedupe_key=key,
        status=AlertDeliveryStatus.SENDING.value,
        lease_until=now + timedelta(minutes=5),
        attempts=1,
        event_type="candidate_eligible",
        interval_version=alert_interval_version(lot),
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return None
    return row.id


async def finish_alert_delivery(session, delivery_id: int, delivered: bool, error: str | None = None) -> None:
    """Persist the result of one Telegram call without reopening the race."""
    row = await session.get(AlertState, delivery_id, with_for_update=True)
    if row is None:
        return
    row.lease_until = None
    if delivered:
        row.status = AlertDeliveryStatus.SENT.value
        row.sent_at = datetime.now(UTC)
        row.last_error = None
    else:
        row.status = AlertDeliveryStatus.FAILED.value
        row.last_error = (error or "Telegram rejected delivery")[:500]
    await session.commit()


def build_alert_candidates_stmt(now: datetime, limit: int, price_freshness_hours: int = 24):
    """Build the candidate query with all freshness and auction guards."""
    return (
        select(Lot)
        .join(Trade, Lot.trade_id == Trade.id)
        .where(Lot.is_receivable == True)  # noqa: E712
        .where(Lot.score_class.in_([LotClass.A.value, LotClass.B.value]))
        .where(Lot.score_ev > 0)
        .where(func.coalesce(func.cardinality(Lot.score_gaps), 0) == 0)
        .where(Lot.current_price.is_not(None))
        .where(
            Lot.price_schedule_status.in_(
                [PriceScheduleStatus.PARSED.value]
            )
        )
        .where(Lot.price_observed_at.is_not(None))
        .where(Lot.price_observed_at >= now - timedelta(hours=price_freshness_hours))
        .where(Lot.score_updated_at.is_not(None))
        .where(Lot.score_updated_at >= Lot.updated_at)
        .where(Trade.status.in_(PARTICIPABLE_TRADE_STATUSES))
        .where(Trade.applications_to.is_not(None))
        .where(Trade.applications_to > now)
        .where(
            or_(
                Lot.current_interval_from.is_(None),
                Lot.current_interval_from <= now,
            )
        )
        .where(
            or_(
                Lot.current_interval_to.is_(None),
                Lot.current_interval_to > now,
            )
        )
        .order_by(Lot.score_ev.desc())
        .limit(limit * 3)
    )


async def run_alerts(dedupe_hours: int = 20, limit: int = 5) -> int:
    """Находит свежие лоты A/B с EV > 0 и шлёт алерты.

    Возвращает число отправленных сообщений.
    """
    if not settings.telegram_bot_token or not settings.telegram_chat_ids_list:
        logger.debug("alerts: telegram not configured, skip")
        return 0

    logger.info("alerts: starting")
    health_sent = 0
    candidate_sent = 0

    async with async_session_factory() as session:
        # Health alerts must still be evaluated when the latest ingest is
        # paused/failed; otherwise a silent source outage hides behind the
        # early return below.
        source_names = {
            {"cdt": "cdt_public", "efrsb": "efrsb_public"}.get(value, value)
            for value in settings.ingest_sources_list
        }
        if not source_names:
            source_names = {settings.primary_ingest_source}
        for source_name in sorted(source_names):
            health_sent += await send_zero_lot_alert(
                session,
                source_name,
                settings.telegram_chat_ids_list,
                now=datetime.now(UTC),
                window_hours=max(1, int(getattr(settings, "zero_lot_alert_hours", 6))),
            )
        latest_import_status = await session.scalar(
            select(ImportRun.status)
            .where(ImportRun.source == settings.primary_ingest_source)
            .order_by(ImportRun.started_at.desc())
            .limit(1)
        )
        if latest_import_status is not None and latest_import_status != "finished":
            logger.warning(
                "alerts: latest %s import is %s, skip",
                settings.primary_ingest_source,
                latest_import_status,
            )
            return health_sent

        stmt = build_alert_candidates_stmt(
            datetime.now(UTC),
            limit,
            price_freshness_hours=max(1, int(getattr(settings, "price_freshness_hours", 24))),
        )
        stmt = stmt.options(
            selectinload(Lot.trade).selectinload(Trade.source_refs)
        )
        result = await session.execute(stmt)
        lots = result.scalars().all()

        for lot in lots:
            if candidate_sent >= limit:
                break
            if lot.score_stop_factors:
                continue

            feedback = (
                await session.execute(
                    select(UserFeedback.action)
                    .where(UserFeedback.lot_id == lot.id)
                    .order_by(UserFeedback.created_at.desc(), UserFeedback.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if feedback in {"reject", "bought"}:
                continue

            text = fmt_lot_message(
                {
                    "score_class": lot.score_class,
                    "score_ev": lot.score_ev,
                    "score_ev_low": lot.score_ev_low,
                    "score_ev_high": lot.score_ev_high,
                    "current_price": lot.current_price,
                    "score_max_bid": lot.score_max_bid,
                    "nominal_claimed": lot.nominal_claimed,
                    "current_interval_to": lot.current_interval_to,
                    "score_stop_factors": lot.score_stop_factors,
                    # Не отправляем имя/ИНН должника во внешний Telegram API.
                    "claims": [],
                    "title": lot.title,
                    "source_refs": [
                        {
                            "source": ref.source,
                            "source_url": ref.source_url,
                        }
                        for ref in (lot.trade.source_refs if lot.trade else [])
                    ],
                    "lot_url": (
                        lot.trade.source_refs[0].source_url
                        if lot.trade and lot.trade.source_refs
                        else lot.trade.efrsb_url if lot.trade else None
                    ),
                    "efrsb_url": (
                        lot.trade.source_refs[0].source_url
                        if lot.trade and lot.trade.source_refs
                        else lot.trade.efrsb_url if lot.trade else None
                    ),
                }
            )
            for chat_id in settings.telegram_chat_ids_list:
                if candidate_sent >= limit:
                    break
                delivery_id = await reserve_alert_delivery(session, lot, chat_id, dedupe_hours)
                if delivery_id is None:
                    continue
                delivered = await send_message(text, chat_id=chat_id)
                await finish_alert_delivery(
                    session,
                    delivery_id,
                    delivered,
                    None if delivered else "Telegram API returned an unsuccessful response",
                )
                if delivered:
                    candidate_sent += 1

    total_sent = health_sent + candidate_sent
    if total_sent:
        logger.info("alerts: %d sent", total_sent)
    return total_sent
