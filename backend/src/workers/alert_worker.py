"""Alert worker — Telegram-алерты по классам A/B с EV > 0.

Дедупликация: один и тот же лот не шлём чаще, чем раз в ALERT_DEDUPE_HOURS
(состояние в таблице alerts_state, без Redis).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from src.config import get_settings
from src.database import async_session_factory
from src.models.entities import AlertState, Lot
from src.models.enums import LotClass
from src.telegram import fmt_lot_message, send_message

logger = logging.getLogger(__name__)
settings = get_settings()


async def _was_alerted(session, lot_id: int, dedupe_hours: int) -> bool:
    since = datetime.now(UTC) - timedelta(hours=dedupe_hours)
    stmt = select(AlertState).where(
        AlertState.lot_id == lot_id,
        AlertState.alerted_at >= since,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


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
            .where(Lot.is_receivable == True)  # noqa: E712
            .where(Lot.score_class.in_([LotClass.A.value, LotClass.B.value]))
            .where(Lot.score_ev > 0)
            .order_by(Lot.score_ev.desc())
            .limit(limit * 3)
        )
        result = await session.execute(stmt)
        lots = result.scalars().all()

        for lot in lots:
            if sent >= limit:
                break
            if await _was_alerted(session, lot.id, dedupe_hours):
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
                    "claims": [
                        {
                            "debtor_party": (
                                {
                                    "name": c.debtor_party.name if c.debtor_party else None,
                                    "inn": c.debtor_party.inn if c.debtor_party else None,
                                }
                                for c in lot.claims
                            )
                        }
                    ],
                }
            )
            if await send_message(text):
                sent += 1
                session.add(AlertState(lot_id=lot.id, alerted_at=datetime.now(UTC)))
                await session.commit()

    if sent:
        logger.info("alerts: %d sent", sent)
    return sent
