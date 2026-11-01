"""llama.cpp ``llama-server`` adapter (03 §2.4, §4.3; 08 §3).

Manages the server subprocess (spawn, readiness with time-modelled progress, restart with ``--parallel`` halved
after a crash) and talks to it through the OpenAI-compatible API. The binary comes from the active runtime pack;
tests point ``command`` at FakeRuntime.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
from typing import Any

import httpx
from opagent_core.operation import NullReporter, OperationReporter
from opagent_inference.types import HealthStatus, InferenceError, LoadOptions

from .openai_compat import OpenAICompatAdapter


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class LlamaCppServerAdapter(OpenAICompatAdapter):
    adapter_key = "llama.cpp"

    def __init__(
        self,
        command: list[str],
        model_file: str,
        mmproj_file: str | None = None,
        *,
        parallel: int = 4,
        ctx_per_slot: int = 4096,
        n_gpu_layers: int = 99,
        extra_args: list[str] | None = None,
        expected_load_s: float = 15.0,
        load_timeout_s: float = 300.0,
        fingerprint: str | None = None,
        categories: list[str] | None = None,
        has_gpu: bool = False,
        vram_probe: Any = None,
    ) -> None:
        self.port = free_port()
        super().__init__(
            f"http://127.0.0.1:{self.port}",
            model=os.path.basename(model_file),
            fingerprint=fingerprint,
            max_concurrency=parallel,
            categories=categories,
            has_gpu=has_gpu,
        )
        self.command = list(command)
        self.model_file, self.mmproj_file = model_file, mmproj_file
        self.parallel = parallel
        self.ctx_per_slot = ctx_per_slot
        self.n_gpu_layers = n_gpu_layers
        self.extra_args = list(extra_args or [])
        self.expected_load_s = expected_load_s
        self.load_timeout_s = load_timeout_s
        self.vram_probe = vram_probe
        self.proc: subprocess.Popen[bytes] | None = None
        self.crashes = 0
        self.restarts = 0
        self.events: list[dict[str, Any]] = []

    def args(self) -> list[str]:
        a = [*self.command, "-m", self.model_file]
        if self.mmproj_file:
            a += ["--mmproj", self.mmproj_file]
        a += [
            "--host", "127.0.0.1", "--port", str(self.port),
            "--parallel", str(self.parallel), "-c", str(self.parallel * self.ctx_per_slot),
            "-ngl", str(self.n_gpu_layers), "--jinja",
        ]  # fmt: skip
        return a + self.extra_args

    async def load(self, opts: LoadOptions, reporter: OperationReporter | None = None) -> None:
        rep = reporter or NullReporter()
        if self.loaded and self.proc and self.proc.poll() is None:
            return
        rep.start("model.load", 100, "steps")
        rep.phase("spawn")
        self.proc = subprocess.Popen(self.args(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rep.phase("load_weights")
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        async with httpx.AsyncClient(timeout=2.0) as c:
            while True:
                if self.proc.poll() is not None:
                    rep.finish("failed")
                    raise InferenceError("RUNTIME", True, f"llama-server exited with {self.proc.returncode}")
                try:
                    if (await c.get(self.base_url + "/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                elapsed = loop.time() - t0
                if elapsed > self.load_timeout_s:
                    self.close()
                    rep.finish("failed")
                    raise InferenceError("TIMEOUT", True, "llama-server did not become ready")
                # llama-server gives no load percentage: model it by time, capped at 95 % until ready (INF-CONF-013)
                rep.progress(min(95, int(100 * elapsed / max(self.expected_load_s, 0.1))), 100)
                await asyncio.sleep(0.05)
        self.loaded = True
        rep.progress(100, 100)
        rep.finish("succeeded")

    def _on_connection_lost(self) -> None:
        if self.proc is not None:
            try:
                self.proc.wait(timeout=2)  # a crashed server is reaped within moments of the reset
            except subprocess.TimeoutExpired:
                return
        if self.proc is not None and self.proc.poll() is not None:
            self.crashes += 1
            self.loaded = False
            self.events.append(
                {"kind": "runtime.crashed", "returncode": self.proc.returncode, "parallel": self.parallel}
            )

    async def restart(self, halve_parallel: bool = True) -> None:
        """After a crash: restart once with ``--parallel`` halved (03 §4.3)."""
        self.close()
        if halve_parallel:
            self.parallel = max(1, self.parallel // 2)
            self.max_concurrency = self.parallel
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.restarts += 1
        self.events.append({"kind": "runtime.restarted", "parallel": self.parallel})
        await self.load(LoadOptions())

    async def unload(self) -> None:
        self.close()

    def close(self) -> None:
        self.loaded = False
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            p.send_signal(signal.SIGTERM)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()

    async def health(self) -> HealthStatus:
        alive = self.proc is not None and self.proc.poll() is None
        ok = alive and self.loaded and await self._ping()
        vram = int(self.vram_probe(self.proc.pid)) if (ok and self.vram_probe and self.proc) else 0
        return HealthStatus(ok=ok, vram_used_mb=vram)
