"""Константы — enum'ы и справочники."""
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


class Gap(StrEnum):
    DEBTOR_INN_MISSING = "debtor_inn_missing"
    DEBTOR_INN_UNVERIFIED = "debtor_inn_unverified"
    BO_MISSING = "bo_missing"
    KAD_MISSING = "kad_missing"
    FSSP_MISSING = "fssp_missing"
    SCHEDULE_UNPARSED = "schedule_unparsed"
    NOMINAL_ESTIMATED = "nominal_estimated"  # Номинал взят из start_price
    NOMINAL_MISSING = "nominal_missing"
    BUNDLE_NO_DETAIL = "bundle_no_detail"


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
