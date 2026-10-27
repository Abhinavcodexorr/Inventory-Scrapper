"""add cars.updated_at and cars.original_price

Two client-requested fields:

  updated_at     "Last updated date" — set only when a listing's content actually
                 changes. Distinct from last_seen_at, which is bumped on every
                 sighting and so reads "today" for every active car forever.

  original_price the struck-through / pre-discount figure on a discounted
                 listing. Previously parsed and thrown away (the adapter kept
                 only the sale price), so a discount was invisible.

Backfill: updated_at is seeded from the vehicle's most recent *content* audit
event, so existing rows carry a truthful value instead of NULL. Cars that have
never changed since creation fall back to first_seen_at.

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-08-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Events that mean "the listing itself changed". 'created' is excluded — it is
# the birth of the row, not an update to it.
_CONTENT_EVENTS = "('updated', 'price_changed', 'mileage_changed', 'status_changed')"


def upgrade() -> None:
    op.add_column("cars", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cars", sa.Column("original_price", sa.Numeric(12, 2), nullable=True))

    # Seed updated_at from the audit trail: the latest real change per vehicle.
    op.execute(
        f"""
        UPDATE cars
           SET updated_at = (
               SELECT MAX(occurred_at)
                 FROM vehicle_audit_logs
                WHERE vehicle_audit_logs.car_id = cars.id
                  AND vehicle_audit_logs.event_type IN {_CONTENT_EVENTS}
           )
        """
    )
    # Never-changed cars: treat creation as the last update, so the column is
    # always populated and the UI never has to render a blank.
    op.execute("UPDATE cars SET updated_at = first_seen_at WHERE updated_at IS NULL")

    op.create_index("ix_cars_updated_at", "cars", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_cars_updated_at", table_name="cars")
    op.drop_column("cars", "original_price")
    op.drop_column("cars", "updated_at")
