"""add website_provider to dealers

Revision ID: b1c2d3e4f5a6
Revises: af3ebda831f0
Create Date: 2026-07-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "af3ebda831f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dealers", sa.Column("website_provider", sa.String(length=120), nullable=True))
    op.create_index("ix_dealers_website_provider", "dealers", ["website_provider"])


def downgrade() -> None:
    op.drop_index("ix_dealers_website_provider", table_name="dealers")
    op.drop_column("dealers", "website_provider")
