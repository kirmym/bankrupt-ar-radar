"""File-обработчик — воркер для скачивания и извлечения фактов из файлов лота."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.config import get_settings
from src.connectors.files import (
    extract_facts_from_text,
    extract_text,
    propose_fact_updates,
    sha256_hex,
)
from src.connectors.llm import extract_facts_with_llm
from src.database import async_session_factory
from src.models.entities import Claim, Document

logger = logging.getLogger(__name__)
settings = get_settings()


def adapter_for_document_url(url: str):
    """Return an adapter only for hosts explicitly supported by the project."""
    host = (urlparse(url).hostname or "").lower()
    if host == "elektortorgi.ru" or host.endswith(".elektortorgi.ru"):
        from src.connectors.etp_cdt import CdtAdapter

        return CdtAdapter
    if host == "utp.sberbank-ast.ru" or host.endswith(".sberbank-ast.ru"):
        from src.connectors.etp_sberbank import SberbankAdapter

        return SberbankAdapter
    return None


async def process_file(doc: Document, lot_id: int, session) -> dict | None:
    """Скачивает файл, извлекает текст, сохраняет факты."""
    from src.connectors.etp_base import EtpFile  # local import to avoid cycles

    if not doc.url:
        return None

    etp_file = EtpFile(
        title=doc.title or "document",
        url=doc.url,
        kind=doc.kind or "прочее",
    )

    # Скачиваем
    adapter_cls = adapter_for_document_url(doc.url)
    if adapter_cls is None:
        logger.warning("Unsupported document host for %s", doc.url)
        return None
    try:
        async with adapter_cls() as adapter:
            data = await adapter.download_file(etp_file)
    except Exception as e:
        logger.exception("Failed to download %s: %s", doc.url, e)
        return None

    # Хэш
    doc.sha256 = sha256_hex(data)
    doc.downloaded_at = datetime.now(UTC)

    # Текст
    text = extract_text(data)
    doc.text = text

    # Факты
    if settings.openai_api_key:
        try:
            from openai import AsyncOpenAI  # type: ignore[import-not-found]

            client = AsyncOpenAI(api_key=settings.openai_api_key)
            result = await extract_facts_with_llm(text, client)
            doc.extracted_facts = result
        except Exception as e:
            logger.exception("LLM failed for %s: %s", doc.url, e)
            doc.extracted_facts = {"facts": extract_facts_from_text(text), "source": "regex_fallback"}
    else:
        doc.extracted_facts = {"facts": extract_facts_from_text(text), "source": "regex"}

    claim = (
        await session.execute(
            select(Claim)
            .where(Claim.lot_id == lot_id)
            .options(selectinload(Claim.debtor_party))
            .order_by(Claim.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if doc.extracted_facts is not None:
        doc.extracted_facts["proposal"] = propose_fact_updates(
            doc.extracted_facts,
            claim=claim,
            debtor=claim.debtor_party if claim else None,
        )

    return doc.extracted_facts


async def run_files(batch_size: int = 20) -> int:
    """Скачивает и обрабатывает файлы лотов, у которых их ещё нет."""
    logger.info("files: starting")

    async with async_session_factory() as session:
        # Лоты, у которых есть URL'ы файлов, но нет downloaded_at
        stmt = (
            select(Document)
            .where(Document.url.isnot(None))
            .where(Document.downloaded_at.is_(None))
            .limit(batch_size)
        )
        result = await session.execute(stmt)
        docs = result.scalars().all()

        count = 0
        for doc in docs:
            try:
                facts = await process_file(doc, doc.lot_id, session)
                if facts:
                    count += 1
                await session.commit()
                logger.info("files: doc %d processed", doc.id)
            except Exception:
                logger.exception("files: doc %d failed", doc.id)
                await session.rollback()

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
