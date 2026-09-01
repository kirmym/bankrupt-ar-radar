"""FastAPI — REST API + SPA-статика + фоновые воркеры в одном процессе."""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, cast

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import and_, desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from src.analytics.calibration import build_calibration_report
from src.api.diagnostics import router as diagnostics_router
from src.api.security import require_api_access
from src.config import get_settings
from src.connectors.efrsb import is_valid_inn
from src.database import get_db
from src.models.entities import (
    AlertState,
    Claim,
    Document,
    ImportCheckpoint,
    ImportRun,
    Lot,
    Party,
    ScoreSnapshot,
    Trade,
    UserFeedback,
)
from src.models.enums import (
    PARTICIPABLE_TRADE_STATUSES,
    ClaimKind,
    LotClass,
    PartyRole,
    PersonKind,
    PriceScheduleStatus,
    TradeStatus,
)
from src.runtime import start_background_tasks, stop_background_tasks, worker_status_snapshot
from src.schemas.lot import (
    CalibrationReportSchema,
    DashboardStats,
    DebtorAssignCreate,
    DocumentProposalUpdates,
    DocumentSchema,
    FeedbackCreate,
    FeedbackSchema,
    HealthResponse,
    LotCardSchema,
    LotListSchema,
    LotSchema,
    ScoreSnapshotSchema,
)
from src.version import VERSION

logger = logging.getLogger(__name__)
settings = get_settings()


def _participation_clause(now: datetime):
    """Return the single fail-closed predicate used by user-facing views."""
    return and_(
        Trade.status.in_(PARTICIPABLE_TRADE_STATUSES),
        Trade.applications_to.is_not(None),
        Trade.applications_to > now,
    )


def _review_clause(now: datetime):
    """Return the conservative predicate for a manual-review candidate."""
    return and_(
        Lot.current_price.is_not(None),
        Lot.price_schedule_status == PriceScheduleStatus.PARSED.value,
        Lot.price_observed_at.is_not(None),
        Lot.price_observed_at >= now - timedelta(hours=max(1, settings.price_freshness_hours)),
        Lot.score_ev > 0,
        func.coalesce(func.cardinality(Lot.score_stop_factors), 0) == 0,
        Lot.score_updated_at.is_not(None),
        Lot.score_updated_at >= Lot.updated_at,
        or_(Lot.current_interval_from.is_(None), Lot.current_interval_from <= now),
        or_(Lot.current_interval_to.is_(None), Lot.current_interval_to > now),
    )


def _ready_clause(now: datetime):
    """Return the stricter recommendation predicate used by alerts."""
    return and_(
        Lot.score_class.in_([LotClass.A.value, LotClass.B.value]),
        _review_clause(now),
        func.coalesce(func.cardinality(Lot.score_gaps), 0) == 0,
    )


def _list_item_payload(lot: Lot) -> LotSchema:
    """Serialize a lot and expose its participation/source summary inline."""
    payload = LotSchema.model_validate(lot, from_attributes=True)
    trade = getattr(lot, "trade", None)
    if trade is None:
        return payload
    try:
        payload.trade_status = TradeStatus(str(trade.status))
    except ValueError:
        payload.trade_status = None
    payload.applications_from = trade.applications_from
    payload.applications_to = trade.applications_to
    payload.participation_exclusion_reason = trade.participation_exclusion_reason
    refs = list(getattr(trade, "source_refs", []) or [])
    if refs:
        payload.source_name = refs[0].source
        payload.source_url = refs[0].source_url
    elif trade.efrsb_url:
        payload.source_name = "efrsb_legacy"
        payload.source_url = trade.efrsb_url
    elif trade.etp_url:
        payload.source_name = trade.etp_name or "etp"
        payload.source_url = trade.etp_url
    return payload


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    tasks: list = []
    if settings.enable_workers:
        logger.info("lifespan: starting background workers")
        tasks = start_background_tasks()
    yield
    if tasks:
        await stop_background_tasks(tasks)


app = FastAPI(
    title="AR Radar API",
    description="Радар дебиторской задолженности — API",
    version=VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(version=VERSION)


@app.get("/ready", tags=["system"])
async def readiness(db: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, str]:
    """Readiness probe: the database connection and required schema must be usable."""
    try:
        result = await db.execute(text("SELECT to_regclass('public.import_runs')"))
    except Exception as exc:
        logger.warning("readiness: database check failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    if result.scalar_one_or_none() is None:
        logger.warning("readiness: required database schema is missing")
        raise HTTPException(status_code=503, detail="database schema unavailable")
    return {"status": "ok", "database": "ok"}


app.include_router(diagnostics_router, prefix="/api/v1", tags=["diagnostics"])


@app.middleware("http")
async def security_headers(request, call_next):
    """Add baseline browser hardening headers to API and SPA responses."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; connect-src 'self' https:; font-src 'self' data:",
    )
    if settings.app_env.lower() in {"production", "prod"}:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.get(
    "/api/v1/ingest/status",
    tags=["diagnostics"],
    dependencies=[Depends(require_api_access)],
)
async def ingest_status(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object | None]:
    """Return the latest ingest run and durable page checkpoint."""
    source = settings.primary_ingest_source
    run = (
        await db.execute(
            select(ImportRun)
            .where(ImportRun.source == source)
            .order_by(ImportRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    checkpoint = await db.scalar(
        select(ImportCheckpoint).where(ImportCheckpoint.source == source)
    )
    return {
        "source": source,
        "run": (
            {
                "id": run.id,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "last_page": run.last_page,
                "items_seen": run.items_seen,
                "items_upserted": run.items_upserted,
                "items_changed": run.items_changed,
                "items_unchanged": run.items_unchanged,
                "items_rejected": run.items_rejected,
                "error_code": run.error_code,
                "error_message": run.error_message,
            }
            if run
            else None
        ),
        "checkpoint": (
            {"cursor": checkpoint.cursor, "updated_at": checkpoint.updated_at}
            if checkpoint
            else None
        ),
    }


@app.get(
    "/api/v1/workers/status",
    tags=["diagnostics"],
    dependencies=[Depends(require_api_access)],
)
async def workers_status() -> dict[str, object]:
    """Return process-local worker state for operational diagnostics."""
    return {"workers": worker_status_snapshot()}


# ── Лоты ─────────────────────────────────────────────────────────────────────


@app.get(
    "/api/v1/lots",
    response_model=LotListSchema,
    tags=["lots"],
    dependencies=[Depends(require_api_access)],
)
async def list_lots(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(ge=1, default=1),
    page_size: int = Query(ge=1, le=100, default=20),
    score_class: LotClass | None = None,
    min_ev: Annotated[Decimal | None, Query(ge=0)] = None,
    max_ev: Annotated[Decimal | None, Query(ge=0)] = None,
    debtor_inn: Annotated[str | None, Query(pattern=r"^\d{10}(\d{2})?$")] = None,
    search: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    trade_status: TradeStatus | None = None,
    deadline_before: datetime | None = None,
    etp_name: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    has_debtor: bool | None = None,
    has_court: bool | None = None,
    price_status: PriceScheduleStatus | None = None,
    view: Literal["active", "review", "ready"] = "active",
    sort_by: Literal["ev", "price", "deadline", "updated"] = "ev",
    sort_order: Literal["asc", "desc"] = "desc",
) -> LotListSchema:
    """Лента лотов с фильтрацией."""
    now = datetime.now(UTC)
    q = (
        select(Lot)
        .join(Trade, Lot.trade_id == Trade.id)
        .where(Lot.is_receivable == True)  # noqa: E712
        .where(_participation_clause(now))
        .options(
            selectinload(Lot.trade).selectinload(Trade.source_refs),
            selectinload(Lot.claims)
            .selectinload(Claim.debtor_party),
            selectinload(Lot.claims)
            .selectinload(Claim.guarantor_party),
            selectinload(Lot.price_intervals),
        )
    )

    if view == "review":
        q = q.where(_review_clause(now))
    elif view == "ready":
        q = q.where(_ready_clause(now))

    if score_class:
        q = q.where(Lot.score_class == score_class.value)
    if min_ev is not None:
        q = q.where(Lot.score_ev >= min_ev)
    if max_ev is not None:
        q = q.where(Lot.score_ev <= max_ev)
    if debtor_inn:
        q = q.where(Lot.claims.any(Claim.debtor_party.has(inn=debtor_inn)))
    if search:
        pattern = f"%{search.strip()}%"
        q = q.where(
            or_(
                Lot.title.ilike(pattern),
                Lot.description_text.ilike(pattern),
                Lot.claims.any(Claim.debtor_party.has(Party.name.ilike(pattern))),
                Lot.claims.any(Claim.debtor_party.has(Party.inn.ilike(pattern))),
            )
        )
    if trade_status:
        q = q.where(Trade.status == trade_status.value)
    if etp_name:
        q = q.where(Trade.etp_name.ilike(f"%{etp_name.strip()}%"))
    if has_debtor is True:
        q = q.where(Lot.claims.any(Claim.debtor_party_id.isnot(None)))
    elif has_debtor is False:
        q = q.where(~Lot.claims.any(Claim.debtor_party_id.isnot(None)))
    if has_court is True:
        q = q.where(Lot.claims.any(Claim.court_case_no.isnot(None)))
    elif has_court is False:
        q = q.where(~Lot.claims.any(Claim.court_case_no.isnot(None)))
    if price_status:
        q = q.where(Lot.price_schedule_status == price_status.value)
    if deadline_before is not None:
        if deadline_before.tzinfo is None:
            raise HTTPException(status_code=422, detail="deadline_before must include a timezone")
        q = q.where(Trade.applications_to <= deadline_before)

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    sort_column = {
        "ev": Lot.score_ev,
        "price": Lot.current_price,
        "deadline": Trade.applications_to,
        "updated": Lot.updated_at,
    }[sort_by]
    ordering = sort_column.asc() if sort_order == "asc" else sort_column.desc()
    q = q.order_by(ordering.nullslast(), Lot.id.asc())
    q = q.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(q)
    lots = result.scalars().unique().all()

    pages = (total + page_size - 1) // page_size
    return LotListSchema(
        items=[_list_item_payload(lot) for lot in lots],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@app.get(
    "/api/v1/lots/{lot_id}",
    response_model=LotCardSchema,
    tags=["lots"],
    dependencies=[Depends(require_api_access)],
)
async def get_lot(
    lot_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LotCardSchema:
    """Карточка одного лота."""
    result = await db.execute(
        select(Lot)
        .join(Trade, Lot.trade_id == Trade.id)
        .where(Lot.id == lot_id)
        .where(
            and_(
                Trade.status.in_(PARTICIPABLE_TRADE_STATUSES),
                Trade.applications_to.is_not(None),
                Trade.applications_to > datetime.now(UTC),
            )
        )
        .options(
            selectinload(Lot.trade).selectinload(Trade.bankrupt_party),
            selectinload(Lot.trade).selectinload(Trade.source_refs),
            selectinload(Lot.claims).selectinload(Claim.debtor_party),
            selectinload(Lot.claims)
            .selectinload(Claim.debtor_party)
            .selectinload(Party.source_checks),
            selectinload(Lot.claims).selectinload(Claim.guarantor_party),
            selectinload(Lot.price_intervals),
            selectinload(Lot.documents),
        )
    )
    lot = result.scalar_one_or_none()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    snapshots = (
        await db.execute(
            select(ScoreSnapshot)
            .where(ScoreSnapshot.lot_id == lot.id)
            .order_by(desc(ScoreSnapshot.scored_at))
            .limit(50)
        )
    ).scalars().all()
    set_committed_value(lot, "score_snapshots", snapshots)
    payload = LotCardSchema.model_validate(lot, from_attributes=True)
    payload.score_snapshots = [
        ScoreSnapshotSchema.model_validate(snapshot, from_attributes=True)
        for snapshot in snapshots
    ]
    return payload


@app.put(
    "/api/v1/lots/{lot_id}/debtor",
    response_model=LotCardSchema,
    tags=["lots"],
    dependencies=[Depends(require_api_access)],
)
async def assign_debtor(
    lot_id: int,
    payload: DebtorAssignCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LotCardSchema:
    """Attach a manually verified debtor INN and invalidate the lot score."""
    if not is_valid_inn(payload.inn):
        raise HTTPException(status_code=422, detail="Invalid INN check digits")
    lot = (
        await db.execute(
            select(Lot)
            .where(Lot.id == lot_id)
            .options(selectinload(Lot.claims))
        )
    ).scalar_one_or_none()
    if lot is None:
        raise HTTPException(status_code=404, detail="Lot not found")

    debtor = await db.scalar(
        select(Party).where(
            Party.role == PartyRole.DEBTOR.value,
            Party.inn == payload.inn,
        )
    )
    if debtor is None:
        debtor = Party(
            role=PartyRole.DEBTOR.value,
            person_kind=PersonKind.UL.value if len(payload.inn) == 10 else PersonKind.FL.value,
            inn=payload.inn,
            name=payload.name,
        )
        db.add(debtor)
        await db.flush()
    elif payload.name and not debtor.name:
        debtor.name = payload.name

    claim = min(lot.claims, key=lambda item: item.id, default=None)
    if claim is None:
        claim = Claim(lot_id=lot.id, kind=ClaimKind.TRADE_AR.value)
        db.add(claim)
    claim.debtor_party_id = debtor.id
    lot.updated_at = datetime.now(UTC)
    await db.commit()

    refreshed = (
        await db.execute(
            select(Lot)
            .where(Lot.id == lot_id)
            .options(
                selectinload(Lot.trade).selectinload(Trade.bankrupt_party),
                selectinload(Lot.trade).selectinload(Trade.source_refs),
                selectinload(Lot.claims).selectinload(Claim.debtor_party),
                selectinload(Lot.claims)
                .selectinload(Claim.debtor_party)
                .selectinload(Party.source_checks),
                selectinload(Lot.claims).selectinload(Claim.guarantor_party),
                selectinload(Lot.price_intervals),
                selectinload(Lot.documents),
            )
        )
    ).scalar_one()
    snapshots = (
        await db.execute(
            select(ScoreSnapshot)
            .where(ScoreSnapshot.lot_id == refreshed.id)
            .order_by(desc(ScoreSnapshot.scored_at))
            .limit(50)
        )
    ).scalars().all()
    set_committed_value(refreshed, "score_snapshots", snapshots)
    response_payload = LotCardSchema.model_validate(refreshed, from_attributes=True)
    response_payload.score_snapshots = [
        ScoreSnapshotSchema.model_validate(snapshot, from_attributes=True)
        for snapshot in snapshots
    ]
    return response_payload


@app.post(
    "/api/v1/documents/{document_id}/proposal/apply",
    response_model=DocumentSchema,
    tags=["documents"],
    dependencies=[Depends(require_api_access)],
)
async def apply_document_proposal(
    document_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DocumentSchema:
    """Apply only the reviewable facts extracted from a document."""
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    payload = document.extracted_facts or {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=409, detail="Document has no fact proposal")
    if payload.get("proposal_applied_at"):
        return DocumentSchema.model_validate(document, from_attributes=True)
    proposal = payload.get("proposal") if isinstance(payload, dict) else None
    updates = proposal.get("updates") if isinstance(proposal, dict) else None
    if not isinstance(updates, dict):
        raise HTTPException(status_code=409, detail="Document has no fact proposal")
    evidence = proposal.get("evidence") if isinstance(proposal, dict) else None
    evidence = evidence if isinstance(evidence, dict) else {}
    raw_claim_updates = updates.get("claim")
    claim_updates: dict[str, object] = (
        cast(dict[str, object], raw_claim_updates)
        if isinstance(raw_claim_updates, dict)
        else {}
    )
    critical_fields = {
        "has_judgment",
        "has_writ",
        "secured",
        "assignment_forbidden",
        "counterclaim_risk",
        "personal_claim",
        "court_case_no",
    }
    missing_evidence = sorted(
        field for field in claim_updates if field in critical_fields and not evidence.get(field)
    )
    if missing_evidence:
        raise HTTPException(
            status_code=409,
            detail=f"Critical proposal fields require evidence: {', '.join(missing_evidence)}",
        )
    try:
        validated_updates = DocumentProposalUpdates.model_validate(updates)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid document proposal values") from exc

    claim = (
        await db.execute(
            select(Claim)
            .where(Claim.lot_id == document.lot_id)
            .options(selectinload(Claim.debtor_party))
            .order_by(Claim.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    lot = await db.get(Lot, document.lot_id)
    if lot is None:
        raise HTTPException(status_code=404, detail="Lot not found")

    claim_updates = validated_updates.claim.model_dump(exclude_unset=True) if validated_updates.claim else {}
    debtor_updates = validated_updates.debtor.model_dump(exclude_unset=True) if validated_updates.debtor else {}
    if claim is None and (claim_updates or debtor_updates):
        claim = Claim(lot_id=lot.id, kind=ClaimKind.TRADE_AR.value)
        db.add(claim)
        await db.flush()
    if claim is not None and isinstance(claim_updates, dict):
        allowed_claim_fields = {
            "kind", "principal", "penalties", "currency", "base_contract", "base_date",
            "due_date", "court_case_no", "has_judgment", "has_writ", "secured",
            "assignment_forbidden", "counterclaim_risk", "personal_claim",
        }
        for field, value in claim_updates.items():
            if field in allowed_claim_fields:
                if hasattr(value, "value"):
                    value = value.value
                setattr(claim, field, value)

    if claim is not None and isinstance(debtor_updates, dict):
        debtor = claim.debtor_party
        if debtor is None and debtor_updates.get("inn"):
            debtor = Party(
                role=PartyRole.DEBTOR.value,
                person_kind=PersonKind.UL.value
                if len(str(debtor_updates["inn"])) == 10
                else PersonKind.FL.value,
                inn=str(debtor_updates["inn"]),
            )
            db.add(debtor)
            await db.flush()
            claim.debtor_party = debtor
        if debtor is not None:
            for field in ("name", "inn", "ogrn"):
                if field in debtor_updates and getattr(debtor, field, None) is None:
                    setattr(debtor, field, debtor_updates[field])

    lot.updated_at = datetime.now(UTC)
    payload["proposal_applied_at"] = datetime.now(UTC).isoformat()
    document.extracted_facts = payload
    await db.commit()
    await db.refresh(document)
    return DocumentSchema.model_validate(document, from_attributes=True)


# ── Статистика ────────────────────────────────────────────────────────────────


@app.get(
    "/api/v1/stats",
    response_model=DashboardStats,
    tags=["stats"],
    dependencies=[Depends(require_api_access)],
)
async def get_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DashboardStats:
    """Дашборд — агрегированная статистика."""

    now = datetime.now(UTC)

    counts = (
        await db.execute(
            select(
                func.count(Lot.id),
                func.count(Lot.id).filter(Lot.is_receivable == True),  # noqa: E712
                func.count(Lot.id).filter(Lot.score_class.isnot(None)),
                func.count(Lot.id).filter(Lot.score_class == LotClass.A.value),
                func.count(Lot.id).filter(Lot.score_class == LotClass.B.value),
                func.count(Lot.id).filter(Lot.score_class == LotClass.C.value),
                func.count(Lot.id).filter(Lot.score_class == LotClass.D.value),
                func.count(Lot.id).filter(
                    Lot.score_class.isnot(None),
                    or_(Lot.score_updated_at.is_(None), Lot.score_updated_at < Lot.updated_at),
                ),
            )
        )
    ).one()

    active_lots = (
        await db.scalar(
            select(func.count(Lot.id))
            .join(Trade, Lot.trade_id == Trade.id)
            .where(Lot.is_receivable == True)  # noqa: E712
            .where(_participation_clause(now))
        )
        or 0
    )
    review_candidates = (
        await db.scalar(
            select(func.count(Lot.id))
            .join(Trade, Lot.trade_id == Trade.id)
            .where(Lot.is_receivable == True)  # noqa: E712
            .where(_participation_clause(now))
            .where(_review_clause(now))
        )
        or 0
    )
    ready_recommendations = (
        await db.scalar(
            select(func.count(Lot.id))
            .join(Trade, Lot.trade_id == Trade.id)
            .where(Lot.is_receivable == True)  # noqa: E712
            .where(_participation_clause(now))
            .where(_ready_clause(now))
        )
        or 0
    )
    document_counts = (
        await db.execute(
            select(
                func.count(Document.id),
                func.count(Document.id).filter(
                    Document.processing_status == "completed"
                ),
                func.count(Document.id).filter(
                    Document.processing_status == "pending"
                ),
                func.count(Document.id).filter(
                    Document.processing_status == "needs_review"
                ),
                func.count(Document.id).filter(
                    Document.processing_status == "retrying"
                ),
            )
        )
    ).one()

    last_ingest = (
        await db.execute(
            select(func.max(ImportRun.finished_at)).where(
                ImportRun.source == settings.primary_ingest_source,
                ImportRun.status == "finished",
            )
        )
    ).scalar()
    source_status = (
        await db.scalar(
            select(ImportRun.status)
            .where(ImportRun.source == settings.primary_ingest_source)
            .order_by(ImportRun.started_at.desc())
            .limit(1)
        )
        or "unknown"
    )
    alerts_sent_today = (
        await db.execute(
            select(func.count(AlertState.id)).where(
                AlertState.sent_at >= datetime.now(UTC) - timedelta(days=1),
                AlertState.status == "sent",
            )
        )
    ).scalar() or 0

    return DashboardStats(
        total_lots=counts[0],
        receivable_lots=counts[1],
        scored_lots=counts[2],
        class_a=counts[3],
        class_b=counts[4],
        class_c=counts[5],
        class_d=counts[6],
        stale_scored_lots=counts[7],
        alerts_sent_today=alerts_sent_today,
        last_ingest_at=last_ingest,
        source_status=str(source_status),
        active_lots=int(active_lots),
        excluded_lots=max(0, int(counts[1]) - int(active_lots)),
        ready_recommendations=int(ready_recommendations),
        review_candidates=int(review_candidates),
        documents_total=int(document_counts[0]),
        documents_completed=int(document_counts[1]),
        documents_pending=int(document_counts[2]),
        documents_needs_review=int(document_counts[3]),
        documents_retrying=int(document_counts[4]),
    )


# ── Feedback ──────────────────────────────────────────────────────────────────


@app.post(
    "/api/v1/feedback",
    response_model=FeedbackSchema,
    tags=["feedback"],
    dependencies=[Depends(require_api_access)],
)
async def create_feedback(
    payload: FeedbackCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FeedbackSchema:
    lot = await db.get(Lot, payload.lot_id)
    if lot is None:
        raise HTTPException(status_code=404, detail="Lot not found")
    if payload.action != "bought" and (
        payload.outcome is not None
        or payload.recovered_amount is not None
        or payload.expense_amount is not None
        or payload.outcome_at is not None
    ):
        raise HTTPException(
            status_code=422,
            detail="recovery outcome fields are valid only for bought lots",
        )
    if payload.outcome == "recovered" and (
        payload.recovered_amount is None or payload.recovered_amount <= 0
    ):
        raise HTTPException(
            status_code=422,
            detail="recovered outcome requires a positive recovered_amount",
        )
    if payload.outcome == "not_recovered" and payload.recovered_amount not in (None, Decimal(0)):
        raise HTTPException(
            status_code=422,
            detail="not_recovered outcome cannot contain a positive recovered_amount",
        )
    # A later bought/outcome submission completes the open purchase record
    # instead of creating a second purchase and biasing calibration counts.
    if payload.action == "bought" and payload.outcome is not None:
        existing = (
            await db.execute(
                select(UserFeedback)
                .where(
                    UserFeedback.lot_id == payload.lot_id,
                    UserFeedback.action == "bought",
                    or_(UserFeedback.outcome.is_(None), UserFeedback.outcome == "in_progress"),
                )
                .order_by(UserFeedback.created_at.desc(), UserFeedback.id.desc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.recovered_amount = payload.recovered_amount
            existing.expense_amount = payload.expense_amount
            existing.outcome = payload.outcome
            existing.outcome_at = payload.outcome_at or datetime.now(UTC)
            if payload.note is not None:
                existing.note = payload.note
            await db.commit()
            await db.refresh(existing)
            return FeedbackSchema.model_validate(existing, from_attributes=True)
    fb = UserFeedback(
        lot_id=payload.lot_id,
        action=payload.action,
        recovered_amount=payload.recovered_amount,
        expense_amount=payload.expense_amount,
        outcome=payload.outcome,
        outcome_at=payload.outcome_at,
        note=payload.note,
        decision_score_class=lot.score_class,
        decision_score_ev=lot.score_ev,
        decision_max_bid=lot.score_max_bid,
        decision_price=lot.current_price,
        decision_nominal=lot.nominal_claimed,
        decision_score_version=lot.score_version,
    )
    db.add(fb)
    await db.commit()
    await db.refresh(fb)
    return FeedbackSchema.model_validate(fb, from_attributes=True)


@app.get(
    "/api/v1/lots/{lot_id}/feedback",
    response_model=list[FeedbackSchema],
    tags=["feedback"],
    dependencies=[Depends(require_api_access)],
)
async def list_lot_feedback(
    lot_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[FeedbackSchema]:
    """Return the immutable decision/outcome history for one lot."""
    if await db.get(Lot, lot_id) is None:
        raise HTTPException(status_code=404, detail="Lot not found")
    rows = (
        await db.execute(
            select(UserFeedback)
            .where(UserFeedback.lot_id == lot_id)
            .order_by(UserFeedback.created_at.desc(), UserFeedback.id.desc())
        )
    ).scalars().all()
    return [FeedbackSchema.model_validate(row, from_attributes=True) for row in rows]


@app.get(
    "/api/v1/feedback/calibration",
    response_model=CalibrationReportSchema,
    tags=["feedback"],
    dependencies=[Depends(require_api_access)],
)
async def feedback_calibration(
    db: Annotated[AsyncSession, Depends(get_db)],
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> CalibrationReportSchema:
    """Return outcome metrics without presenting them as calibrated odds."""
    stmt = select(UserFeedback).order_by(UserFeedback.created_at.asc(), UserFeedback.id.asc())
    if created_from is not None:
        stmt = stmt.where(UserFeedback.created_at >= created_from)
    if created_to is not None:
        stmt = stmt.where(UserFeedback.created_at <= created_to)
    rows = (await db.execute(stmt)).scalars().all()
    report = build_calibration_report(
        list(rows),
        min_resolved=max(1, int(getattr(settings, "calibration_min_resolved", 10))),
    )
    return CalibrationReportSchema.model_validate(report)


# ── SPA-статика (собранный фронтенд) ────────────────────────────────────────

web_dist = Path(settings.web_dist_dir) if settings.web_dist_dir else Path(__file__).parent.parent.parent / "web" / "dist"
web_dist = web_dist.resolve()


def safe_static_file(root: Path, relative_path: str) -> Path | None:
    """Return a file below ``root`` without following an escaping symlink."""
    root = root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None

if web_dist.is_dir():
    assets_dir = web_dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/", include_in_schema=False)
    async def spa_index() -> FileResponse:
        return FileResponse(web_dist / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        # Не перехватываем API и health
        if full_path.startswith(("api/", "health", "docs", "openapi.json")):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = safe_static_file(web_dist, full_path)
        if candidate is not None:
            return FileResponse(candidate)
        return FileResponse(web_dist / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
