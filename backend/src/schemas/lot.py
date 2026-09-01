"""Pydantic v2 схемы для API."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.enums import (
    ClaimKind,
    Gap,
    LotClass,
    OrgStatus,
    Scenario,
    StopFactor,
    TradeKind,
    TradeStatus,
)

# ── Лот ──────────────────────────────────────────────────────────────────────


class PriceIntervalSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    price: Decimal
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_current: bool = False


class PartySourceCheckSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    status: str
    checked_at: datetime | None = None
    next_retry_at: datetime | None = None
    failures: int = 0
    source_url: str | None = None
    last_error: str | None = None
    evidence: dict[str, object] | None = None


class DebtorPartySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    inn: str | None = None
    name: str | None = None
    status: OrgStatus | None = None
    invalid_address: bool = False
    invalid_director: bool = False
    pending_exclusion: bool = False
    address: str | None = None
    director_name: str | None = None
    revenue: Decimal | None = None
    cash: Decimal | None = None
    equity: Decimal | None = None
    kad_as_defendant_count: int | None = None
    kad_bankruptcy_open: bool | None = None
    fssp_sum: Decimal | None = None
    fssp_uncollectible: bool | None = None
    source_as_of: datetime | None = None
    source_checks: list[PartySourceCheckSchema] = Field(default_factory=list)


class ClaimSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: ClaimKind
    principal: Decimal | None = None
    penalties: Decimal | None = None
    currency: str = "RUB"
    base_contract: str | None = None
    base_date: date | None = None
    due_date: date | None = None
    limitations_deadline: date | None = None
    il_issue_date: date | None = None
    il_present_deadline: date | None = None
    court_case_no: str | None = None
    has_judgment: bool | None = None
    has_writ: bool | None = None
    enforcement_alive: bool | None = None
    secured: bool = False
    assignment_forbidden: bool = False
    counterclaim_risk: bool = False
    personal_claim: bool = False
    debtor_party: DebtorPartySchema | None = None
    guarantor_party: DebtorPartySchema | None = None


class LotSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    guid: UUID
    lot_no: int
    title: str | None = None
    description_text: str | None = None
    is_receivable: bool = False
    data_state: str = "unknown"

    nominal_claimed: Decimal | None = None
    start_price: Decimal | None = None
    current_price: Decimal | None = None
    current_interval_from: datetime | None = None
    current_interval_to: datetime | None = None
    cutoff_price: Decimal | None = None

    score_class: LotClass | None = None
    score_ev: Decimal | None = None
    score_ev_low: Decimal | None = None
    score_ev_high: Decimal | None = None
    score_scenario: Scenario | None = None
    score_stop_factors: list[StopFactor] = Field(default_factory=list)
    score_gaps: list[Gap] = Field(default_factory=list)
    score_max_bid: Decimal | None = None
    score_version: str | None = None
    score_updated_at: datetime | None = None
    price_schedule_status: str = "unknown"
    price_observed_at: datetime | None = None
    price_source: str | None = None

    # Flattened trade fields used by the paginated list. The detail endpoint
    # keeps the complete ``trade`` object below, while list rows should not
    # need a second request just to show participation data.
    trade_status: TradeStatus | None = None
    applications_from: datetime | None = None
    applications_to: datetime | None = None
    source_name: str | None = None
    source_url: str | None = None
    participation_exclusion_reason: str | None = None

    price_intervals: list[PriceIntervalSchema] = Field(default_factory=list)
    claims: list[ClaimSchema] = Field(default_factory=list)

    created_at: datetime
    updated_at: datetime


class LotCardSchema(LotSchema):
    """Полная карточка лота для детальной страницы."""

    trade: TradeBriefSchema
    documents: list[DocumentSchema] = Field(default_factory=list)
    score_snapshots: list[ScoreSnapshotSchema] = Field(default_factory=list)


class LotListSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[LotSchema]
    total: int
    page: int
    page_size: int
    pages: int


# ── Торги ─────────────────────────────────────────────────────────────────────


class TradeSourceRefSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    source_url: str
    external_trade_id: str | None = None
    external_lot_id: str | None = None
    captured_at: datetime


class TradeBriefSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    guid: UUID
    efrsb_url: str | None = None
    etp_name: str | None = None
    etp_url: str | None = None
    trade_kind: TradeKind
    status: TradeStatus
    case_number: str | None = None
    court_name: str | None = None
    am_name: str | None = None
    applications_from: datetime | None = None
    applications_to: datetime | None = None
    source_refs: list[TradeSourceRefSchema] = Field(default_factory=list)


class TradeSchema(TradeBriefSchema):
    lots: list[LotSchema] = Field(default_factory=list)


# ── Документы ────────────────────────────────────────────────────────────────


class DocumentSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str | None = None
    title: str | None = None
    external_id: str | None = None
    url: str | None = None
    downloaded_at: datetime | None = None
    text: str | None = None
    extracted_facts: dict | None = None
    processing_status: str = "pending"
    download_attempts: int = 0
    next_retry_at: datetime | None = None
    last_error: str | None = None


# ── Скоринг ──────────────────────────────────────────────────────────────────


class ScoreSnapshotSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    score_class: LotClass
    ev: Decimal
    ev_low: Decimal | None = None
    ev_high: Decimal | None = None
    max_bid: Decimal | None = None
    scenario: Scenario | None = None
    stop_factors: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    model_version: str
    scored_at: datetime


# ── Фильтры ─────────────────────────────────────────────────────────────────


class LotFilter(BaseModel):
    page: int = Field(ge=1, default=1)
    page_size: int = Field(ge=1, le=100, default=20)

    # Класс
    score_class: LotClass | None = None
    min_ev: Decimal | None = Field(default=None, ge=0)
    max_ev: Decimal | None = Field(default=None, ge=0)

    # Дебитор
    debtor_inn: str | None = None

    # Скоринг
    has_stop_factors: bool | None = None
    scenarios: list[Scenario] | None = None

    # Статус торгов
    trade_status: TradeStatus | None = None
    trade_kind: TradeKind | None = None

    # Время
    active_after: datetime | None = None
    deadline_before: datetime | None = None

    @field_validator("debtor_inn")
    @classmethod
    def validate_inn(cls, v: str | None) -> str | None:
        if v and (len(v) not in (10, 12)):
            raise ValueError("ИНН должен быть 10 или 12 цифр")
        return v


# ── Статистика ──────────────────────────────────────────────────────────────


class DashboardStats(BaseModel):
    total_lots: int
    receivable_lots: int
    scored_lots: int
    class_a: int
    class_b: int
    class_c: int
    class_d: int
    alerts_sent_today: int
    stale_scored_lots: int = 0
    last_ingest_at: datetime | None = None
    source_status: str = "unknown"
    active_lots: int = 0
    excluded_lots: int = 0
    ready_recommendations: int = 0
    review_candidates: int = 0
    documents_total: int = 0
    documents_completed: int = 0
    documents_pending: int = 0
    documents_needs_review: int = 0
    documents_retrying: int = 0


# ── Telegram ─────────────────────────────────────────────────────────────────


class AlertCreate(BaseModel):
    lot_id: int
    chat_id: str
    message_id: int | None = None


# ── User Feedback ───────────────────────────────────────────────────────────


class FeedbackCreate(BaseModel):
    lot_id: int = Field(gt=0)
    action: Annotated[str, Field(pattern="^(watch|reject|bought)$")]
    recovered_amount: Decimal | None = Field(default=None, ge=0)
    expense_amount: Decimal | None = Field(default=None, ge=0)
    outcome: Annotated[str | None, Field(pattern="^(in_progress|recovered|not_recovered)$")] = None
    outcome_at: datetime | None = None
    note: str | None = Field(default=None, max_length=4000)


class FeedbackSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lot_id: int
    action: str
    recovered_amount: Decimal | None = None
    expense_amount: Decimal | None = None
    outcome: str | None = None
    outcome_at: datetime | None = None
    note: str | None = None
    created_at: datetime


class CalibrationBucketSchema(BaseModel):
    score_class: str | None = None
    bought: int = 0
    resolved: int = 0
    recovered: int = 0
    unresolved: int = 0
    recovery_rate: Decimal | None = None
    recovered_amount: Decimal = Decimal(0)
    expense_amount: Decimal = Decimal(0)
    net_recovered_amount: Decimal = Decimal(0)
    avg_predicted_ev: Decimal | None = None
    mean_abs_recovered_vs_ev: Decimal | None = None


class CalibrationReportSchema(BaseModel):
    status: str
    min_resolved: int
    total_feedback: int
    decision_counts: dict[str, int]
    purchases: int
    resolved_purchases: int
    unresolved_purchases: int
    recovered_purchases: int
    recovery_rate: Decimal | None = None
    recovered_amount: Decimal = Decimal(0)
    expense_amount: Decimal = Decimal(0)
    net_recovered_amount: Decimal = Decimal(0)
    mean_abs_recovered_vs_ev: Decimal | None = None
    model_versions: list[str] = Field(default_factory=list)
    by_class: list[CalibrationBucketSchema] = Field(default_factory=list)


class DebtorAssignCreate(BaseModel):
    """Manual debtor assignment used when a public card omits the debtor INN."""

    inn: str = Field(min_length=10, max_length=12, pattern=r"^\d{10}(\d{2})?$")
    name: str | None = Field(default=None, max_length=500)


class ClaimProposal(BaseModel):
    """Strict, reviewable subset of facts that may be applied to a claim."""

    model_config = ConfigDict(extra="forbid")

    kind: ClaimKind | None = None
    principal: Decimal | None = Field(default=None, ge=0, le=10**18)
    penalties: Decimal | None = Field(default=None, ge=0, le=10**18)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    base_contract: str | None = Field(default=None, max_length=500)
    base_date: date | None = None
    due_date: date | None = None
    court_case_no: str | None = Field(default=None, max_length=50)
    has_judgment: bool | None = None
    has_writ: bool | None = None
    secured: bool | None = None
    assignment_forbidden: bool | None = None
    counterclaim_risk: bool | None = None
    personal_claim: bool | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class DebtorProposal(BaseModel):
    """Strict debtor facts accepted from document extraction."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=500)
    inn: str | None = Field(default=None, pattern=r"^\d{10}(\d{2})?$")
    ogrn: str | None = Field(default=None, pattern=r"^\d{13}(\d{2})?$")

    @field_validator("inn")
    @classmethod
    def validate_inn_checksum(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from src.connectors.efrsb import is_valid_inn

        if not is_valid_inn(value):
            raise ValueError("invalid INN check digits")
        return value


class DocumentProposalUpdates(BaseModel):
    """The only mutable portion of an LLM document proposal."""

    model_config = ConfigDict(extra="forbid")

    claim: ClaimProposal | None = None
    debtor: DebtorProposal | None = None


# ── Ответы ───────────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.3.0"
    database: str = "not_checked"
    redis: str = "not_used"


class MessageResponse(BaseModel):
    message: str


# These classes are declared below LotCardSchema for a compact file layout.
# Resolve their annotations before FastAPI handles the first real response.
LotCardSchema.model_rebuild()
