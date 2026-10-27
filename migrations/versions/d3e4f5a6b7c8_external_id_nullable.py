"""allow cars.external_id to be NULL (listing with no dealer stock number)

Real feeds omit the stock number for cars that are not on the lot yet — DealerOn
leaves VehicleStockNumber empty for factory-ordered (InProduction) and in-transit
units, ~38% of one Toyota store's new inventory. Those listings are still real
vehicles with a VIN, so they must be storable.

NOT NULL forced them all to "", which every listing after the first collided on
via uq_car_dealer_external. NULL is the honest value, and Postgres treats NULLs
as distinct in a unique index, so the constraint still enforces "one row per
dealer stock number" for every listing that actually has one.

Identity for these rows comes from VIN (and URL), which _CarIndex already prefers
over external_id anyway.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("cars", "external_id", existing_type=sa.String(length=200), nullable=True)
    # Any row already carrying the placeholder is a listing with no stock number.
    op.execute("UPDATE cars SET external_id = NULL WHERE external_id = ''")


def downgrade() -> None:
    # NOT NULL cannot be restored while blank-stock listings exist; drop them
    # rather than inventing stock numbers that were never real.
    op.execute("DELETE FROM cars WHERE external_id IS NULL")
    op.alter_column("cars", "external_id", existing_type=sa.String(length=200), nullable=False)
