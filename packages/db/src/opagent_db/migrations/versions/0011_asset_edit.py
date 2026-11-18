"""A photo's edit as a view setting.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-18

EDIT-001 (ADR-0057): light, colour, crop and a filter, kept in the library and drawn by the page; never written to the
file (invariant 4). NULL for every photo already there.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("asset")}
    if "edit" not in cols:
        op.add_column("asset", sa.Column("edit", JSONB(), nullable=True))
    if "edit_rev" not in cols:
        op.add_column("asset", sa.Column("edit_rev", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:  # pragma: no cover - forward-only (08 §6, VERSIONING §4)
    raise NotImplementedError("migrations are forward-only; restore a backup instead")
