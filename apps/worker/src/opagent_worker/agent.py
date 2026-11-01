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
from opagent_fs.safepath import RootConfig, RootUnavailable, Sandbox

from .scanner import HashCache, scan


class _Waiting(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class WorkerAgent:
    def __init__(
        self,
        client: Any,
        roots: list[dict[str, str]],
        home: Path,
        backend: Any = None,
        embedder: Any = None,
        worker_id: str | None = None,
        hw_profile: str | None = None,
        models_root: Path | None = None,
        download_sources: list[str] | None = None,
        name: str = "local",
        clock: Any = None,
    ) -> None:
        self.client = client
        self.roots = [RootConfig(alias=r["alias"], path=r["path"], mode=r.get("mode", "ro")) for r in roots]  # type: ignore[arg-type]
        self.sb = Sandbox(self.roots)
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.backend = backend
        self.hw_profile = hw_profile
        self.doctor_env: dict[str, Any] = {}  # raw-fact overrides for the doctor (tests / FakeWorkerLink)
        self.models_root = Path(models_root) if models_root else self.home / "models"
        self.download_sources = download_sources or ["https://huggingface.co"]
        caps = backend.describe().task_types if backend is not None else ()
        self.embedder = embedder if embedder is not None else (backend if "embed" in caps else None)
        self._cascade: Any = None
        self._facts: dict[str, dict[str, Any]] = {}
        self.worker_id = worker_id or self._load_id()
        self.name = name
        self.executor = Executor(self.sb, RealFs(), Journal(self.home / "journal.db"))
        self.tz = SystemClock().now().astimezone().tzinfo
        self.cache = HashCache(self.home / "scan-cache.db")
        self.clock = clock or SystemClock()
        self.waiting: dict[str, tuple[dict[str, Any], float]] = {}
        self.page_size = 1000
        self.crash_after_pages: int | None = None  # test hook: simulate kill -9 after N reported pages
        self.last_scan_skipped_before_cursor = 0

    @property
    def cascade_fingerprint(self) -> str:
        """Cache-key fingerprint of the whole cascade (invariant 7): embedder + VLM fingerprints."""
        import hashlib

        parts = [self.embedder.describe().model_fingerprint if self.embedder else "-"]
        parts.append(self.backend.describe().model_fingerprint if self.backend else "-")
        return "cascade-" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    def doctor_report(self) -> dict[str, Any]:
        """Quick doctor checks on this machine (06 §3); results feed preflight via the control plane."""
        import asyncio

        from opagent_doctor.checks import Ctx, run_checks, to_preflight
        from opagent_doctor.probes import collect_env, collect_probes, read_profile

        probes = read_profile(self.hw_profile)["probes"] if self.hw_profile else collect_probes()
        roots = [{"alias": r.alias, "path": str(r.path), "mode": r.mode} for r in self.roots]
        env = {**collect_env(roots, str(self.models_root)), **self.doctor_env}
        results = asyncio.run(run_checks(Ctx(probes=probes, env=env, clock=self.clock), group="quick"))
        return {"doctor": to_preflight(results), "doctor_results": [r.to_dict() for r in results]}

    def hw_summary(self) -> dict[str, Any]:
        """Doctor's hardware summary: a recorded/synthesized profile in tests, live probes otherwise."""
        from dataclasses import asdict

        from opagent_doctor.probes import collect_probes, load_profile, summarize

        hw = load_profile(self.hw_profile) if self.hw_profile else summarize(collect_probes())
        return asdict(hw)

    def _load_id(self) -> str:
        f = self.home / "worker_id"
        if f.exists():
            return f.read_text().strip()
        wid = "w-" + uuid.uuid4().hex[:12]
        f.write_text(wid)
        return wid

    def register(self) -> None:
        models = {}
        if self.backend is not None:
            info = self.backend.describe()
            models = {t: info.model_fingerprint for t in info.task_types}
            if self.embedder is not None:
                models["embed"] = self.embedder.describe().model_fingerprint
                models["classify"] = self.cascade_fingerprint
        r = self.client.post(
            "/worker-api/v1/register",
            json={
                "worker_id": self.worker_id,
                "name": self.name,
                "models": models,
                "hw": self.hw_summary(),
                **self.doctor_report(),
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
        self._run_task(task)
        return True

    def _run_task(self, task: dict[str, Any]) -> None:
        try:
            result = getattr(self, f"_do_{task['kind']}")(task["payload"], task["id"])
            ok = True
        except _Waiting as w:  # e.g. SOURCE_OFFLINE: keep the task, retry automatically (SCAN-020)
            self.client.post(f"/worker-api/v1/tasks/{task['id']}/progress", json={"waiting_reason": w.reason})
            self.waiting[task["id"]] = (task, self.clock.monotonic())
            return
        except Exception as e:
            result, ok = {"error": type(e).__name__, "detail": str(e)[:500]}, False
        self.waiting.pop(task["id"], None)
        self.client.post(f"/worker-api/v1/tasks/{task['id']}/complete", json={"result": result, "ok": ok})

    RETRY_WAITING_S = 5.0

    def tick_waiting(self) -> None:
        for tid, (task, since) in list(self.waiting.items()):
            if self.clock.monotonic() - since >= self.RETRY_WAITING_S:
                self.waiting[tid] = (task, self.clock.monotonic())
                self._run_task(task)

    batch_size = 16

    def process_items_once(self) -> int:
        """Lease one batch of job items, run inference (halving the batch on OOM), report results (02 §4.3)."""
        r = self.client.post(
            "/worker-api/v1/leases",
            json={
                "worker_id": self.worker_id,
                "max_items": self.batch_size,
                "loaded_models": [t for t in getattr(self, "loaded_tasks", [])],
            },
        )
        if r.status_code == 204:
            return 0
        r.raise_for_status()
        items = r.json()["items"]
        reports = self._infer(items)
        from opagent_fs.crash import crash_point

        crash_point("worker_after_infer")  # chaos: die after inference, before reporting (Q-022)
        self._send_results(reports)
        return len(items)

    # ---- offline tolerance: results go to a local outbox when the CP is unreachable (02 §4.3, Q-013) ----
    def _outbox(self) -> Any:
        import sqlite3

        db = sqlite3.connect(self.home / "outbox.db")
        db.execute("CREATE TABLE IF NOT EXISTS outbox(k TEXT PRIMARY KEY, body TEXT NOT NULL)")
        return db

    def _send_results(self, reports: list[dict[str, Any]]) -> None:
        import json

        import httpx

        try:
            r = self.client.post("/worker-api/v1/results", json={"worker_id": self.worker_id, "items": reports})
            r.raise_for_status()
        except httpx.HTTPError:
            db = self._outbox()
            with db:
                db.executemany(
                    "INSERT OR REPLACE INTO outbox(k, body) VALUES (?, ?)",
                    [(x["idempotency_key"], json.dumps(x)) for x in reports],
                )
            db.close()

    def outbox_size(self) -> int:
        db = self._outbox()
        n = int(db.execute("SELECT count(*) FROM outbox").fetchone()[0])
        db.close()
        return n

    def flush_outbox(self) -> int:
        import json

        db = self._outbox()
        rows = db.execute("SELECT k, body FROM outbox").fetchall()
        if not rows:
            db.close()
            return 0
        r = self.client.post(
            "/worker-api/v1/results", json={"worker_id": self.worker_id, "items": [json.loads(b) for _, b in rows]}
        )
        r.raise_for_status()
        with db:
            db.executemany("DELETE FROM outbox WHERE k=?", [(k,) for k, _ in rows])
        db.close()
        return len(rows)

    def _prepare(self, it: dict[str, Any]) -> tuple[Any, dict[str, Any] | None]:
        from opagent_inference.types import ImageInput

        from .imaging import open_image, oriented
        from .scanner import content_hash

        try:
            sp = self.sb.resolve(it["root_alias"], it["rel"])
            side = self.backend.describe().preferred_input.max_side if self.backend else 512
            with open_image(sp.real) as im:
                im.load()
                from .imaging import exif_facts

                self._facts[it["id"]] = exif_facts(im, it["rel"])
                pic = oriented(im).convert("RGB")
                pic.thumbnail((side, side))
                import io

                buf = io.BytesIO()
                pic.save(buf, "JPEG", quality=90)
            return ImageInput(item_id=it["id"], content_hash=content_hash(sp.real), data=buf.getvalue()), None
        except Exception:
            return None, self._fail(it, "DECODE_FAILED", retryable=False)

    def _fail(self, it: dict[str, Any], code: str, retryable: bool) -> dict[str, Any]:
        return {
            "job_item_id": it["id"],
            "attempt": it["attempt"],
            "status": "failed",
            "error_code": code,
            "retryable": retryable,
            "idempotency_key": f"{it['id']}:{it['attempt']}",
        }

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
        tc0 = prepared[0][0].get("task_config") or {}
        if tc0.get("strategy") == "cascade" and self.embedder is not None:
            return reports + self._infer_cascade(prepared, tc0)
        backend = self.backend
        asyncio.run(backend.load(LoadOptions()))
        fp = backend.describe().model_fingerprint
        task = prepared[0][0]["task"]

        def run(chunk: list[tuple[dict[str, Any], Any]]) -> None:
            try:
                tc = chunk[0][0].get("task_config") or {}
                outs = asyncio.run(
                    backend.run_batch(
                        BatchRequest(
                            task_type=task,
                            items=[i for _, i in chunk],
                            variables={"categories": tc.get("categories") or []},
                        )
                    )
                )
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
                reports.append(
                    {
                        "job_item_id": it["id"],
                        "attempt": it["attempt"],
                        "status": "ok",
                        "idempotency_key": f"{it['id']}:{it['attempt']}",
                        "model_fingerprint": fp,
                        "provenance": {"batch_size": len(chunk), "adapter": backend.describe().adapter_key},
                        "output": {
                            "category": o.category,
                            "confidence": o.confidence,
                            "caption": o.caption,
                            "tags": [t for t, _ in (o.tags or [])],
                        },
                    }
                )

        run(prepared)
        return reports

    def _infer_cascade(self, prepared: list[tuple[dict[str, Any], Any]], tc: dict[str, Any]) -> list[dict[str, Any]]:
        """By-content organize: rules → embedding → VLM → decision (03 §3.3). Stages are reported so the control
        plane can re-apply thresholds without inference (ADR-0001)."""
        import asyncio

        from opagent_inference.cascade import Cascade, Item

        if self._cascade is None:
            self._cascade = Cascade(self.embedder, self.backend)
        items = [Item(inp, self._facts.pop(it["id"], {})) for it, inp in prepared]
        results = asyncio.run(self._cascade.run(items, _taxonomy(tc)))
        fp = self.cascade_fingerprint
        reports = []
        for (it, _), r in zip(prepared, results, strict=True):
            o = r.outcome
            reports.append(
                {
                    "job_item_id": it["id"],
                    "attempt": it["attempt"],
                    "status": "ok",
                    "idempotency_key": f"{it['id']}:{it['attempt']}",
                    "model_fingerprint": fp,
                    "provenance": {"stages": r.provenance, "adapter": "cascade", "error": r.error},
                    "output": {
                        "category": o.category or "other",
                        "confidence": o.confidence,
                        "caption": r.caption,
                        "tags": r.tags or [],
                        "decision": o.decision,
                        "source": o.source,
                        "reason": o.reason,
                        "stages": r.stages,
                        "stage_fingerprint": r.model_fingerprint,
                    },
                }
            )
        return reports

    def run_until_idle(self) -> None:
        while self.run_once():
            pass

    def run_forever(self, stop: Any) -> None:
        while not stop.is_set():
            try:
                busy = self.run_once(wait_s=0.5)
                if self.backend is not None:
                    busy = bool(self.process_items_once()) or busy
                self.tick_waiting()
                if self.outbox_size():
                    self.flush_outbox()
                if not busy:
                    stop.wait(0.2)
            except Exception:
                stop.wait(1.0)

    # ---- task handlers -------------------------------------------------
    def _do_scan(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        try:
            res = self._scan(p)
        except RootUnavailable:
            raise _Waiting("SOURCE_OFFLINE") from None
        self.last_scan_skipped_before_cursor = res.skipped_before_cursor
        entries = [self._entry(e) for e in res.entries]
        # pages with a resume cursor (SCAN-019)
        for pages, i in enumerate(range(0, len(entries), self.page_size), start=1):
            page = entries[i : i + self.page_size]
            self.client.post(
                f"/worker-api/v1/tasks/{task_id}/progress", json={"entries": page, "cursor": page[-1]["rel"]}
            ).raise_for_status()
            if self.crash_after_pages is not None and pages >= self.crash_after_pages:
                raise SystemExit(137)
        return {
            "entries": [],
            "skipped": [{"rel": s.rel, "reason": s.reason} for s in res.skipped],
            "bytes_read": res.bytes_read,
        }

    @staticmethod
    def _entry(e: Any) -> dict[str, Any]:
        return {
            "rel": e.rel,
            "size": e.size,
            "mtime": e.mtime.isoformat(),
            "content_hash": e.content_hash,
            "taken_at": e.taken_at.isoformat() if e.taken_at and e.date_source != "mtime" else None,
            "date_source": e.date_source,
            **e.extra,
        }

    def _scan(self, p: dict[str, Any]) -> Any:
        return scan(
            self.sb,
            p["root_alias"],
            p.get("sub_path", ""),
            p["include"],
            p["exclude"],
            tz=self.tz,  # type: ignore[arg-type]
            cache=self.cache,
            exclude_subtrees=p.get("exclude_subtrees") or [],
            after=p.get("cursor"),
        )

    def _do_apply(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        actions = [Action(**a) for a in p["actions"]]
        rep = self.executor.apply(p["plan_id"], actions, move_confirmed=bool(p.get("move_confirmed")))
        return {
            "results": [r.__dict__ for r in rep.results],
            "status": rep.status,
            "waiting_reason": rep.waiting_reason,
        }

    def _do_undo(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        res = self.executor.undo(p["plan_id"])
        return {"results": [r.__dict__ for r in res]}

    def _do_download(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """model.download (06 §2): progress is streamed to the Operation; the CP records the installation."""
        import asyncio

        from .downloads import CancelToken, DlFile, Downloader, DownloadError

        token = CancelToken()
        last = [0.0]

        def progress(ev: dict[str, Any]) -> None:
            now = self.clock.monotonic()
            if now - last[0] < 0.25 and ev["done_bytes"] < ev["total_bytes"]:
                return
            last[0] = now
            r = self.client.post(
                f"/worker-api/v1/tasks/{task_id}/progress",
                json={
                    "progress": {
                        "done": ev["done_bytes"],
                        "total": ev["total_bytes"],
                        "unit": "bytes",
                        "file": ev["file"],
                    }
                },
            )
            ctl = r.json().get("control") if r.status_code == 200 else None
            if ctl == "pause":
                token.pause()
            elif ctl == "cancel":
                token.cancel()

        d = Downloader(self.models_root, sources=self.download_sources, on_progress=progress, clock=self.clock)
        files = [DlFile(f["repo"], f["revision"], f["path"], int(f["size"]), f["sha256"]) for f in p["files"]]
        try:
            res = asyncio.run(d.download(p["op_id"], files, token))
        except DownloadError as e:
            return {"status": "failed", "error_code": e.code, "details": e.details, "permanent": e.permanent}
        return {
            "status": res.status,
            "error_code": res.error_code,
            "warnings": res.warnings,
            "installed": res.installed,
        }

    def _do_fix(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """Whitelisted auto-fixes executed on this machine (06 §3.6). Desktop-shell actions (open a system settings
        panel, switch the API port) are forwarded to the shell, which owns those capabilities."""
        fid = p["fix_id"]
        if fid == "cleanup_downloads":
            import shutil

            d = self.models_root / ".downloads"
            freed = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) if d.exists() else 0
            shutil.rmtree(d, ignore_errors=True)
            return {"status": "done", "freed_bytes": freed}
        return {"status": "forwarded_to_shell", "fix_id": fid}

    def _do_remove(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """Delete an installation's snapshot links and blobs under the models root only."""
        from .downloads import DlFile, Downloader

        d = Downloader(self.models_root, sources=self.download_sources)
        freed = 0
        for f in p.get("files") or []:
            df = DlFile(f["repo"], f["revision"], f["path"], int(f["size"]), f["sha256"])
            for path in (d._snapshot(df), d._blob(df)):
                if path.is_symlink() or path.exists():
                    if path == d._blob(df):
                        freed += path.stat().st_size
                    path.unlink()
        return {"status": "done", "freed_bytes": freed}

    def _do_try_one(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """One photo through the cascade, streaming each stage to the UI (ORG-008)."""
        import asyncio

        from opagent_inference.cascade import Cascade, Item

        def send(stage: str, **data: Any) -> None:
            self.client.post(f"/worker-api/v1/tasks/{task_id}/progress", json={"events": [{"stage": stage, **data}]})

        inp, _fail = self._prepare({"id": "try", "root_alias": p["root_alias"], "rel": p["rel"], "attempt": 1})
        facts = self._facts.pop("try", {})
        if inp is None:
            send("decision", decision="failed", error="DECODE_FAILED")
            return {"decision": "failed", "error": "DECODE_FAILED"}
        send("rules")
        cas = Cascade(self.embedder, self.backend, on_stage=send)
        if self._cascade is not None:  # share caches with the batch cascade
            cas.image_cache, cas.text_cache, cas.vlm_cache = (
                self._cascade.image_cache,
                self._cascade.text_cache,
                self._cascade.vlm_cache,
            )
        [r] = asyncio.run(cas.run([Item(inp, facts)], _taxonomy(p["task_config"])))
        out = {
            "asset_id": p["asset_id"],
            "decision": r.outcome.decision,
            "category": r.outcome.category,
            "confidence": r.outcome.confidence,
            "source": r.outcome.source,
            "caption": r.caption,
            "tags": r.tags,
        }
        send("decision", **{k: v for k, v in out.items() if k != "asset_id"})
        return out

    def _do_estimate(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        import asyncio

        from opagent_inference.cascade import Cascade

        if self.embedder is None:
            return {"counts": {}, "routed_to_vlm": 0, "n_embedded": 0, "total": p["total"]}
        if self._cascade is None:
            self._cascade = Cascade(self.embedder, self.backend)
        images = []
        for k, it in enumerate(p["items"]):
            inp, _fail = self._prepare({"id": f"est{k}", "root_alias": p["root_alias"], "rel": it["rel"], "attempt": 1})
            self._facts.pop(f"est{k}", None)
            if inp is not None:
                images.append(inp)
        est = asyncio.run(self._cascade.estimate(images, _taxonomy(p["task_config"])))
        return {
            "counts": est.counts,
            "routed_to_vlm": est.routed_to_vlm,
            "n_embedded": len(images),
            "total": p["total"],
        }

    def _do_browse(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        sp = self.sb.resolve(p["root_alias"], p.get("path", ""))
        dirs = sorted(
            e.name for e in os.scandir(sp.real) if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")
        )
        return {"dirs": dirs}


def _taxonomy(tc: dict[str, Any]) -> Any:
    from opagent_inference.cascade import ClassDef, Taxonomy

    classes = [ClassDef(c["id"], c.get("name") or c["id"], c.get("description") or "") for c in tc.get("classes") or []]
    if not classes:
        classes = [ClassDef(k, k) for k in tc.get("categories") or []]
    return Taxonomy(classes=classes, rules=list(tc.get("rules") or []))
