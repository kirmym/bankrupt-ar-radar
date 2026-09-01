"""File-обработчик — воркер для скачивания и извлечения фактов из файлов лота."""
from __future__ import annotations

import asyncio
import copy
import logging
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from src.config import get_settings
from src.connectors.etp_base import EtpAdapter
from src.connectors.files import (
    extract_facts_from_text,
    extract_text,
    extract_text_with_ocr,
    propose_fact_updates,
    sha256_hex,
)
from src.connectors.llm import extract_facts_with_llm
from src.database import async_session_factory
from src.models.entities import Claim, Document, Lot, Trade
from src.models.enums import DocumentProcessingStatus

logger = logging.getLogger(__name__)
settings = get_settings()


def defer_download_retry(doc: Document, error: Exception, now: datetime | None = None) -> None:
    """Record a bounded exponential retry delay after a transient download error."""
    attempts = int(doc.download_attempts or 0) + 1
    retry_after_minutes = min(15 * (2 ** min(attempts - 1, 7)), 24 * 60)
    reference = now or datetime.now(UTC)
    doc.download_attempts = attempts
    doc.last_error = f"{type(error).__name__}: {error}"[:500]
    max_attempts = max(1, int(getattr(settings, "document_max_attempts", 8)))
    if attempts >= max_attempts:
        doc.processing_status = DocumentProcessingStatus.NEEDS_REVIEW.value
        doc.next_retry_at = None
        return
    doc.processing_status = DocumentProcessingStatus.RETRYING.value
    doc.next_retry_at = reference + timedelta(minutes=retry_after_minutes)


class EfrsbDocumentAdapter(EtpAdapter):
    """Download public EFRSB documents with the shared SSRF/browser guards."""

    name = "efrsb"
    base_url = "https://bankrot.fedresurs.ru"

    def __init__(self, timeout: float = 30.0):
        super().__init__(timeout)
        configured_host = (urlparse(get_settings().efrsb_public_url).hostname or "").lower()
        self.allowed_hosts = {
            host for host in {configured_host, "bankrot.fedresurs.ru", "old.bankrot.fedresurs.ru"} if host
        }

    async def fetch_lot(self, etp_trade_id: str, lot_no: int):
        raise NotImplementedError

    async def fetch_files(self, etp_trade_id: str, lot_no: int):
        raise NotImplementedError


class CdtDocumentAdapter(EtpAdapter):
    """Download files exposed by the public CDT document endpoint."""

    name = "cdt"
    base_url = "https://webapi.torgi.cdtrf.ru"

    async def fetch_lot(self, etp_trade_id: str, lot_no: int):
        raise NotImplementedError

    async def fetch_files(self, etp_trade_id: str, lot_no: int):
        raise NotImplementedError


def adapter_for_document_url(url: str):
    """Return an adapter only for hosts explicitly supported by the project."""
    host = (urlparse(url).hostname or "").lower()
    if host == "elektortorgi.ru" or host.endswith(".elektortorgi.ru"):
        from src.connectors.etp_cdt import CdtAdapter

        return CdtAdapter
    if host == "utp.sberbank-ast.ru" or host.endswith(".sberbank-ast.ru"):
        from src.connectors.etp_sberbank import SberbankAdapter

        return SberbankAdapter
    if host == "webapi.torgi.cdtrf.ru":
        return CdtDocumentAdapter
    configured_efrsb_host = (urlparse(get_settings().efrsb_public_url).hostname or "").lower()
    if host in {configured_efrsb_host, "bankrot.fedresurs.ru", "old.bankrot.fedresurs.ru"}:
        return EfrsbDocumentAdapter
    return None


def _document_host(url: str | None) -> str:
    return (urlparse(url or "").hostname or "").lower()


async def _find_cached_document(doc: Document, session) -> Document | None:
    """Find a completed copy of the same public file without downloading it."""
    if session is None or not doc.external_id or not doc.url:
        return None
    result = await session.execute(
        select(Document)
        .where(Document.external_id == doc.external_id)
        .where(Document.id != doc.id)
        .where(Document.processing_status == DocumentProcessingStatus.COMPLETED.value)
        .where(Document.text.is_not(None))
        .order_by(Document.downloaded_at.desc().nullslast(), Document.id.asc())
        .limit(25)
    )
    target_host = _document_host(doc.url)
    for candidate in result.scalars():
        if _document_host(candidate.url) == target_host:
            return candidate
    return None


async def _load_claim(session, lot_id: int) -> Claim | None:
    if session is None:
        return None
    return (
        await session.execute(
            select(Claim)
            .where(Claim.lot_id == lot_id)
            .options(selectinload(Claim.debtor_party))
            .order_by(Claim.id)
            .limit(1)
        )
    ).scalar_one_or_none()


async def _reuse_cached_document(doc: Document, lot_id: int, session) -> dict | None:
    """Copy shared content and rebuild only the lot-specific proposal."""
    cached = await _find_cached_document(doc, session)
    if cached is None or not cached.text:
        return None
    payload = copy.deepcopy(cached.extracted_facts or {})
    if not isinstance(payload, dict):
        return None
    payload.pop("proposal", None)
    payload.pop("proposal_applied_at", None)
    payload["reused_from_document_id"] = cached.id
    doc.sha256 = cached.sha256
    doc.text = cached.text
    doc.extracted_facts = payload
    doc.download_attempts = 0
    doc.next_retry_at = None
    doc.last_error = None
    doc.downloaded_at = datetime.now(UTC)
    doc.processing_status = DocumentProcessingStatus.COMPLETED.value
    claim = await _load_claim(session, lot_id)
    doc.extracted_facts["proposal"] = propose_fact_updates(
        doc.extracted_facts,
        claim=claim,
        debtor=claim.debtor_party if claim else None,
    )
    return doc.extracted_facts


async def process_file(doc: Document, lot_id: int, session) -> dict | None:
    """Скачивает файл, извлекает текст, сохраняет факты."""
    from src.connectors.etp_base import EtpFile  # local import to avoid cycles

    if not doc.url:
        return None

    cached_facts = await _reuse_cached_document(doc, lot_id, session)
    if cached_facts is not None:
        logger.info("files: reusing document %s from shared external_id", doc.external_id)
        return cached_facts

    etp_file = EtpFile(
        title=doc.title or "document",
        url=doc.url,
        kind=doc.kind or "прочее",
    )

    # Скачиваем
    adapter_cls = adapter_for_document_url(doc.url)
    if adapter_cls is None:
        logger.warning("Unsupported document host for %s", doc.url)
        doc.extracted_facts = {
            "facts": {},
            "source": "unsupported_host",
            "status": "needs_review",
        }
        doc.processing_status = DocumentProcessingStatus.NEEDS_REVIEW.value
        return doc.extracted_facts
    try:
        async with adapter_cls() as adapter:
            data = await adapter.download_file(etp_file)
    except Exception as e:
        logger.exception("Failed to download %s: %s", doc.url, e)
        defer_download_retry(doc, e)
        return None

    doc.download_attempts = 0
    doc.next_retry_at = None
    doc.last_error = None
    doc.processing_status = DocumentProcessingStatus.PENDING.value

    # Хэш
    doc.sha256 = sha256_hex(data)

    # Текст
    lower_url = urlparse(doc.url).path.lower()
    if lower_url.endswith(".doc"):
        doc.text = None
        doc.extracted_facts = {
            "facts": {},
            "source": "unsupported_legacy_doc",
            "status": "needs_review",
        }
        doc.processing_status = DocumentProcessingStatus.NEEDS_REVIEW.value
        doc.downloaded_at = None
        return doc.extracted_facts
    content_type = (
        "application/pdf"
        if lower_url.endswith(".pdf")
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if lower_url.endswith(".docx")
        else ""
    )
    # PDF parsing/OCR is CPU-bound and must not block the async scheduler.
    text = await asyncio.to_thread(extract_text, data, content_type)
    extraction_source = "text"
    if not text.strip():
        text = await asyncio.to_thread(extract_text_with_ocr, data, content_type)
        extraction_source = "ocr" if text.strip() else "parse_failed"
    doc.text = text

    # Факты
    if not text.strip():
        doc.extracted_facts = {
            "facts": {},
            "source": extraction_source,
            "status": "needs_review",
        }
        doc.processing_status = DocumentProcessingStatus.NEEDS_REVIEW.value
        # Не помечаем повреждённый/неподдерживаемый файл как обработанный:
        # очередь сможет повторить попытку после исправления адаптера.
        doc.downloaded_at = None
    elif settings.openai_api_key:
        try:
            from openai import AsyncOpenAI  # type: ignore[import-not-found]

            client = AsyncOpenAI(api_key=settings.openai_api_key)
            result = await extract_facts_with_llm(text, client)
            doc.extracted_facts = result
        except Exception as e:
            logger.exception("LLM failed for %s: %s", doc.url, e)
            doc.extracted_facts = {
                "facts": extract_facts_from_text(text),
                "source": f"regex_{extraction_source}",
            }
    else:
        doc.extracted_facts = {
            "facts": extract_facts_from_text(text),
            "source": f"regex_{extraction_source}",
        }
    if text.strip():
        doc.downloaded_at = datetime.now(UTC)
        doc.processing_status = DocumentProcessingStatus.COMPLETED.value

    claim = await _load_claim(session, lot_id)
    if doc.extracted_facts is not None:
        doc.extracted_facts["proposal"] = propose_fact_updates(
            doc.extracted_facts,
            claim=claim,
            debtor=claim.debtor_party if claim else None,
        )

    return doc.extracted_facts


async def run_files(batch_size: int | None = None) -> int:
    """Скачивает и обрабатывает файлы лотов, у которых их ещё нет."""
    logger.info("files: starting")

    async with async_session_factory() as session:
        now = datetime.now(UTC)
        # Лоты, у которых есть URL'ы файлов, но нет downloaded_at
        effective_batch_size = batch_size or max(1, int(settings.files_batch_size))
        stmt = (
            select(Document)
            .join(Lot, Document.lot_id == Lot.id)
            .join(Trade, Lot.trade_id == Trade.id)
            .where(Document.url.isnot(None))
            .where(Document.downloaded_at.is_(None))
            .where(
                Document.processing_status.in_(
                    [
                        DocumentProcessingStatus.PENDING.value,
                        DocumentProcessingStatus.RETRYING.value,
                    ]
                )
            )
            .where(
                or_(
                    Document.next_retry_at.is_(None),
                    Document.next_retry_at <= now,
                )
            )
            .order_by(
                Trade.applications_to.asc().nullslast(),
                Document.next_retry_at.asc().nulls_first(),
                Document.id.asc(),
            )
            .limit(effective_batch_size)
        )
        result = await session.execute(stmt)
        docs = result.scalars().all()

        count = 0
        started = time.monotonic()
        for doc in docs:
            if time.monotonic() - started >= max(1.0, float(settings.files_batch_timeout_seconds)):
                logger.warning("files: batch time budget reached after %d documents", count)
                break
            try:
                facts = await asyncio.wait_for(
                    process_file(doc, doc.lot_id, session),
                    timeout=max(1.0, float(settings.files_document_timeout_seconds)),
                )
                if facts and facts.get("status") != "needs_review":
                    count += 1
                await session.commit()
                logger.info(
                    "files: doc %d status=%s", doc.id, doc.processing_status
                )
            except TimeoutError as exc:
                logger.warning("files: doc %d timed out", doc.id)
                await session.rollback()
                retry_doc = await session.get(Document, doc.id)
                if retry_doc is not None:
                    defer_download_retry(retry_doc, exc)
                    await session.commit()
            except Exception as exc:
                logger.exception("files: doc %d failed", doc.id)
                await session.rollback()
                retry_doc = await session.get(Document, doc.id)
                if retry_doc is not None:
                    defer_download_retry(retry_doc, exc)
                    await session.commit()

        logger.info("files: %d documents processed", count)
        return count


async def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    while True:
        try:
            await run_files()
        except Exception:
            logger.exception("files: failed")
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
