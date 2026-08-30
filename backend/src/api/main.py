"""FastAPI — REST API + SPA-статика + фоновые воркеры в одном процессе."""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.diagnostics import router as diagnostics_router
from src.api.security import require_api_access
from src.config import get_settings
from src.database import get_db
from src.models.entities import (
    AlertState,
    Claim,
    ImportCheckpoint,
    ImportRun,
    Lot,
    Party,
    Trade,
    UserFeedback,
)
from src.models.enums import LotClass, TradeStatus
from src.runtime import start_background_tasks, stop_background_tasks
from src.schemas.lot import (
    DashboardStats,
    FeedbackCreate,
    FeedbackSchema,
    HealthResponse,
    LotCardSchema,
    LotListSchema,
    LotSchema,
)
from src.version import VERSION

logger = logging.getLogger(__name__)
settings = get_settings()


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
    """Readiness probe: the process and its database must both be usable."""
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("readiness: database check failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok", "database": "ok"}


app.include_router(diagnostics_router, prefix="/api/v1", tags=["diagnostics"])


@app.get(
    "/api/v1/ingest/status",
    tags=["diagnostics"],
    dependencies=[Depends(require_api_access)],
)
async def ingest_status(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object | None]:
    """Return the latest ingest run and durable page checkpoint."""
    run = (
        await db.execute(
            select(ImportRun)
            .where(ImportRun.source == "efrsb_public")
            .order_by(ImportRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    checkpoint = await db.scalar(
        select(ImportCheckpoint).where(ImportCheckpoint.source == "efrsb_public")
    )
    return {
        "source": "efrsb_public",
        "run": (
            {
                "id": run.id,
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "last_page": run.last_page,
                "items_seen": run.items_seen,
                "items_upserted": run.items_upserted,
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


# ── Лоты ─────────────────────────────────────────────────────────────────────


@app.get("/api/v1/lots", response_model=LotListSchema, tags=["lots"])
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
) -> LotListSchema:
    """Лента лотов с фильтрацией."""
    q = (
        select(Lot)
        .join(Trade, Lot.trade_id == Trade.id)
        .where(Lot.is_receivable == True)  # noqa: E712
        .options(
            selectinload(Lot.claims)
            .selectinload(Claim.debtor_party),
            selectinload(Lot.claims)
            .selectinload(Claim.guarantor_party),
            selectinload(Lot.price_intervals),
        )
    )

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
    if deadline_before is not None:
        if deadline_before.tzinfo is None:
            raise HTTPException(status_code=422, detail="deadline_before must include a timezone")
        q = q.where(Lot.current_interval_to <= deadline_before)

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    q = q.order_by(Lot.score_ev.desc().nullslast(), Lot.updated_at.desc())
    q = q.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(q)
    lots = result.scalars().unique().all()

    pages = (total + page_size - 1) // page_size
    return LotListSchema(
        items=[LotSchema.model_validate(lot, from_attributes=True) for lot in lots],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


@app.get("/api/v1/lots/{lot_id}", response_model=LotCardSchema, tags=["lots"])
async def get_lot(
    lot_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LotCardSchema:
    """Карточка одного лота."""
    result = await db.execute(
        select(Lot)
        .where(Lot.id == lot_id)
        .options(
            selectinload(Lot.trade).selectinload(Trade.bankrupt_party),
            selectinload(Lot.claims).selectinload(Claim.debtor_party),
            selectinload(Lot.claims).selectinload(Claim.guarantor_party),
            selectinload(Lot.price_intervals),
            selectinload(Lot.documents),
            selectinload(Lot.score_snapshots),
        )
    )
    lot = result.scalar_one_or_none()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    return LotCardSchema.model_validate(lot, from_attributes=True)


# ── Статистика ────────────────────────────────────────────────────────────────


@app.get("/api/v1/stats", response_model=DashboardStats, tags=["stats"])
async def get_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DashboardStats:
    """Дашборд — агрегированная статистика."""

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

    last_ingest = (
        await db.execute(
            select(func.max(ImportRun.finished_at)).where(
                ImportRun.source == "efrsb_public",
                ImportRun.status == "finished",
            )
        )
    ).scalar()
    alerts_sent_today = (
        await db.execute(
            select(func.count(AlertState.id)).where(
                AlertState.alerted_at >= datetime.now(UTC) - timedelta(days=1)
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
    if await db.get(Lot, payload.lot_id) is None:
        raise HTTPException(status_code=404, detail="Lot not found")
    fb = UserFeedback(
        lot_id=payload.lot_id,
        action=payload.action,
        recovered_amount=payload.recovered_amount,
        note=payload.note,
    )
    db.add(fb)
    await db.commit()
    await db.refresh(fb)
    return FeedbackSchema.model_validate(fb, from_attributes=True)


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
