"""Previews cut short by a restart are not jobs to resume.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-11

OP-021: a preview running at a restart is now cancelled, not interrupted. Operations an older version left
interrupted for a preview job are cancelled, so the 「上次有工作被中斷」 card no longer shows them.
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE operation o SET status = 'cancelled', finished_at = now(), updated_at = now(), seq = o.seq + 1 "
        "FROM job j WHERE j.id = o.id AND j.kind = 'preview' AND o.status = 'interrupted'"
    )


def downgrade() -> None:  # pragma: no cover - forward-only (08 §6, VERSIONING §4)
    raise NotImplementedError("migrations are forward-only; restore a backup instead")
