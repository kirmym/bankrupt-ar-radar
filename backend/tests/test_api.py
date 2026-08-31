"""Regression tests for API safety and source diagnostics."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from src.api.diagnostics import _check
from src.api.main import app, safe_static_file
from src.api.security import require_api_access
from src.connectors.efrsb import parse_lot_card, parse_price
from src.models.enums import TradeKind, TradeStatus
from src.schemas.lot import (
    DocumentProposalUpdates,
    HealthResponse,
    LotCardSchema,
    TradeBriefSchema,
)
from src.telegram import fmt_lot_message, fmt_money


def test_static_path_cannot_escape_root(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    root.mkdir()
    (tmp_path / "canary.txt").write_text("private", encoding="utf-8")
    (root / "index.html").write_text("public", encoding="utf-8")

    assert safe_static_file(root, "index.html") == (root / "index.html").resolve()
    assert safe_static_file(root, "../canary.txt") is None
    assert safe_static_file(root, str((tmp_path / "canary.txt").resolve())) is None


@pytest.mark.asyncio
async def test_source_diagnostics_does_not_treat_forbidden_as_success() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _check(client, "test", "https://source.invalid", True)

    assert result["ok"] is False
    assert result["state"] == "challenge"
    assert result["status_code"] == 403


@pytest.mark.asyncio
async def test_source_diagnostics_classifies_connect_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _check(client, "test", "https://source.invalid", True)

    assert result["ok"] is False
    assert result["state"] == "transport_connect"


def test_api_guard_requires_key_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.api.security.get_settings",
        lambda: SimpleNamespace(api_auth_token="secret"),
    )
    with pytest.raises(HTTPException) as exc_info:
        require_api_access(None)
    assert exc_info.value.status_code == 401
    require_api_access("secret")


def test_api_guard_fails_closed_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.api.security.get_settings",
        lambda: SimpleNamespace(api_auth_token="", app_env="production"),
    )
    with pytest.raises(HTTPException) as exc_info:
        require_api_access(None)
    assert exc_info.value.status_code == 503


def test_uuid_schema_accepts_orm_uuid() -> None:
    result = TradeBriefSchema.model_validate(
        {
            "id": 1,
            "guid": uuid4(),
            "trade_kind": TradeKind.PUBLIC_OFFER,
            "status": TradeStatus.IN_PROGRESS,
        }
    )
    assert result.guid.version == 4


def test_health_response_does_not_claim_database_is_checked() -> None:
    health = HealthResponse()
    assert health.status == "ok"
    assert health.database == "not_checked"
    assert health.redis == "not_used"


def test_ingest_status_route_keeps_its_handler() -> None:
    routes = [route for route in app.routes if getattr(route, "path", None) == "/api/v1/ingest/status"]
    assert len(routes) == 1
    assert routes[0].endpoint.__name__ == "ingest_status"


def test_lot_card_schema_resolves_nested_models() -> None:
    assert LotCardSchema.model_fields["trade"].annotation.__name__ == "TradeBriefSchema"


def test_document_proposal_schema_validates_types_and_inn_checksum() -> None:
    proposal = DocumentProposalUpdates.model_validate(
        {
            "claim": {"kind": "trade_ar", "principal": "125000.00", "currency": "rub"},
            "debtor": {"name": "ООО Ромашка", "inn": "7707083893"},
        }
    )
    assert proposal.claim is not None
    assert proposal.claim.currency == "RUB"
    assert proposal.debtor is not None
    assert proposal.debtor.inn == "7707083893"


def test_document_proposal_schema_rejects_unknown_and_invalid_values() -> None:
    with pytest.raises(ValueError):
        DocumentProposalUpdates.model_validate(
            {"debtor": {"inn": "7707083894"}}
        )
    with pytest.raises(ValueError):
        DocumentProposalUpdates.model_validate(
            {"claim": {"principal": -1}}
        )
    with pytest.raises(ValueError):
        DocumentProposalUpdates.model_validate(
            {"claim": {"unexpected": "value"}}
        )


def test_public_parser_and_money_formatter_keep_decimal_values() -> None:
    parsed = parse_lot_card(
        "<h1>Лот 42</h1><div class='description'>ИНН 7707083893</div>",
        "https://bankrot.fedresurs.ru/lot/42",
    )
    assert parsed["title"] == "Лот 42"
    assert parse_price("12 345,67 руб.") == Decimal("12345.67")
    assert fmt_money("12345.67") == "12 346 ₽"
    assert "EV" in fmt_lot_message({"score_ev": "12345.67", "claims": []})
