"""Alert worker — Telegram-алерты по классам A/B с EV > 0.

Дедупликация: один и тот же лот не шлём чаще, чем раз в ALERT_DEDUPE_HOURS
(состояние в таблице alerts_state, без Redis).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from src.config import get_settings
from src.database import async_session_factory
from src.models.entities import AlertState, ImportRun, Lot, Trade, UserFeedback
from src.models.enums import AlertDeliveryStatus, LotClass, PriceScheduleStatus, TradeStatus
from src.telegram import fmt_lot_message, send_message

logger = logging.getLogger(__name__)
settings = get_settings()


def alert_dedupe_key(lot: Lot, chat_id: str, dedupe_hours: int, now: datetime) -> str:
    """Create one idempotency key per recipient, score revision and time window."""
    window_seconds = max(1, dedupe_hours * 60 * 60)
    bucket = int(now.timestamp()) // window_seconds
    version = lot.score_version or "unversioned"
    return f"lot:{lot.id}:chat:{chat_id}:score:{version}:window:{bucket}"


async def reserve_alert_delivery(session, lot: Lot, chat_id: str, dedupe_hours: int) -> int | None:
    """Atomically claim an outbox item before talking to Telegram.

    A database uniqueness constraint makes concurrent workers converge on one
    row.  A lease lets a later worker recover a process that died mid-delivery.
    """
    now = datetime.now(UTC)
    key = alert_dedupe_key(lot, chat_id, dedupe_hours, now)
    existing = await session.scalar(
        select(AlertState)
        .where(AlertState.dedupe_key == key)
        .with_for_update(skip_locked=True)
    )
    if existing is not None:
        if existing.status == AlertDeliveryStatus.SENT.value:
            return None
        if existing.lease_until is not None and existing.lease_until > now:
            return None
        existing.status = AlertDeliveryStatus.SENDING.value
        existing.lease_until = now + timedelta(minutes=5)
        existing.attempts = int(existing.attempts or 0) + 1
        existing.last_error = None
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
        .where(Lot.current_price.is_not(None))
        .where(
            Lot.price_schedule_status.in_(
                [PriceScheduleStatus.PARSED.value, PriceScheduleStatus.NOT_PRESENT.value]
            )
        )
        .where(Lot.price_observed_at.is_not(None))
        .where(Lot.price_observed_at >= now - timedelta(hours=price_freshness_hours))
        .where(Lot.score_updated_at.is_not(None))
        .where(Lot.score_updated_at >= Lot.updated_at)
        .where(
            Trade.status.in_(
                [
                    TradeStatus.ANNOUNCED.value,
                    TradeStatus.APPLICATIONS_OPEN.value,
                    TradeStatus.IN_PROGRESS.value,
                ]
            )
        )
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
    sent = 0

    async with async_session_factory() as session:
        latest_import_status = await session.scalar(
            select(ImportRun.status)
            .where(ImportRun.source == "efrsb_public")
            .order_by(ImportRun.started_at.desc())
            .limit(1)
        )
        if latest_import_status is not None and latest_import_status != "finished":
            logger.warning("alerts: latest EFRSB import is %s, skip", latest_import_status)
            return 0

        stmt = build_alert_candidates_stmt(
            datetime.now(UTC),
            limit,
            price_freshness_hours=max(1, int(getattr(settings, "price_freshness_hours", 24))),
        )
        result = await session.execute(stmt)
        lots = result.scalars().all()

        for lot in lots:
            if sent >= limit:
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
                    # Имена и ИНН не отправляем во внешний Telegram API.
                    "claims": [],
                }
            )
            for chat_id in settings.telegram_chat_ids_list:
                if sent >= limit:
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
                    sent += 1

    if sent:
        logger.info("alerts: %d sent", sent)
    return sent
