"""Stage 3 feedback, contract REST and source-health regressions."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from src.analytics.calibration import build_calibration_report, feedback_outcome
from src.connectors.efrsb import EfrsbRestSource, SourceAccessError, search_public_offers_rest
from src.workers.alert_worker import (
    build_zero_lot_alert_message,
    source_has_zero_lot_gap,
    zero_lot_window_start,
)


def test_calibration_keeps_unresolved_purchases_out_of_recovery_rate() -> None:
    rows = [
        SimpleNamespace(
            action="bought",
            outcome="recovered",
            recovered_amount=Decimal("800"),
            expense_amount=Decimal("100"),
            decision_score_class="A",
            decision_score_ev=Decimal("700"),
            decision_score_version="v1.0",
        ),
        SimpleNamespace(
            action="bought",
            outcome="in_progress",
            recovered_amount=None,
            expense_amount=None,
            decision_score_class="A",
            decision_score_ev=Decimal("600"),
            decision_score_version="v1.0",
        ),
        SimpleNamespace(action="reject", outcome=None, recovered_amount=None),
    ]

    report = build_calibration_report(rows, min_resolved=2)

    assert report["status"] == "insufficient_data"
    assert report["purchases"] == 2
    assert report["resolved_purchases"] == 1
    assert report["unresolved_purchases"] == 1
    assert report["recovery_rate"] == Decimal(1)
    assert report["net_recovered_amount"] == Decimal("700")
    assert feedback_outcome(rows[1]) == "in_progress"


def test_zero_lot_gap_waits_six_hours_and_ignores_recent_positive_run() -> None:
    now = datetime(2026, 8, 31, 18, 37, tzinfo=UTC)
    old_empty = SimpleNamespace(
        status="finished",
        items_seen=0,
        started_at=now - timedelta(hours=7),
        finished_at=now - timedelta(hours=7),
    )
    recent_positive = SimpleNamespace(
        status="finished",
        items_seen=2,
        started_at=now - timedelta(hours=1),
        finished_at=now - timedelta(hours=1),
    )

    assert source_has_zero_lot_gap([old_empty], now, 6) is True
    assert source_has_zero_lot_gap([old_empty, recent_positive], now, 6) is False
    assert zero_lot_window_start(now, 6).tzinfo == UTC
    assert "не дал лотов" in build_zero_lot_alert_message("cdt_public", now, 6, old_empty)

    running = SimpleNamespace(
        status="running",
        items_seen=0,
        started_at=now - timedelta(minutes=10),
        finished_at=None,
    )
    assert source_has_zero_lot_gap([old_empty, running], now, 6) is False


@pytest.mark.asyncio
async def test_contract_rest_source_normalizes_items_and_keeps_bearer_header() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "items": [
                    {
                        "guid": "trade-1",
                        "url": "https://rest.example/v1/messages/trade-1",
                        "title": "Публичное предложение",
                        "description": "Дебиторская задолженность",
                        "price": "1000",
                    }
                ]
            },
        )

    source = EfrsbRestSource("https://rest.example", "contract-token")
    await source.__aenter__()
    assert source._client is not None
    headers = dict(source._client.headers)
    await source._client.aclose()
    source._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers=headers,
    )
    try:
        rows = await source.search_public_offers(page=2, per_page=20)
    finally:
        await source._client.aclose()

    assert len(rows) == 1
    assert rows[0]["source_name"] == "efrsb_rest"
    assert rows[0]["trade_number"] == "trade-1"
    assert seen[0].headers["authorization"] == "Bearer contract-token"
    assert "page=2" in str(seen[0].url)


@pytest.mark.asyncio
async def test_contract_rest_requires_explicit_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.connectors.efrsb.get_settings",
        lambda: SimpleNamespace(
            efrsb_rest_enabled=True,
            efrsb_rest_contract_confirmed=False,
            efrsb_rest_base_url="https://rest.example",
            efrsb_rest_token="token",
        ),
    )

    with pytest.raises(SourceAccessError, match="contract confirmation"):
        await search_public_offers_rest()
