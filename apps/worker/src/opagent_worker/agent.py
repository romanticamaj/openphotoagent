"""Worker agent: leases tasks from the API over the Worker protocol and executes them (docs/02 §5.3).

Only the worker touches files (invariant 1); every path goes through SafePath (invariant 2) and every file
change through the journaled executor (invariant 3).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from opagent_core.clock import SystemClock
from opagent_core.operation import NullReporter
from opagent_fs.executor import Action, Executor
from opagent_fs.fsops import RealFs
from opagent_fs.journal import Journal
from opagent_fs.safepath import RootConfig, RootUnavailable, Sandbox

from .scanner import HashCache, scan


class _Waiting(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Abandoned(Exception):
    """The worker is stopping: the download is dropped like a crash and resumed by the next worker (DL-014)."""


WATCH_SYNC_S = 15.0
CLUSTER_CHECK_S = 60.0
CLUSTER_TIMEOUT_S = 600.0  # 20,000 faces take minutes (07 §7)
_log = logging.getLogger("opagent.worker")
# The only messages the control plane can send (02 §6.3, MU-SEC-004). There is no "run a command" kind; anything
# else is refused and logged, never looked up dynamically.
RPC_KINDS = frozenset(
    {
        "apply",
        "benchmark",
        "browse",
        "download",
        "embed_assets",
        "embed_captions",
        "embed_image",
        "embed_text",
        "estimate",
        "face_embed_query",
        "face_purge",
        "fix",
        "folder_preview",
        "folder_thumb",
        "hash_sha1",
        "probe_root",
        "remove",
        "scan",
        "scan_models",
        "thumb_fetch",
        "query_plan",
        "try_one",
        "undo",
        "vlm_ask",
    }
)


# quick and read-only: a person is waiting on these, so they do not queue behind a scan (IMP-004)
QUICK_KINDS = ("browse", "folder_preview", "folder_thumb")


class _ScanReporter(NullReporter):
    """A scan's count, at most once a second (invariant 9); the last one always goes out."""

    def __init__(self, client: Any, clock: Any, task_id: str) -> None:
        self.client, self.clock, self.task_id = client, clock, task_id
        self.last = -1e9

    def progress(self, done: int, total: int | None = None) -> None:
        now = self.clock.monotonic()
        if now - self.last < 1.0 and done != total:
            return
        self.last = now
        body = {"progress": {"done": done, "total": total, "unit": "files"}}
        self.client.post(f"/worker-api/v1/tasks/{self.task_id}/progress", json=body)


class WorkerAgent:
    def __init__(
        self,
        client: Any,
        roots: list[dict[str, str]],
        home: Path,
        backend: Any = None,
        embedder: Any = None,
        face_backend: Any = None,
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
        self.bench_backends: dict[str, Any] = {}  # benchmark candidates by name (03 §6); loaded on demand
        self.text_embedders: dict[str, Any] = {}  # caption text models by registry key (R2, ADR-0014)
        self.backend_factory: Any = None  # production: rebuilds the adapters from the models on disk (backends.py)
        self.face_backend = face_backend  # detection + embedding run only here, on the decoded image (07 §2.2)
        self._faces: dict[str, tuple[str, list[dict[str, Any]]]] = {}
        self._active_child: Any = None  # the running download child (ADR-0007)
        self._dl_abandon = threading.Event()
        self.download_reserve_floor: int | None = None  # None = 06 §2.5 default (max(2 GB, 2 %)); test harness only
        self.policy: Any = None  # ResourcePolicy: heavy labelling waits on battery / busy GPU (06 §5)
        self.hw_profile = hw_profile
        self.doctor_env: dict[str, Any] = {}  # raw-fact overrides for the doctor (tests / FakeWorkerLink)
        self.models_root = Path(models_root) if models_root else self.home / "models"
        self.download_sources = download_sources or ["https://huggingface.co"]
        self._outbox().close()  # create it in WAL now, single-threaded, before any loop or RPC thread opens it (Q-029)
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
        self._cluster_checked = -1e9
        self.page_size = 100  # small enough that a long scan shows photos early (IMP-006)
        self.scan_threads = max(1, min(8, (os.cpu_count() or 2) - 2))
        self.crash_after_pages: int | None = None  # test hook: simulate kill -9 after N reported pages
        self.last_scan_skipped_before_cursor = 0
        self._dl_thread: threading.Thread | None = None
        self.boot_id = uuid.uuid4().hex  # one per process (restart detection, OP-006)
        self.apply_throttle_s = 0.0  # test hook (E2E-B-008): pause between file actions
        # remote worker (v0.5): the control plane is elsewhere; egress policy and channel apply (remote.py)
        from .remote import PROTOCOL_VERSION

        self.remote = hasattr(client, "egress")
        self.egress: Any = getattr(client, "egress", None)
        self.channel: Any = None
        self.protocol_version = PROTOCOL_VERSION
        self.welcome: dict[str, Any] | None = None
        self.blocked_reason: str | None = None

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
            return f.read_text(encoding="utf-8").strip()
        wid = "w-" + uuid.uuid4().hex[:12]
        f.write_text(wid, encoding="utf-8")
        return wid

    def refresh_backends(self) -> None:
        """A model that finished downloading goes live here, with no restart; a running one is left alone."""
        if self.backend_factory is None:
            return

        def fp(b: Any) -> str | None:
            return b.describe().model_fingerprint if b is not None else None

        try:
            new = self.backend_factory()
        except Exception:  # a broken model must cost that model, not the worker and its running download
            _log.exception("cannot load the models on disk")
            return
        faces = getattr(new, "faces", None)
        if fp(faces) != fp(self.face_backend):
            self.face_backend = faces  # the fingerprint travels with each result, so no re-registration
        if fp(new.vlm) == fp(self.backend) and fp(new.embedder) == fp(self.embedder):
            return
        old = self.backend
        if fp(new.vlm) != fp(old):
            self.backend = new.vlm
            if old is not None and hasattr(old, "close"):
                old.close()
        if fp(new.embedder) != fp(self.embedder):
            self.embedder = new.embedder
        self._cascade = None
        self.register()

    def register(self) -> None:
        models: dict[str, str] = {}
        if self.backend is not None:
            info = self.backend.describe()
            models = {t: info.model_fingerprint for t in info.task_types}
        if self.embedder is not None:  # an embed-only worker (VLM still downloading) can already search
            models["embed"] = self.embedder.describe().model_fingerprint
            if self.backend is not None:
                models["classify"] = self.cascade_fingerprint
        r = self.client.post(
            "/worker-api/v1/register",
            json={
                "worker_id": self.worker_id,
                "name": self.name,
                "boot_id": self.boot_id,
                "app_version": _version(),
                "models": models,
                "hw": self.hw_summary(),
                **self.doctor_report(),
                "roots": [{"alias": r.alias, "mode": r.mode} for r in self.roots],
                "protocol_version": self.protocol_version,
                "deployment": "remote" if self.remote else "desktop",
            },
        )
        r.raise_for_status()
        self.on_welcome(r.json().get("welcome"))

    def on_welcome(self, w: dict[str, Any] | None) -> None:
        """The CP's terms (02 §5.3): stop taking work when the protocol is too old; adopt the egress policy."""
        if not w:
            return
        self.welcome = w
        self.blocked_reason = None if w.get("status") == "ok" else str(w.get("status"))
        if self.blocked_reason:
            _log.error(
                "control plane says %s (CP protocol %s, ours %s)",
                w.get("status"),
                w.get("protocol"),
                self.protocol_version,
            )
        if self.egress is not None and w.get("egress_policy"):
            self.egress.apply(w["egress_policy"])

    def run_once(self, wait_s: float = 0.0, background_downloads: bool = False) -> bool:
        """Lease and run one task. With ``background_downloads`` (the long-running loop) a download runs on its own
        thread — one at a time (06 §2.4) — so browsing, scans and labelling keep going meanwhile (04 §8.2 W6 ∥ W7)."""
        if self.blocked_reason:  # e.g. upgrade_required: no work until this worker is updated (MU-WORKER-005)
            return False
        busy = self._dl_thread is not None and self._dl_thread.is_alive()
        body: dict[str, Any] = {"worker_id": self.worker_id, "wait_s": wait_s, "boot_id": self.boot_id}
        if background_downloads and busy:
            body["exclude_kinds"] = ["download"]
        if self.channel is not None:
            from .remote import UpgradeRequired

            try:
                if not self.channel.wait_for_work(wait_s):
                    return False
                leased = self.channel.lease(wait_s, self.boot_id)
            except UpgradeRequired:
                self.blocked_reason = "upgrade_required"
                return False
            if leased is None:
                return False
            task = leased
        else:
            r = self.client.post("/worker-api/v1/lease", json=body)
            if r.status_code == 204:
                return False
            if r.status_code == 426:
                self.blocked_reason = "upgrade_required"
                return False
            r.raise_for_status()
            task = r.json()
        if background_downloads and task["kind"] == "download":
            # the thread only supervises: the bytes move in a child process (ADR-0007, DL-019)
            self._dl_thread = threading.Thread(target=self._run_task, args=(task, True), daemon=True)
            self._dl_thread.start()
            return True
        self._run_task(task)
        return True

    def _run_task(self, task: dict[str, Any], in_child: bool = False) -> None:
        try:
            if task.get("kind") not in RPC_KINDS:
                _log.warning("UNKNOWN_RPC: refused task kind %r", str(task.get("kind"))[:64])
                self.client.post(
                    f"/worker-api/v1/tasks/{task['id']}/complete",
                    json={"result": {"error": "UNKNOWN_RPC"}, "ok": False},
                )
                return
            if self.egress is not None:
                from .remote import EgressDenied

                try:
                    self.egress.task_allowed(task["kind"])
                except EgressDenied:
                    self.client.post(
                        f"/worker-api/v1/tasks/{task['id']}/complete",
                        json={"result": {"error": "EGRESS_POLICY_DENIED"}, "ok": False},
                    )
                    return
            if in_child:
                result = self._download_in_child(task["payload"], task["id"])
            else:
                result = getattr(self, f"_do_{task['kind']}")(task["payload"], task["id"])
            ok = True
        except _Abandoned:
            return  # report nothing: the restart recovery marks it interrupted and resumes it
        except _Waiting as w:  # e.g. SOURCE_OFFLINE: keep the task, retry automatically (SCAN-020)
            self.client.post(f"/worker-api/v1/tasks/{task['id']}/progress", json={"waiting_reason": w.reason})
            self.waiting[task["id"]] = (task, self.clock.monotonic())
            return
        except Exception as e:
            from .remote import EgressDenied

            code = "EGRESS_POLICY_DENIED" if isinstance(e, EgressDenied) else type(e).__name__
            result, ok = {"error": code, "detail": str(e)[:500]}, False
        self.waiting.pop(task["id"], None)
        self.client.post(f"/worker-api/v1/tasks/{task['id']}/complete", json={"result": result, "ok": ok})
        if ok and task.get("kind") == "download":
            self.refresh_backends()

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
                "boot_id": self.boot_id,
                "max_items": self.batch_size,
                "loaded_models": [t for t in getattr(self, "loaded_tasks", [])],
                "paused_reason": self.policy.paused_reason() if self.policy is not None else None,
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
        self._send_faces(reports)
        return len(items)

    def _backlog_cursor(self) -> Path:
        """Per model: a new one starts over. Inside ``faces/``, so a purge removes it with the rest (07 §6.3)."""
        name = hashlib.sha256(self.face_backend.fingerprint.encode()).hexdigest()[:16]
        return self.home / "faces" / f"backlog-{name}.txt"

    def faces_cluster_once(self, scanned: int) -> None:
        """Group the faces found so far (07 §2.3): a full batch while the backlog runs, the rest once it is done."""
        if self.face_backend is None or self.remote:
            return
        now = self.clock.monotonic()
        if not scanned and now - self._cluster_checked < CLUSTER_CHECK_S:
            return
        if not scanned:
            self._cluster_checked = now
        self.client.post(
            "/worker-api/v1/faces/cluster-due", json={"min_new": 200 if scanned else 1}, timeout=CLUSTER_TIMEOUT_S
        )

    def faces_backlog_once(self, limit: int = 32) -> int:
        """Photos that never had a face step, one page at a time, when there is nothing else to do."""
        if self.face_backend is None or self.remote:
            return 0
        from .imaging import open_image, oriented

        cursor = self._backlog_cursor()
        after = cursor.read_text(encoding="utf-8").strip() if cursor.exists() else ""
        r = self.client.get("/worker-api/v1/faces/backlog", params={"after": after, "limit": limit})
        r.raise_for_status()
        page = r.json()
        for it in page["items"]:
            try:
                sp = self.sb.resolve(it["root_alias"], it["rel"])
                with open_image(sp.real) as im:
                    faces = self.face_backend.detect(it["rel"], oriented(im).convert("RGB"))
            except Exception:  # a moved or unreadable photo costs that photo, not the scan
                _log.warning("face scan skipped a photo", exc_info=True)
                continue
            self.client.post(
                "/worker-api/v1/faces",
                json={
                    "worker_id": self.worker_id,
                    "asset_id": it["asset_id"],
                    "fingerprint": self.face_backend.fingerprint,
                    "faces": [{k: v for k, v in f.items() if k != "person_hint"} for f in faces],
                },
            )
        cursor.parent.mkdir(parents=True, exist_ok=True)
        cursor.write_text(str(page["next"]), encoding="utf-8")
        return len(page["items"])

    def _send_faces(self, reports: list[dict[str, Any]]) -> None:
        from opagent_core.faces import should_run

        if self.face_backend is None:
            return
        for rep in reports:
            got = self._faces.pop(rep.get("job_item_id", ""), None)
            if got is None or rep.get("status") != "ok":
                continue
            if not should_run(category=(rep.get("output") or {}).get("category")):  # e.g. screenshots
                continue
            aid, faces = got
            self.client.post(
                "/worker-api/v1/faces",
                json={
                    "worker_id": self.worker_id,
                    "asset_id": aid,
                    "fingerprint": self.face_backend.fingerprint,
                    "faces": [{k: v for k, v in f.items() if k != "person_hint"} for f in faces],
                },
            )

    def close(self) -> None:
        """Release the database files: Windows cannot delete or replace a file that is still open."""
        self.executor.journal.close()
        self.cache.close()

    # ---- offline tolerance: results go to a local outbox when the CP is unreachable (02 §4.3, Q-013) ----
    def _outbox(self) -> Any:
        import sqlite3

        db = sqlite3.connect(self.home / "outbox.db")
        db.execute(
            "PRAGMA journal_mode=WAL"
        )  # like scanner/journal: no rollback journal, append while flushing (Q-029)
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
            rel = self.egress.resolve(it["rel"]) if self.egress is not None else it["rel"]
            sp = self.sb.resolve(it["root_alias"], rel)
            side = self.backend.describe().preferred_input.max_side if self.backend else 512
            with open_image(sp.real) as im:
                im.load()
                from .imaging import exif_facts

                self._facts[it["id"]] = exif_facts(im, it["rel"])
                pic = oriented(im).convert("RGB")
                if it.get("faces") and self.face_backend is not None:  # same decode as the VLM (FACE-DET-004)
                    self._faces[it["id"]] = (it["asset_id"], self.face_backend.detect(it["rel"], pic))
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
        results = asyncio.run(self._cascade.run(items, self._taxonomy_for(self._cascade, tc)))
        self._post_embeddings([inp for _, inp in prepared])
        fp = self.cascade_fingerprint
        reports = []
        for (it, _), r in zip(prepared, results, strict=True):
            if r.retryable and r.error:  # no guess under the real cache key: the item is asked again (INF-CAS-018)
                reports.append(self._fail(it, r.error, retryable=True))
                continue
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

    def stop_downloads(self) -> None:
        """Worker shutdown: drop the running download (no report; parts kept) and wait for its supervisor."""
        self._dl_abandon.set()
        if self._dl_thread is not None:
            self._dl_thread.join(10)
        deadline = time.monotonic() + 10  # noqa: TID251 - shutdown wait on a real child process
        while self._active_child is not None and time.monotonic() < deadline:  # noqa: TID251
            time.sleep(0.05)  # noqa: TID251
        self._dl_abandon.clear()

    def run_forever(self, stop: Any) -> None:
        try:
            self._run_forever(stop)
        finally:
            self.stop_downloads()

    def _run_forever(self, stop: Any) -> None:
        from .watcher import WatchManager

        watch = WatchManager(self)  # watch folders (05 §5): synced with the API every WATCH_SYNC_S
        synced_at: float | None = None
        if self.channel is None:
            threading.Thread(target=self._quick_loop, args=(stop,), daemon=True).start()
        try:
            while not stop.is_set():
                now = self.clock.monotonic()
                if synced_at is None or now - synced_at >= WATCH_SYNC_S:
                    synced_at = now
                    try:
                        watch.sync()
                    except Exception:  # the API may be restarting; try again next round
                        _log.warning("watch sync failed", exc_info=True)
                try:
                    watch.step()
                except Exception:
                    _log.warning("watch step failed", exc_info=True)
                self._loop_once(stop)
        finally:
            watch.stop()

    def _quick_loop(self, stop: Any) -> None:
        """Browsing and folder previews answer while the main loop is inside a scan (IMP-004)."""
        body = {"worker_id": self.worker_id, "wait_s": 0.5, "boot_id": self.boot_id, "only_kinds": list(QUICK_KINDS)}
        while not stop.is_set():
            try:
                r = self.client.post("/worker-api/v1/lease", json=body)
                if r.status_code == 200:
                    self._run_task(r.json())
                elif r.status_code != 204:
                    stop.wait(1.0)
            except Exception:  # the API may be restarting
                stop.wait(1.0)

    def _loop_once(self, stop: Any) -> None:
        try:
            busy = self.run_once(wait_s=0.5, background_downloads=True)
            if self.backend is not None:
                busy = bool(self.process_items_once()) or busy
            self.tick_waiting()
            if self.outbox_size():
                self.flush_outbox()
            if not busy:
                self.warm_embedder()
                scanned = self.faces_backlog_once()
                self.faces_cluster_once(scanned)
                busy = scanned > 0
            if not busy:
                stop.wait(0.2)
        except Exception:
            stop.wait(1.0)

    # ---- task handlers -------------------------------------------------
    def _do_scan(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        pages = 0

        def send(found: list[Any]) -> None:  # pages with a resume cursor (SCAN-019), as the scan goes (IMP-006)
            nonlocal pages
            page = [self._entry(e) for e in found]
            if self.remote:
                self._upload_thumbs(page)
            self.client.post(
                f"/worker-api/v1/tasks/{task_id}/progress", json={"entries": page, "cursor": page[-1]["rel"]}
            ).raise_for_status()
            pages += 1
            if self.crash_after_pages is not None and pages >= self.crash_after_pages:
                raise SystemExit(137)

        try:
            res = self._scan(p, _ScanReporter(self.client, self.clock, task_id) if task_id else None, send)
        except RootUnavailable:
            raise _Waiting("SOURCE_OFFLINE") from None
        self.last_scan_skipped_before_cursor = res.skipped_before_cursor
        return {
            "entries": [],
            "skipped": [{"rel": s.rel, "reason": s.reason} for s in res.skipped],
            "bytes_read": res.bytes_read,
        }

    def _upload_thumbs(self, entries: list[dict[str, Any]]) -> None:
        """A remote CP has no access to this disk: send the thumbnails the egress policy allows (02 §1.4)."""
        from .remote import THUMB_LIMIT, VARIANTS

        limit = THUMB_LIMIT[self.egress.mode]
        for e in entries:
            h = e["content_hash"]
            for v in e.get("thumbs") or []:
                if VARIANTS.get(v, 10**9) > limit:
                    continue
                f = self.home / "thumbs" / h[:2] / f"{h}.{v}.webp"
                if f.is_file():
                    self.client.put(
                        f"/worker-api/v1/thumbs/{h}/{v}", content=f.read_bytes(), headers={"Content-Type": "image/webp"}
                    )

    def _do_thumb_fetch(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """edge-thumbs (02 §1.4): the browser's thumbnail is relayed through the CP, never stored there."""
        import base64
        import re

        h, v = str(p["content_hash"]), str(p.get("variant", "t256"))
        if not re.fullmatch(r"[0-9a-f]{16,128}", h) or v not in ("t256", "t512", "p1024"):
            raise ValueError("bad thumbnail request")
        f = self.home / "thumbs" / h[:2] / f"{h}.{v}.webp"
        if not f.is_file():
            return {"missing": True}
        return {"thumbnail_b64": base64.b64encode(f.read_bytes()).decode(), "variant": v}

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

    def _scan(self, p: dict[str, Any], reporter: Any = None, sink: Any = None) -> Any:
        return scan(
            self.sb,
            p["root_alias"],
            p.get("sub_path", ""),
            p["include"],
            p["exclude"],
            tz=self.tz,  # type: ignore[arg-type]
            cache=self.cache,
            exclude_subtrees=p.get("exclude_subtrees") or [],
            after=self.egress.resolve(p["cursor"]) if self.egress is not None and p.get("cursor") else p.get("cursor"),
            thumbs_dir=self.home / "thumbs",
            reporter=reporter,
            sink=sink,
            page_size=self.page_size,
            threads=self.scan_threads,
        )

    def _do_apply(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        eg = self.egress  # results-only with hashed paths: the CP only knows the hashes
        actions = [Action(**({**a, "src_rel": eg.resolve(a["src_rel"])} if eg else a)) for a in p["actions"]]
        done = [0]

        def control() -> str | None:
            """Between file actions: report progress and pick up pause/cancel from the control channel."""
            if self.apply_throttle_s:
                threading.Event().wait(self.apply_throttle_s)
            r = self.client.post(
                f"/worker-api/v1/tasks/{task_id}/progress",
                json={"progress": {"done": done[0], "total": len(actions), "unit": "files"}},
            )
            done[0] += 1
            return r.json().get("control") if r.status_code == 200 else None

        rep = self.executor.apply(
            p["plan_id"], actions, move_confirmed=bool(p.get("move_confirmed")), control=control if task_id else None
        )
        return {
            "results": [r.__dict__ for r in rep.results],
            "status": rep.status,
            "waiting_reason": rep.waiting_reason,
        }

    def _do_undo(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        res = self.executor.undo(p["plan_id"])
        return {"results": [r.__dict__ for r in res]}

    def _progress_poster(self, task_id: str, on_control: Any) -> Any:
        """Stream download progress to the Operation (≤ 4/s) and hand pause/cancel requests back (06 §2)."""
        last = [0.0]

        def progress(ev: dict[str, Any]) -> None:
            now = self.clock.monotonic()
            waiting = ev.get("waiting_reason")
            if not waiting and now - last[0] < 0.25 and ev["done_bytes"] < ev["total_bytes"]:
                return
            last[0] = now
            body: dict[str, Any] = {
                "progress": {
                    "done": ev["done_bytes"],
                    "total": ev["total_bytes"],
                    "unit": "bytes",
                    "file": ev.get("file"),
                    **({"retry_in_s": ev.get("retry_in_s")} if waiting else {}),
                }
            }
            if waiting:
                body["waiting_reason"] = waiting
            r = self.client.post(f"/worker-api/v1/tasks/{task_id}/progress", json=body)
            ctl = r.json().get("control") if r.status_code == 200 else None
            if ctl in ("pause", "cancel"):
                on_control(ctl)

        return progress

    def _do_download(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """model.download (06 §2), in-process (one-shot runs and tests with an injected clock)."""
        import asyncio

        from .downloads import CancelToken, DlFile, Downloader, DownloadError

        token = CancelToken()
        progress = self._progress_poster(task_id, lambda c: token.pause() if c == "pause" else token.cancel())
        d = Downloader(
            self.models_root,
            sources=self.download_sources,
            on_progress=progress,
            clock=self.clock,
            reserve_floor=self.download_reserve_floor,
        )
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
            "waiting_reason": res.waiting_reason,
        }

    def _download_in_child(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """model.download in the long-running loop: the same Downloader in a child process (ADR-0007)."""
        from .dlchild import ChildDownload

        child = ChildDownload(
            self.models_root, self.download_sources, p["op_id"], list(p["files"]), self.download_reserve_floor
        )
        self._active_child = child
        progress = self._progress_poster(task_id, child.send)
        try:
            while True:
                if self._dl_abandon.is_set():
                    child.proc.terminate()  # like kill -9: parts stay on disk for the resume
                    child.proc.join(5)
                    raise _Abandoned()
                ev = child.next_event()
                if ev is None:
                    continue
                kind, data = ev
                if kind == "progress":
                    progress(data)
                elif kind == "result":
                    return dict(data)
        finally:
            self._active_child = None
            child.close()

    def _post_embeddings(self, images: list[Any]) -> None:
        """Upload image vectors computed by the cascade (searchable right away, 05 §1.1)."""
        if self._cascade is None or self.embedder is None:
            return
        if self.egress is not None and self.egress.mode == "results-only":
            return  # no vectors leave the machine (02 §1.4)
        items = [
            {"content_hash": i.content_hash, "vec": [float(x) for x in self._cascade.image_cache[i.content_hash]]}
            for i in images
            if i.content_hash in self._cascade.image_cache
        ]
        if not items:
            return
        info = self.embedder.describe()
        self.client.post(
            "/worker-api/v1/embeddings",
            json={
                "worker_id": self.worker_id,
                "model_key": info.adapter_key,
                "fingerprint": info.model_fingerprint,
                "dim": len(items[0]["vec"]),
                "items": items,
            },
        )

    def _do_embed_assets(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """search.reindex: embed assets that have no vector in the active space."""
        import asyncio

        from opagent_inference.cascade import Cascade, Item

        if self.embedder is None:
            return {"error": "MODEL_UNAVAILABLE"}
        if self._cascade is None:
            self._cascade = Cascade(self.embedder, self.backend)
        images = []
        for k, it in enumerate(p["items"]):
            inp, _fail = self._prepare({"id": f"ix{k}", "root_alias": it["root_alias"], "rel": it["rel"], "attempt": 1})
            self._facts.pop(f"ix{k}", None)
            if inp is not None:
                images.append(inp)

        from opagent_inference.types import LoadOptions

        embedder, cascade = self.embedder, self._cascade

        async def go() -> None:
            await embedder.load(LoadOptions())
            await cascade._embed([Item(i) for i in images])

        asyncio.run(go())
        self._post_embeddings(images)
        return {"embedded": len(images)}

    def _do_query_plan(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """No LLM planner here yet: an empty answer sends the API to its rule planner at once (SRCH-PLAN-011)."""
        return {}

    @staticmethod
    def _ensure_loaded(emb: Any) -> None:
        """Real adapters load on demand; only a job used to do it, so search died with every worker restart."""
        import asyncio

        from opagent_inference.types import LoadOptions

        if not getattr(emb, "loaded", True):
            asyncio.run(emb.load(LoadOptions()))

    def warm_embedder(self) -> None:
        """A cold load takes seconds and a search waits 2: idle time is when to pay for it."""
        if self.embedder is not None:
            self._ensure_loaded(self.embedder)

    def _do_embed_text(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """``embed.text`` RPC (05 §2.6): the query vector must come from the same space as the stored vectors."""
        import asyncio
        import inspect

        emb = self.text_embedders.get(p.get("model") or "") if p.get("modality") == "caption" else self.embedder
        if emb is None:
            return {"error": "MODEL_UNAVAILABLE"}
        own = emb.describe().model_fingerprint
        if p.get("modality") == "caption":
            own = f"caption:{p.get('model')}:{own}"
        if p.get("fingerprint") and p["fingerprint"] != own:
            return {"error": "MODEL_UNAVAILABLE", "reason": "space mismatch"}
        self._ensure_loaded(emb)
        res: Any = emb.embed_texts([p["text"]])
        if inspect.isawaitable(res):

            async def _wait(a: Any) -> Any:
                return await a

            res = asyncio.run(_wait(res))
        return {"vec": [float(x) for x in list(res[0])]}

    def _do_embed_captions(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """R2 (05 §2): caption text → multilingual vector with the space's text model (ADR-0014)."""
        emb = self.text_embedders.get(p["model"])
        if emb is None:
            raise RuntimeError("MODEL_UNAVAILABLE")
        items = p["items"]
        vecs = emb.embed_texts([it["text"] for it in items])
        return {
            "vectors": {it["asset_id"]: [float(x) for x in v] for it, v in zip(items, vecs, strict=True)},
            "fingerprint": emb.describe().model_fingerprint,
            "dim": len(vecs[0]) if vecs else 0,
        }

    def _do_hash_sha1(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """Immich matches assets by SHA-1 (02 §8.2); only the worker reads the photos (invariant 1)."""
        import base64
        import hashlib

        out: dict[str, str] = {}
        for it in p["items"]:
            try:
                sp = self.sb.resolve(it["root_alias"], it["rel"])
                h = hashlib.sha1(usedforsecurity=False)  # Immich's checksum format, not a security use
                with open(sp.real, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                out[it["asset_id"]] = base64.b64encode(h.digest()).decode()
            except OSError:
                continue
        return {"sha1": out}

    def _do_scan_models(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        from .model_import import default_ollama_dir, scan_folder, scan_ollama

        if p.get("source") == "ollama":
            return scan_ollama(Path(getattr(self, "ollama_dir", None) or default_ollama_dir()))
        return scan_folder(Path(str(p["path"])))

    def _do_benchmark(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """03 §6: run each candidate over the golden set; predictions and timings only (scores are computed by the
        API). Images are decoded once and shared by all candidates. Candidates that are not available on this
        worker report an error instead of a score."""
        import asyncio

        from opagent_inference.types import BatchRequest, LoadOptions

        images = []
        failed: list[str] = []
        for it in p["items"]:
            img, _err = self._prepare({**it, "id": it["asset_id"], "attempt": 1})
            if img is None:
                failed.append(it["asset_id"])
            else:
                images.append(img)
        out = []
        for name in p["candidates"]:
            backend = self.bench_backends.get(name)
            if backend is None:
                out.append({"name": name, "error": "MODEL_NOT_INSTALLED"})
                continue

            async def go(b: Any = backend) -> Any:
                await b.load(LoadOptions())
                t0 = time.perf_counter()
                res = await b.run_batch(
                    BatchRequest(task_type="classify", items=images, variables={"categories": p["classes"]})
                )
                return res, time.perf_counter() - t0

            res, took = asyncio.run(go()) if images else ([], 0.0)
            per = took * 1000 / max(1, len(images))
            out.append(
                {
                    "name": name,
                    "preds": {o.item_id: [o.category, float(o.confidence or 0.0)] for o in res if o.category},
                    "latency_ms": [per] * len(res),
                    "items_per_s": len(res) / took if took > 0 else float(len(res) or 1),
                    "vram_mb": backend.describe().est_vram_mb,
                }
            )
        return {"candidates": out, "failed": failed}

    def _do_vlm_ask(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """describe / verify / compare for the assistant and MCP (10 §8.3): the VLM looks at the photos here, on
        this machine; only the text answer goes back (07 §6.2 F3, ASK-PRV-002)."""
        import asyncio
        import io
        import time

        from opagent_inference.types import BatchRequest, ImageInput, LoadOptions

        from .imaging import open_image, oriented

        if self.backend is None:
            return {"error": "MODEL_UNAVAILABLE"}
        t0 = time.perf_counter()
        imgs = []
        for it in p.get("items") or []:
            sp = self.sb.resolve(it["root_alias"], it["rel"])
            with open_image(sp.real) as im:
                pic = oriented(im).convert("RGB")
            pic.thumbnail((1024, 1024))
            buf = io.BytesIO()
            pic.save(buf, "JPEG", quality=90)
            imgs.append(ImageInput(item_id=it["asset_id"], content_hash=it["asset_id"], data=buf.getvalue()))
        backend = self.backend
        mode = p.get("mode")
        task: Literal["classify", "caption"] = "classify" if mode == "verify" else "caption"
        variables = {"categories": ["yes", "no", "unsure"], "question": p.get("text") or ""}

        async def go() -> Any:
            await backend.load(LoadOptions())
            return await backend.run_batch(BatchRequest(task_type=task, items=imgs, variables=variables))

        outs = asyncio.run(go()) if imgs else []
        ms = int((time.perf_counter() - t0) * 1000)
        model = backend.describe().model_fingerprint
        if mode == "verify":
            return {
                "results": [
                    {
                        "asset_id": i.item_id,
                        "verdict": o.category if o.category in ("yes", "no") else "unsure",
                        "score": float(o.confidence or 0.0),
                        "reason": (o.caption or "")[:30],
                    }
                    for i, o in zip(imgs, outs, strict=True)
                ],
                "model": model,
                "latency_ms": ms,
            }
        return {"answer": "\n".join(o.caption or "" for o in outs), "model": model, "latency_ms": ms}

    def _do_face_embed_query(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """「拖照片找人」: the uploaded picture is fetched once from the API's memory and its face vectors are only in
        this answer — nothing on disk, nothing in a log (FACE-FIND-002)."""
        import io

        from PIL import Image

        from .imaging import oriented

        if self.face_backend is None:
            return {"error": "MODEL_UNAVAILABLE"}
        r = self.client.get(f"/worker-api/v1/blobs/{p['blob']}")
        if r.status_code != 200:
            return {"error": "BLOB_GONE"}
        try:
            with Image.open(io.BytesIO(r.content)) as im:
                pic = oriented(im).convert("RGB")
        except Exception:
            return {"error": "DECODE_FAILED"}
        faces = self.face_backend.detect(p.get("name") or "query", pic)
        return {"faces": [{k: v for k, v in f.items() if k != "person_hint"} for f in faces]}

    def _do_face_purge(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """Worker side of the one-click purge (07 §6.3): face thumbnails and any vault go, the workspace key is
        destroyed so sealed backups cannot be opened again (FACE-PRG-002). The report holds counts only."""
        import shutil

        from . import facevault

        d = self.home / "faces"
        n = sum(1 for f in d.rglob("*") if f.is_file()) if d.exists() else 0
        if d.exists():
            shutil.rmtree(d)
        try:
            destroyed = facevault.destroy_key(p["workspace_id"]) or facevault._get(p["workspace_id"]) is None
        except Exception:  # no keyring at all: nothing was ever sealed with it
            destroyed = True
        return {"thumbnails": n, "key_destroyed": bool(destroyed), "vault_removed": True}

    def _do_embed_image(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """``embed.image`` RPC (05 §2.2): an uploaded query picture, fetched once from the API's memory, embedded
        in memory and dropped — never written to disk (SRCH-IMG-002)."""
        import asyncio
        import io

        import blake3
        from opagent_inference.types import BatchRequest, ImageInput
        from PIL import Image

        from .imaging import oriented

        if self.embedder is None:
            return {"error": "MODEL_UNAVAILABLE"}
        if p.get("fingerprint") and p["fingerprint"] != self.embedder.describe().model_fingerprint:
            return {"error": "MODEL_UNAVAILABLE", "reason": "space mismatch"}
        r = self.client.get(f"/worker-api/v1/blobs/{p['token']}")
        if r.status_code != 200:
            return {"error": "BLOB_GONE"}
        data = r.content
        try:
            with Image.open(io.BytesIO(data)) as im:
                pic = oriented(im).convert("RGB")
        except Exception:
            return {"error": "DECODE_FAILED"}
        pic.thumbnail((512, 512))
        buf = io.BytesIO()
        pic.save(buf, "JPEG", quality=90)
        img = ImageInput(item_id="query", content_hash=blake3.blake3(data).hexdigest(), data=buf.getvalue())
        from opagent_inference.types import LoadOptions

        embedder = self.embedder

        async def go() -> Any:
            await embedder.load(LoadOptions())  # no-op when already loaded
            return await embedder.run_batch(BatchRequest(task_type="embed", items=[img]))

        res = asyncio.run(go())
        return {"vec": [float(x) for x in list(res[0].embedding)]}

    def _do_probe_root(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        """Preflight RPC: can this source be read right now? (first 10 entries, 06 §3.3 fs.roots)"""
        try:
            sp = self.sb.resolve(p["root_alias"], p.get("sub_path") or "")
            with os.scandir(sp.real) as it:
                for _i, _e in zip(range(10), it, strict=False):
                    pass
            return {"state": "ok"}
        except PermissionError:
            return {"state": "EACCES"}
        except (FileNotFoundError, RootUnavailable):
            return {"state": "ENOENT"}

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
        [r] = asyncio.run(cas.run([Item(inp, facts)], self._taxonomy_for(cas, p["task_config"])))
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
        est = asyncio.run(self._cascade.estimate(images, self._taxonomy_for(self._cascade, p["task_config"])))
        return {
            "counts": est.counts,
            "routed_to_vlm": est.routed_to_vlm,
            "n_embedded": len(images),
            "total": p["total"],
        }

    def _taxonomy_for(self, cas: Any, tc: dict[str, Any]) -> Any:
        """Taxonomy with the example photos of custom categories embedded as prototypes (03 §3.3)."""
        import asyncio

        tax = _taxonomy(tc)
        for cdef, raw in zip(tax.classes, tc.get("classes") or [], strict=False):
            refs = raw.get("examples") or []
            if not refs:
                continue
            imgs = []
            for k, ref in enumerate(refs):
                inp, _fail = self._prepare(
                    {"id": f"ex{k}", "root_alias": ref["root_alias"], "rel": ref["rel"], "attempt": 1}
                )
                self._facts.pop(f"ex{k}", None)
                if inp is not None:
                    imgs.append(inp)
            if imgs:
                cdef.examples = [v.tolist() for v in asyncio.run(cas.image_vectors(imgs))]
        return tax

    def _do_folder_preview(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        from .folder_preview import folder_preview

        return folder_preview(self.sb, p["root_alias"], p.get("path", ""), self.tz)  # type: ignore[arg-type]

    def _do_folder_thumb(self, p: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        from .folder_preview import folder_thumb

        return folder_thumb(self.sb, p["root_alias"], p["path"])

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
    return Taxonomy(
        classes=classes, rules=list(tc.get("rules") or []), vlm_max_candidates=int(tc.get("vlm_max_candidates") or 16)
    )


def _version() -> str:
    from opagent_core.version import VERSION

    return VERSION
