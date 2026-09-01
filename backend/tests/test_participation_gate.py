"""Participation eligibility contract tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.models.enums import (
    TradeStatus,
    is_participable_trade_now,
    is_participable_trade_status,
    participation_exclusion_reason,
)

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def test_started_trade_is_never_participable() -> None:
    assert not is_participable_trade_status(TradeStatus.IN_PROGRESS.value)
    assert not is_participable_trade_now(
        TradeStatus.IN_PROGRESS.value,
        NOW + timedelta(days=1),
        now=NOW,
    )
    assert participation_exclusion_reason(
        TradeStatus.IN_PROGRESS.value,
        NOW + timedelta(days=1),
        now=NOW,
    ) == "trading_started"


def test_application_window_requires_a_future_timezone_aware_deadline() -> None:
    assert is_participable_trade_now(
        TradeStatus.APPLICATIONS_OPEN.value,
        NOW + timedelta(minutes=1),
        now=NOW,
    )
    assert not is_participable_trade_now(
        TradeStatus.APPLICATIONS_OPEN.value,
        NOW,
        now=NOW,
    )
    assert participation_exclusion_reason(
        TradeStatus.APPLICATIONS_OPEN.value,
        NOW,
        now=NOW,
    ) == "application_deadline_passed"
    assert participation_exclusion_reason(
        TradeStatus.APPLICATIONS_OPEN.value,
        None,
        now=NOW,
    ) == "deadline_unknown"


def test_announced_trade_with_future_deadline_is_allowed() -> None:
    assert is_participable_trade_now(
        TradeStatus.ANNOUNCED.value,
        NOW + timedelta(days=1),
        now=NOW,
    )


def test_naive_deadline_fails_closed() -> None:
    naive_deadline = datetime.fromisoformat("2026-09-02T12:00:00")
    assert not is_participable_trade_now(
        TradeStatus.ANNOUNCED.value,
        naive_deadline,
        now=NOW,
    )
    assert participation_exclusion_reason(
        TradeStatus.ANNOUNCED.value,
        naive_deadline,
        now=NOW,
    ) == "deadline_unknown"


def test_closed_and_unknown_statuses_are_excluded() -> None:
    for status in (
        TradeStatus.COMPLETED.value,
        TradeStatus.CANCELLED.value,
        TradeStatus.SUSPENDED.value,
        "source_status_not_mapped",
    ):
        assert not is_participable_trade_now(status, NOW + timedelta(days=1), now=NOW)
        assert participation_exclusion_reason(
            status,
            NOW + timedelta(days=1),
            now=NOW,
        ) == "status_not_eligible"
