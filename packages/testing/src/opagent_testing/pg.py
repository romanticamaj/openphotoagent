"""embedded_pg: session-level initdb + per-test template clone (docs/09 §2.3, PG-013)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class PgCluster:
    host: str
    port: int
    user: str
    template: str

    def dsn(self, db: str) -> str:
        return f"postgresql://{self.user}@{self.host}:{self.port}/{db}"


@dataclass
class ClonedDb:
    dsn: str
    name: str
    clone_ms: float


def start_cluster(base_dir: str, init_schema: Callable[[str], None] | None = None) -> PgCluster:
    return PgCluster(host="", port=0, user="", template="")


def clone(cluster: PgCluster) -> ClonedDb:
    return ClonedDb(dsn="", name="", clone_ms=float("inf"))


def drop(cluster: PgCluster, db: ClonedDb) -> None:
    pass


def stop_cluster(cluster: PgCluster) -> None:
    pass
