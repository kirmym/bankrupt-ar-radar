"""Regression tests for safe alert candidate selection."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from src.workers.alert_worker import alert_dedupe_key, build_alert_candidates_stmt


def test_alert_query_requires_active_price_and_fresh_score():
    statement = build_alert_candidates_stmt(datetime(2026, 8, 30, tzinfo=UTC), 5)
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "lots.current_price IS NOT NULL" in sql
    assert "lots.score_updated_at IS NOT NULL" in sql
    assert "lots.score_updated_at >= lots.updated_at" in sql


def test_alert_dedupe_key_does_not_change_at_window_boundary():
    lot = SimpleNamespace(
        id=42,
        score_version="v1",
        score_updated_at=datetime(2026, 8, 31, tzinfo=UTC),
        current_price=100,
        current_interval_to=datetime(2026, 9, 1, tzinfo=UTC),
    )
    before = datetime(2026, 8, 31, 15, 59, 59, tzinfo=UTC)
    after = before + timedelta(seconds=2)
    assert alert_dedupe_key(lot, "100", 20, before) == alert_dedupe_key(lot, "100", 20, after)
