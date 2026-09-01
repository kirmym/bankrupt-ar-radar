"""Add explicit alert event and interval revision fields.

Revision ID: 0010
Revises: 0009
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "alerts_state",
        sa.Column("event_type", sa.String(length=50), nullable=False, server_default="candidate"),
    )
    op.add_column(
        "alerts_state",
        sa.Column("interval_version", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "alerts_state",
        sa.Column("telegram_message_id", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("alerts_state", "telegram_message_id")
    op.drop_column("alerts_state", "interval_version")
    op.drop_column("alerts_state", "event_type")
