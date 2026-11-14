"""v1.0 catch-up: bring a database written by any v0.x release to the frozen v1.0 schema.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03

Before v1.0 the baseline tracked the models, so a v0.x install is stamped ``0001`` but lacks whatever came later
(MIG-001). This revision only **adds**: missing tables (with their DDL hooks: the ``bio`` schema, the append-only
trigger), missing columns (all nullable or with a server default — checked) and missing indexes. It never drops or
retypes anything; something the comparison cannot add safely stops the upgrade (the pre-migration backup is kept).
"""

from typing import Any

from alembic import op
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from opagent_db.frozen_v1_0 import metadata

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

IGNORED = {"remove_table", "remove_index", "remove_column", "remove_constraint", "remove_fk"}  # never drop data


class CatchUpError(RuntimeError):
    pass


def _kind(diff: Any) -> str:  # compare_metadata yields tuples, or lists of tuples for column modifications
    return str(diff[0] if isinstance(diff, tuple) else diff[0][0])


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    metadata.create_all(bind, checkfirst=True)  # missing tables, with their after_create hooks
    for diff in compare_metadata(MigrationContext.configure(bind), metadata):
        kind = _kind(diff)
        if kind == "add_column":
            _, schema, table, col = diff
            if not col.nullable and col.server_default is None and col.computed is None:
                raise CatchUpError(f"{table}.{col.name} is NOT NULL without a default")
            op.add_column(table, col.copy(), schema=schema)
        elif kind == "add_index":
            diff[1].create(bind, checkfirst=True)  # the frozen Index itself: expressions and DESC included
        elif kind in IGNORED:
            continue
        else:  # a type or nullability change between a v0.x release and v1.0 would need its own migration
            raise CatchUpError(f"cannot catch up automatically: {diff!r}"[:300])


def downgrade() -> None:  # pragma: no cover - forward-only (08 §6, VERSIONING §4)
    raise NotImplementedError("migrations are forward-only; restore a backup instead")
