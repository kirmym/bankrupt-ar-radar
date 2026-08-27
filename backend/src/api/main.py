"""FastAPI — REST API + SPA-статика + фоновые воркеры в одном процессе."""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import get_settings
from src.database import get_db
from src.models.entities import Claim, Lot, Trade, UserFeedback
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
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(version="0.1.0")


# ── Лоты ─────────────────────────────────────────────────────────────────────


@app.get("/api/v1/lots", response_model=LotListSchema, tags=["lots"])
async def list_lots(
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(ge=1, default=1),
    page_size: int = Query(ge=1, le=100, default=20),
    score_class: LotClass | None = None,
    min_ev: float | None = Query(default=None, ge=0),
    max_ev: float | None = Query(default=None, ge=0),
    debtor_inn: str | None = None,
    trade_status: TradeStatus | None = None,
    deadline_before: str | None = None,
) -> LotListSchema:
    """Лента лотов с фильтрацией."""
    q = (
        select(Lot)
        .join(Trade, Lot.trade_id == Trade.id)
        .where(Lot.is_receivable == True)  # noqa: E712
        .options(
            selectinload(Lot.claims).selectinload(Claim.debtor_party),
        )
    )

    if score_class:
        q = q.where(Lot.score_class == score_class.value)
    if min_ev is not None:
        q = q.where(Lot.score_ev >= min_ev)
    if max_ev is not None:
        q = q.where(Lot.score_ev <= max_ev)
    if debtor_inn:
        q = q.join(Claim, Claim.lot_id == Lot.id).join(
            Claim.debtor_party, isouter=True
        ).where(Claim.debtor_party.has(inn=debtor_inn))
    if trade_status:
        q = q.where(Trade.status == trade_status.value)

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

    async def _count(where=None) -> int:
        stmt = select(func.count(Lot.id))
        if where is not None:
            stmt = stmt.where(where)
        return (await db.execute(stmt)).scalar() or 0

    total = await _count()
    receivable = await _count(Lot.is_receivable == True)  # noqa: E712
    scored = await _count(Lot.score_class.isnot(None))
    a_count = await _count(Lot.score_class == LotClass.A.value)
    b_count = await _count(Lot.score_class == LotClass.B.value)
    c_count = await _count(Lot.score_class == LotClass.C.value)
    d_count = await _count(Lot.score_class == LotClass.D.value)

    last_ingest = (
        await db.execute(select(func.max(Lot.updated_at)))
    ).scalar()

    return DashboardStats(
        total_lots=total,
        receivable_lots=receivable,
        scored_lots=scored,
        class_a=a_count,
        class_b=b_count,
        class_c=c_count,
        class_d=d_count,
        alerts_sent_today=0,
        last_ingest_at=last_ingest,
    )


# ── Feedback ──────────────────────────────────────────────────────────────────


@app.post("/api/v1/feedback", response_model=FeedbackSchema, tags=["feedback"])
async def create_feedback(
    payload: FeedbackCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FeedbackSchema:
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
        candidate = web_dist / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(web_dist / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
