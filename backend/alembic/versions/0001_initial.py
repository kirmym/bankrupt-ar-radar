"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-27
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "parties",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("guid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("person_kind", sa.String(length=10), nullable=True),
        sa.Column("inn", sa.String(length=12), nullable=True),
        sa.Column("ogrn", sa.String(length=15), nullable=True),
        sa.Column("name", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("reg_date", sa.Date(), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("invalid_address", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("invalid_director", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("pending_exclusion", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("mass_address", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("director_name", sa.String(length=200), nullable=True),
        sa.Column("revenue", sa.Numeric(18, 2), nullable=True),
        sa.Column("cash", sa.Numeric(18, 2), nullable=True),
        sa.Column("current_assets", sa.Numeric(18, 2), nullable=True),
        sa.Column("equity", sa.Numeric(18, 2), nullable=True),
        sa.Column("short_term_liab", sa.Numeric(18, 2), nullable=True),
        sa.Column("profit", sa.Numeric(18, 2), nullable=True),
        sa.Column("bo_year", sa.Integer(), nullable=True),
        sa.Column("tax_debt", sa.Numeric(18, 2), nullable=True),
        sa.Column("headcount", sa.Integer(), nullable=True),
        sa.Column("kad_as_defendant_count", sa.Integer(), nullable=True),
        sa.Column("kad_bankruptcy_open", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("fssp_sum", sa.Numeric(18, 2), nullable=True),
        sa.Column("fssp_uncollectible", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("guid"),
        sa.UniqueConstraint("role", "inn", name="uq_party_role_inn"),
    )
    op.create_index("ix_parties_inn", "parties", ["inn"])
    op.create_index("ix_parties_role_status", "parties", ["role", "status"])
    op.create_index("ix_parties_kad_bankruptcy", "parties", ["kad_bankruptcy_open"])

    op.create_table(
        "raw_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("content_type", sa.String(length=50), nullable=True),
        sa.Column("raw_content", sa.Text(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_raw_snapshots_source", "raw_snapshots", ["source"])

    op.create_table(
        "trades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("guid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("efrsb_trade_guid", sa.String(length=50), nullable=True),
        sa.Column("efrsb_message_guid", sa.String(length=50), nullable=True),
        sa.Column("efrsb_url", sa.String(length=500), nullable=True),
        sa.Column("trade_id_on_etp", sa.String(length=100), nullable=True),
        sa.Column("etp_inn", sa.String(length=12), nullable=True),
        sa.Column("etp_name", sa.String(length=200), nullable=True),
        sa.Column("etp_url", sa.String(length=500), nullable=True),
        sa.Column("trade_kind", sa.String(length=30), nullable=False),
        sa.Column("trade_form", sa.String(length=20), nullable=True),
        sa.Column("is_repeat", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("organizer_inn", sa.String(length=12), nullable=True),
        sa.Column("organizer_name", sa.String(length=300), nullable=True),
        sa.Column("am_inn", sa.String(length=12), nullable=True),
        sa.Column("am_name", sa.String(length=200), nullable=True),
        sa.Column("bankrupt_party_id", sa.Integer(), nullable=True),
        sa.Column("case_number", sa.String(length=30), nullable=True),
        sa.Column("court_name", sa.String(length=200), nullable=True),
        sa.Column("applications_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applications_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_info", sa.Text(), nullable=True),
        sa.Column("sale_agreement_rules", sa.Text(), nullable=True),
        sa.Column("raw_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["bankrupt_party_id"], ["parties.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["raw_snapshot_id"], ["raw_snapshots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("guid"),
        sa.UniqueConstraint("efrsb_trade_guid"),
    )
    op.create_index("ix_trades_status", "trades", ["status"])
    op.create_index("ix_trades_etp_inn", "trades", ["etp_inn"])
    op.create_index("ix_trades_applications_to", "trades", ["applications_to"])

    op.create_table(
        "lots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("guid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_id", sa.Integer(), nullable=False),
        sa.Column("lot_no", sa.Integer(), nullable=False),
        sa.Column("classifier_codes", postgresql.ARRAY(sa.String(20)), nullable=False, server_default="{}"),
        sa.Column("classifier_labels", postgresql.ARRAY(sa.String(200)), nullable=False, server_default="{}"),
        sa.Column("is_receivable", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("description_html", sa.Text(), nullable=True),
        sa.Column("description_text", sa.Text(), nullable=True),
        sa.Column("nominal_claimed", sa.Numeric(18, 2), nullable=True),
        sa.Column("start_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("current_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("current_interval_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_interval_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cutoff_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("deposit_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("deposit_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("price_reduction_html", sa.Text(), nullable=True),
        sa.Column("inspection_rules", sa.Text(), nullable=True),
        sa.Column("bundle_flag", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("docs_on_efrsb", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("docs_on_etp", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("score_class", sa.String(length=2), nullable=True),
        sa.Column("score_ev", sa.Numeric(18, 2), nullable=True),
        sa.Column("score_ev_low", sa.Numeric(18, 2), nullable=True),
        sa.Column("score_ev_high", sa.Numeric(18, 2), nullable=True),
        sa.Column("score_scenario", sa.String(length=30), nullable=True),
        sa.Column("score_stop_factors", postgresql.ARRAY(sa.String(50)), nullable=False, server_default="{}"),
        sa.Column("score_gaps", postgresql.ARRAY(sa.String(50)), nullable=False, server_default="{}"),
        sa.Column("score_max_bid", sa.Numeric(18, 2), nullable=True),
        sa.Column("score_version", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["trade_id"], ["trades.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("guid"),
        sa.UniqueConstraint("trade_id", "lot_no", name="uq_trade_lot_no"),
    )
    op.create_index("ix_lots_score_class", "lots", ["score_class"])
    op.create_index("ix_lots_score_ev", "lots", ["score_ev"])
    op.create_index("ix_lots_is_receivable", "lots", ["is_receivable"])
    op.create_index("ix_lots_current_interval_to", "lots", ["current_interval_to"])

    op.create_table(
        "price_intervals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(18, 2), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(["lot_id"], ["lots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_price_intervals_lot_seq", "price_intervals", ["lot_id", "seq"])

    op.create_table(
        "claims",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("guid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("principal", sa.Numeric(18, 2), nullable=True),
        sa.Column("penalties", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="RUB"),
        sa.Column("base_contract", sa.String(length=500), nullable=True),
        sa.Column("base_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("limitations_deadline", sa.Date(), nullable=True),
        sa.Column("il_issue_date", sa.Date(), nullable=True),
        sa.Column("il_present_deadline", sa.Date(), nullable=True),
        sa.Column("court_case_no", sa.String(length=50), nullable=True),
        sa.Column("has_judgment", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("has_writ", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("enforcement_alive", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("secured", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("assignment_forbidden", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("counterclaim_risk", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("personal_claim", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("debtor_party_id", sa.Integer(), nullable=True),
        sa.Column("guarantor_party_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["debtor_party_id"], ["parties.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["guarantor_party_id"], ["parties.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["lot_id"], ["lots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("guid"),
    )
    op.create_index("ix_claims_lot_id", "claims", ["lot_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("guid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=50), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("extracted_facts", postgresql.JSONB(), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["lot_id"], ["lots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("guid"),
    )

    op.create_table(
        "score_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("guid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("score_class", sa.String(length=2), nullable=False),
        sa.Column("ev", sa.Numeric(18, 2), nullable=False),
        sa.Column("ev_low", sa.Numeric(18, 2), nullable=True),
        sa.Column("ev_high", sa.Numeric(18, 2), nullable=True),
        sa.Column("max_bid", sa.Numeric(18, 2), nullable=True),
        sa.Column("scenario", sa.String(length=30), nullable=True),
        sa.Column("stop_factors", postgresql.ARRAY(sa.String(50)), nullable=False, server_default="{}"),
        sa.Column("gaps", postgresql.ARRAY(sa.String(50)), nullable=False, server_default="{}"),
        sa.Column("model_version", sa.String(length=20), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["lot_id"], ["lots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("guid"),
    )
    op.create_index("ix_score_snapshots_lot_scored", "score_snapshots", ["lot_id", "scored_at"])

    op.create_table(
        "user_feedbacks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("recovered_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["lot_id"], ["lots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_feedbacks_lot_id", "user_feedbacks", ["lot_id"])


def downgrade() -> None:
    op.drop_index("ix_user_feedbacks_lot_id", table_name="user_feedbacks")
    op.drop_table("user_feedbacks")

    op.drop_index("ix_score_snapshots_lot_scored", table_name="score_snapshots")
    op.drop_table("score_snapshots")

    op.drop_table("documents")

    op.drop_index("ix_claims_lot_id", table_name="claims")
    op.drop_table("claims")

    op.drop_index("ix_price_intervals_lot_seq", table_name="price_intervals")
    op.drop_table("price_intervals")

    op.drop_index("ix_lots_current_interval_to", table_name="lots")
    op.drop_index("ix_lots_is_receivable", table_name="lots")
    op.drop_index("ix_lots_score_ev", table_name="lots")
    op.drop_index("ix_lots_score_class", table_name="lots")
    op.drop_table("lots")

    op.drop_index("ix_trades_applications_to", table_name="trades")
    op.drop_index("ix_trades_etp_inn", table_name="trades")
    op.drop_index("ix_trades_status", table_name="trades")
    op.drop_table("trades")

    op.drop_index("ix_raw_snapshots_source", table_name="raw_snapshots")
    op.drop_table("raw_snapshots")

    op.drop_index("ix_parties_kad_bankruptcy", table_name="parties")
    op.drop_index("ix_parties_role_status", table_name="parties")
    op.drop_index("ix_parties_inn", table_name="parties")
    op.drop_table("parties")
