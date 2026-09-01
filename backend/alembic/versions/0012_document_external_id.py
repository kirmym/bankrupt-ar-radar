"""Persist external document/file identifiers for evidence provenance.

Revision ID: 0012
Revises: 0011
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("external_id", sa.String(length=200), nullable=True))
    op.create_index("ix_documents_external_id", "documents", ["external_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_external_id", table_name="documents")
    op.drop_column("documents", "external_id")
