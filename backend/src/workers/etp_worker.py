"""Refresh current ETP price, interval and trade status."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from src.config import get_settings
from src.database import async_session_factory
from src.models.entities import Document, Lot, Trade
from src.models.enums import TradeStatus
from src.workers.document_lock import lock_document
from src.workers.files_worker import adapter_for_document_url

logger = logging.getLogger(__name__)
settings = get_settings()


def etp_retry_at(
    now: datetime, failures: int, max_attempts: int | None = None
) -> datetime:
    """Use bounded backoff so unsupported or broken ETP pages yield the queue."""
    if max_attempts is not None and failures >= max(1, max_attempts):
        return now + timedelta(days=7)
    minutes = min(15 * (2 ** min(max(failures - 1, 0), 7)), 24 * 60)
    return now + timedelta(minutes=minutes)


def normalize_trade_status(value: str | None) -> str | None:
    """Map common Russian/English ETP labels to the canonical enum."""
    if not value:
        return None
    text = value.strip().lower()
    mapping = (
        (("отмен", "cancel"), TradeStatus.CANCELLED.value),
        (("приостанов", "suspend"), TradeStatus.SUSPENDED.value),
        (("не состоя", "did not take", "failed"), TradeStatus.DID_NOT_TAKE_PLACE.value),
        (("заверш", "completed", "состоял"), TradeStatus.COMPLETED.value),
        (("идут", "in progress", "активн"), TradeStatus.IN_PROGRESS.value),
        (("прием заяв", "application", "announced"), TradeStatus.APPLICATIONS_OPEN.value),
    )
    for markers, status in mapping:
        if any(marker in text for marker in markers):
            return status
    return None


async def run_etp_refresh(batch_size: int = 50) -> int:
    """Refresh lots with a known supported ETP URL."""
    refreshed = 0
    async with async_session_factory() as session:
        now = datetime.now(UTC)
        result = await session.execute(
            select(Lot)
            .join(Trade, Lot.trade_id == Trade.id)
            .where(Trade.etp_url.isnot(None), Trade.trade_id_on_etp.isnot(None))
            .where(or_(Lot.etp_next_retry_at.is_(None), Lot.etp_next_retry_at <= now))
            .options(selectinload(Lot.trade))
            .order_by(Lot.etp_next_retry_at.asc().nulls_first(), Lot.id.asc())
            .limit(batch_size)
        )
        lots = result.scalars().all()
        for lot in lots:
            trade = lot.trade
            checked_at = datetime.now(UTC)
            input_updated_at = lot.updated_at
            if not trade.etp_url or not trade.trade_id_on_etp:
                continue
            adapter_cls = adapter_for_document_url(trade.etp_url)
            if adapter_cls is None:
                lot.etp_checked_at = checked_at
                lot.etp_failures = int(lot.etp_failures or 0) + 1
                lot.etp_next_retry_at = checked_at + timedelta(days=7)
                lot.etp_last_error = "unsupported ETP host"
                lot.updated_at = input_updated_at
                await session.commit()
                continue
            try:
                async with adapter_cls() as adapter:
                    update = await adapter.fetch_lot(trade.trade_id_on_etp, lot.lot_no)
                if update is None:
                    failures = int(lot.etp_failures or 0) + 1
                    lot.etp_checked_at = checked_at
                    lot.etp_failures = failures
                    lot.etp_next_retry_at = etp_retry_at(
                        checked_at, failures, settings.etp_max_attempts
                    )
                    lot.etp_last_error = "ETP lot was not found"
                    lot.updated_at = input_updated_at
                    await session.commit()
                    continue
                input_changed = False
                if update.current_price is not None:
                    input_changed = input_changed or lot.current_price != update.current_price
                    lot.current_price = update.current_price
                    lot.price_observed_at = checked_at
                    lot.price_source = adapter.name
                if update.current_interval_from is not None:
                    input_changed = input_changed or lot.current_interval_from != update.current_interval_from
                    lot.current_interval_from = update.current_interval_from
                if update.current_interval_to is not None:
                    input_changed = input_changed or lot.current_interval_to != update.current_interval_to
                    lot.current_interval_to = update.current_interval_to
                if update.cutoff_price is not None:
                    input_changed = input_changed or lot.cutoff_price != update.cutoff_price
                    lot.cutoff_price = update.cutoff_price
                if update.deposit_amount is not None:
                    input_changed = input_changed or lot.deposit_amount != update.deposit_amount
                    lot.deposit_amount = update.deposit_amount
                normalized = normalize_trade_status(update.status)
                if normalized:
                    input_changed = input_changed or trade.status != normalized
                    trade.status = normalized

                lot.etp_checked_at = checked_at
                lot.etp_next_retry_at = checked_at + timedelta(
                    minutes=max(1, settings.etp_interval_minutes)
                )
                lot.etp_failures = 0
                lot.etp_last_error = None
                if not input_changed:
                    # Poll metadata is not a scoring input.  Do not make a
                    # score look stale merely because the remote value was
                    # checked and remained unchanged.
                    lot.updated_at = input_updated_at

                # Цена/статус — самостоятельный результат обновления. Коммитим
                # их до необязательного списка вложений, чтобы ошибка файла не
                # откатывала актуальные торги.
                await session.commit()
                refreshed += 1

                files: list = []
                try:
                    async with adapter_cls() as adapter:
                        files = (
                            update.files
                            if update.files is not None
                            else await adapter.fetch_files(trade.trade_id_on_etp, lot.lot_no)
                        )
                except Exception:
                    logger.exception("etp files refresh failed for lot %d", lot.id)

                for file in files:
                    await lock_document(session, lot.id, file.url)
                    document = (
                        await session.execute(
                            select(Document).where(
                                Document.lot_id == lot.id,
                                Document.url == file.url,
                            )
                        )
                    ).scalar_one_or_none()
                    if document is None:
                        session.add(
                            Document(
                                lot_id=lot.id,
                                kind=file.kind,
                                title=file.title[:300],
                                url=file.url,
                            )
                        )
                    else:
                        document.kind = file.kind
                        document.title = file.title[:300]
                await session.commit()
            except Exception:
                logger.exception("etp refresh failed for lot %d", lot.id)
                await session.rollback()
                retried = await session.get(Lot, lot.id, with_for_update={"skip_locked": True})
                if retried is not None:
                    failures = int(retried.etp_failures or 0) + 1
                    retried.etp_checked_at = checked_at
                    retried.etp_failures = failures
                    retried.etp_next_retry_at = etp_retry_at(
                        checked_at, failures, settings.etp_max_attempts
                    )
                    retried.etp_last_error = "ETP refresh worker exception"
                    retried.updated_at = input_updated_at
                    await session.commit()
    return refreshed
