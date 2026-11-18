"""Choose the covers again: typical and sharp.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-10

FACE-UI-018: the cover is the sharpest of the five most typical clear faces. Covers chosen by the first rule (the
most typical alone, often a soft crop) are cleared; the people page picks them again once.
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE person SET cover_face_id = NULL")


def downgrade() -> None:  # pragma: no cover - forward-only (08 §6, VERSIONING §4)
    raise NotImplementedError("migrations are forward-only; restore a backup instead")
