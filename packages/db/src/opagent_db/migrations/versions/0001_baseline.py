"""v0.1 baseline: the schema as shipped in the first desktop release.

Revision ID: 0001
Revises:
Create Date: 2026-11-02

Until v0.1.0 is tagged this baseline tracks ``opagent_db.schema``; from then on it is frozen and every
schema change is a new revision (VERSIONING §4: forward-only).
"""

from alembic import op
from opagent_db.schema import metadata

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    metadata.create_all(bind, checkfirst=True)


def downgrade() -> None:  # pragma: no cover - forward-only (08 §6, VERSIONING §4)
    raise NotImplementedError("migrations are forward-only; restore a backup instead")
