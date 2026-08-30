"""Tests for document-ingest race prevention."""
from __future__ import annotations

from src.workers.document_lock import document_lock_key


def test_document_lock_key_is_stable_and_scoped_to_lot_and_url():
    key = document_lock_key(1, "https://example.test/a.pdf")

    assert key == document_lock_key(1, "https://example.test/a.pdf")
    assert key != document_lock_key(2, "https://example.test/a.pdf")
    assert key != document_lock_key(1, "https://example.test/b.pdf")
