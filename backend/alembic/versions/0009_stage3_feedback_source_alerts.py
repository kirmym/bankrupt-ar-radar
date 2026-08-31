"""Add stage 3 feedback snapshots and source health alert state.

Revision ID: 0009
Revises: 0008
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_feedbacks", sa.Column("expense_amount", sa.Numeric(18, 2), nullable=True))
    op.add_column("user_feedbacks", sa.Column("outcome", sa.String(length=20), nullable=True))
    op.add_column("user_feedbacks", sa.Column("outcome_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user_feedbacks", sa.Column("decision_score_class", sa.String(length=2), nullable=True))
    op.add_column("user_feedbacks", sa.Column("decision_score_ev", sa.Numeric(18, 2), nullable=True))
    op.add_column("user_feedbacks", sa.Column("decision_max_bid", sa.Numeric(18, 2), nullable=True))
    op.add_column("user_feedbacks", sa.Column("decision_price", sa.Numeric(18, 2), nullable=True))
    op.add_column("user_feedbacks", sa.Column("decision_nominal", sa.Numeric(18, 2), nullable=True))
    op.add_column("user_feedbacks", sa.Column("decision_score_version", sa.String(length=20), nullable=True))

    op.create_table(
        "source_alerts_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("alert_type", sa.String(length=50), nullable=False),
        sa.Column("chat_id", sa.String(length=100), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="sending"),
        sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.UniqueConstraint(
            "source", "alert_type", "chat_id", "window_start",
            name="uq_source_alert_window",
        ),
    )
    op.create_index(
        "ix_source_alerts_state_status",
        "source_alerts_state",
        ["source", "status", "window_start"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_alerts_state_status", table_name="source_alerts_state")
    op.drop_table("source_alerts_state")
    for column in (
        "decision_score_version",
        "decision_nominal",
        "decision_price",
        "decision_max_bid",
        "decision_score_ev",
        "decision_score_class",
        "outcome_at",
        "outcome",
        "expense_amount",
    ):
        op.drop_column("user_feedbacks", column)
