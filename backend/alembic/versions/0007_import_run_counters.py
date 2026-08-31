"""Track changed, unchanged and rejected source items per import run.

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in ("items_changed", "items_unchanged", "items_rejected"):
        op.add_column(
            "import_runs",
            sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for name in ("items_rejected", "items_unchanged", "items_changed"):
        op.drop_column("import_runs", name)
