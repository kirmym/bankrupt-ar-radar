"""FastAPI — REST API."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import FastAPI, Depends, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.database import get_db
from src.models.entities import Lot, ScoreSnapshot, Trade, UserFeedback
from src.models.enums import LotClass, TradeStatus
from src.schemas.lot import (
    DashboardStats,
    FeedbackCreate,
    FeedbackSchema,
    HealthResponse,
    LotCardSchema,
    LotFilter,
    LotListSchema,
    LotSchema,
    MessageResponse,
)

settings = get_settings()

app = FastAPI(
    title="AR Radar API",
    description="Радар дебиторской задолженности — API",
    version="0.1.0",
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
    filter: Annotated[LotFilter, Query()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LotListSchema:
    """Лента лотов с фильтрацией."""
    q = (
        select(Lot)
        .outerjoin(Trade, Lot.trade_id == Trade.id)
        .where(Lot.is_receivable == True)  # noqa: E712
    )

    if filter.score_class:
        q = q.where(Lot.score_class == filter.score_class.value)
    if filter.min_ev is not None:
        q = q.where(Lot.score_ev >= filter.min_ev)
    if filter.max_ev is not None:
        q = q.where(Lot.score_ev <= filter.max_ev)
    if filter.trade_status:
        q = q.where(Trade.status == filter.trade_status.value)
    if filter.deadline_before:
        q = q.where(Lot.current_interval_to <= filter.deadline_before)
    if filter.active_after:
        q = q.where(Lot.current_interval_to >= filter.active_after)

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    q = q.order_by(Lot.score_ev.desc().nullslast(), Lot.updated_at.desc())
    q = q.offset((filter.page - 1) * filter.page_size).limit(filter.page_size)

    result = await db.execute(q)
    lots = result.scalars().all()

    pages = (total + filter.page_size - 1) // filter.page_size
    return LotListSchema(
        items=[LotSchema.model_validate(l) for l in lots],
        total=total,
        page=filter.page,
        page_size=filter.page_size,
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
            *Lot.__mapper__.iterate_properties  # load all relationships
        )
    )
    lot = result.scalar_one_or_none()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found")
    return LotCardSchema.model_validate(lot)


# ── Статистика ────────────────────────────────────────────────────────────────


@app.get("/api/v1/stats", response_model=DashboardStats, tags=["stats"])
async def get_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DashboardStats:
    """Дашборд — агрегированная статистика."""
    total = (await db.execute(select(func.count(Lot.id)))).scalar() or 0
    receivable = (
        await db.execute(
            select(func.count(Lot.id)).where(Lot.is_receivable == True)  # noqa: E712
        )
    ).scalar() or 0
    scored = (
        await db.execute(
            select(func.count(Lot.id)).where(Lot.score_class.isnot(None))
        )
    ).scalar() or 0

    a_count = (
        await db.execute(
            select(func.count(Lot.id)).where(Lot.score_class == LotClass.A.value)
        )
    ).scalar() or 0
    b_count = (
        await db.execute(
            select(func.count(Lot.id)).where(Lot.score_class == LotClass.B.value)
        )
    ).scalar() or 0
    c_count = (
        await db.execute(
            select(func.count(Lot.id)).where(Lot.score_class == LotClass.C.value)
        )
    ).scalar() or 0
    d_count = (
        await db.execute(
            select(func.count(Lot.id)).where(Lot.score_class == LotClass.D.value)
        )
    ).scalar() or 0

    return DashboardStats(
        total_lots=total,
        receivable_lots=receivable,
        scored_lots=scored,
        class_a=a_count,
        class_b=b_count,
        class_c=c_count,
        class_d=d_count,
        alerts_sent_today=0,
        last_ingest_at=None,
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
    return FeedbackSchema.model_validate(fb)


# ── Веб-интерфейс (SPA fallback) ─────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    return """
    <!doctype html>
    <html lang="ru">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>AR Radar</title>
        <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
        <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
        <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
      </head>
      <body>
        <div id="root"></div>
        <script type="text/babel" src="/static/app.jsx"></script>
      </body>
    </html>
    """


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
