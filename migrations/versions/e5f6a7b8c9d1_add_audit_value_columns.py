"""Add numeric old_value/new_value to the vehicle audit log

Revision ID: e5f6a7b8c9d1
Revises: d4e5f6a7b8c9
Create Date: 2026-09-18

Price and mileage movement was only ever stored in the human-readable `note`
("44990 -> 42990"). That displays fine and sorts uselessly — "every vehicle
that dropped more than $2,000 this week" cannot be asked of a string, and that
comparison is the point of tracking inventory daily.

Existing rows are backfilled by parsing the note, so history written before
this migration is not lost to the new filters.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Text

revision: str = "e5f6a7b8c9d1"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vehicle_audit_logs", sa.Column("old_value", sa.Numeric(12, 2), nullable=True))
    op.add_column("vehicle_audit_logs", sa.Column("new_value", sa.Numeric(12, 2), nullable=True))

    # Backfill from the note for the two event types that carry a number.
    # Only plain "<digits> -> <digits>" notes are converted; anything else
    # (a NULL old price, a non-numeric field) is left NULL rather than guessed.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("""
            UPDATE vehicle_audit_logs
               SET old_value = NULLIF(split_part(note, ' -> ', 1), '')::numeric,
                   new_value = NULLIF(split_part(note, ' -> ', 2), '')::numeric
             WHERE event_type IN ('price_changed', 'mileage_changed')
               AND note ~ '^[0-9]+(\\.[0-9]+)? -> [0-9]+(\\.[0-9]+)?$'
        """))
    else:
        # SQLite (tests): no regex operator, so do it in Python.
        rows = bind.execute(sa.text(
            "SELECT id, note FROM vehicle_audit_logs "
            "WHERE event_type IN ('price_changed','mileage_changed') AND note LIKE '% -> %'"
        )).fetchall()
        for row_id, note in rows:
            try:
                before, after = (p.strip() for p in note.split("->", 1))
                bind.execute(
                    sa.text("UPDATE vehicle_audit_logs SET old_value=:o, new_value=:n WHERE id=:i"),
                    {"o": float(before), "n": float(after), "i": row_id},
                )
            except (ValueError, TypeError):
                continue  # unparseable note — leave both NULL


def downgrade() -> None:
    op.drop_column("vehicle_audit_logs", "new_value")
    op.drop_column("vehicle_audit_logs", "old_value")
