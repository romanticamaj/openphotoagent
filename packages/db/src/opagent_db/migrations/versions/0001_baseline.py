"""v0.1 baseline: the schema as shipped in the first desktop release.

Revision ID: 0001
Revises:
Create Date: 2026-11-02

Builds the frozen v1.0 schema (``opagent_db.frozen_v1_0``) on an empty database. Databases written by v0.x were
stamped ``0001`` with whatever that release had; ``0002`` brings them up to v1.0. Every later change is a new
explicit revision (VERSIONING §4: forward-only).
"""

from alembic import op
from opagent_db.frozen_v1_0 import metadata

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
