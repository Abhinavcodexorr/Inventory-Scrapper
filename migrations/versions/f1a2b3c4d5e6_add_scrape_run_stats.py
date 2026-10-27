"""add scrape_runs.stats and discovery_runs.options

Purely additive: latency-optimization instrumentation (dealer_scraper/timing.py)
persists a per-stage timing snapshot on every ScrapeRun, and the discovery cache
(dealer_scraper/discovery/learning.py) needs a per-run options bag (e.g.
ignore_cache) that isn't part of the discovered result itself.

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("scrape_runs", sa.Column("stats", _json_type(), nullable=True))
    op.add_column("discovery_runs", sa.Column("options", _json_type(), nullable=True))


def downgrade() -> None:
    op.drop_column("discovery_runs", "options")
    op.drop_column("scrape_runs", "stats")
