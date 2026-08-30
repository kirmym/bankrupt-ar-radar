"""Durable worker queues, alert outbox and source freshness metadata.

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lots",
        sa.Column("price_schedule_status", sa.String(length=20), nullable=False, server_default="unknown"),
    )
    op.add_column("lots", sa.Column("price_observed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("lots", sa.Column("price_source", sa.String(length=50), nullable=True))
    op.add_column("lots", sa.Column("etp_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("lots", sa.Column("etp_next_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "lots", sa.Column("etp_failures", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("lots", sa.Column("etp_last_error", sa.String(length=500), nullable=True))
    op.create_index("ix_lots_etp_retry_at", "lots", ["etp_next_retry_at"])

    op.add_column("parties", sa.Column("enrich_attempted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("parties", sa.Column("enrich_next_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "parties", sa.Column("enrich_failures", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("parties", sa.Column("enrich_last_error", sa.String(length=500), nullable=True))
    op.create_index("ix_parties_enrich_retry_at", "parties", ["enrich_next_retry_at"])

    op.add_column(
        "documents",
        sa.Column("processing_status", sa.String(length=20), nullable=False, server_default="pending"),
    )
    op.create_index(
        "ix_documents_processing_retry", "documents", ["processing_status", "next_retry_at"]
    )

    op.add_column("alerts_state", sa.Column("dedupe_key", sa.String(length=200), nullable=True))
    op.add_column(
        "alerts_state", sa.Column("status", sa.String(length=20), nullable=False, server_default="sent")
    )
    op.add_column("alerts_state", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("alerts_state", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "alerts_state", sa.Column("attempts", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column("alerts_state", sa.Column("last_error", sa.String(length=500), nullable=True))
    op.execute("UPDATE alerts_state SET dedupe_key = 'legacy:' || id WHERE dedupe_key IS NULL")
    op.alter_column("alerts_state", "dedupe_key", nullable=False)
    op.create_unique_constraint("uq_alerts_state_dedupe_key", "alerts_state", ["dedupe_key"])
    op.create_index("ix_alerts_state_status_lease", "alerts_state", ["status", "lease_until"])


def downgrade() -> None:
    op.drop_index("ix_alerts_state_status_lease", table_name="alerts_state")
    op.drop_constraint("uq_alerts_state_dedupe_key", "alerts_state", type_="unique")
    op.drop_column("alerts_state", "last_error")
    op.drop_column("alerts_state", "attempts")
    op.drop_column("alerts_state", "sent_at")
    op.drop_column("alerts_state", "lease_until")
    op.drop_column("alerts_state", "status")
    op.drop_column("alerts_state", "dedupe_key")

    op.drop_index("ix_documents_processing_retry", table_name="documents")
    op.drop_column("documents", "processing_status")

    op.drop_index("ix_parties_enrich_retry_at", table_name="parties")
    op.drop_column("parties", "enrich_last_error")
    op.drop_column("parties", "enrich_failures")
    op.drop_column("parties", "enrich_next_retry_at")
    op.drop_column("parties", "enrich_attempted_at")

    op.drop_index("ix_lots_etp_retry_at", table_name="lots")
    op.drop_column("lots", "etp_last_error")
    op.drop_column("lots", "etp_failures")
    op.drop_column("lots", "etp_next_retry_at")
    op.drop_column("lots", "etp_checked_at")
    op.drop_column("lots", "price_source")
    op.drop_column("lots", "price_observed_at")
    op.drop_column("lots", "price_schedule_status")
