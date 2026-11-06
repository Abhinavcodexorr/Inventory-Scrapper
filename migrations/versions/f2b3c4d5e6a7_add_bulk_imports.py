"""Add bulk dealer imports

Revision ID: f2b3c4d5e6a7
Revises: e5f6a7b8c9d1
Create Date: 2026-11-05

A list of dealer websites is worked through in the background, so its progress
has to live in the database: it must survive an API restart and be readable as a
report after the operator has closed the page.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2b3c4d5e6a7"
down_revision: Union[str, None] = "e5f6a7b8c9d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BIG_PK = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    op.create_table(
        "bulk_imports",
        sa.Column("id", _BIG_PK, primary_key=True),
        sa.Column("filename", sa.String(200)),
        sa.Column("provider", sa.String(120)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("notice", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "bulk_import_rows",
        sa.Column("id", _BIG_PK, primary_key=True),
        sa.Column("import_id", _BIG_PK, sa.ForeignKey("bulk_imports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("website", sa.String(500), nullable=False),
        sa.Column("name", sa.String(200)),
        sa.Column("address", sa.String(300)),
        sa.Column("city", sa.String(120)),
        sa.Column("province", sa.String(80)),
        sa.Column("postal_code", sa.String(20)),
        sa.Column("phone", sa.String(40)),
        sa.Column("provider", sa.String(120)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(1000)),
        sa.Column("discovery_run_id", sa.BigInteger),
        sa.Column("dispatch_mode", sa.String(20)),
        sa.Column("dealer_id", sa.Integer),
        sa.Column("vehicles_found", sa.Integer),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_bulk_import_rows_import_position", "bulk_import_rows", ["import_id", "position"])
    op.create_index("ix_bulk_import_rows_status", "bulk_import_rows", ["status"])


def downgrade() -> None:
    op.drop_table("bulk_import_rows")
    op.drop_table("bulk_imports")
