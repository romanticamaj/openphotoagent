"""One person's choices about memories: hidden ones and subjects they want fewer of.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-18

MEM-041 (ADR-0049 addendum 8): its own table, not workspace.settings, which the face switch also writes.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "memory_feedback" in sa.inspect(op.get_bind()).get_table_names():
        return  # a database created from the current models already has it
    op.create_table(
        "memory_feedback",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False, server_default="local"),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ref", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "user_id", "kind", "ref", name="memory_feedback_once"),
    )


def downgrade() -> None:  # pragma: no cover - forward-only (08 §6, VERSIONING §4)
    raise NotImplementedError("migrations are forward-only; restore a backup instead")
