"""Track Telegram alert delivery per recipient."""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("alerts_state", sa.Column("chat_id", sa.String(length=100), nullable=True))
    op.create_index(
        "ix_alerts_state_lot_chat_time",
        "alerts_state",
        ["lot_id", "chat_id", "alerted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_alerts_state_lot_chat_time", table_name="alerts_state")
    op.drop_column("alerts_state", "chat_id")
