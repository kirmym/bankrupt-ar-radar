"""Тесты для скоринга v1."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from src.models.enums import (
    ClaimKind,
    Gap,
    LotClass,
    OrgStatus,
    Scenario,
    StopFactor,
)
from src.schemas.lot import ClaimSchema, DebtorPartySchema
from src.scoring.v1 import (
    ScoreInput,
    compute_ev_and_class,
    compute_success_rate,
    estimate_recovery_time,
)


def make_debtor(
    inn: str = "7701234567",
    status: OrgStatus = OrgStatus.ACTIVE,
    cash: Decimal | None = Decimal("1000000"),
    equity: Decimal | None = Decimal("1000000"),
    revenue: Decimal | None = Decimal("10000000"),
    fssp_uncollectible: bool = False,
    kad_bankruptcy_open: bool = False,
) -> DebtorPartySchema:
    return DebtorPartySchema(
        inn=inn,
        name="Test LLC",
        status=status,
        cash=cash,
        equity=equity,
        revenue=revenue,
        fssp_uncollectible=fssp_uncollectible,
        kad_bankruptcy_open=kad_bankruptcy_open,
    )


def make_claim(
    principal: Decimal = Decimal("1000000"),
    has_judgment: bool = False,
    has_writ: bool = False,
    enforcement_alive: bool = False,
    secured: bool = False,
    counterclaim_risk: bool = False,
    limitations_deadline: date | None = None,
    il_present_deadline: date | None = None,
    kind: ClaimKind = ClaimKind.TRADE_AR,
) -> ClaimSchema:
    return ClaimSchema(
        id=1,
        kind=kind,
        principal=principal,
        currency="RUB",
        has_judgment=has_judgment,
        has_writ=has_writ,
        enforcement_alive=enforcement_alive,
        secured=secured,
        counterclaim_risk=counterclaim_risk,
        limitations_deadline=limitations_deadline,
        il_present_deadline=il_present_deadline,
    )


# ── estimate_recovery_time ──────────────────────────────────────────────────


def test_estimate_recovery_time_with_writ():
    med, _ = estimate_recovery_time(
        has_judgment=True,
        has_writ=True,
        enforcement_alive=False,
        limitations_deadline=None,
        il_present_deadline=None,
    )
    assert med == 6


def test_estimate_recovery_time_with_judgment():
    med, _ = estimate_recovery_time(
        has_judgment=True,
        has_writ=False,
        enforcement_alive=False,
        limitations_deadline=None,
        il_present_deadline=None,
    )
    assert med == 12


def test_estimate_recovery_time_no_docs():
    future = date.today() + timedelta(days=365 * 5)
    med, _ = estimate_recovery_time(
        has_judgment=False,
        has_writ=False,
        enforcement_alive=False,
        limitations_deadline=future,
        il_present_deadline=None,
    )
    assert med == 24


def test_estimate_recovery_time_limitations_expired():
    past = date.today() - timedelta(days=1)
    med, _ = estimate_recovery_time(
        has_judgment=False,
        has_writ=False,
        enforcement_alive=False,
        limitations_deadline=past,
        il_present_deadline=None,
    )
    assert med == 999


# ── compute_success_rate ────────────────────────────────────────────────────


def test_success_rate_with_writ():
    rate = compute_success_rate(
        has_judgment=True,
        has_writ=True,
        enforcement_alive=False,
        claim_kind="trade_ar",
        secured=True,
        counterclaim_risk=False,
        debtor_cash=Decimal("1000000"),
        debtor_equity=Decimal("1000000"),
        debtor_fssp_uncollectible=False,
        debtor_kad_bankruptcy_open=False,
    )
    assert rate >= Decimal("0.85")


def test_success_rate_with_counterclaim():
    rate = compute_success_rate(
        has_judgment=False,
        has_writ=False,
        enforcement_alive=False,
        claim_kind="trade_ar",
        secured=False,
        counterclaim_risk=True,
        debtor_cash=Decimal("1000000"),
        debtor_equity=Decimal("1000000"),
        debtor_fssp_uncollectible=False,
        debtor_kad_bankruptcy_open=False,
    )
    # 0.30 - 0.15 = 0.15
    assert rate <= Decimal("0.20")


def test_success_rate_with_bankruptcy():
    rate = compute_success_rate(
        has_judgment=True,
        has_writ=False,
        enforcement_alive=False,
        claim_kind="trade_ar",
        secured=False,
        counterclaim_risk=False,
        debtor_cash=Decimal("1000000"),
        debtor_equity=Decimal("1000000"),
        debtor_fssp_uncollectible=False,
        debtor_kad_bankruptcy_open=True,
    )
    # 0.55 * 0.6 = 0.33
    assert rate < Decimal("0.40")


# ── compute_ev_and_class ────────────────────────────────────────────────────


def test_class_a_with_judgment():
    """Лот с решением суда и живым дебитором → класс A."""
    inp = ScoreInput(
        lot_id=1,
        start_price=Decimal("100000"),
        current_price=Decimal("100000"),
            nominal_claimed=Decimal("1100000"),
        debtor=make_debtor(cash=Decimal("10000000")),
        claims=[make_claim(
                principal=Decimal("1100000"),
            has_judgment=True,
            has_writ=True,
        )],
    )
    result = compute_ev_and_class(inp)
    assert result.score_class == LotClass.A
    assert result.ev > 0


def test_class_d_with_excluded_debtor():
    """Дебитор исключён из ЕГРЮЛ → класс D."""
    inp = ScoreInput(
        lot_id=1,
        start_price=Decimal("100000"),
        current_price=Decimal("100000"),
        nominal_claimed=Decimal("1000000"),
        debtor=make_debtor(status=OrgStatus.EXCLUDED),
        claims=[make_claim()],
    )
    result = compute_ev_and_class(inp)
    assert result.score_class == LotClass.D
    assert StopFactor.DEBTOR_EXCLUDED in result.stop_factors


def test_class_d_with_expired_limitations():
    """Истёк срок исковой давности → класс D."""
    inp = ScoreInput(
        lot_id=1,
        start_price=Decimal("100000"),
        current_price=Decimal("100000"),
        nominal_claimed=Decimal("1000000"),
        debtor=make_debtor(),
        claims=[make_claim(
            limitations_deadline=date.today() - timedelta(days=1)
        )],
    )
    result = compute_ev_and_class(inp)
    assert result.score_class == LotClass.D
    assert StopFactor.LIMITATIONS_EXPIRED in result.stop_factors


def test_no_debtor_inn_stops():
    """Нет ИНН дебитора → стоп-фактор, не выше D."""
    debtor = DebtorPartySchema(
        inn=None,
        name="Без ИНН",
    )
    inp = ScoreInput(
        lot_id=1,
        start_price=Decimal("100000"),
        current_price=Decimal("100000"),
        debtor=debtor,
        claims=[make_claim()],
    )
    result = compute_ev_and_class(inp)
    assert StopFactor.NO_DEBTOR_INN in result.stop_factors
    assert result.score_class == LotClass.D


def test_class_b_with_strong_claim():
    """Решение + ИЛ + деньги у дебитора → минимум B."""
    inp = ScoreInput(
        lot_id=1,
        start_price=Decimal("500000"),
        current_price=Decimal("500000"),
        nominal_claimed=Decimal("3000000"),
        debtor=make_debtor(
            cash=Decimal("5000000"),
            equity=Decimal("10000000"),
        ),
        claims=[make_claim(
            principal=Decimal("3000000"),
            has_judgment=True,
        )],
    )
    result = compute_ev_and_class(inp)
    assert result.score_class in (LotClass.A, LotClass.B)
    assert result.scenario == Scenario.COURT


def test_scenario_negotiation_default():
    """Без суда и ИЛ — сценарий negotiation."""
    inp = ScoreInput(
        lot_id=1,
        start_price=Decimal("100000"),
        current_price=Decimal("100000"),
        nominal_claimed=Decimal("1000000"),
        debtor=make_debtor(),
        claims=[make_claim()],
    )
    result = compute_ev_and_class(inp)
    assert result.scenario == Scenario.NEGOTIATION


def test_scenario_enforcement_with_writ():
    """ИЛ → исполнительное производство."""
    inp = ScoreInput(
        lot_id=1,
        start_price=Decimal("100000"),
        current_price=Decimal("100000"),
        nominal_claimed=Decimal("1000000"),
        debtor=make_debtor(),
        claims=[make_claim(has_writ=True)],
    )
    result = compute_ev_and_class(inp)
    assert result.scenario == Scenario.ENFORCEMENT


def test_max_bid_capped_by_current_price():
    """Max bid не должен превышать current_price + 20%."""
    inp = ScoreInput(
        lot_id=1,
        start_price=Decimal("100000"),
        current_price=Decimal("100000"),
        nominal_claimed=Decimal("10000000"),
        debtor=make_debtor(cash=Decimal("50000000")),
        claims=[make_claim(
            principal=Decimal("10000000"),
            has_judgment=True,
            has_writ=True,
        )],
    )
    result = compute_ev_and_class(inp)
    # max_bid <= current_price * 1.2 = 120000
    assert result.max_bid <= Decimal("120000")


def test_gaps_when_no_debtor_inn():
    inp = ScoreInput(
        lot_id=1,
        start_price=Decimal("100000"),
        current_price=Decimal("100000"),
        debtor=None,
        claims=[make_claim()],
    )
    result = compute_ev_and_class(inp)
    assert Gap.DEBTOR_INN_MISSING in result.gaps


def test_purchase_price_is_subtracted_from_ev():
    base = {
        "lot_id": 1,
        "nominal_claimed": Decimal("1000000"),
        "debtor": make_debtor(cash=Decimal("10000000")),
        "claims": [make_claim(principal=Decimal("1000000"), has_judgment=True, has_writ=True)],
    }
    cheap = compute_ev_and_class(ScoreInput(**base, current_price=Decimal("100000")))
    expensive = compute_ev_and_class(ScoreInput(**base, current_price=Decimal("500000")))
    assert expensive.ev < cheap.ev
    assert expensive.max_bid >= Decimal(0)


def test_missing_nominal_is_not_scored_as_start_price():
    result = compute_ev_and_class(
        ScoreInput(
            lot_id=1,
            start_price=Decimal("100000"),
            current_price=Decimal("100000"),
            debtor=make_debtor(),
            claims=[],
        )
    )
    assert result.score_class == LotClass.D
    assert Gap.NOMINAL_MISSING in result.gaps


def test_bundle_without_claim_details_is_blocked():
    result = compute_ev_and_class(
        ScoreInput(
            lot_id=1,
            start_price=Decimal("100000"),
            current_price=Decimal("100000"),
            nominal_claimed=Decimal("1000000"),
            is_bundle=True,
            debtor=make_debtor(),
            claims=[],
        )
    )
    assert result.score_class == LotClass.D
    assert StopFactor.BUNDLE_NO_DETAIL in result.stop_factors


def test_personal_claim_is_blocked():
    result = compute_ev_and_class(
        ScoreInput(
            lot_id=1,
            current_price=Decimal("100000"),
            nominal_claimed=Decimal("1000000"),
            debtor=make_debtor(),
            claims=[ClaimSchema(
                id=1,
                kind=ClaimKind.TRADE_AR,
                principal=Decimal("1000000"),
                personal_claim=True,
            )],
        )
    )
    assert result.score_class == LotClass.D
    assert StopFactor.PERSONAL_CLAIM in result.stop_factors
