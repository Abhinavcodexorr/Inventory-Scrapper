"""drop cars.images

The scraper no longer captures vehicle photos — every extraction path
(discovery field-mapping, json_api/browser adapters, normalize.py) has had
its images handling removed. This column is now write-only dead weight.

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.drop_column("cars", "images")


def downgrade() -> None:
    op.add_column("cars", sa.Column("images", _json_type(), nullable=True))
