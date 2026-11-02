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
_APPEND_ONLY = DDL(
    "CREATE OR REPLACE FUNCTION opagent_append_only() RETURNS trigger LANGUAGE plpgsql AS "
    "$$ BEGIN RAISE EXCEPTION '%% is append-only', TG_TABLE_NAME; END $$;"
    "CREATE TRIGGER mcp_call_log_append_only BEFORE UPDATE OR DELETE ON mcp_call_log "
    "FOR EACH ROW EXECUTE FUNCTION opagent_append_only();"
)
sa_event.listen(mcp_call_log, "after_create", _APPEND_ONLY)


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
