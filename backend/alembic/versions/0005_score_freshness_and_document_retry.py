"""Protect alerts from stale scores and bound document retries.

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lots", sa.Column("score_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "documents",
        sa.Column("download_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("documents", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("documents", sa.Column("last_error", sa.String(length=500), nullable=True))
    op.create_index("ix_documents_retry_at", "documents", ["next_retry_at"])


def downgrade() -> None:
    op.drop_index("ix_documents_retry_at", table_name="documents")
    op.drop_column("documents", "last_error")
    op.drop_column("documents", "next_retry_at")
    op.drop_column("documents", "download_attempts")
    op.drop_column("lots", "score_updated_at")
