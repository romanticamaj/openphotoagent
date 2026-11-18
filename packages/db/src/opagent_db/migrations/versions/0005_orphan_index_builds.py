"""Close index builds an older version left running.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-10

Before SRCH-R2-007 a caption-index build whose worker tasks failed, or that a newer build replaced, kept its
operation running for ever: Activity said 「建立搜尋索引 進行中 1 秒」 and its icon spun. An operation of a space that
is no longer building is cancelled.
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE operation o SET status = 'cancelled', finished_at = now(), updated_at = now(), seq = o.seq + 1 "
        "FROM embedding_space s "
        "WHERE o.type = 'search.reindex' AND o.status IN ('running', 'queued', 'waiting', 'paused') "
        "AND s.thresholds->>'operation_id' = o.id AND s.status <> 'building'"
    )


def downgrade() -> None:  # pragma: no cover - forward-only (08 §6, VERSIONING §4)
    raise NotImplementedError("migrations are forward-only; restore a backup instead")
