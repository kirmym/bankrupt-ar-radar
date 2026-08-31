"""Stage 1 regression tests for source diagnostics and score freshness."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from src.api.diagnostics import _check, configured_sources
from src.config import Settings
from src.models.entities import Lot, Trade
from src.workers.ingest_worker import _lot_score_signature, persist_trade_and_lot


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _Db:
    def __init__(self, values: list[object]) -> None:
        self.values = iter(values)

    async def execute(self, _statement: object, *_args: object) -> _Result:
        return _Result(next(self.values))


def test_configured_sources_uses_cdt_as_critical_primary_check() -> None:
    sources = configured_sources(
        Settings(
            cdt_api_url="https://cdt.example/api/",
            efrsb_public_url="https://efrsb.example",
        )
    )
    by_name = {name: (url, critical) for name, url, critical in sources}

    assert by_name["cdt_public"] == (
        (
            "https://cdt.example/api/Trade/trades?Declare=true&RecieveReq=true&TradeTypeIds=3&Find="
            "&PageSize=1&PageNum=1&Sort="
        ),
        True,
    )
    assert by_name["efrsb"] == ("https://efrsb.example", False)


def test_observation_timestamp_is_not_a_score_input() -> None:
    lot = Lot(
        start_price=Decimal("100"),
        current_price=Decimal("80"),
        price_schedule_status="parsed",
        price_observed_at=datetime(2026, 8, 31, tzinfo=UTC),
    )
    before = _lot_score_signature(lot)
    lot.price_observed_at = datetime(2026, 9, 1, tzinfo=UTC)

    assert _lot_score_signature(lot) == before


@pytest.mark.asyncio
async def test_source_diagnostics_classifies_unauthorized_as_challenge() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _check(client, "test", "https://source.invalid", False)

    assert result["state"] == "challenge"
    assert result["status_code"] == 401


@pytest.mark.asyncio
async def test_idempotent_ingest_restores_updated_at_when_only_observation_changes() -> None:
    old_updated_at = datetime(2026, 8, 31, 12, tzinfo=UTC)
    trade = Trade(id=1, efrsb_url="https://source.example/lot/1")
    lot = Lot(
        id=2,
        trade_id=1,
        lot_no=1,
        start_price=Decimal("100"),
        current_price=Decimal("80"),
        price_schedule_status="parsed",
        price_observed_at=datetime(2026, 8, 31, 11, tzinfo=UTC),
        updated_at=old_updated_at,
    )

    await persist_trade_and_lot(
        {
            "efrsb_url": trade.efrsb_url,
            "lot_no": 1,
            "start_price": Decimal("100"),
            "current_price": Decimal("80"),
            "price_schedule_status": "parsed",
            "price_observed_at": datetime(2026, 8, 31, 12, tzinfo=UTC),
        },
        _Db([trade, lot]),
    )

    assert lot.updated_at == old_updated_at
    assert lot._ingest_changed is False
