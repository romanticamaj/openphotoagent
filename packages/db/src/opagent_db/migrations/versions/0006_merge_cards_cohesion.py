"""Drop merge cards made by the centroid-only rule.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-10

ADR-0033: 「可能是同一人？」 needs two groups about as alike across as each is inside; the centroid-only rule paired a
boy with a girl in 4 of 5 cards. Open cards are derived data, recomputed at the next grouping; answered pairs
(dismissed) stay.
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM person_link WHERE kind = 'merge_suggestion' AND status = 'open'")


def downgrade() -> None:  # pragma: no cover - forward-only (08 §6, VERSIONING §4)
    raise NotImplementedError("migrations are forward-only; restore a backup instead")
