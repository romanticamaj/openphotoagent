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
        """Lease one batch of job items, run inference (halving the batch on OOM), report results (02 §4.3)."""
        r = self.client.post("/worker-api/v1/leases", json={
            "worker_id": self.worker_id, "max_items": self.batch_size,
            "loaded_models": [t for t in getattr(self, "loaded_tasks", [])]})
        if r.status_code == 204:
            return 0
        r.raise_for_status()
        items = r.json()["items"]
        reports = self._infer(items)
        self.client.post("/worker-api/v1/results", json={"worker_id": self.worker_id, "items": reports})
        return len(items)

    def _prepare(self, it: dict[str, Any]) -> tuple[Any, dict[str, Any] | None]:
        from opagent_inference.types import ImageInput

        from .imaging import open_image, oriented
        from .scanner import content_hash

        try:
            sp = self.sb.resolve(it["root_alias"], it["rel"])
            side = self.backend.describe().preferred_input.max_side if self.backend else 512
            with open_image(sp.real) as im:
                im.load()
                pic = oriented(im).convert("RGB")
                pic.thumbnail((side, side))
                import io

                buf = io.BytesIO()
                pic.save(buf, "JPEG", quality=90)
            return ImageInput(item_id=it["id"], content_hash=content_hash(sp.real), data=buf.getvalue()), None
        except Exception:  # noqa: BLE001 - unreadable source: permanent failure for this item
            return None, self._fail(it, "DECODE_FAILED", retryable=False)

    def _fail(self, it: dict[str, Any], code: str, retryable: bool) -> dict[str, Any]:
        return {"job_item_id": it["id"], "attempt": it["attempt"], "status": "failed", "error_code": code,
                "retryable": retryable, "idempotency_key": f"{it['id']}:{it['attempt']}"}

    def _infer(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        import asyncio

        from opagent_inference.types import BatchRequest, InferenceError, LoadOptions

        reports: list[dict[str, Any]] = []
        prepared = []
        for it in items:
            inp, failure = self._prepare(it)
            if failure:
                reports.append(failure)
            else:
                prepared.append((it, inp))
        if not prepared:
            return reports
        backend = self.backend
        asyncio.run(backend.load(LoadOptions()))
        fp = backend.describe().model_fingerprint
        task = prepared[0][0]["task"]

        def run(chunk: list[tuple[dict[str, Any], Any]]) -> None:
            try:
                outs = asyncio.run(backend.run_batch(BatchRequest(task_type=task, items=[i for _, i in chunk])))
            except InferenceError as e:
                if e.code == "OOM" and len(chunk) > 1:  # halve and retry (02 §4.3, Q-007)
                    half = len(chunk) // 2
                    self.batch_size = max(1, half)
                    run(chunk[:half])
                    run(chunk[half:])
                    return
                for it, _ in chunk:
                    reports.append(self._fail(it, e.error_code, e.retryable))
                return
            by_id = {o.item_id: o for o in outs}
            for it, _ in chunk:
                o = by_id[it["id"]]
                reports.append({"job_item_id": it["id"], "attempt": it["attempt"], "status": "ok",
                                "idempotency_key": f"{it['id']}:{it['attempt']}", "model_fingerprint": fp,
                                "provenance": {"batch_size": len(chunk), "adapter": backend.describe().adapter_key},
                                "output": {"category": o.category, "confidence": o.confidence, "caption": o.caption,
                                           "tags": [t for t, _ in (o.tags or [])]}})

        run(prepared)
        return reports

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
