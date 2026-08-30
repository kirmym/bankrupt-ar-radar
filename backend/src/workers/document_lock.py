"""PostgreSQL transaction locks for idempotent document discovery."""
from __future__ import annotations

from hashlib import sha256

from sqlalchemy import text


def document_lock_key(lot_id: int, url: str) -> int:
    """Return a stable signed 64-bit advisory-lock key for a document URL."""
    digest = sha256(f"{lot_id}\0{url}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


async def lock_document(session, lot_id: int, url: str) -> None:
    """Serialize check-and-insert for one document until the transaction commits."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": document_lock_key(lot_id, url)},
    )
