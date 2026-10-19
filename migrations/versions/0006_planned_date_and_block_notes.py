"""planned_date and block notes on task, planned-run lines moved into the date

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-19 10:00:00.000000

Adds to ``task``:

- ``planned_date`` (date, nullable): the day the task is planned to run.
- ``blocked_by``, ``blocked_reason``, ``unblock_by`` (nullable text): who
  blocked the task, why, and who or what releases it (block_task).

Data migration: a description whose first line is
``Planerad körning: YYYY-MM-DD`` gets planned_date set from it, and that line
(plus one following empty line) is removed from the description. Lines with an
impossible date are left untouched.
"""
import re
from datetime import date
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PLANNED_LINE = re.compile(r"^Planerad körning:\s*(\d{4}-\d{2}-\d{2})[ \t]*$")


def split_planned_line(description: str):
    """Return (date, remaining description) or None when the first line is not
    a valid "Planerad körning: YYYY-MM-DD" line."""
    lines = description.split("\n")
    m = _PLANNED_LINE.match(lines[0].rstrip("\r"))
    if m is None:
        return None
    try:
        planned = date.fromisoformat(m.group(1))
    except ValueError:
        return None
    rest = lines[1:]
    if rest and rest[0].strip() == "":
        rest = rest[1:]
    return planned, "\n".join(rest)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("task", sa.Column("planned_date", sa.Date(), nullable=True))
    op.add_column("task", sa.Column("blocked_by", sa.String(), nullable=True))
    op.add_column("task", sa.Column("blocked_reason", sa.String(), nullable=True))
    op.add_column("task", sa.Column("unblock_by", sa.String(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, description FROM task WHERE description LIKE 'Planerad körning:%'")
    ).fetchall()
    for task_id, description in rows:
        parsed = split_planned_line(description or "")
        if parsed is None:
            continue
        planned, rest = parsed
        bind.execute(
            sa.text(
                "UPDATE task SET planned_date = :d, description = :desc WHERE id = :id"
            ),
            {"d": planned, "desc": rest, "id": task_id},
        )


def downgrade() -> None:
    """Downgrade schema. The "Planerad körning" lines are not restored."""
    op.drop_column("task", "unblock_by")
    op.drop_column("task", "blocked_reason")
    op.drop_column("task", "blocked_by")
    op.drop_column("task", "planned_date")
