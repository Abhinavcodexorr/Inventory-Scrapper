"""add discovery_runs, site_patterns, dealers.detected_platform

Purely additive: the redesigned discovery engine (dealer_scraper/discovery/)
persists every run instead of computing-and-discarding a DiscoveryResult, and
learns per-host patterns across runs. Nothing here changes existing tables'
data or the scraping pipeline.

Revision ID: e5f6a7b8c9d0
Revises: d3e4f5a6b7c8
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("dealers", sa.Column("detected_platform", sa.String(length=120), nullable=True))

    op.create_table(
        "discovery_runs",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("dealer_id", sa.Integer(), sa.ForeignKey("dealers.id"), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("phase", sa.String(length=40), nullable=True),
        sa.Column("phases_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("phases_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("confidence", sa.String(length=20), nullable=True),
        sa.Column("verdict", sa.String(length=20), nullable=True),
        sa.Column("requires_custom_work", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("adapter_mode", sa.String(length=40), nullable=True),
        sa.Column("vehicles_verified", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scrape_config", _json_type(), nullable=True),
        sa.Column("result", _json_type(), nullable=True),
        sa.Column("report", _json_type(), nullable=True),
        sa.Column("error", sa.String(length=1000), nullable=True),
    )
    op.create_index("ix_discovery_runs_base_url", "discovery_runs", ["base_url"])
    op.create_index("ix_discovery_runs_dealer", "discovery_runs", ["dealer_id"])

    op.create_table(
        "site_patterns",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("cms_platform", sa.String(length=80), nullable=True),
        sa.Column("pattern_kind", sa.String(length=40), nullable=False),
        sa.Column("pattern", _json_type(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_run_id", sa.Integer(), sa.ForeignKey("discovery_runs.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("host", "pattern_kind", name="uq_site_pattern_host_kind"),
    )
    op.create_index("ix_site_patterns_host", "site_patterns", ["host"])
    op.create_index("ix_site_patterns_cms", "site_patterns", ["cms_platform"])


def downgrade() -> None:
    op.drop_index("ix_site_patterns_cms", table_name="site_patterns")
    op.drop_index("ix_site_patterns_host", table_name="site_patterns")
    op.drop_table("site_patterns")

    op.drop_index("ix_discovery_runs_dealer", table_name="discovery_runs")
    op.drop_index("ix_discovery_runs_base_url", table_name="discovery_runs")
    op.drop_table("discovery_runs")

    op.drop_column("dealers", "detected_platform")
