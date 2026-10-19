"""normalise task.preferred_model to Klådan, Sonnet, GLM, Kimi (or NULL)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-19 10:05:00.000000

Data-only migration. preferred_model now accepts only Klådan, Sonnet, GLM and
Kimi. Existing values map as follows (case-insensitive):

- Opus, Klådan, Klada            -> Klådan
- GLM, Glammet                   -> GLM
- Kimi, Kinesen                  -> Kimi
- Sonnet                         -> Sonnet
- anything else (e.g. Chrille)   -> NULL

The mapping is repeated here on purpose: a migration must not change when
application code does.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MAPPING = {
    "opus": "Klådan",
    "klådan": "Klådan",
    "klada": "Klådan",
    "glm": "GLM",
    "glammet": "GLM",
    "kimi": "Kimi",
    "kinesen": "Kimi",
    "sonnet": "Sonnet",
}


def upgrade() -> None:
    """Normalise existing values."""
    bind = op.get_bind()
    values = [
        row[0]
        for row in bind.execute(
            sa.text("SELECT DISTINCT preferred_model FROM task WHERE preferred_model IS NOT NULL")
        )
    ]
    for old in values:
        new = _MAPPING.get(old.strip().lower())
        if new == old:
            continue
        bind.execute(
            sa.text("UPDATE task SET preferred_model = :new WHERE preferred_model = :old"),
            {"new": new, "old": old},
        )


def downgrade() -> None:
    """Nothing to undo: the old spellings are not recoverable."""
