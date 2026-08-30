"""Скоринг v1 — чистые функции без I/O.

Формула ожидаемой стоимости (EV) и класс лота A–D.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from src.config import get_settings
from src.models.enums import Gap, LotClass, Scenario, StopFactor

if TYPE_CHECKING:
    from src.schemas.lot import ClaimSchema, DebtorPartySchema


def _d(value: Decimal | float | int | None, default: Decimal = Decimal(0)) -> Decimal:
    if value is None:
        return default
    return Decimal(str(value))


def estimate_recovery_time(
    has_judgment: bool,
    has_writ: bool,
    enforcement_alive: bool,
    limitations_deadline: date | None,
    il_present_deadline: date | None,
) -> tuple[int, int]:
    """Возвращает (медиана_мес, пессимизм_мес)."""
    today = date.today()

    if has_writ:
        # Есть ИЛ → исполнительное
        return 6, 18

    if has_judgment:
        # Есть решение суда, ИЛ нет
        return 12, 36

    if enforcement_alive:
        # Открытое ИП
        return 12, 30

    # Нет ни решения, ни ИЛ
    # Если срок исковой давности близко или прошёл — не взыскивать
    if limitations_deadline is not None:
        months_left = max(
            0, (limitations_deadline.year - today.year) * 12 + (limitations_deadline.month - today.month)
        )
        if months_left < 3:
            return 999, 999  # флаг стоп-фактора выставится отдельно

    return 24, 60


def compute_success_rate(
    has_judgment: bool,
    has_writ: bool,
    enforcement_alive: bool,
    claim_kind: str,
    secured: bool,
    counterclaim_risk: bool,
    debtor_cash: Decimal | None,
    debtor_equity: Decimal | None,
    debtor_fssp_uncollectible: bool,
    debtor_kad_bankruptcy_open: bool,
) -> Decimal:
    """Шанс реального взыскания (0.0 – 1.0)."""
    rate = Decimal("0.30")  # базовое

    if has_writ:
        rate = Decimal("0.75")
    elif has_judgment:
        rate = Decimal("0.55")
    elif enforcement_alive:
        rate = Decimal("0.45")

    if secured:
        rate += Decimal("0.15")

    if counterclaim_risk:
        rate -= Decimal("0.15")

    if debtor_kad_bankruptcy_open:
        rate *= Decimal("0.60")

    if debtor_fssp_uncollectible:
        rate *= Decimal("0.30")

    cash = _d(debtor_cash)
    equity = _d(debtor_equity)
    if cash > 0 or equity > 0:
        rate = min(Decimal("1.0"), rate + Decimal("0.05"))

    return max(Decimal("0.0"), min(Decimal("1.0"), rate))


def _claim_rank(claim: ClaimSchema) -> tuple[Decimal, int, int, int, int, int]:
    """Return a stable evidence rank for selecting a representative claim."""
    return (
        _d(claim.principal),
        int(bool(claim.has_writ)),
        int(bool(claim.has_judgment)),
        int(bool(claim.enforcement_alive)),
        int(bool(claim.secured)),
        -claim.id,
    )


def _scenario_for_claim(claim: ClaimSchema, override: Scenario | None) -> Scenario:
    if override is not None:
        return override
    if claim.has_writ:
        return Scenario.ENFORCEMENT
    if claim.has_judgment:
        return Scenario.COURT
    return Scenario.NEGOTIATION


def _cost_for_scenario(scenario: Scenario, settings) -> Decimal:
    if scenario == Scenario.ENFORCEMENT:
        return Decimal(settings.cost_enforcement_rub)
    if scenario == Scenario.COURT:
        return Decimal(settings.cost_court_rub)
    if scenario == Scenario.NEGOTIATION:
        return Decimal(settings.cost_court_rub) * Decimal("0.5")
    if scenario == Scenario.SUBSIDIARY:
        return Decimal(settings.cost_bankruptcy_rub)
    return Decimal(settings.cost_enforcement_rub)


def _discount_for_scenario(scenario: Scenario, settings) -> Decimal:
    if scenario == Scenario.ENFORCEMENT:
        return Decimal(settings.default_discount_a)
    if scenario == Scenario.COURT:
        return Decimal(settings.default_discount_b)
    if scenario == Scenario.NEGOTIATION:
        return Decimal(settings.default_discount_c)
    return Decimal(settings.default_discount_d)


@dataclass
class ScoreInput:
    """Входные данные для скоринга одного лота."""

    lot_id: int
    start_price: Decimal | None = None
    current_price: Decimal | None = None
    # False when a schedule is known but there is no active price interval.
    current_price_confirmed: bool = True
    cutoff_price: Decimal | None = None
    nominal_claimed: Decimal | None = None
    is_bundle: bool = False
    description_text: str | None = None

    # Данные дебитора
    debtor: DebtorPartySchema | None = None

    # Данные требований
    claims: list[ClaimSchema] = field(default_factory=list)

    # Настройки
    scenario_override: Scenario | None = None

    # Гэпы
    gaps: list[Gap] = field(default_factory=list)


@dataclass
class ScoreResult:
    """Результат скоринга."""

    lot_id: int
    score_class: LotClass
    ev: Decimal
    ev_low: Decimal
    ev_high: Decimal
    max_bid: Decimal
    scenario: Scenario
    stop_factors: list[StopFactor]
    gaps: list[Gap]
    version: str = "v1.0"

    def to_dict(self) -> dict:
        return {
            "lot_id": self.lot_id,
            "score_class": self.score_class.value,
            "ev": str(self.ev),
            "ev_low": str(self.ev_low),
            "ev_high": str(self.ev_high),
            "max_bid": str(self.max_bid),
            "scenario": self.scenario.value,
            "stop_factors": [s.value for s in self.stop_factors],
            "gaps": [g.value for g in self.gaps],
            "version": self.version,
        }


def compute_ev_and_class(inp: ScoreInput) -> ScoreResult:
    """Основная функция скоринга."""
    settings = get_settings()

    stop_factors: list[StopFactor] = []
    gaps: list[Gap] = list(inp.gaps)

    # ── 1. Цена ──────────────────────────────────────────────────────────────
    # Текущая цена = start_price если ещё не стартовала
    current_price = _d(inp.current_price) or _d(inp.start_price, Decimal(0))
    cutoff = _d(inp.cutoff_price, Decimal(0))

    if not inp.current_price_confirmed:
        stop_factors.append(StopFactor.CURRENT_PRICE_UNAVAILABLE)
    if current_price == 0:
        stop_factors.append(StopFactor.NO_SOURCE_OF_FUNDS)

    # ── 2. Дебитор ───────────────────────────────────────────────────────────
    debtor = inp.debtor

    if debtor:
        debtor_status = debtor.status.value if debtor.status else None
        if debtor_status == "excluded":
            stop_factors.append(StopFactor.DEBTOR_EXCLUDED)
        elif debtor_status == "liquidation":
            stop_factors.append(StopFactor.DEBTOR_LIQUIDATION)
        source_as_of = debtor.source_as_of
        if (
            debtor.status is None
            or source_as_of is None
            or source_as_of.tzinfo is None
            or source_as_of < datetime.now(UTC) - timedelta(days=1)
        ):
            stop_factors.append(StopFactor.DEBTOR_UNVERIFIED)

        if debtor.fssp_uncollectible:
            gaps.append(Gap.FSSP_UNCOLLECTIBLE)

        if debtor.kad_bankruptcy_open:
            gaps.append(Gap.KAD_BANKRUPTCY_OPEN)

    # ── 3. Требования ────────────────────────────────────────────────────────
    total_principal = Decimal(0)
    total_with_penalties = Decimal(0)
    best_claim_has_judgment = False
    best_claim_has_writ = False
    best_enforcement_alive = False
    best_limitation_deadline: date | None = None
    best_il_present_deadline: date | None = None
    best_claim_counterclaim_risk = False
    best_secured = False
    best_kind = "unknown"

    for claim in inp.claims:
        principal = _d(claim.principal)
        penalties = _d(claim.penalties)
        total_principal += principal
        total_with_penalties += principal + penalties

    best_claim = max(inp.claims, key=_claim_rank, default=None)
    if best_claim is not None:
        best_claim_has_judgment = bool(best_claim.has_judgment)
        best_claim_has_writ = bool(best_claim.has_writ)
        best_enforcement_alive = bool(best_claim.enforcement_alive)
        best_limitation_deadline = best_claim.limitations_deadline
        best_il_present_deadline = best_claim.il_present_deadline
        best_claim_counterclaim_risk = best_claim.counterclaim_risk
        best_secured = best_claim.secured
        best_kind = best_claim.kind.value

    # ── 4. Стоп-факторы ──────────────────────────────────────────────────────
    today = date.today()

    if not debtor or not debtor.inn:
        stop_factors.append(StopFactor.NO_DEBTOR_INN)
        gaps.append(Gap.DEBTOR_INN_MISSING)

    debtor_inns = {
        claim.debtor_party.inn
        for claim in inp.claims
        if claim.debtor_party is not None and claim.debtor_party.inn
    }
    if len(debtor_inns) > 1:
        stop_factors.append(StopFactor.MULTIPLE_DEBTORS)

    if any(
        claim.limitations_deadline is not None and claim.limitations_deadline < today
        for claim in inp.claims
    ):
        stop_factors.append(StopFactor.LIMITATIONS_EXPIRED)
    if any(
        claim.il_present_deadline is not None and claim.il_present_deadline < today
        for claim in inp.claims
    ):
        stop_factors.append(StopFactor.IL_PRESENT_EXPIRED)

    if any(claim.personal_claim for claim in inp.claims):
        stop_factors.append(StopFactor.PERSONAL_CLAIM)
    if any(claim.assignment_forbidden for claim in inp.claims):
        stop_factors.append(StopFactor.ASSIGNMENT_FORBIDDEN)
    if any(claim.counterclaim_risk for claim in inp.claims):
        stop_factors.append(StopFactor.COUNTERCLAIM_RISK)
    if any(claim.currency.upper() != "RUB" for claim in inp.claims):
        stop_factors.append(StopFactor.UNSUPPORTED_CURRENCY)
    if any(claim.kind.value == "registry_claim_on_bankrupt" for claim in inp.claims):
        stop_factors.append(StopFactor.REGISTRY_CLAIM_ON_BANKRUPT)
    if inp.is_bundle and len(inp.claims) <= 1:
        gaps.append(Gap.BUNDLE_NO_DETAIL)
        stop_factors.append(StopFactor.BUNDLE_NO_DETAIL)

    # ── 5. Определение сценария ──────────────────────────────────────────────
    if inp.scenario_override:
        scenario = inp.scenario_override
    elif best_claim_has_writ:
        scenario = Scenario.ENFORCEMENT
    elif best_claim_has_judgment:
        scenario = Scenario.COURT
    else:
        scenario = Scenario.NEGOTIATION

    # ── 6. Время и успех ────────────────────────────────────────────────────
    med_months, pessim_months = estimate_recovery_time(
        best_claim_has_judgment,
        best_claim_has_writ,
        best_enforcement_alive,
        best_limitation_deadline,
        best_il_present_deadline,
    )
    if med_months == 999:
        if StopFactor.LIMITATIONS_EXPIRED not in stop_factors:
            stop_factors.append(StopFactor.LIMITATIONS_EXPIRED)
        return ScoreResult(
            lot_id=inp.lot_id,
            score_class=LotClass.D,
            ev=Decimal(0),
            ev_low=Decimal(0),
            ev_high=Decimal(0),
            max_bid=Decimal(0),
            scenario=scenario,
            stop_factors=stop_factors,
            gaps=gaps,
        )

    if total_principal > 0 and inp.claims:
        # Claims may have different evidence. Weight their individual
        # recovery probabilities by principal instead of applying the
        # strongest claim's rate to the whole bundle.
        weighted_success = Decimal(0)
        for claim in inp.claims:
            principal = _d(claim.principal)
            if principal <= 0:
                continue
            weighted_success += principal * compute_success_rate(
                bool(claim.has_judgment),
                bool(claim.has_writ),
                bool(claim.enforcement_alive),
                claim.kind.value,
                bool(claim.secured),
                bool(claim.counterclaim_risk),
                debtor.cash if debtor else None,
                debtor.equity if debtor else None,
                bool(debtor.fssp_uncollectible) if debtor else False,
                bool(debtor.kad_bankruptcy_open) if debtor else False,
            )
        success_rate = weighted_success / total_principal
    else:
        success_rate = compute_success_rate(
            best_claim_has_judgment,
            best_claim_has_writ,
            best_enforcement_alive,
            best_kind,
            best_secured,
            best_claim_counterclaim_risk,
            debtor.cash if debtor else None,
            debtor.equity if debtor else None,
            bool(debtor.fssp_uncollectible) if debtor else False,
            bool(debtor.kad_bankruptcy_open) if debtor else False,
        )

    # ── 8. Выбор базы ────────────────────────────────────────────────────────
    # Приоритет: номинал → сумма требований → start_price
    if total_principal > 0:
        base = total_principal
    elif inp.nominal_claimed and inp.nominal_claimed > 0:
        base = inp.nominal_claimed
        gaps.append(Gap.NOMINAL_ESTIMATED)
        stop_factors.append(StopFactor.NOMINAL_UNVERIFIED)
    elif inp.nominal_claimed is None or inp.nominal_claimed <= 0:
        base = Decimal(0)
        gaps.append(Gap.NOMINAL_MISSING)
    else:
        base = Decimal(0)
        gaps.append(Gap.NOMINAL_MISSING)

    # ── 9. EV ───────────────────────────────────────────────────────────────
    # A bundle can contain claims with different evidence. Apply each claim's
    # discount, recovery time and cost before aggregating; using the strongest
    # representative claim for the whole bundle overstates EV.
    if total_principal > 0 and inp.claims:
        recovered = Decimal(0)
        recovered_low = Decimal(0)
        recovered_high = Decimal(0)
        weighted_months = Decimal(0)
        weighted_pessimistic_months = Decimal(0)
        weighted_cost = Decimal(0)
        for claim in inp.claims:
            principal = _d(claim.principal)
            if principal <= 0:
                continue
            claim_scenario = _scenario_for_claim(claim, inp.scenario_override)
            claim_success = compute_success_rate(
                bool(claim.has_judgment),
                bool(claim.has_writ),
                bool(claim.enforcement_alive),
                claim.kind.value,
                bool(claim.secured),
                bool(claim.counterclaim_risk),
                debtor.cash if debtor else None,
                debtor.equity if debtor else None,
                bool(debtor.fssp_uncollectible) if debtor else False,
                bool(debtor.kad_bankruptcy_open) if debtor else False,
            )
            claim_months, claim_pessimistic_months = estimate_recovery_time(
                bool(claim.has_judgment),
                bool(claim.has_writ),
                bool(claim.enforcement_alive),
                claim.limitations_deadline,
                claim.il_present_deadline,
            )
            if claim_months == 999:
                if StopFactor.LIMITATIONS_EXPIRED not in stop_factors:
                    stop_factors.append(StopFactor.LIMITATIONS_EXPIRED)
                return ScoreResult(
                    lot_id=inp.lot_id,
                    score_class=LotClass.D,
                    ev=Decimal(0),
                    ev_low=Decimal(0),
                    ev_high=Decimal(0),
                    max_bid=Decimal(0),
                    scenario=scenario,
                    stop_factors=stop_factors,
                    gaps=gaps,
                )
            discount = _discount_for_scenario(claim_scenario, settings)
            recovered += principal * discount * claim_success
            recovered_low += principal * Decimal("0.3") * claim_success
            recovered_high += principal * discount * min(
                Decimal("1.0"), claim_success + Decimal("0.1")
            )
            weighted_months += principal * Decimal(claim_months)
            weighted_pessimistic_months += principal * Decimal(claim_pessimistic_months)
            weighted_cost += principal * _cost_for_scenario(claim_scenario, settings)
        cost = weighted_cost / total_principal
        med_months_decimal = weighted_months / total_principal
        pessimistic_months_decimal = weighted_pessimistic_months / total_principal
    else:
        discount = _discount_for_scenario(scenario, settings)
        cost = _cost_for_scenario(scenario, settings)
        recovered = base * discount * success_rate
        recovered_low = base * Decimal("0.3") * success_rate
        recovered_high = base * discount * min(
            Decimal("1.0"), success_rate + Decimal("0.1")
        )
        med_months_decimal = Decimal(med_months)
        pessimistic_months_decimal = Decimal(pessim_months)

    purchase_price = current_price
    annual_rate = _d(settings.alternative_rate)
    time_cost = purchase_price * annual_rate * med_months_decimal / Decimal(12)
    pessimistic_time_cost = (
        purchase_price * annual_rate * pessimistic_months_decimal / Decimal(12)
    )
    ev_optimistic = recovered - purchase_price - cost - time_cost
    ev_low_raw = recovered_low - purchase_price - cost - pessimistic_time_cost
    ev_high_raw = recovered_high - purchase_price - cost - time_cost

    ev_optimistic = ev_optimistic.quantize(Decimal("1"), ROUND_HALF_UP)
    ev_low = ev_low_raw.quantize(Decimal("1"), ROUND_HALF_UP)
    ev_high = ev_high_raw.quantize(Decimal("1"), ROUND_HALF_UP)

    # ── 11. Класс ────────────────────────────────────────────────────────────
    if stop_factors:
        cls = LotClass.D
    elif ev_optimistic >= Decimal("500000") and success_rate >= Decimal("0.5"):
        cls = LotClass.A
    elif ev_optimistic >= Decimal("100000") and success_rate >= Decimal("0.35"):
        cls = LotClass.B
    elif ev_optimistic > 0:
        cls = LotClass.C
    else:
        cls = LotClass.D

    # ── 12. Max Bid ──────────────────────────────────────────────────────────
    # Не покупать выше чем EV * 0.5
    max_bid = max(Decimal(0), ev_optimistic * Decimal("0.5")).quantize(Decimal("1"), ROUND_HALF_UP)
    # Если есть cutoff — не выше него
    if cutoff > 0:
        max_bid = min(max_bid, cutoff)
    # Не выше текущей цены + 20%
    if current_price > 0:
        max_bid = min(max_bid, current_price * Decimal("1.2"))
    max_bid = max(Decimal(0), max_bid)
    if stop_factors:
        max_bid = Decimal(0)

    return ScoreResult(
        lot_id=inp.lot_id,
        score_class=cls,
        ev=ev_optimistic,
        ev_low=ev_low,
        ev_high=ev_high,
        max_bid=max_bid,
        scenario=scenario,
        stop_factors=stop_factors,
        gaps=gaps,
    )
