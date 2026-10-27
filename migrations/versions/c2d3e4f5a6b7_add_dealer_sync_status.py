"""add last_sync_status / last_sync_duration_seconds to dealers

Denormalized 'last sync' block so the admin list and Dealer Overview can show
sync health without joining scrape_runs.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dealers", sa.Column("last_sync_status", sa.String(length=20), nullable=True))
    op.add_column(
        "dealers",
        sa.Column("last_sync_duration_seconds", sa.Float(), nullable=True),
    )
    # Backfill from the most recent terminating run so existing dealers do not
    # render as "never synced" right after the upgrade.
    op.execute(
        """
        UPDATE dealers d
        SET last_sync_status = CASE r.status
                                   WHEN 'completed' THEN 'success'
                                   ELSE r.status
                               END,
            last_sync_duration_seconds =
                EXTRACT(EPOCH FROM (r.finished_at - r.started_at))
        FROM (
            SELECT DISTINCT ON (dealer_id) dealer_id, status, started_at, finished_at
            FROM scrape_runs
            WHERE status <> 'running'
            ORDER BY dealer_id, id DESC
        ) r
        WHERE r.dealer_id = d.id
        """
    )


def downgrade() -> None:
    op.drop_column("dealers", "last_sync_duration_seconds")
    op.drop_column("dealers", "last_sync_status")
