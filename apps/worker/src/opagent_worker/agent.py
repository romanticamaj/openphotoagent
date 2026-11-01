"""Worker agent: leases tasks from the API over the Worker protocol and executes them (docs/02 §5.3).

Only the worker touches files (invariant 1); every path goes through SafePath (invariant 2) and every file
change through the journaled executor (invariant 3).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from opagent_core.clock import SystemClock
from opagent_fs.executor import Action, Executor
from opagent_fs.fsops import RealFs
from opagent_fs.journal import Journal
from opagent_fs.safepath import RootConfig, Sandbox

from .scanner import HashCache, scan


class WorkerAgent:
    def __init__(
        self,
        client: Any,
        roots: list[dict[str, str]],
        home: Path,
        backend: Any = None,
        worker_id: str | None = None,
        name: str = "local",
    ) -> None:
        self.client = client
        self.roots = [RootConfig(alias=r["alias"], path=r["path"], mode=r.get("mode", "ro")) for r in roots]  # type: ignore[arg-type]
        self.sb = Sandbox(self.roots)
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.backend = backend
        self.worker_id = worker_id or self._load_id()
        self.name = name
        self.executor = Executor(self.sb, RealFs(), Journal(self.home / "journal.db"))
        self.tz = SystemClock().now().astimezone().tzinfo
        self.cache = HashCache(self.home / "scan-cache.db")

    def _load_id(self) -> str:
        f = self.home / "worker_id"
        if f.exists():
            return f.read_text().strip()
        wid = "w-" + uuid.uuid4().hex[:12]
        f.write_text(wid)
        return wid

    def register(self) -> None:
        r = self.client.post(
            "/worker-api/v1/register",
            json={
                "worker_id": self.worker_id,
                "name": self.name,
                "roots": [{"alias": r.alias, "mode": r.mode} for r in self.roots],
            },
        )
        r.raise_for_status()

    def run_once(self, wait_s: float = 0.0) -> bool:
        r = self.client.post("/worker-api/v1/lease", json={"worker_id": self.worker_id, "wait_s": wait_s})
        if r.status_code == 204:
            return False
        r.raise_for_status()
        task = r.json()
        try:
            result = getattr(self, f"_do_{task['kind']}")(task["payload"])
            ok = True
        except Exception as e:
            result, ok = {"error": type(e).__name__, "detail": str(e)[:500]}, False
        self.client.post(f"/worker-api/v1/tasks/{task['id']}/complete", json={"result": result, "ok": ok})
        return True

    batch_size = 16

    def process_items_once(self) -> int:
        return 0

    def run_until_idle(self) -> None:
        while self.run_once():
            pass

    def run_forever(self, stop: Any) -> None:
        while not stop.is_set():
            try:
                self.run_once(wait_s=1.0)
            except Exception:
                stop.wait(1.0)

    # ---- task handlers -------------------------------------------------
    def _do_scan(self, p: dict[str, Any]) -> dict[str, Any]:
        res = scan(
            self.sb,
            p["root_alias"],
            p.get("sub_path", ""),
            p["include"],
            p["exclude"],
            tz=self.tz,  # type: ignore[arg-type]
            cache=self.cache,
            exclude_subtrees=p.get("exclude_subtrees") or [],
        )
        entries = [
            {
                "rel": e.rel,
                "size": e.size,
                "mtime": e.mtime.isoformat(),
                "content_hash": e.content_hash,
                "taken_at": e.taken_at.isoformat() if e.taken_at and e.date_source != "mtime" else None,
                "date_source": e.date_source,
                **e.extra,
            }
            for e in res.entries
        ]
        return {
            "entries": entries,
            "skipped": [{"rel": s.rel, "reason": s.reason} for s in res.skipped],
            "bytes_read": res.bytes_read,
        }

    def _do_apply(self, p: dict[str, Any]) -> dict[str, Any]:
        actions = [Action(**a) for a in p["actions"]]
        rep = self.executor.apply(p["plan_id"], actions, move_confirmed=bool(p.get("move_confirmed")))
        return {
            "results": [r.__dict__ for r in rep.results],
            "status": rep.status,
            "waiting_reason": rep.waiting_reason,
        }

    def _do_undo(self, p: dict[str, Any]) -> dict[str, Any]:
        res = self.executor.undo(p["plan_id"])
        return {"results": [r.__dict__ for r in res]}

    def _do_browse(self, p: dict[str, Any]) -> dict[str, Any]:
        sp = self.sb.resolve(p["root_alias"], p.get("path", ""))
        dirs = sorted(
            e.name for e in os.scandir(sp.real) if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")
        )
        return {"dirs": dirs}
