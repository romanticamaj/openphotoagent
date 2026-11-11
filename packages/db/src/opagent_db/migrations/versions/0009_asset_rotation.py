"""A photo's rotation as a view setting.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-12

ROT-001: rotating a photo in the app is kept in the library, never written to the file (invariant 4); 0 for every
photo already there.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "rotation" in {c["name"] for c in insp.get_columns("asset")}:
        return  # a database created from the current models already has it
    op.add_column("asset", sa.Column("rotation", sa.SmallInteger(), nullable=False, server_default="0"))
    op.create_check_constraint("asset_rotation_quarter", "asset", "rotation IN (0, 90, 180, 270)")


def downgrade() -> None:  # pragma: no cover - forward-only (08 §6, VERSIONING §4)
    raise NotImplementedError("migrations are forward-only; restore a backup instead")
