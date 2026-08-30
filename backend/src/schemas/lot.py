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


class DebtorPartySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    inn: str | None = None
    name: str | None = None
    status: OrgStatus | None = None
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
    has_judgment: bool = False
    has_writ: bool = False
    enforcement_alive: bool = False
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


class TradeSchema(TradeBriefSchema):
    lots: list[LotSchema] = Field(default_factory=list)


# ── Документы ────────────────────────────────────────────────────────────────


class DocumentSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str | None = None
    title: str | None = None
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
    note: str | None = Field(default=None, max_length=4000)


class FeedbackSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lot_id: int
    action: str
    recovered_amount: Decimal | None = None
    note: str | None = None
    created_at: datetime


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
