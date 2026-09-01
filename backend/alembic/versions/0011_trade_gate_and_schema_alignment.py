"""Align ORM types and persist trade observation/gate state.

Revision ID: 0011
Revises: 0010
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Match Party.role's ORM enum_string(PartyRole, 20). Existing values are
    # all shorter than 20, so PostgreSQL can narrow the VARCHAR safely.
    op.alter_column(
        "parties",
        "role",
        existing_type=sa.String(length=50),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
    # The model uses JSONB for indexed/provenance evidence. Keep the existing
    # JSON values intact while aligning the physical type.
    op.alter_column(
        "party_source_checks",
        "evidence",
        existing_type=sa.JSON(),
        type_=postgresql.JSONB(),
        existing_nullable=True,
        postgresql_using="evidence::jsonb",
    )
    op.add_column(
        "trades",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "trades",
        sa.Column(
            "participation_exclusion_reason",
            sa.String(length=50),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_trades_last_seen_at",
        "trades",
        ["last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_trades_last_seen_at", table_name="trades")
    op.drop_column("trades", "participation_exclusion_reason")
    op.drop_column("trades", "last_seen_at")
    op.alter_column(
        "party_source_checks",
        "evidence",
        existing_type=postgresql.JSONB(),
        type_=sa.JSON(),
        existing_nullable=True,
        postgresql_using="evidence::json",
    )
    op.alter_column(
        "parties",
        "role",
        existing_type=sa.String(length=20),
        type_=sa.String(length=50),
        existing_nullable=False,
    )
