"""SQLAlchemy ORM-модели — каноническая схема данных."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from datetime import date as _date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base
from src.models.enums import (
    ClaimKind,
    LotClass,
    OrgStatus,
    PartyRole,
    PersonKind,
    Scenario,
    TradeForm,
    TradeKind,
    TradeStatus,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def enum_string(enum_cls: type[StrEnum], length: int) -> SAEnum:
    """Store enum values as VARCHAR, matching the existing Alembic schema."""
    return SAEnum(
        enum_cls,
        name=f"{enum_cls.__name__.lower()}_enum",
        values_callable=lambda members: [member.value for member in members],
        native_enum=False,
        length=length,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Trade — торги
# ─────────────────────────────────────────────────────────────────────────────


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    guid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, default=uuid.uuid4
    )

    efrsb_trade_guid: Mapped[str | None] = mapped_column(String(50), unique=True)
    efrsb_message_guid: Mapped[str | None] = mapped_column(String(50))
    efrsb_url: Mapped[str | None] = mapped_column(String(500))

    trade_id_on_etp: Mapped[str | None] = mapped_column(String(100))
    etp_inn: Mapped[str | None] = mapped_column(String(12))
    etp_name: Mapped[str | None] = mapped_column(String(200))
    etp_url: Mapped[str | None] = mapped_column(String(500))

    trade_kind: Mapped[str] = mapped_column(
        enum_string(TradeKind, 30),
        default=TradeKind.PUBLIC_OFFER.value,
    )
    trade_form: Mapped[str | None] = mapped_column(
        enum_string(TradeForm, 20)
    )
    is_repeat: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(
        enum_string(TradeStatus, 30),
        default=TradeStatus.ANNOUNCED.value,
    )

    organizer_inn: Mapped[str | None] = mapped_column(String(12))
    organizer_name: Mapped[str | None] = mapped_column(String(300))
    am_inn: Mapped[str | None] = mapped_column(String(12))
    am_name: Mapped[str | None] = mapped_column(String(200))

    bankrupt_party_id: Mapped[int | None] = mapped_column(
        ForeignKey("parties.id", ondelete="SET NULL")
    )
    bankrupt_party: Mapped[Party | None] = relationship(
        "Party", foreign_keys=[bankrupt_party_id], lazy="selectin"
    )

    case_number: Mapped[str | None] = mapped_column(String(30))
    court_name: Mapped[str | None] = mapped_column(String(200))

    applications_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applications_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    payment_info: Mapped[str | None] = mapped_column(Text)
    sale_agreement_rules: Mapped[str | None] = mapped_column(Text)

    raw_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_snapshots.id", ondelete="SET NULL")
    )
    raw_snapshot: Mapped[RawSnapshot | None] = relationship(
        "RawSnapshot", lazy="selectin"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    lots: Mapped[list[Lot]] = relationship(
        "Lot", back_populates="trade", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_trades_status", "status"),
        Index("ix_trades_etp_inn", "etp_inn"),
        Index("ix_trades_applications_to", "applications_to"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Lot — лот
# ─────────────────────────────────────────────────────────────────────────────


class Lot(Base):
    __tablename__ = "lots"

    id: Mapped[int] = mapped_column(primary_key=True)
    guid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, default=uuid.uuid4
    )

    trade_id: Mapped[int] = mapped_column(
        ForeignKey("trades.id", ondelete="CASCADE")
    )
    trade: Mapped[Trade] = relationship("Trade", back_populates="lots")

    lot_no: Mapped[int] = mapped_column(Integer)

    classifier_codes: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list)
    classifier_labels: Mapped[list[str]] = mapped_column(ARRAY(String(200)), default=list)
    is_receivable: Mapped[bool] = mapped_column(Boolean, default=False)

    title: Mapped[str | None] = mapped_column(String(500))
    description_html: Mapped[str | None] = mapped_column(Text)
    description_text: Mapped[str | None] = mapped_column(Text)

    nominal_claimed: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    start_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    current_interval_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    current_interval_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    cutoff_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))

    deposit_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    deposit_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    price_reduction_html: Mapped[str | None] = mapped_column(Text)

    inspection_rules: Mapped[str | None] = mapped_column(Text)
    bundle_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    docs_on_efrsb: Mapped[bool] = mapped_column(Boolean, default=False)
    docs_on_etp: Mapped[bool] = mapped_column(Boolean, default=False)

    score_class: Mapped[str | None] = mapped_column(
        enum_string(LotClass, 2)
    )
    score_ev: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    score_ev_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    score_ev_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    score_scenario: Mapped[str | None] = mapped_column(
        enum_string(Scenario, 30)
    )
    score_stop_factors: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), default=list
    )
    score_gaps: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list)
    score_max_bid: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    score_version: Mapped[str | None] = mapped_column(String(20))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    price_intervals: Mapped[list[PriceInterval]] = relationship(
        "PriceInterval", back_populates="lot", cascade="all, delete-orphan"
    )
    claims: Mapped[list[Claim]] = relationship(
        "Claim", back_populates="lot", cascade="all, delete-orphan"
    )
    score_snapshots: Mapped[list[ScoreSnapshot]] = relationship(
        "ScoreSnapshot", back_populates="lot", cascade="all, delete-orphan"
    )
    documents: Mapped[list[Document]] = relationship(
        "Document", back_populates="lot", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("trade_id", "lot_no", name="uq_trade_lot_no"),
        Index("ix_lots_score_class", "score_class"),
        Index("ix_lots_score_ev", "score_ev"),
        Index("ix_lots_is_receivable", "is_receivable"),
        Index("ix_lots_current_interval_to", "current_interval_to"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# PriceInterval — шаг публичного предложения
# ─────────────────────────────────────────────────────────────────────────────


class PriceInterval(Base):
    __tablename__ = "price_intervals"

    id: Mapped[int] = mapped_column(primary_key=True)

    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id", ondelete="CASCADE"))
    lot: Mapped[Lot] = relationship("Lot", back_populates="price_intervals")

    seq: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_price_intervals_lot_seq", "lot_id", "seq"),)


# ─────────────────────────────────────────────────────────────────────────────
# Party — лицо (банкрот, дебитор, поручитель и т.д.)
# ─────────────────────────────────────────────────────────────────────────────


class Party(Base):
    __tablename__ = "parties"

    id: Mapped[int] = mapped_column(primary_key=True)
    guid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, default=uuid.uuid4
    )

    role: Mapped[str] = mapped_column(enum_string(PartyRole, 20))
    person_kind: Mapped[str | None] = mapped_column(
        enum_string(PersonKind, 10)
    )
    inn: Mapped[str | None] = mapped_column(String(12), index=True)
    ogrn: Mapped[str | None] = mapped_column(String(15))
    name: Mapped[str | None] = mapped_column(String(500))

    status: Mapped[str | None] = mapped_column(
        enum_string(OrgStatus, 20)
    )
    reg_date: Mapped[_date | None] = mapped_column(Date)

    address: Mapped[str | None] = mapped_column(String(500))
    invalid_address: Mapped[bool] = mapped_column(Boolean, default=False)
    invalid_director: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_exclusion: Mapped[bool] = mapped_column(Boolean, default=False)
    mass_address: Mapped[bool] = mapped_column(Boolean, default=False)

    director_name: Mapped[str | None] = mapped_column(String(200))

    revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    cash: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    current_assets: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    equity: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    short_term_liab: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    bo_year: Mapped[int | None] = mapped_column(Integer)
    tax_debt: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    headcount: Mapped[int | None] = mapped_column(Integer)

    kad_as_defendant_count: Mapped[int | None] = mapped_column(Integer)
    kad_bankruptcy_open: Mapped[bool] = mapped_column(Boolean, default=False)

    fssp_sum: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    fssp_uncollectible: Mapped[bool] = mapped_column(Boolean, default=False)

    source_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    bankrupt_trades: Mapped[list[Trade]] = relationship(
        "Trade",
        foreign_keys="Trade.bankrupt_party_id",
        back_populates="bankrupt_party",
    )
    debtor_claims: Mapped[list[Claim]] = relationship(
        "Claim",
        foreign_keys="Claim.debtor_party_id",
        back_populates="debtor_party",
    )
    guarantor_claims: Mapped[list[Claim]] = relationship(
        "Claim",
        foreign_keys="Claim.guarantor_party_id",
        back_populates="guarantor_party",
    )

    __table_args__ = (
        UniqueConstraint("role", "inn", name="uq_party_role_inn"),
        Index("ix_parties_role_status", "role", "status"),
        Index("ix_parties_kad_bankruptcy", "kad_bankruptcy_open"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Claim — уступаемое требование
# ─────────────────────────────────────────────────────────────────────────────


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    guid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, default=uuid.uuid4
    )

    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id", ondelete="CASCADE"))
    lot: Mapped[Lot] = relationship("Lot", back_populates="claims")

    kind: Mapped[str] = mapped_column(
        enum_string(ClaimKind, 30), default=ClaimKind.UNKNOWN.value
    )

    principal: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    penalties: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), default="RUB")

    base_contract: Mapped[str | None] = mapped_column(String(500))
    base_date: Mapped[_date | None] = mapped_column(Date)
    due_date: Mapped[_date | None] = mapped_column(Date)
    limitations_deadline: Mapped[_date | None] = mapped_column(Date)

    il_issue_date: Mapped[_date | None] = mapped_column(Date)
    il_present_deadline: Mapped[_date | None] = mapped_column(Date)
    court_case_no: Mapped[str | None] = mapped_column(String(50))
    has_judgment: Mapped[bool] = mapped_column(Boolean, default=False)
    has_writ: Mapped[bool] = mapped_column(Boolean, default=False)
    enforcement_alive: Mapped[bool] = mapped_column(Boolean, default=False)

    secured: Mapped[bool] = mapped_column(Boolean, default=False)
    assignment_forbidden: Mapped[bool] = mapped_column(Boolean, default=False)
    counterclaim_risk: Mapped[bool] = mapped_column(Boolean, default=False)
    personal_claim: Mapped[bool] = mapped_column(Boolean, default=False)

    debtor_party_id: Mapped[int | None] = mapped_column(
        ForeignKey("parties.id", ondelete="SET NULL")
    )
    debtor_party: Mapped[Party | None] = relationship(
        "Party", foreign_keys=[debtor_party_id], back_populates="debtor_claims"
    )
    guarantor_party_id: Mapped[int | None] = mapped_column(
        ForeignKey("parties.id", ondelete="SET NULL")
    )
    guarantor_party: Mapped[Party | None] = relationship(
        "Party", foreign_keys=[guarantor_party_id], back_populates="guarantor_claims"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (Index("ix_claims_lot_id", "lot_id"),)


# ─────────────────────────────────────────────────────────────────────────────
# Document — документы лота
# ─────────────────────────────────────────────────────────────────────────────


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    guid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, default=uuid.uuid4
    )

    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id", ondelete="CASCADE"))
    lot: Mapped[Lot] = relationship("Lot", back_populates="documents")

    kind: Mapped[str | None] = mapped_column(String(50))
    title: Mapped[str | None] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(1000))
    sha256: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str | None] = mapped_column(Text)
    extracted_facts: Mapped[dict | None] = mapped_column(JSONB)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


# ─────────────────────────────────────────────────────────────────────────────
# ScoreSnapshot — снимок скоринга
# ─────────────────────────────────────────────────────────────────────────────


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    guid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, default=uuid.uuid4
    )

    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id", ondelete="CASCADE"))
    lot: Mapped[Lot] = relationship("Lot", back_populates="score_snapshots")

    score_class: Mapped[str] = mapped_column(enum_string(LotClass, 2))
    ev: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    ev_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    ev_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    max_bid: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    scenario: Mapped[str | None] = mapped_column(
        enum_string(Scenario, 30)
    )
    stop_factors: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list)
    gaps: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list)
    model_version: Mapped[str] = mapped_column(String(20))
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_score_snapshots_lot_scored", "lot_id", "scored_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# RawSnapshot — сырой HTML/XML снимок
# ─────────────────────────────────────────────────────────────────────────────


class RawSnapshot(Base):
    __tablename__ = "raw_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    content_type: Mapped[str | None] = mapped_column(String(50))
    raw_content: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_raw_snapshots_source", "source"),)


# ─────────────────────────────────────────────────────────────────────────────
# ImportRun / ImportCheckpoint — наблюдаемость и возобновление ingest
# ─────────────────────────────────────────────────────────────────────────────


class ImportRun(Base):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_page: Mapped[int] = mapped_column(Integer, default=0)
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_upserted: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(String(500))


class ImportCheckpoint(Base):
    __tablename__ = "import_checkpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50), unique=True)
    cursor: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# AlertState — дедупликация Telegram-алертов
# ─────────────────────────────────────────────────────────────────────────────


class AlertState(Base):
    __tablename__ = "alerts_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    alerted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_alerts_state_lot_time", "lot_id", "alerted_at"),
        Index("ix_alerts_state_lot_chat_time", "lot_id", "chat_id", "alerted_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# UserFeedback — обратная связь
# ─────────────────────────────────────────────────────────────────────────────


class UserFeedback(Base):
    __tablename__ = "user_feedbacks"

    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lots.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(20))  # watch, reject, bought
    recovered_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    __table_args__ = (Index("ix_user_feedbacks_lot_id", "lot_id"),)
