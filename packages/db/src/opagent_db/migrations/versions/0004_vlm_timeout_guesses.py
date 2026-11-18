"""Remove the guesses a VLM timeout left behind.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-10

Before INF-CAS-018, a photo whose VLM call failed for a passing reason (the GPU busy with other programs) was stored
as the embedding's guess, usually 其他 at about 0.2, under the normal cache key: analysing again returned it unchanged.
These results are the ones that were routed to the VLM, got no VLM answer, and carry an error. They go; their job items
are marked failed with that error, pending reviews of them go, and the photos are indexed again so the library shows
them as not analysed yet (the 分類 page offers to analyse them).
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

GUESSES = """
CREATE TEMP TABLE timeout_guess ON COMMIT DROP AS
SELECT id, asset_id, raw->'provenance'->>'error' AS code FROM result
WHERE coalesce(raw->'provenance'->>'error', '') <> ''
  AND raw->'output'->'stages'->>'route' = 'vlm'
  AND jsonb_typeof(raw->'output'->'stages'->'vlm') IS DISTINCT FROM 'object'
"""


def upgrade() -> None:
    op.execute(GUESSES)
    op.execute(
        "UPDATE job_item j SET status = 'failed', result_id = NULL, error_code = g.code "
        "FROM timeout_guess g WHERE j.result_id = g.id"
    )
    op.execute("DELETE FROM review r USING timeout_guess g WHERE r.result_id = g.id AND r.status = 'pending'")
    op.execute("INSERT INTO index_outbox(asset_id) SELECT DISTINCT asset_id FROM timeout_guess")
    op.execute("DELETE FROM result r USING timeout_guess g WHERE r.id = g.id")


def downgrade() -> None:  # pragma: no cover - forward-only (08 §6, VERSIONING §4)
    raise NotImplementedError("migrations are forward-only; restore a backup instead")
