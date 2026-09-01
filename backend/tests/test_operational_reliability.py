"""Regression tests for durable queues, source policy and parser contracts."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.connectors.etp_base import EtpAccessError
from src.connectors.etp_cdt import CdtAdapter
from src.connectors.etp_sberbank import SberbankAdapter
from src.connectors.providers import provider_api_enabled
from src.models.entities import Document, Lot
from src.workers.alert_worker import alert_dedupe_key
from src.workers.enrich_worker import enrich_retry_at
from src.workers.etp_worker import etp_retry_at
from src.workers.files_worker import defer_download_retry


def test_failed_queue_items_back_off_and_yield_the_next_batch() -> None:
    now = datetime(2026, 8, 30, tzinfo=UTC)
    assert enrich_retry_at(now, 2) > enrich_retry_at(now, 1) > now
    assert etp_retry_at(now, 2) > etp_retry_at(now, 1) > now


def test_document_retries_become_manual_review_after_the_configured_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.workers import files_worker

    monkeypatch.setattr(files_worker, "settings", SimpleNamespace(document_max_attempts=2))
    document = Document(lot_id=1, url="https://bankrot.fedresurs.ru/files/contract.pdf")
    defer_download_retry(document, RuntimeError("first"))
    assert document.processing_status == "retrying"
    defer_download_retry(document, RuntimeError("second"))
    assert document.processing_status == "needs_review"
    assert document.next_retry_at is None


def test_alert_key_changes_for_new_score_or_time_window() -> None:
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    lot = Lot(id=42, score_version="v1")
    key = alert_dedupe_key(lot, "100", 20, now)
    assert key == alert_dedupe_key(lot, "100", 20, now)
    lot.score_version = "v2"
    assert alert_dedupe_key(lot, "100", 20, now) != key


def test_alert_key_ignores_timestamp_but_changes_when_economics_change() -> None:
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    lot = Lot(id=42, score_version="v1", score_updated_at=now, current_price=100)
    key = alert_dedupe_key(lot, "100", 20, now)
    lot.score_updated_at = now.replace(minute=1)
    assert alert_dedupe_key(lot, "100", 20, now) == key
    lot.score_updated_at = now
    lot.current_price = 90
    assert alert_dedupe_key(lot, "100", 20, now) != key


def test_free_api_policy_requires_an_explicit_exact_allowlist() -> None:
    assert provider_api_enabled("fssp", {"fssp"}, "https://api-ip.fssprus.ru")
    assert not provider_api_enabled("fssp", set(), "https://api-ip.fssprus.ru")
    assert not provider_api_enabled("fssp", {"fssp"}, "https://example.invalid")
    assert not provider_api_enabled("kad", {"kad"}, "https://kad.arbitr.ru/Kad/SearchCases")


@pytest.mark.asyncio
async def test_etp_parser_contract_rejects_empty_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = SberbankAdapter()
    adapter._client = object()

    async def fake_fetch(_url: str) -> tuple[int, str]:
        return 200, "<html><body>Торги</body></html>"

    async def no_wait(_seconds: float = 1.0) -> None:
        return None

    monkeypatch.setattr(adapter, "fetch_html", fake_fetch)
    monkeypatch.setattr(adapter, "rate_limit", no_wait)
    with pytest.raises(EtpAccessError, match="no lot fields"):
        await adapter.fetch_lot("trade", 1)


@pytest.mark.asyncio
async def test_etp_parser_contract_accepts_a_semantic_price(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = CdtAdapter()
    adapter._client = object()

    async def fake_fetch(_url: str) -> tuple[int, str]:
        return 200, "<html><body><div class='current-price'>1 234,50 ₽</div></body></html>"

    async def no_wait(_seconds: float = 1.0) -> None:
        return None

    monkeypatch.setattr(adapter, "fetch_html", fake_fetch)
    monkeypatch.setattr(adapter, "rate_limit", no_wait)
    update = await adapter.fetch_lot("trade", 1)
    assert update is not None
    assert str(update.current_price) == "1234.50"
