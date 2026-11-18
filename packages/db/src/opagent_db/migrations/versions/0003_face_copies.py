"""Remove the copies of a face that analysing a photo again left behind.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-07

The ingest route used to add a photo's faces every time it was told about them (FACE-DET-010). Three copies of a face
are at distance 0 from each other, which HDBSCAN reads as a cluster of its own, so groups were mostly one face repeated.
Per face this keeps the copy a person decided on, else the oldest. A rejection said about a copy moves to the survivor.
The run history goes too: it is what tells the worker there is nothing to group, and those groups were built on copies.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

COPIES = """
CREATE TEMP TABLE face_copy ON COMMIT DROP AS
SELECT id, keep FROM (
  SELECT f.id, first_value(f.id) OVER (
    PARTITION BY f.asset_id, f.space_id, b.box
    ORDER BY f.state = 'confirmed' DESC, coalesce(p.is_frozen, false) DESC, f.id
  ) AS keep
  FROM face f
  LEFT JOIN person p ON p.id = f.person_id
  CROSS JOIN LATERAL (SELECT array_agg(round(v::numeric, 3)) AS box FROM unnest(f.bbox) v) b
  WHERE f.source = 'detected'
) ranked
WHERE id <> keep
"""


def upgrade() -> None:
    op.execute(COPIES)
    op.execute(
        "UPDATE face_assignment a SET face_id = c.keep FROM face_copy c WHERE a.face_id = c.id AND a.kind = 'rejected'"
    )
    op.execute("DELETE FROM face_cluster_run WHERE EXISTS (SELECT 1 FROM face_copy)")
    op.execute("DELETE FROM bio.face_embedding e USING face_copy c WHERE e.face_id = c.id")
    op.execute("DELETE FROM face f USING face_copy c WHERE f.id = c.id")


def downgrade() -> None:  # pragma: no cover - forward-only (08 §6, VERSIONING §4)
    raise NotImplementedError("migrations are forward-only; restore a backup instead")
