"""Worker agent: leases tasks from the API over the Worker protocol and executes them (docs/02 §5.3).

Only the worker touches files (invariant 1); every path goes through SafePath (invariant 2) and every file
change through the journaled executor (invariant 3).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from opagent_core.clock import SystemClock
from opagent_fs.executor import Action, Executor
from opagent_fs.fsops import RealFs
from opagent_fs.journal import Journal
from opagent_fs.safepath import RootConfig, Sandbox

from .scanner import content_hash, list_files, read_meta


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
        alias = p["root_alias"]
        rels = list_files(self.sb, alias, p.get("sub_path", ""), p["include"], p["exclude"])
        entries = []
        for rel in rels:
            sp = self.sb.resolve(alias, rel)
            st = os.stat(sp.real)
            mtime = datetime.fromtimestamp(st.st_mtime, tz=self.tz)
            meta = read_meta(sp.real, mtime, self.tz)  # type: ignore[arg-type]
            entries.append(
                {
                    "rel": sp.rel,
                    "size": st.st_size,
                    "mtime": mtime.isoformat(),
                    "content_hash": content_hash(sp.real),
                    "taken_at": meta.taken_at.isoformat() if meta.taken_at and meta.date_source != "mtime" else None,
                    "date_source": meta.date_source,
                }
            )
        return {"entries": entries}

    def _do_apply(self, p: dict[str, Any]) -> dict[str, Any]:
        actions = [Action(**a) for a in p["actions"]]
        rep = self.executor.apply(p["plan_id"], actions, move_confirmed=bool(p.get("move_confirmed")))
        return {"results": [r.__dict__ for r in rep.results], "status": rep.status,
                "waiting_reason": rep.waiting_reason}

    def _do_undo(self, p: dict[str, Any]) -> dict[str, Any]:
        res = self.executor.undo(p["plan_id"])
        return {"results": [r.__dict__ for r in res]}

    def _do_browse(self, p: dict[str, Any]) -> dict[str, Any]:
        sp = self.sb.resolve(p["root_alias"], p.get("path", ""))
        dirs = sorted(
            e.name for e in os.scandir(sp.real) if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")
        )
        return {"dirs": dirs}
