"""Константы — enum'ы и справочники."""
from datetime import UTC, datetime
from enum import StrEnum


class TradeKind(StrEnum):
    PUBLIC_OFFER = "public_offer"
    OPEN_BIDDING = "open_bidding"
    CLOSED_BIDDING = "closed_bidding"


class TradeForm(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class TradeStatus(StrEnum):
    ANNOUNCED = "announced"
    APPLICATIONS_OPEN = "applications_open"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DID_NOT_TAKE_PLACE = "did_not_take_place"
    CANCELLED = "cancelled"
    SUSPENDED = "suspended"


# Only these statuses are shown to users. ``in_progress`` is deliberately not
# included: once the auction has started, a new buyer cannot submit a bid.
# Unknown values are intentionally excluded until the source label is mapped
# explicitly.
PARTICIPABLE_TRADE_STATUSES = frozenset(
    {
        TradeStatus.ANNOUNCED.value,
        TradeStatus.APPLICATIONS_OPEN.value,
    }
)


def is_participable_trade_status(value: object) -> bool:
    """Return whether a normalized status is eligible for user-facing output."""
    return isinstance(value, str) and value in PARTICIPABLE_TRADE_STATUSES


def participation_exclusion_reason(
    status: object,
    applications_to: object,
    *,
    now: datetime | None = None,
) -> str | None:
    """Return a stable reason when a lot cannot be shown as actionable.

    The gate fails closed. A missing/naive deadline is not treated as an open
    application window, while a known future deadline is required for both
    announced and application-open trades.
    """
    if status == TradeStatus.IN_PROGRESS.value:
        return "trading_started"
    if not is_participable_trade_status(status):
        return "status_not_eligible"
    if not isinstance(applications_to, datetime):
        return "deadline_unknown"
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    if applications_to.tzinfo is None:
        return "deadline_unknown"
    if applications_to.astimezone(UTC) <= reference.astimezone(UTC):
        return "application_deadline_passed"
    return None


def is_participable_trade_now(
    status: object,
    applications_to: object,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether a trade is safe to expose as currently actionable."""
    return participation_exclusion_reason(status, applications_to, now=now) is None


def normalize_trade_status(value: object) -> str | None:
    """Map Russian/source labels to the canonical trade status enum.

    The mapping is deliberately conservative: a label that is not explicitly
    understood returns ``None`` and is therefore rejected by the ingest gate.
    """
    text = str(value or "").strip().casefold().replace("ё", "е")
    if not text:
        return None
    if text in {status.value for status in TradeStatus}:
        return text
    mapping = (
        ("прием заявок заверш", TradeStatus.COMPLETED.value),
        ("приём заявок заверш", TradeStatus.COMPLETED.value),
        ("идут торги", TradeStatus.IN_PROGRESS.value),
        ("торги идут", TradeStatus.IN_PROGRESS.value),
        ("подведение итогов", TradeStatus.COMPLETED.value),
        ("не состоял", TradeStatus.DID_NOT_TAKE_PLACE.value),
        ("аннулирован", TradeStatus.CANCELLED.value),
        ("отмен", TradeStatus.CANCELLED.value),
        ("приостанов", TradeStatus.SUSPENDED.value),
        ("заверш", TradeStatus.COMPLETED.value),
        ("открыт прием заявок", TradeStatus.APPLICATIONS_OPEN.value),
        ("открыт приём заявок", TradeStatus.APPLICATIONS_OPEN.value),
        ("прием заявок", TradeStatus.APPLICATIONS_OPEN.value),
        ("приём заявок", TradeStatus.APPLICATIONS_OPEN.value),
        ("объявлен", TradeStatus.ANNOUNCED.value),
    )
    return next((status for marker, status in mapping if marker in text), None)


class PersonKind(StrEnum):
    UL = "ul"  # Юрлицо
    IP = "ip"  # ИП
    FL = "fl"  # Физлицо


class PartyRole(StrEnum):
    BANKRUPT = "bankrupt"
    DEBTOR = "debtor"
    GUARANTOR = "guarantor"
    KDL = "kdl"  # Контролирующее должника лицо
    ORGANIZER = "organizer"
    AM = "am"  # Арбитражный управляющий


class OrgStatus(StrEnum):
    ACTIVE = "active"
    LIQUIDATION = "liquidation"
    EXCLUDED = "excluded"
    BANKRUPTCY = "bankruptcy"
    INVALID = "invalid"  # Недостоверные сведения


class ClaimKind(StrEnum):
    TRADE_AR = "trade_ar"  # Требование, уступленное в рамках АР
    ADVANCE = "advance"  # Аванс
    LOAN = "loan"  # Заём / кредит
    RESTITUTION = "restitution"  # Реституция
    SUBSIDIARY = "subsidiary"  # Субсидиарка
    REGISTRY_CLAIM_ON_BANKRUPT = "registry_claim_on_bankrupt"  # Место в реестре
    UNKNOWN = "unknown"


class LotClass(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"  # Сток-фактор, покупать запрещено


class Scenario(StrEnum):
    NEGOTIATION = "negotiation"  # Мировое соглашение
    COURT = "court"  # Судебное взыскание
    ENFORCEMENT = "enforcement"  # Исполнительное производство
    DEBTOR_BANKRUPTCY = "debtor_bankruptcy"  # Банкротство дебитора
    SUBSIDIARY = "subsidiary"  # Субсидиарная ответственность КДЛ


class StopFactor(StrEnum):
    NO_DEBTOR_INN = "no_debtor_inn"
    DEBTOR_UNVERIFIED = "debtor_unverified"
    MULTIPLE_DEBTORS = "multiple_debtors"
    DEBTOR_EXCLUDED = "debtor_excluded"
    DEBTOR_LIQUIDATION = "debtor_liquidation"
    LIMITATIONS_EXPIRED = "limitations_expired"
    IL_PRESENT_EXPIRED = "il_present_expired"
    PERSONAL_CLAIM = "personal_claim"
    ASSIGNMENT_FORBIDDEN = "assignment_forbidden"
    BUNDLE_NO_DETAIL = "bundle_no_detail"
    NO_SOURCE_OF_FUNDS = "no_source_of_funds"
    DEBTOR_BANKRUPT_FINISHED = "debtor_bankrupt_finished"
    COUNTERCLAIM_RISK = "counterclaim_risk"
    UNSUPPORTED_CURRENCY = "unsupported_currency"
    REGISTRY_CLAIM_ON_BANKRUPT = "registry_claim_on_bankrupt"
    NOMINAL_UNVERIFIED = "nominal_unverified"
    CURRENT_PRICE_UNAVAILABLE = "current_price_unavailable"
    DEBTOR_INVALID = "debtor_invalid"
    DEBTOR_PENDING_EXCLUSION = "debtor_pending_exclusion"


class Gap(StrEnum):
    DEBTOR_INN_MISSING = "debtor_inn_missing"
    DEBTOR_INN_UNVERIFIED = "debtor_inn_unverified"
    DEBTOR_STATUS_MISSING = "debtor_status_missing"
    BO_MISSING = "bo_missing"
    KAD_MISSING = "kad_missing"
    FSSP_MISSING = "fssp_missing"
    KAD_UNVERIFIED = "kad_unverified"
    FSSP_UNVERIFIED = "fssp_unverified"
    KAD_BANKRUPTCY_OPEN = "kad_bankruptcy_open"
    FSSP_UNCOLLECTIBLE = "fssp_uncollectible"
    CLAIM_EVIDENCE_UNVERIFIED = "claim_evidence_unverified"
    SCHEDULE_UNPARSED = "schedule_unparsed"
    NOMINAL_ESTIMATED = "nominal_estimated"  # Номинал взят из start_price
    NOMINAL_MISSING = "nominal_missing"
    BUNDLE_NO_DETAIL = "bundle_no_detail"


class PriceScheduleStatus(StrEnum):
    UNKNOWN = "unknown"
    PARSED = "parsed"
    NOT_PRESENT = "not_present"
    NOT_STARTED = "not_started"
    UNPARSED = "unparsed"
    EXPIRED = "expired"
    STALE = "stale"


class DocumentProcessingStatus(StrEnum):
    PENDING = "pending"
    RETRYING = "retrying"
    COMPLETED = "completed"
    NEEDS_REVIEW = "needs_review"


class AlertDeliveryStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


# Белый список классификаторов ЕФРСБ для дебиторской задолженности
# 7-значные коды из справочника GetClassifier (ЭТП↔ЕФРСБ v2.45)
DZ_CLASSIFIER_KEYWORDS = frozenset([
    "дебиторск",
    "права требования",
    "дебиторская задолженность",
    "задолженность дебиторская",
    "право требования",
    "денежное требование",
])

DZ_CLASSIFIER_CODES = frozenset([
    # Код 0104000000 — Права требования на деньги
    "0104000000",
    "0104010000",
    "0104020000",
    "0104030000",
    "0104040000",
    # ДЗ как имущественное право
    "0101000000",
    "0101010000",
    "0101020000",
])

# Коды классификатора, которые точно НЕ ДЗ
EXCLUDED_CLASSIFIER_CODES = frozenset([
    "0200000000",  # Недвижимость
    "0300000000",  # Земельные участки
    "0400000000",  # Автотранспорт
    "0500000000",  # Оборудование
    "0600000000",  # Товары
    "0700000000",  # Ценные бумаги
    "0800000000",  # Прочее имущество
])
