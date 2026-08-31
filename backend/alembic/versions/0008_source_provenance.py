"""Add source provenance, per-registry checks and tri-state legal facts.

Revision ID: 0008
Revises: 0007
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trade_source_refs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "trade_id",
            sa.Integer(),
            sa.ForeignKey("trades.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=False),
        sa.Column("external_trade_id", sa.String(length=200), nullable=True),
        sa.Column("external_lot_id", sa.String(length=200), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("source", "source_url", name="uq_trade_source_ref_url"),
    )
    op.create_index("ix_trade_source_refs_trade_id", "trade_source_refs", ["trade_id"])
    op.create_index(
        "ix_trade_source_refs_external",
        "trade_source_refs",
        ["source", "external_trade_id"],
    )

    op.create_table(
        "party_source_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "party_id",
            sa.Integer(),
            sa.ForeignKey("parties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.UniqueConstraint("party_id", "source", name="uq_party_source_check"),
    )
    op.create_index(
        "ix_party_source_checks_party_id", "party_source_checks", ["party_id"]
    )
    op.create_index(
        "ix_party_source_checks_status",
        "party_source_checks",
        ["source", "status", "checked_at"],
    )

    # Existing URLs were stored in an EFRSB-named column. Preserve them as
    # provenance records; CDT URLs are identified by their public domain.
    op.execute(
        """
        INSERT INTO trade_source_refs
            (trade_id, source, source_url, external_trade_id, captured_at)
        SELECT id,
               CASE WHEN efrsb_url LIKE '%torgi.cdtrf.ru%'
                         OR efrsb_url LIKE '%webapi.torgi.cdtrf.ru%'
                    THEN 'cdt_public' ELSE 'efrsb_public' END,
               efrsb_url,
               COALESCE(efrsb_trade_guid, trade_id_on_etp),
               COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
        FROM trades
        WHERE efrsb_url IS NOT NULL
        ON CONFLICT (source, source_url) DO NOTHING
        """
    )

    # Legacy false values did not distinguish a verified negative from an
    # unchecked source. They become NULL until a registry check writes a result.
    for table, columns in {
        "parties": ("kad_bankruptcy_open", "fssp_uncollectible"),
        "claims": ("has_judgment", "has_writ", "enforcement_alive"),
    }.items():
        for column in columns:
            op.alter_column(table, column, nullable=True, server_default=None)
            op.execute(sa.text(f"UPDATE {table} SET {column} = NULL"))


def downgrade() -> None:
    # A downgrade cannot reconstruct which old False values were verified.
    # Restore a conservative non-null representation for compatibility.
    for table, columns in {
        "parties": ("kad_bankruptcy_open", "fssp_uncollectible"),
        "claims": ("has_judgment", "has_writ", "enforcement_alive"),
    }.items():
        for column in columns:
            op.execute(sa.text(f"UPDATE {table} SET {column} = FALSE WHERE {column} IS NULL"))
            op.alter_column(table, column, nullable=False, server_default=sa.text("false"))
    op.drop_index("ix_party_source_checks_status", table_name="party_source_checks")
    op.drop_index("ix_party_source_checks_party_id", table_name="party_source_checks")
    op.drop_table("party_source_checks")
    op.drop_index("ix_trade_source_refs_external", table_name="trade_source_refs")
    op.drop_index("ix_trade_source_refs_trade_id", table_name="trade_source_refs")
    op.drop_table("trade_source_refs")
