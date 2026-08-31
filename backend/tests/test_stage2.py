"""Stage 2 provenance and evidence contract tests."""
from __future__ import annotations

from decimal import Decimal

from src.models.entities import Party, PartySourceCheck, Trade, TradeSourceRef
from src.models.enums import Gap, PartyRole
from src.schemas.lot import ClaimSchema, DebtorPartySchema
from src.scoring.v1 import ScoreInput, compute_ev_and_class


def test_source_models_keep_generic_provenance() -> None:
    trade = Trade(id=10)
    ref = TradeSourceRef(
        trade=trade,
        source="cdt_public",
        source_url="https://torgi.cdtrf.ru/trades/1",
        external_trade_id="1",
        external_lot_id="2",
    )
    assert ref.trade is trade
    assert ref.source != "efrsb_public"


def test_party_source_check_is_explicit_and_serializable() -> None:
    party = Party(
        id=1,
        role=PartyRole.DEBTOR.value,
        inn="7707083893",
        invalid_address=False,
        invalid_director=False,
        pending_exclusion=False,
    )
    party.source_checks.append(
        PartySourceCheck(
            source="kad", status="unavailable", failures=1, last_error="challenge"
        )
    )
    payload = DebtorPartySchema.model_validate(party, from_attributes=True)
    assert payload.kad_bankruptcy_open is None
    assert payload.source_checks[0].status == "unavailable"


def test_unknown_legal_facts_are_gaps_and_do_not_become_negative() -> None:
    debtor = DebtorPartySchema(inn="7707083893", source_as_of=None)
    claim = ClaimSchema(id=1, kind="trade_ar", principal=Decimal("100000"))
    result = compute_ev_and_class(
        ScoreInput(
            lot_id=1,
            start_price=Decimal("1000"),
            current_price=Decimal("1000"),
            current_price_confirmed=True,
            nominal_claimed=Decimal("100000"),
            debtor=debtor,
            claims=[claim],
        )
    )
    assert Gap.KAD_UNVERIFIED in result.gaps
    assert Gap.FSSP_UNVERIFIED in result.gaps
    assert Gap.CLAIM_EVIDENCE_UNVERIFIED in result.gaps
