"""All-in-one stack for Playwright journeys (09 §2 layer E): embedded PG + API serving the web build + an in-process
Worker on FakeBackend, a recorded HF server holding pinned fixture weights, and a synthesized hardware profile.

    python -m opagent_testing.e2e_stack --port 47811 --base /tmp/opagent-e2e-x --web-dist apps/web/dist

Test-only control endpoints live under ``/__e2e`` and exist only in this harness (never in ``create_app``):

* ``POST /__e2e/reset`` ``{"hw": "rtx5070ti-win11", "photos": "trip", "net": "on", "library": false,
  "organize": null, "models": "ready"}`` — wipes the DB, the photo tree and the worker home, re-registers a
  worker with that hardware profile; ``library`` scans the photos root, ``organize`` runs a by-content job
  until its plan is ready;
  ``models: "none"`` starts without models and attaches each fake model once its download is installed (onboarding).
* ``GET /__e2e/tree`` — paths of the photo roots (also written to ``<web>/e2e/.run/tree.json``).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI

from .hfserver import HfFile, HfServer

REPO = Path(__file__).resolve().parents[4]
FIX = REPO / "fixtures" / "photos"
SIGLIP_REPO = "openphotoagent/siglip2-so400m-patch14-384-onnx"
QWEN_REPO = "Qwen/Qwen3-VL-8B-Instruct-GGUF"
REV = "0123456789abcdef0123456789abcdef01234567"
# small stand-ins for the pinned weights; sizes are what the recorded server really serves
SIGLIP = [("vision_model_int8.onnx", 600_000), ("text_model_int8.onnx", 400_000), ("tokenizer.json", 20_000)]
QWEN = [("Qwen3VL-8B-Instruct-Q4_K_M.gguf", 6_000_000), ("mmproj-Qwen3VL-8B-Instruct-F16.gguf", 1_000_000)]
DEAD = "http://127.0.0.1:9"  # discard port: connection refused → NET_UNREACHABLE


def _files(spec: list[tuple[str, int]], seed0: int) -> list[HfFile]:
    return [HfFile(p, n, seed=seed0 + i) for i, (p, n) in enumerate(spec)]


class Stack:
    def __init__(self, base: Path, port: int, web_dist: str | None, rate_bps: float) -> None:
        from opagent_db.embedded import EmbeddedPg
        from opagent_registry.loader import BUILTIN_DIR

        self.base, self.port = base, port
        self.home = base / "home"
        self.photos = base / "roots" / "photos"
        self.sorted = base / "roots" / "sorted"
        self.thumbs = self.home / "worker" / "thumbs"
        siglip, qwen = _files(SIGLIP, 0), _files(QWEN, 10)
        self.default_rate_bps = rate_bps
        self.hf = HfServer(SIGLIP_REPO, siglip, more={QWEN_REPO: qwen}, rate_bps=rate_bps).start()
        self.registry = base / "registry"
        if self.registry.exists():
            shutil.rmtree(self.registry)
        shutil.copytree(BUILTIN_DIR, self.registry)
        pins = [
            {
                "model": "siglip2-so400m-384",
                "variant": "onnx-int8",
                "revision": REV,
                "files": [{"path": f.path, "size": f.size, "sha256": f.sha256, "lfs_oid": f.sha256} for f in siglip],
            },
            {
                "model": "qwen3-vl-8b-instruct",
                "variant": "q4_k_m",
                "revision": REV,
                "files": [{"path": f.path, "size": f.size, "sha256": f.sha256, "lfs_oid": f.sha256} for f in qwen],
            },
        ]
        (self.registry / "pins.lock").write_text(json.dumps({"schema_version": 1, "pins": pins}))
        self.pg = EmbeddedPg(self.home / "pg")
        self.dsn = self.pg.start()
        from opagent_db.schema import create_all

        create_all(self.dsn)
        self.web_dist = web_dist
        self.worker: Any = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.app = self._app()

    # ---- app ---------------------------------------------------------------
    def _app(self) -> FastAPI:
        from opagent_api.app import create_app

        ctl = FastAPI()

        @ctl.post("/reset")
        def reset(body: dict[str, Any] | None = None) -> dict[str, Any]:
            return self.reset(**(body or {}))

        @ctl.get("/tree")
        def tree() -> dict[str, str]:
            return self.tree()

        @ctl.get("/ready")
        def ready() -> Any:
            from fastapi.responses import Response

            return {"ready": True} if self.worker is not None else Response(status_code=503)

        @ctl.get("/stats")
        def stats() -> dict[str, int]:
            w = self.worker
            vlm = len(w.backend.requests()) if w is not None and w.backend is not None else 0
            emb = len(w.embedder.requests()) if w is not None and w.embedder is not None else 0
            return {"vlm_requests": vlm, "embed_requests": emb}

        @ctl.post("/slow")
        def slow(body: dict[str, float]) -> dict[str, bool]:
            """Make a step observable: VLM latency per item, pause between file actions while applying."""
            if "vlm_latency_s" in body and self.worker.backend is not None:
                self.worker.backend.latency_s = float(body["vlm_latency_s"])
                # the worker reports its measured speed, as after a load benchmark (06 §5.9 basis=observed)
                self.worker.client.post(
                    "/worker-api/v1/observations",
                    json={
                        "worker_id": "local",
                        "kind": "items_per_s",
                        "key": "vlm",
                        "value": 1 / max(float(body["vlm_latency_s"]), 1e-3),
                    },
                )
            if "apply_throttle_s" in body:
                self.worker.apply_throttle_s = float(body["apply_throttle_s"])
            return {"ok": True}

        @ctl.post("/slow-downloads")
        def slow_downloads(body: dict[str, float]) -> dict[str, bool]:
            self.hf.rate_bps = float(body.get("rate_bps", 200_000))
            return {"ok": True}

        @ctl.post("/attach-models")
        def attach_models() -> dict[str, bool]:
            from opagent_inference.fake import FakeBackend

            self.worker.backend = self.worker.backend or FakeBackend(vlm_p=0.9, vectors="semantic")
            self.worker.embedder = self.worker.embedder or FakeBackend(scores="mixed", vectors="semantic")
            self.worker.register()
            return {"ok": True}

        @ctl.post("/restart")
        def restart() -> dict[str, bool]:
            """Close and reopen the app: the old worker process dies mid-work (its connection is cut), a new
            process with the same identity registers (new boot id) and recovers (OP-006)."""
            return self.restart()

        @ctl.post("/move-into-sorted")
        def move_into_sorted() -> dict[str, str]:
            """Photos in a writable root (sorted/inbox) so a move can be planned (E2E-B-005)."""
            inbox = self.sorted / "inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            for f in sorted((self.photos / "trip").glob("*.jpg")):
                shutil.copy2(f, inbox / f.name)
            return {"inbox": str(inbox)}

        @ctl.post("/dup-library")
        def dup_library() -> dict[str, str]:
            """Each trip photo at two places in a writable root (sorted/dups/a, sorted/dups/b), scanned: 20 exact
            duplicate groups to clean up (TRASH-005)."""
            dups = self.sorted / "dups"
            for sub in ("a", "b"):
                (dups / sub).mkdir(parents=True, exist_ok=True)
                for f in sorted((self.photos / "trip").glob("*.jpg")):
                    shutil.copy2(f, dups / sub / f.name)
            client = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=30)
            src = client.post("/api/v1/sources", json={"root_alias": "sorted", "sub_path": "dups"}).json()["id"]
            client.post(f"/api/v1/sources/{src}/scan").raise_for_status()
            self._wait(client, lambda: self._scan_done(src), 60)
            return {"dups": str(dups), "source_id": src}

        @ctl.post("/big-move-plan")
        def big_move_plan(body: dict[str, int]) -> dict[str, str]:
            return self._big_move_plan(int(body.get("n", 10_001)))

        @ctl.post("/drop-embeddings")
        def drop_embeddings(body: dict[str, float]) -> dict[str, int]:
            """Index partly built (E2E-S-003): keep only a fraction of the image vectors."""
            from opagent_db.schema import engine_for
            from sqlalchemy import text

            eng = engine_for(self.dsn)
            with eng.begin() as c:
                total = c.execute(text("SELECT count(*) FROM search_doc")).scalar_one()
                keep = int(total * float(body.get("keep", 0.5)))
                c.execute(
                    text(
                        "DELETE FROM asset_embedding WHERE asset_id NOT IN "
                        "(SELECT asset_id FROM asset_embedding ORDER BY asset_id LIMIT :k)"
                    ),
                    {"k": keep},
                )
                have = c.execute(text("SELECT count(*) FROM asset_embedding")).scalar_one()
            eng.dispose()
            return {"searchable": int(have), "total": int(total)}

        @ctl.post("/bulk-library")
        def bulk_library(body: dict[str, Any]) -> dict[str, int]:
            """Thousands of indexed photos sharing a tag (E2E-SEL-001): rows only, no files or thumbnails."""
            from opagent_db.schema import engine_for
            from sqlalchemy import text

            n, tag = int(body.get("n", 1000)), str(body.get("tag", "beach"))
            th = body.get("thumbhash")  # perf cases: every tile gets a real placeholder (VIEW-003)
            eng = engine_for(self.dsn)
            with eng.begin() as c:
                c.execute(
                    text(
                        "INSERT INTO asset(id, workspace_id, content_hash, size_bytes, taken_at, local_at, width, "
                        "height, thumbhash, exif) SELECT 'bulk'||g, 'default', 'bulkhash'||g, 1, "
                        "timestamptz '2022-01-01Z' + g * interval '1 hour', timestamp '2022-01-01' + g * interval "
                        "'1 hour', CASE WHEN g % 3 = 0 THEN 3000 ELSE 4000 END, "
                        "CASE WHEN g % 3 = 0 THEN 4000 ELSE 3000 END, "
                        ":th, '{}' FROM generate_series(1, :n) g"
                    ),
                    {"n": n, "th": th},
                )
                c.execute(
                    text(
                        "INSERT INTO search_doc(asset_id, workspace_id, caption, tags, tag_labels, category, taken_at, "
                        "taken_year, scope_keys, body, tsv) SELECT 'bulk'||g, 'default', 'a ' || :t || ' photo', "
                        "ARRAY[:t], to_jsonb(ARRAY[:t]), 'travel', "
                        "timestamptz '2022-01-01Z' + g * interval '1 hour', 2022, "
                        "ARRAY[]::text[], :t, setweight(array_to_tsvector(ARRAY[:t]), 'A') "
                        "FROM generate_series(1, :n) g"
                    ),
                    {"n": n, "t": tag},
                )
            eng.dispose()
            return {"inserted": n}

        @ctl.get("/client-config")
        def client_config(client: str) -> dict[str, Any]:
            """Where the connect wizard will write (a sandboxed home with an existing config, MCP-PAT-002)."""
            from opagent_api import mcp_clients

            return {"path": str(mcp_clients.config_path(client))}

        @ctl.post("/mcp-apply-grant")
        def mcp_apply_grant(body: dict[str, Any]) -> dict[str, Any]:
            """organize:apply is never issued by the UI (CLAUDE.md §3); a v0.5 OAuth grant could hold it."""
            from opagent_db.schema import engine_for, mcp_token
            from sqlalchemy import func, update

            eng = engine_for(self.dsn)
            with eng.begin() as c:
                c.execute(
                    update(mcp_token)
                    .where(mcp_token.c.id == body["token_id"])
                    .values(scopes=func.array_append(mcp_token.c.scopes, "organize:apply"))
                )
            eng.dispose()
            return {"ok": True}

        @ctl.get("/hf")
        def hf() -> dict[str, Any]:
            return {"url": self.hf.url, "gets": len(self.hf.gets())}

        from starlette.routing import Mount

        app = create_app(
            dsn=self.dsn,
            static_dir=self.web_dist,
            registry_dir=self.registry,
            thumbs_dir=self.thumbs,
            home=self.home / "data",
        )
        app.router.routes.insert(0, Mount("/__e2e", app=ctl))  # before the SPA catch-all mount
        return app

    def tree(self) -> dict[str, str]:
        return {"base": str(self.base), "photos": str(self.photos), "sorted": str(self.sorted)}

    # ---- scenario ----------------------------------------------------------
    def _stop_worker(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(15)
        self._stop = threading.Event()
        self._thread = None

    def _wipe_db(self) -> None:
        from opagent_db.schema import engine_for, metadata
        from sqlalchemy import text

        from sqlalchemy.exc import OperationalError

        eng = engine_for(self.dsn)
        names = ", ".join(f'"{t.name}"' for t in metadata.sorted_tables)
        for attempt in range(5):  # a request left over from the previous test may hold a lock: PG picks one victim
            try:
                with eng.begin() as c:
                    c.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
                break
            except OperationalError as e:
                if "deadlock" not in str(e) or attempt == 4:
                    raise
                time.sleep(0.2 * (attempt + 1))  # noqa: TID251 - test harness only
        eng.dispose()
        self.app.state.opa.ensure_workspace()

    def _client_configs(self) -> None:
        """AI client config files live in a sandboxed home; Claude Desktop already has another server configured."""
        from opagent_api import mcp_clients

        home = self.base / "client-home"
        if home.exists():
            shutil.rmtree(home)
        os.environ["OPAGENT_CLIENT_CONFIG_HOME"] = str(home)
        os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring"  # never the developer's keyring
        os.environ["OPAGENT_CLI_PATH"] = str(self.base / "app" / "bin" / "opagent")
        p = mcp_clients.config_path("claude-desktop")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"mcpServers": {"other": {"command": "/usr/bin/other-server"}}}, indent=2) + "\n")
        (self.home / "data" / "secrets").mkdir(parents=True, exist_ok=True)
        shutil.rmtree(self.home / "data" / "secrets")

    def _photos(self, which: str) -> None:
        for d in (self.photos, self.sorted):
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True)
        (self.photos / "other").mkdir()
        if which in ("trip", "library"):
            (self.photos / "trip").mkdir()
            for f in sorted((FIX / "basic").glob("*.jpg")):
                shutil.copy2(f, self.photos / "trip" / f.name)
        if which == "library":
            for f in sorted((FIX / "no-exif").glob("*.jpg")):
                shutil.copy2(f, self.photos / "other" / f.name)
        run = Path(self.web_dist).parent / "e2e" / ".run" if self.web_dist else self.base / ".run"
        run.mkdir(parents=True, exist_ok=True)
        (run / "tree.json").write_text(json.dumps(self.tree()))

    def reset(
        self,
        hw: str = "rtx5070ti-win11",
        photos: str = "trip",
        net: str = "on",
        library: bool = False,
        organize: str | None = None,
        vlm_p: float = 0.9,
        models: str = "ready",
    ) -> dict[str, Any]:
        from opagent_inference.fake import FakeBackend
        from opagent_worker.agent import WorkerAgent

        self._stop_worker()
        self._wipe_db()
        self.hf.rate_bps = self.default_rate_bps  # a previous test may have slowed the model host (slow-downloads)
        self._photos(photos)
        self._client_configs()
        wh = self.home / "worker"
        if wh.exists():
            shutil.rmtree(wh)
        client = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=30)
        roots = [
            {"alias": "photos", "path": str(self.photos), "mode": "ro"},
            {"alias": "sorted", "path": str(self.sorted), "mode": "rw"},
        ]
        self.worker = WorkerAgent(
            client,
            roots=roots,
            home=wh,
            backend=FakeBackend(vlm_p=vlm_p, vectors="semantic") if models == "ready" else None,
            embedder=FakeBackend(scores="mixed", vectors="semantic") if models == "ready" else None,
            worker_id="local",
            hw_profile=hw,
            models_root=wh / "models",
            download_sources=[self.hf.url if net == "on" else DEAD],
        )
        # stand-in models are a few MB: do not make the suite depend on the host's spare gigabytes (06 §2.5 reserve)
        self.worker.download_reserve_floor = 0
        self.worker.register()
        self._thread = threading.Thread(target=self.worker.run_forever, args=(self._stop,), daemon=True)
        self._thread.start()
        if models == "none":  # onboarding: the fake models "load" once their downloads are installed
            threading.Thread(target=self._attach_on_install, args=(self.worker, self._stop, vlm_p), daemon=True).start()
        out: dict[str, Any] = {"worker_id": "local", **self.tree()}
        if library or organize:
            src = client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
            r = client.post(f"/api/v1/sources/{src}/scan")
            r.raise_for_status()
            self._wait(client, lambda: client.get("/api/v1/timeline/buckets").json()["buckets"], 60)
            self._wait(client, lambda: self._scan_done(src), 60)
            out["source_id"] = src
            if organize:
                j = client.post(
                    "/api/v1/jobs",
                    json={
                        "kind": "organize",
                        "preset": organize,
                        "source_id": src,
                        "target": {"root_alias": "sorted", "sub_path": ""},
                    },
                ).json()
                self._wait(
                    client,
                    lambda: (
                        client.get(f"/api/v1/jobs/{j['id']}").json()["status"] in ("awaiting_approval", "completed")
                    ),
                    120,
                )
                out["job_id"] = j["id"]
        return out

    def _attach_on_install(self, w: Any, stop: threading.Event, vlm_p: float) -> None:
        from opagent_db.schema import engine_for, installation
        from opagent_inference.fake import FakeBackend
        from sqlalchemy import select

        eng = engine_for(self.dsn)
        while not stop.wait(0.2):
            with eng.connect() as c:
                keys = {k for (k,) in c.execute(select(installation.c.key).where(installation.c.status == "installed"))}
            changed = False
            if w.embedder is None and any(k.startswith("siglip2") for k in keys):
                w.embedder, changed = FakeBackend(scores="mixed", vectors="semantic"), True
            if w.backend is None and any(k.startswith("qwen3-vl") for k in keys):
                w.backend, changed = FakeBackend(vlm_p=vlm_p, vectors="semantic"), True
            if changed:
                w.register()
            if w.embedder is not None and w.backend is not None:
                return

    def restart(self) -> dict[str, bool]:
        from opagent_worker.agent import WorkerAgent

        class _Dead:
            def __getattr__(self, _name: str) -> Any:
                def fail(*_a: Any, **_k: Any) -> Any:
                    raise httpx.ConnectError("process gone")

                return fail

        old = self.worker
        old.client = _Dead()
        self._stop.set()
        self._stop = threading.Event()
        client = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=30)
        self.worker = WorkerAgent(
            client,
            roots=[{"alias": r.alias, "path": str(r.path), "mode": r.mode} for r in old.roots],
            home=old.home,
            backend=old.backend,
            embedder=old.embedder,
            worker_id=old.worker_id,
            hw_profile=old.hw_profile,
            models_root=old.models_root,
            download_sources=old.download_sources,
        )
        # stand-in models are a few MB: do not make the suite depend on the host's spare gigabytes (06 §2.5 reserve)
        self.worker.download_reserve_floor = 0
        self.worker.register()
        self._thread = threading.Thread(target=self.worker.run_forever, args=(self._stop,), daemon=True)
        self._thread.start()
        return {"ok": True}

    def _big_move_plan(self, n: int) -> dict[str, str]:
        """A draft plan with ``n`` move actions (never applied): the > 10,000-move confirmation (E2E-B-005)."""
        import uuid

        from opagent_db.schema import asset, engine_for, job, output_action, output_plan, source
        from sqlalchemy import insert

        eng = engine_for(self.dsn)
        jid, pid, sid = (uuid.uuid4().hex for _ in range(3))
        with eng.begin() as c:
            c.execute(
                insert(source).values(
                    id=sid,
                    workspace_id="default",
                    worker_id="local",
                    root_alias="sorted",
                    sub_path="big",
                    include=["**/*.jpg"],
                    exclude=[],
                )
            )
            c.execute(
                insert(job).values(
                    id=jid,
                    workspace_id="default",
                    kind="organize",
                    status="awaiting_approval",
                    mode="dry_run",
                    plan_id=pid,
                    params={
                        "source_id": sid,
                        "preset": "by-date",
                        "action": "move",
                        "target": {"root_alias": "sorted", "sub_path": ""},
                    },
                )
            )
            c.execute(
                insert(output_plan).values(
                    id=pid, job_id=jid, status="draft", summary={"copy": 0, "move": n, "folders": 1, "total": n}
                )
            )
            c.execute(
                insert(asset),
                [
                    {"id": f"big{i}", "workspace_id": "default", "content_hash": f"bighash{i}", "size_bytes": 1}
                    for i in range(n)
                ],
            )
            c.execute(
                insert(output_action),
                [
                    {
                        "id": f"{pid}-{i}",
                        "plan_id": pid,
                        "seq": i,
                        "asset_id": f"big{i}",
                        "action": "move",
                        "src_alias": "sorted",
                        "src_rel": f"big/{i}.jpg",
                        "dst_alias": "sorted",
                        "dst_rel": f"2024/{i}.jpg",
                        "src_hash": f"bighash{i}",
                    }
                    for i in range(n)
                ],
            )
        eng.dispose()
        return {"plan_id": pid, "job_id": jid}

    def _scan_done(self, src: str) -> bool:
        from opagent_db.schema import engine_for, worker_task
        from sqlalchemy import select

        eng = engine_for(self.dsn)
        with eng.connect() as c:
            st = c.execute(
                select(worker_task.c.status).where(
                    worker_task.c.kind == "scan", worker_task.c.payload["source_id"].astext == src
                )
            ).scalar()
        return st == "done"

    @staticmethod
    def _wait(client: httpx.Client, cond: Any, timeout: float) -> None:
        t0 = time.monotonic()  # noqa: TID251 - test harness wall clock
        while time.monotonic() - t0 < timeout:  # noqa: TID251
            if cond():
                return
            time.sleep(0.1)  # noqa: TID251
        raise TimeoutError("e2e stack: condition not reached")

    def close(self) -> None:
        self._stop_worker()
        self.hf.stop()
        self.pg.stop()


def reclaim(base: Path) -> None:
    """Start from an empty base: a previous run killed with SIGKILL may have left its postmaster and files."""
    from opagent_db.embedded import find_pg_bin

    for pid_file in base.glob("**/postmaster.pid"):
        data = pid_file.parent
        prefix = ["runuser", "-u", "postgres", "--"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []
        subprocess.run(
            [*prefix, str(find_pg_bin() / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
            capture_output=True,
            check=False,
        )
    if base.exists():
        for child in base.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    base.mkdir(parents=True, exist_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=47811)
    ap.add_argument("--base", required=True)
    ap.add_argument("--web-dist")
    ap.add_argument("--rate-bps", type=float, default=1_500_000)
    a = ap.parse_args()
    reclaim(Path(a.base))
    st = Stack(Path(a.base), a.port, a.web_dist, a.rate_bps)
    server = uvicorn.Server(uvicorn.Config(st.app, host="127.0.0.1", port=a.port, log_level="warning"))

    def first_reset() -> None:
        time.sleep(1.0)  # noqa: TID251 - let uvicorn bind before the worker registers over HTTP
        st.reset()

    def orphan_watch() -> None:
        # Playwright may SIGKILL the node launcher; then nobody would stop PG or delete the base directory
        parent = os.getppid()
        while not server.should_exit:
            if os.getppid() != parent:
                server.should_exit = True
                return
            time.sleep(0.5)  # noqa: TID251

    threading.Thread(target=first_reset, daemon=True).start()
    threading.Thread(target=orphan_watch, daemon=True).start()
    try:
        server.run()
    finally:
        st.close()
        shutil.rmtree(a.base, ignore_errors=True)  # PG cluster + photo trees: never left in the temp dir


if __name__ == "__main__":
    main()
