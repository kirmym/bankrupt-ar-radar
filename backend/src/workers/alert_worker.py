"""Alert worker — Telegram-алерты по классам A/B с EV > 0.

Дедупликация: один и тот же лот не шлём чаще, чем раз в ALERT_DEDUPE_HOURS
(состояние в таблице alerts_state, без Redis).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from src.config import get_settings
from src.database import async_session_factory
from src.models.entities import AlertState, Lot, Trade, UserFeedback
from src.models.enums import LotClass, TradeStatus
from src.telegram import fmt_lot_message, send_message

logger = logging.getLogger(__name__)
settings = get_settings()


async def _was_alerted(session, lot_id: int, chat_id: str, dedupe_hours: int) -> bool:
    since = datetime.now(UTC) - timedelta(hours=dedupe_hours)
    stmt = select(AlertState).where(
        AlertState.lot_id == lot_id,
        or_(AlertState.chat_id == chat_id, AlertState.chat_id.is_(None)),
        AlertState.alerted_at >= since,
    )
    result = await session.execute(stmt)
    return result.first() is not None


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
        stmt = (
            select(Lot)
            .join(Trade, Lot.trade_id == Trade.id)
            .where(Lot.is_receivable == True)  # noqa: E712
            .where(Lot.score_class.in_([LotClass.A.value, LotClass.B.value]))
            .where(Lot.score_ev > 0)
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
                    Lot.current_interval_from <= datetime.now(UTC),
                )
            )
            .where(
                or_(
                    Lot.current_interval_to.is_(None),
                    Lot.current_interval_to > datetime.now(UTC),
                )
            )
            .order_by(Lot.score_ev.desc())
            .limit(limit * 3)
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
                if await _was_alerted(session, lot.id, chat_id, dedupe_hours):
                    continue
                if await send_message(text, chat_id=chat_id):
                    sent += 1
                    session.add(
                        AlertState(
                            lot_id=lot.id,
                            chat_id=chat_id,
                            alerted_at=datetime.now(UTC),
                        )
                    )
                    await session.commit()

    if sent:
        logger.info("alerts: %d sent", sent)
    return sent
