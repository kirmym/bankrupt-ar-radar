"""Regression tests for safe alert candidate selection."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from src.workers.alert_worker import build_alert_candidates_stmt


def test_alert_query_requires_active_price_and_fresh_score():
    statement = build_alert_candidates_stmt(datetime(2026, 8, 30, tzinfo=UTC), 5)
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "lots.current_price IS NOT NULL" in sql
    assert "lots.score_updated_at IS NOT NULL" in sql
    assert "lots.score_updated_at >= lots.updated_at" in sql
