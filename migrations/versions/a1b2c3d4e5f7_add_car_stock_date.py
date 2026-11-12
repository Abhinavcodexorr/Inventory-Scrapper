"""Add cars.stock_date (the dealer's own "in stock since" date)

Revision ID: a1b2c3d4e5f7
Revises: f2b3c4d5e6a7
Create Date: 2026-11-12

Days in Stock is shown per vehicle. It is stored as the DATE the dealer's website
says the vehicle went into stock, and the day count is computed (today - date),
so it stays correct between syncs. Nullable: not every website reports it, and
existing rows are filled in by the next sync of each dealer.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, None] = "f2b3c4d5e6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cars", sa.Column("stock_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("cars", "stock_date")
