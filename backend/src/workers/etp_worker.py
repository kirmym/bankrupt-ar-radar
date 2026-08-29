"""Refresh current ETP price, interval and trade status."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.database import async_session_factory
from src.models.entities import Document, Lot, Trade
from src.models.enums import TradeStatus
from src.workers.files_worker import adapter_for_document_url

logger = logging.getLogger(__name__)


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
        result = await session.execute(
            select(Lot)
            .join(Trade, Lot.trade_id == Trade.id)
            .where(Trade.etp_url.isnot(None), Trade.trade_id_on_etp.isnot(None))
            .options(selectinload(Lot.trade))
            .order_by(Lot.updated_at.asc())
            .limit(batch_size)
        )
        lots = result.scalars().all()
        for lot in lots:
            trade = lot.trade
            if not trade.etp_url or not trade.trade_id_on_etp:
                continue
            adapter_cls = adapter_for_document_url(trade.etp_url)
            if adapter_cls is None:
                continue
            try:
                async with adapter_cls() as adapter:
                    update = await adapter.fetch_lot(trade.trade_id_on_etp, lot.lot_no)
                if update is None:
                    continue
                if update.current_price is not None:
                    lot.current_price = update.current_price
                if update.current_interval_from is not None:
                    lot.current_interval_from = update.current_interval_from
                if update.current_interval_to is not None:
                    lot.current_interval_to = update.current_interval_to
                if update.cutoff_price is not None:
                    lot.cutoff_price = update.cutoff_price
                if update.deposit_amount is not None:
                    lot.deposit_amount = update.deposit_amount
                normalized = normalize_trade_status(update.status)
                if normalized:
                    trade.status = normalized

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
    return refreshed
