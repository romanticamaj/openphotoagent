"""PostgreSQL schema (docs/02 §3.2, §10.1). Single DB backend (ADR-008). Alembic migrations arrive with PG upgrades."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

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
    _ts("created_at", server_default=func.now()),
    UniqueConstraint("workspace_id", "content_hash"),
)
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
    Column("status", Text, nullable=False, server_default="planned"),
    Column("error", Text),
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
    _ts("lease_expires_at"),
    Column("result", JSONB),
    _ts("created_at", server_default=func.now()),
)
journal_mirror = Table(  # CP mirror of the worker-local journal (02 §3.2 journal_entry); worker copy wins
    "journal_mirror", metadata,
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


def engine_for(dsn: str) -> Engine:
    url = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url, pool_pre_ping=True, future=True)


def create_all(dsn: str) -> None:
    eng = engine_for(dsn)
    metadata.create_all(eng)
    eng.dispose()
