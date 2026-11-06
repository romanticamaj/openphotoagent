"""PostgreSQL schema (docs/02 §3.2, §10.1). Single DB backend (ADR-008). Alembic migrations arrive with PG upgrades."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    DDL,
    BigInteger,
    Boolean,
    Column,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy import event as sa_event
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.engine import Engine
from sqlalchemy.types import UserDefinedType

metadata = MetaData()


def _ts(name: str, **kw: object) -> Column[object]:
    return Column(name, DateTime(timezone=True), **kw)  # type: ignore[arg-type]


workspace = Table(
    "workspace",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("settings", JSONB, nullable=False, server_default="{}"),
    Column("database", Text),  # control plane: the workspace's own database (ADR-0016); NULL = this database
    _ts("created_at", server_default=func.now()),
)
worker = Table(
    "worker",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("roots", JSONB, nullable=False),
    Column("status", Text, nullable=False, server_default="online"),
    Column("disk_free", JSONB),
    Column("capabilities", JSONB),
    _ts("last_heartbeat_at"),
)
source = Table(
    "source",
    metadata,
    Column("id", Text, primary_key=True),
    Column("workspace_id", Text, ForeignKey("workspace.id"), nullable=False),
    Column("worker_id", Text, ForeignKey("worker.id")),
    Column("root_alias", Text, nullable=False),
    Column("sub_path", Text, nullable=False, server_default=""),
    Column("include", JSONB, nullable=False),
    Column("exclude", JSONB, nullable=False),
    Column("status", Text, nullable=False, server_default="online"),
    _ts("last_scan_at"),
    _ts("created_at", server_default=func.now()),
)
asset = Table(
    "asset",
    metadata,
    Column("id", Text, primary_key=True),
    Column("workspace_id", Text, ForeignKey("workspace.id"), nullable=False),
    Column("content_hash", Text, nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    _ts("taken_at"),
    Column("taken_at_offset_min", Integer),
    Column("date_source", Text),
    Column("width", Integer),
    Column("height", Integer),
    Column("thumbhash", Text),
    Column("phash", Text),
    Column("dhash", Text),
    Column("exif", JSONB, nullable=False, server_default="{}"),  # display summary (10 §3.5); untrusted data
    # wall-clock time the photo belongs to (capture time, else file mtime) and its month: the timeline axis (10 §2)
    Column("local_at", DateTime(timezone=False)),
    Column("month_at", DateTime(timezone=False), Computed("date_trunc('month', local_at)", persisted=True)),
    _ts("created_at", server_default=func.now()),
    UniqueConstraint("workspace_id", "content_hash"),
)
Index("asset_timeline", asset.c.workspace_id, asset.c.month_at, asset.c.local_at.desc())
asset_location = Table(
    "asset_location",
    metadata,
    Column("id", Text, primary_key=True),
    Column("asset_id", Text, ForeignKey("asset.id"), nullable=False),
    Column("source_id", Text, ForeignKey("source.id"), nullable=False),
    Column("rel_path", Text, nullable=False),
    _ts("mtime"),
    Column("mtime_offset_min", Integer),
    Column("size", BigInteger),
    Column("missing", Boolean, nullable=False, server_default="false"),
    _ts("last_seen_at", server_default=func.now()),
    UniqueConstraint("source_id", "rel_path"),
)
# near-duplicate groups from the `dedup` recluster (05 §2.2); exact duplicates are asset_location rows
duplicate_member = Table(
    "duplicate_member",
    metadata,
    Column("group_id", Text, nullable=False),
    Column("asset_id", Text, ForeignKey("asset.id", ondelete="CASCADE"), primary_key=True),
    Column("reason", Text, nullable=False, server_default="near"),
    Column("is_representative", Boolean, nullable=False, server_default="false"),
    _ts("computed_at", server_default=func.now()),
)
Index("duplicate_member_group", duplicate_member.c.group_id)
job = Table(
    "job",
    metadata,
    Column("id", Text, primary_key=True),
    Column("workspace_id", Text, ForeignKey("workspace.id"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("mode", Text, nullable=False, server_default="dry_run"),
    Column("params", JSONB, nullable=False),
    Column("counters", JSONB, nullable=False, server_default="{}"),
    Column("plan_id", Text),
    Column("error", Text),
    _ts("created_at", server_default=func.now()),
    _ts("finished_at"),
)
output_plan = Table(
    "output_plan",
    metadata,
    Column("id", Text, primary_key=True),
    Column("job_id", Text, ForeignKey("job.id"), nullable=False),
    Column("status", Text, nullable=False),
    Column("summary", JSONB, nullable=False),
    _ts("approved_at"),
)
output_action = Table(
    "output_action",
    metadata,
    Column("id", Text, primary_key=True),
    Column("plan_id", Text, ForeignKey("output_plan.id"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("asset_id", Text, ForeignKey("asset.id"), nullable=False),
    Column("action", Text, nullable=False),
    Column("src_alias", Text, nullable=False),
    Column("src_rel", Text, nullable=False),
    Column("dst_alias", Text, nullable=False),
    Column("dst_rel", Text, nullable=False),
    Column("src_hash", Text, nullable=False),
    Column("date_source", Text),
    Column("conflict", Text, nullable=False, server_default="none"),
    Column("payload", JSONB),  # sidecar content (sidecar_json / sidecar_csv)
    Column("status", Text, nullable=False, server_default="planned"),
    Column("error", Text),
)
job_item = Table(
    "job_item",
    metadata,
    Column("id", Text, primary_key=True),
    Column("job_id", Text, ForeignKey("job.id"), nullable=False),
    Column("asset_id", Text, ForeignKey("asset.id"), nullable=False),
    Column("root_alias", Text, nullable=False),
    Column("rel", Text, nullable=False),
    Column("task", Text, nullable=False, server_default="classify"),
    Column("lane", Text, nullable=False, server_default="normal"),
    Column("seq", Integer, nullable=False, server_default="0"),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("max_attempts", Integer, nullable=False, server_default="3"),
    Column("lease_owner", Text),
    Column("lease_boot", Text),  # the worker process that holds the lease (restart recovery, OP-006)
    _ts("lease_expires_at"),
    _ts("next_attempt_at"),
    Column("error_code", Text),
    Column("result_id", Text),
)
result = Table(
    "result",
    metadata,
    Column("id", Text, primary_key=True),
    Column("asset_id", Text, ForeignKey("asset.id"), nullable=False),
    Column("task_config_hash", Text, nullable=False),
    Column("model_fingerprint", Text, nullable=False),
    Column("job_item_id", Text),
    Column("task_type", Text, nullable=False),
    Column("category", Text),
    Column("confidence", Float),
    Column("caption", Text),
    Column("tags", JSONB),
    Column("raw", JSONB),
    _ts("created_at", server_default=func.now()),
    UniqueConstraint("asset_id", "task_config_hash", "model_fingerprint"),
)
review = Table(  # 02 §3.2 review; REV-*
    "review",
    metadata,
    Column("id", Text, primary_key=True),
    Column("asset_id", Text, ForeignKey("asset.id"), nullable=False),
    Column("job_id", Text),
    Column("result_id", Text),
    Column("status", Text, nullable=False, server_default="pending"),  # pending|approved|corrected|skipped
    Column("reason", Text),  # low_confidence | conflict | inline
    Column("suggested", JSONB),
    Column("corrected", JSONB),
    Column("source", Text),  # review | review_skip | inline
    _ts("created_at", server_default=func.now()),
    _ts("resolved_at"),
)
Index("review_job_status", review.c.job_id, review.c.status)


class Vector(UserDefinedType):  # type: ignore[type-arg]
    """pgvector ``vector`` (dimension-agnostic column; 05 §1.5 — halfvec + per-space HNSW where pgvector ≥ 0.7)."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "vector"


search_doc = Table(  # 05 §1.2: one row per asset, denormalised facets + application-tokenised tsvector
    "search_doc",
    metadata,
    Column("asset_id", Text, ForeignKey("asset.id"), primary_key=True),
    Column("workspace_id", Text, nullable=False, server_default="default"),
    Column("caption", Text),
    Column("tags", ARRAY(Text), nullable=False, server_default="{}"),  # canonical keys
    Column("tag_labels", JSONB),
    Column("category", Text),
    Column("taken_at", DateTime(timezone=True)),
    Column("taken_year", Integer),
    Column("date_source", Text),
    Column("scope_keys", ARRAY(Text), nullable=False, server_default="{}"),
    Column("person_ids", ARRAY(Text)),  # confirmed/auto persons (07 §3.3); NULL when faces are off
    Column("person_ids_suggested", ARRAY(Text)),
    Column("face_count", Integer),
    Column("review_state", Text),
    Column("confidence", Float),
    Column("body", Text, nullable=False, server_default=""),
    Column("tsv", TSVECTOR),
    _ts("first_indexed_at", server_default=func.now()),  # pagination snapshots (SRCH-FAC-004)
    _ts("updated_at", server_default=func.now()),
)
selection = Table(  # a query handle (10 §6): what "select all results" means, re-resolved on the server
    "selection",
    metadata,
    Column("id", Text, primary_key=True),
    Column("query", JSONB),
    Column("asset_ids", ARRAY(Text)),
    Column("count", Integer, nullable=False),
    _ts("created_at", server_default=func.now()),
)
Index("search_doc_tsv", search_doc.c.tsv, postgresql_using="gin")
Index("search_doc_tags", search_doc.c.tags, postgresql_using="gin")
Index("search_doc_scope", search_doc.c.scope_keys, postgresql_using="gin")
Index("search_doc_ws_taken", search_doc.c.workspace_id, search_doc.c.taken_at)

embedding_space = Table(  # 05 §1.2
    "embedding_space",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("model_key", Text, nullable=False),
    Column("fingerprint", Text, nullable=False),
    Column("dim", Integer, nullable=False),
    Column("modality", Text, nullable=False, server_default="image"),  # image | face (07 §3.2)
    Column("thresholds", JSONB),  # face spaces: auto/suggest/margin/merge/find_* (07 §2.6)
    Column("status", Text, nullable=False, server_default="active"),  # building | active | retired
    _ts("created_at", server_default=func.now()),
    UniqueConstraint("fingerprint"),
)

asset_embedding = Table(  # 05 §1.2
    "asset_embedding",
    metadata,
    Column("asset_id", Text, ForeignKey("asset.id"), primary_key=True),
    Column("space_id", Integer, ForeignKey("embedding_space.id"), primary_key=True),
    Column("vec", Vector(), nullable=False),
    Column("source_hash", Text),
    _ts("created_at", server_default=func.now()),
)

index_outbox = Table(  # 02 §10: asset changes → search_doc, processed in order
    "index_outbox",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("asset_id", Text, nullable=False),
    _ts("created_at", server_default=func.now()),
    _ts("processed_at"),
)

operation = Table(  # 06 §5.1 / 02 §10.1
    "operation",
    metadata,
    Column("id", Text, primary_key=True),
    Column("type", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("subject", JSONB),
    Column("parent_id", Text),
    Column("worker_id", Text),
    Column("waiting_reason", Text),
    Column("phases", JSONB, nullable=False, server_default="[]"),
    Column("progress", JSONB, nullable=False, server_default="{}"),
    Column("eta", JSONB),
    Column("result", JSONB),
    Column("error", JSONB),
    Column("seq", BigInteger, nullable=False, server_default="0"),
    Column("active_ms", BigInteger, nullable=False, server_default="0"),
    Column("paused_ms", BigInteger, nullable=False, server_default="0"),
    _ts("started_at"),
    _ts("finished_at"),
    _ts("heartbeat_at"),
    _ts("updated_at"),
    _ts("created_at", server_default=func.now()),
)
operation_event = Table(
    "operation_event",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("operation_id", Text, nullable=False),
    Column("seq", BigInteger, nullable=False),
    Column("kind", Text, nullable=False),
    Column("payload", JSONB, nullable=False, server_default="{}"),
    _ts("at"),
)
perf_observation = Table(  # 06 §5.5 / 02 §10.1
    "perf_observation",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("worker_id", Text),
    Column("variant", Text),
    Column("op_type", Text, nullable=False),
    Column("items_per_s", Float),
    Column("bytes_per_s", Float),
    Column("load_s", Float),
    Column("vram_gb", Float),  # measured after load (INF-VRAM-005)
    _ts("at"),
)

installation = Table(  # 06 §1.7 / 02 §10.1: an artifact or pack installed on a worker
    "installation",
    metadata,
    Column("id", Text, primary_key=True),
    Column("worker_id", Text, nullable=False),
    Column("kind", Text, nullable=False),  # model | pack
    Column("key", Text, nullable=False),  # model@variant | pack id
    Column("status", Text, nullable=False, server_default="installed"),
    Column("size_bytes", BigInteger),
    Column("files", JSONB),
    Column("managed", Boolean, nullable=False, server_default="true"),
    _ts("created_at", server_default=func.now()),
    UniqueConstraint("worker_id", "kind", "key"),
)

audit_log = Table(  # 06 §3.6: every auto_fix and privileged action (DOC-017)
    "audit_log",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    _ts("at", server_default=func.now()),
    Column("actor", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("target", Text),
    Column("outcome", Text),
    Column("details", JSONB),
)

license_acceptance = Table(  # 06 §1.9
    "license_acceptance",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("workspace_id", Text, nullable=False),
    Column("model_id", Text, nullable=False),
    Column("license_id", Text, nullable=False),
    Column("license_hash", Text, nullable=False),
    _ts("accepted_at", server_default=func.now()),
    _ts("revoked_at"),
)
eta_calibration = Table(
    "eta_calibration",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("operation_id", Text, nullable=False),
    Column("op_type", Text, nullable=False),
    Column("predicted_p50", Float, nullable=False),
    Column("predicted_p10", Float),
    Column("predicted_p90", Float),
    Column("actual_s", Float, nullable=False),
    Column("ratio", Float, nullable=False),
    Column("covered", Boolean),
    _ts("at"),
)
metric = Table(
    "metric",
    metadata,
    Column("name", Text, primary_key=True),
    Column("value", BigInteger, nullable=False, server_default="0"),
)
worker_task = Table(
    "worker_task",
    metadata,
    Column("id", Text, primary_key=True),
    Column("kind", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("worker_id", Text),
    Column("job_id", Text),
    Column("lane", Text, nullable=False, server_default="normal"),  # interactive tasks jump the queue (ORG-008)
    Column("lease_boot", Text),  # the worker process that holds the lease (restart recovery, OP-006)
    _ts("lease_expires_at"),
    Column("result", JSONB),
    _ts("created_at", server_default=func.now()),
)
journal_mirror = Table(  # CP mirror of the worker-local journal (02 §3.2 journal_entry); worker copy wins
    "journal_mirror",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("worker_id", Text, nullable=False),
    Column("plan_id", Text, nullable=False),
    Column("seq", BigInteger, nullable=False),
    Column("action_id", Text, nullable=False),
    Column("op", Text, nullable=False),
    Column("phase", Text, nullable=False),
    Column("args", JSONB, nullable=False),
    Column("pre_state", JSONB, nullable=False),
    Column("post_state", JSONB),
    UniqueConstraint("worker_id", "plan_id", "seq"),
)
event = Table(
    "event",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("kind", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    _ts("at", server_default=func.now()),
)

# ---- faces (07 §3.2; v0.2). Biometric vectors live only in the separate ``bio`` schema ----
person = Table(
    "person",
    metadata,
    Column("id", Text, primary_key=True),
    Column("workspace_id", Text, ForeignKey("workspace.id"), nullable=False),
    Column("display_name", Text),  # NULL = unnamed group
    Column("status", Text, nullable=False, server_default="unnamed"),  # unnamed | named | ignored | hidden
    Column("origin", Text, nullable=False),  # cluster | user | merge | split
    Column("is_frozen", Boolean, nullable=False, server_default="false"),  # any human action freezes it
    Column("age_variation", Boolean, nullable=False, server_default="false"),
    Column("cover_face_id", Text),
    Column("merged_into", Text, ForeignKey("person.id")),
    Column("created_by", Text, nullable=False),
    _ts("created_at", server_default=func.now()),
    _ts("updated_at", server_default=func.now()),
)
Index(
    "person_name_uq",
    person.c.workspace_id,
    func.lower(person.c.display_name),
    unique=True,
    postgresql_where=person.c.display_name.is_not(None) & person.c.merged_into.is_(None),
)
face = Table(
    "face",
    metadata,
    Column("id", Text, primary_key=True),
    Column("workspace_id", Text, ForeignKey("workspace.id"), nullable=False),
    Column("asset_id", Text, ForeignKey("asset.id", ondelete="CASCADE"), nullable=False),
    Column("space_id", Integer, ForeignKey("embedding_space.id")),
    Column("bbox", ARRAY(Float), nullable=False),  # normalised [x, y, w, h] after EXIF rotation
    Column("landmarks", ARRAY(Float)),
    Column("det_score", Float, nullable=False),
    Column("quality", JSONB, nullable=False),
    Column("quality_tier", Integer, nullable=False),  # 0 reject / 1 low / 2 ok / 3 good
    Column("source", Text, nullable=False, server_default="detected"),  # detected | reference | manual
    Column("person_id", Text, ForeignKey("person.id", ondelete="SET NULL")),
    Column("state", Text, nullable=False, server_default="unassigned"),  # unassigned|suggested|auto|confirmed|ignored
    Column("confidence", Float),
    _ts("created_at", server_default=func.now()),
)
Index("face_asset_idx", face.c.asset_id)
Index("face_person_idx", face.c.workspace_id, face.c.person_id)
face_assignment = Table(  # append-only history; current = superseded_at IS NULL (07 §3.2)
    "face_assignment",
    metadata,
    Column("id", Text, primary_key=True),
    Column("face_id", Text, ForeignKey("face.id", ondelete="CASCADE"), nullable=False),
    Column("person_id", Text, ForeignKey("person.id", ondelete="CASCADE"), nullable=False),
    Column("kind", Text, nullable=False),  # auto | suggested | confirmed | rejected (cannot-link)
    Column("confidence", Float),
    Column("margin", Float),
    Column("method", Text, nullable=False),  # online_knn | cluster | find | manual | merge | review | split
    Column("space_id", Integer),
    Column("actor", Text, nullable=False),
    Column("undo_of", Text),  # merge: the batch this row can be undone with
    Column("replaces", Text),  # the assignment this one superseded (undo restores it)
    _ts("created_at", server_default=func.now()),
    _ts("superseded_at"),
)
Index("fa_face_idx", face_assignment.c.face_id)
person_link = Table(
    "person_link",
    metadata,
    Column("workspace_id", Text, primary_key=True),
    Column("person_a", Text, primary_key=True),
    Column("person_b", Text, primary_key=True),
    Column("kind", Text, primary_key=True),  # merge_suggestion | confusable
    Column("score", Float),
    Column("status", Text, nullable=False, server_default="open"),
)
face_cluster_run = Table(
    "face_cluster_run",
    metadata,
    Column("id", Text, primary_key=True),
    Column("workspace_id", Text, nullable=False),
    Column("space_id", Integer),
    Column("algo", Text, nullable=False),
    Column("params", JSONB, nullable=False),
    Column("input_faces", Integer),
    Column("clusters", Integer),
    Column("noise", Integer),
    _ts("started_at"),
    _ts("finished_at"),
)
face_consent = Table(  # who agreed to what, when (07 §4.2); never biometric data
    "face_consent",
    metadata,
    Column("id", Text, primary_key=True),
    Column("workspace_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("action", Text, nullable=False),  # enable | disable | purge | scope_change
    Column("consent_text_version", Text, nullable=False),
    Column("source_ids", JSONB),
    Column("weights_license", Text),
    _ts("at", server_default=func.now()),
)
BIO_DDL = """
CREATE SCHEMA IF NOT EXISTS bio;
CREATE TABLE IF NOT EXISTS bio.face_embedding (
  workspace_id text NOT NULL, face_id text NOT NULL, space_id int NOT NULL, vec vector NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (workspace_id, face_id, space_id)
) PARTITION BY LIST (workspace_id);
CREATE TABLE IF NOT EXISTS bio.person_prototype (
  workspace_id text NOT NULL, person_id text NOT NULL, space_id int NOT NULL, k smallint NOT NULL,
  vec vector NOT NULL, member_count int, updated_at timestamptz,
  PRIMARY KEY (workspace_id, person_id, space_id, k)
) PARTITION BY LIST (workspace_id);
"""
sa_event.listen(face_consent, "after_create", DDL(BIO_DDL))  # type: ignore[no-untyped-call]


# ---- MCP (05 §3.2, §3.6–§3.8; v0.2) ----
mcp_token = Table(  # local PATs for `opagent mcp`; only the SHA-256 of the value is stored
    "mcp_token",
    metadata,
    Column("id", Text, primary_key=True),
    Column("seq", Integer, nullable=False),  # "grant #12" on the approval page
    Column("name", Text, nullable=False),
    Column("client", Text),
    Column("token_hash", Text, nullable=False, unique=True),
    Column("prefix", Text, nullable=False),
    Column("scopes", ARRAY(Text), nullable=False),
    Column("folder_scopes", JSONB, nullable=False, server_default="[]"),
    Column("media_policy", Text),
    Column("owner_user_id", Text),  # control plane: whose role caps the scopes (MU-RBAC-001); NULL on desktop
    _ts("created_at", server_default=func.now()),
    _ts("last_used_at"),
    _ts("revoked_at"),
)
mcp_call_log = Table(  # every MCP request; append-only (MCP-AUD-003)
    "mcp_call_log",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    _ts("at", server_default=func.now()),
    Column("token_id", Text),
    Column("client_name", Text),
    Column("client_version", Text),
    Column("transport", Text),
    Column("method", Text, nullable=False),
    Column("tool", Text),
    Column("args_redacted", JSONB),
    Column("args_hash", Text),
    Column("status", Text, nullable=False),
    Column("error_code", Text),
    Column("result_count", Integer),
    Column("media_count", Integer, nullable=False, server_default="0"),
    Column("media_bytes", BigInteger, nullable=False, server_default="0"),
    Column("plan_id", Text),
    Column("job_id", Text),
    Column("latency_ms", Integer),
    Column("gpu_ms", Integer, nullable=False, server_default="0"),  # VLM time spent for this call (MCP-RATE-002)
    Column("actor_kind", Text, nullable=False, server_default="mcp"),  # mcp | builtin-assistant (10 §10.3)
    Column("conversation_id", Text),
    Column("turn_id", Text),
)
approval = Table(  # an agent's request to apply/undo a plan; decided only in the app (05 §3.6)
    "approval",
    metadata,
    Column("id", Text, primary_key=True),
    Column("plan_id", Text, ForeignKey("output_plan.id"), nullable=False),
    Column("kind", Text, nullable=False),  # apply | undo
    Column("plan_hash", Text, nullable=False),
    Column("token_id", Text),
    Column("client_name", Text),
    Column("note", Text),
    Column("status", Text, nullable=False),  # pending | approved | rejected | expired | stale
    _ts("created_at", nullable=False),
    _ts("expires_at", nullable=False),
    _ts("decided_at"),
)
rate_bucket = Table(  # Postgres token buckets for MCP rate limits (05 §3.7)
    "rate_bucket",
    metadata,
    Column("token_id", Text, primary_key=True),
    Column("kind", Text, primary_key=True),
    Column("tokens", Float, nullable=False),
    Column("at", Float, nullable=False),
)
_APPEND_ONLY = DDL(  # type: ignore[no-untyped-call]
    "CREATE OR REPLACE FUNCTION opagent_append_only() RETURNS trigger LANGUAGE plpgsql AS "
    "$$ BEGIN RAISE EXCEPTION '%% is append-only', TG_TABLE_NAME; END $$;"
    "CREATE TRIGGER mcp_call_log_append_only BEFORE UPDATE OR DELETE ON mcp_call_log "
    "FOR EACH ROW EXECUTE FUNCTION opagent_append_only();"
)
sa_event.listen(mcp_call_log, "after_create", _APPEND_ONLY)


# ---- 「AI 問照片」 (10 §10.3; v0.3) ----
conversation = Table(
    "conversation",
    metadata,
    Column("id", Text, primary_key=True),
    Column("workspace_id", Text, nullable=False),
    Column("title", Text),
    Column("state", JSONB, nullable=False, server_default="{}"),  # working set: filters, selection, photos
    Column("mode", Text, nullable=False, server_default="local"),
    _ts("created_at", server_default=func.now()),
    _ts("updated_at", server_default=func.now()),
    _ts("expires_at"),
)
message = Table(
    "message",
    metadata,
    Column("id", Text, primary_key=True),
    Column("conversation_id", Text, ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False),
    Column("turn_id", Text, nullable=False),
    Column("role", Text, nullable=False),  # user | assistant
    Column("content", Text, nullable=False, server_default=""),
    Column("intent", Text),
    Column("status", Text, nullable=False, server_default="completed"),
    Column("timings", JSONB),
    Column("feedback", Text),
    _ts("created_at", server_default=func.now()),
)
message_step = Table(
    "message_step",
    metadata,
    Column("id", Text, primary_key=True),
    Column("message_id", Text, ForeignKey("message.id", ondelete="CASCADE"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("kind", Text, nullable=False),
    Column("tool", Text),
    Column("status", Text, nullable=False),
    Column("elapsed_ms", Integer),
)
message_citation = Table(
    "message_citation",
    metadata,
    Column("message_id", Text, ForeignKey("message.id", ondelete="CASCADE"), primary_key=True),
    Column("kind", Text, primary_key=True),
    Column("ref_id", Text, primary_key=True),
    Column("ordinal", Integer, nullable=False),
)
assistant_egress_log = Table(  # what left the machine in cloud mode (10 §8.9; ASK-PRV-004)
    "assistant_egress_log",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    _ts("at", server_default=func.now()),
    Column("turn_id", Text),
    Column("endpoint", Text, nullable=False),
    Column("bytes_out", Integer, nullable=False),
    Column("images", Integer, nullable=False, server_default="0"),
)
collection = Table(  # albums are database sets, never files (05 §3.3.5)
    "collection",
    metadata,
    Column("id", Text, primary_key=True),
    Column("workspace_id", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("description", Text),
    _ts("created_at", server_default=func.now()),
)
collection_member = Table(
    "collection_member",
    metadata,
    Column("collection_id", Text, ForeignKey("collection.id", ondelete="CASCADE"), primary_key=True),
    Column("asset_id", Text, ForeignKey("asset.id", ondelete="CASCADE"), primary_key=True),
    _ts("added_at", server_default=func.now()),
)


# 05 §5: automations. v0.3 has one kind — a watch folder per source (debounce, polling, an optional organize rule
# that only ever produces a plan unless the policy allows non-destructive auto-apply; move always needs a person).
automation = Table(
    "automation",
    metadata,
    Column("id", Text, primary_key=True),
    Column("workspace_id", Text, nullable=False),
    Column("source_id", Text, ForeignKey("source.id", ondelete="CASCADE"), unique=True),
    Column("name", Text, nullable=False),
    Column("enabled", Boolean, nullable=False, server_default="false"),
    Column("trigger", JSONB, nullable=False),  # {kind: watch, debounce_s, stable_s, poll_interval_s}
    Column("steps", JSONB, nullable=False, server_default="[]"),  # [{run_pipeline}, {organize: JobIn}]
    Column("approval_policy", Text, nullable=False, server_default="always_manual"),
    Column("state", JSONB, nullable=False, server_default="{}"),  # what the worker reported: mode, fstype
    _ts("created_at", server_default=func.now()),
    _ts("updated_at", server_default=func.now()),
)


# 03 §6: mini benchmark. A golden set is the user's own labelled sample; a benchmark compares candidates on it.
golden_set = Table(
    "golden_set",
    metadata,
    Column("id", Text, primary_key=True),
    Column("workspace_id", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("classes", JSONB, nullable=False),  # category keys
    Column("items", JSONB, nullable=False),  # {asset_id: class key}
    _ts("created_at", server_default=func.now()),
)
benchmark = Table(
    "benchmark",
    metadata,
    Column("id", Text, primary_key=True),
    Column("workspace_id", Text, nullable=False),
    Column("golden_set_id", Text, ForeignKey("golden_set.id", ondelete="CASCADE"), nullable=False),
    Column("status", Text, nullable=False, server_default="queued"),
    Column("candidates", JSONB, nullable=False),
    Column("constraints", JSONB, nullable=False, server_default="{}"),
    Column("raw", JSONB),  # per-candidate predictions and timings from the worker (no images)
    _ts("created_at", server_default=func.now()),
    _ts("finished_at"),
)


# 04 §2.2: organize presets the user imported (built-ins stay files in presets/builtin). ``doc`` is the validated
# schema_version-2 document, unknown fields included, so exporting it again loses nothing.
preset_table = Table(
    "preset",
    metadata,
    Column("key", Text, primary_key=True),
    Column("workspace_id", Text, nullable=False),
    Column("doc", JSONB, nullable=False),
    Column("migrated_from", Integer),
    _ts("created_at", server_default=func.now()),
)


# invariant 10: the user's confirmation to run a model's own code, bound to the exact files (path → sha256)
remote_code_consent = Table(
    "remote_code_consent",
    metadata,
    Column("model_key", Text, primary_key=True),
    Column("files", JSONB, nullable=False),
    Column("confirmed_by", Text, nullable=False),
    _ts("confirmed_at", nullable=False),
)


# 02 §8: write-back to other apps (Immich). Same plan → approve → journal → apply/undo discipline as file actions.
integration_plan = Table(
    "integration_plan",
    metadata,
    Column("id", Text, primary_key=True),
    Column("workspace_id", Text, nullable=False),
    Column("kind", Text, nullable=False),  # immich
    Column("status", Text, nullable=False),  # hashing | draft | applied | undone | failed
    Column("spec", JSONB, nullable=False),
    Column("body", JSONB),  # actions, skipped, summary
    _ts("created_at", server_default=func.now()),
)
integration_journal = Table(
    "integration_journal",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("plan_id", Text, ForeignKey("integration_plan.id", ondelete="CASCADE"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("op", Text, nullable=False),
    Column("params", JSONB, nullable=False),
    Column("state", Text, nullable=False),  # intent | done | undone
    _ts("at", nullable=False),
)


# v0.5 self-hosted control plane (02 §1.3, §5.2, §6.3): people, roles, sessions, and paired remote workers.
app_user = Table(
    "app_user",
    metadata,
    Column("id", Text, primary_key=True),
    Column("email", Text, nullable=False, unique=True),
    Column("password_hash", Text),  # argon2id; NULL for OIDC-only people
    Column("oidc_sub", Text, unique=True),
    Column("disabled", Boolean, nullable=False, server_default="false"),
    _ts("created_at", server_default=func.now()),
)
membership = Table(
    "membership",
    metadata,
    Column("user_id", Text, ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True),
    Column("workspace_id", Text, primary_key=True),
    Column("role", Text, nullable=False),  # org_owner | org_admin | ws_admin | ws_editor | ws_reviewer | ws_viewer
)
user_session = Table(
    "user_session",
    metadata,
    Column("id_hash", Text, primary_key=True),  # SHA-256 of the cookie value
    Column("user_id", Text, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False),
    Column("csrf", Text, nullable=False),
    _ts("expires_at", nullable=False),
)
enrollment_code = Table(
    "enrollment_code",
    metadata,
    Column("code_hash", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("created_by", Text),
    Column("workspace_id", Text, nullable=False, server_default="default"),  # the worker serves this workspace
    _ts("expires_at", nullable=False),
    _ts("used_at"),
)
worker_credential = Table(
    "worker_credential",
    metadata,
    Column("worker_id", Text, primary_key=True),
    Column("public_key", Text, nullable=False),  # ed25519, raw, base64
    Column("refresh_hash", Text, nullable=False),
    Column("workspace_id", Text, nullable=False, server_default="default"),
    _ts("created_at", server_default=func.now()),
    _ts("revoked_at"),
)
worker_nonce = Table(
    "worker_nonce",
    metadata,
    Column("worker_id", Text, primary_key=True),
    Column("nonce", Text, primary_key=True),
    _ts("at", nullable=False),
)

# v0.5 MCP over HTTP: the built-in OAuth 2.1 authorization server (ADR-0015). Control database only.
oauth_client = Table(  # Client ID Metadata Documents fetched from the client_id URL (SSRF-hardened)
    "oauth_client",
    metadata,
    Column("client_id", Text, primary_key=True),
    Column("metadata", JSONB, nullable=False),
    _ts("fetched_at", nullable=False),
)
oauth_grant = Table(  # a person's consent for one client in one workspace; revoking it ends every token at once
    "oauth_grant",
    metadata,
    Column("id", Text, primary_key=True),
    Column("user_id", Text, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False),
    Column("client_id", Text, nullable=False),
    Column("workspace_id", Text, nullable=False),
    Column("scopes", ARRAY(Text), nullable=False),
    Column("resource", Text, nullable=False),
    _ts("created_at", nullable=False),
    _ts("revoked_at"),
    Column("revoked_reason", Text),
)
oauth_code = Table(
    "oauth_code",
    metadata,
    Column("code_hash", Text, primary_key=True),
    Column("grant_id", Text, ForeignKey("oauth_grant.id", ondelete="CASCADE"), nullable=False),
    Column("redirect_uri", Text, nullable=False),
    Column("code_challenge", Text, nullable=False),
    Column("code_challenge_method", Text, nullable=False),
    Column("nonce", Text),
    _ts("expires_at", nullable=False),
    _ts("used_at"),
)
oauth_refresh = Table(  # rotated on every use; presenting a used one revokes the whole grant (reuse detection)
    "oauth_refresh",
    metadata,
    Column("token_hash", Text, primary_key=True),
    Column("grant_id", Text, ForeignKey("oauth_grant.id", ondelete="CASCADE"), nullable=False),
    _ts("created_at", nullable=False),
    _ts("used_at"),
)


def engine_for(dsn: str) -> Engine:
    url = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, pool_pre_ping=True, future=True)


def create_all(dsn: str) -> None:
    from sqlalchemy import text

    eng = engine_for(dsn)
    with eng.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    metadata.create_all(eng)
    eng.dispose()
