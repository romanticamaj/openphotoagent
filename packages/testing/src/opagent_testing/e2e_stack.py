"""All-in-one stack for Playwright journeys (09 §2 layer E): embedded PG + API serving the web build + an in-process
Worker on FakeBackend, a recorded HF server holding pinned fixture weights, and a synthesized hardware profile.

    python -m opagent_testing.e2e_stack --port 47811 --base /tmp/opagent-e2e-x --web-dist apps/web/dist

Test-only control endpoints live under ``/__e2e`` and exist only in this harness (never in ``create_app``):

* ``POST /__e2e/reset`` ``{"hw": "rtx5070ti-win11", "photos": "trip", "net": "on", "library": false,
  "organize": null}`` — wipes the DB, the photo tree and the worker home, re-registers a worker with that hardware
  profile; ``library`` scans the photos root, ``organize`` runs a by-content job until its plan is ready.
* ``GET /__e2e/tree`` — paths of the photo roots (also written to ``<web>/e2e/.run/tree.json``).
"""

from __future__ import annotations

import argparse
import json
import shutil
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
QWEN = [("Qwen3VL-8B-Instruct-Q4_K_M.gguf", 3_000_000), ("mmproj-Qwen3VL-8B-Instruct-F16.gguf", 1_000_000)]
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

        @ctl.get("/hf")
        def hf() -> dict[str, Any]:
            return {"url": self.hf.url, "gets": len(self.hf.gets())}

        from starlette.routing import Mount

        app = create_app(dsn=self.dsn, static_dir=self.web_dist, registry_dir=self.registry, thumbs_dir=self.thumbs)
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

        eng = engine_for(self.dsn)
        with eng.begin() as c:
            names = ", ".join(f'"{t.name}"' for t in metadata.sorted_tables)
            c.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        eng.dispose()
        self.app.state.opa.ensure_workspace()

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
    ) -> dict[str, Any]:
        from opagent_inference.fake import FakeBackend
        from opagent_worker.agent import WorkerAgent

        self._stop_worker()
        self._wipe_db()
        self._photos(photos)
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
            backend=FakeBackend(vlm_p=vlm_p, vectors="semantic"),
            embedder=FakeBackend(scores="mixed", vectors="semantic"),
            worker_id="local",
            hw_profile=hw,
            models_root=wh / "models",
            download_sources=[self.hf.url if net == "on" else DEAD],
        )
        self.worker.register()
        self._thread = threading.Thread(target=self.worker.run_forever, args=(self._stop,), daemon=True)
        self._thread.start()
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=47811)
    ap.add_argument("--base", required=True)
    ap.add_argument("--web-dist")
    ap.add_argument("--rate-bps", type=float, default=1_500_000)
    a = ap.parse_args()
    st = Stack(Path(a.base), a.port, a.web_dist, a.rate_bps)
    server = uvicorn.Server(uvicorn.Config(st.app, host="127.0.0.1", port=a.port, log_level="warning"))
    def first_reset() -> None:
        time.sleep(1.0)  # noqa: TID251 - let uvicorn bind before the worker registers over HTTP
        st.reset()

    threading.Thread(target=first_reset, daemon=True).start()
    try:
        server.run()
    finally:
        st.close()


if __name__ == "__main__":
    main()
