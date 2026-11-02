"""Managed OpenAI-compatible servers beyond llama.cpp (08 §3.1, §4.2; v0.4 advanced packs).

* vLLM (Linux only): ``vllm serve``-style flags, structured output through ``guided_json``.
* MLX (macOS arm64 only): an mlx-vlm OpenAI-compatible server; JSON mode, confidence is self-reported.

Both reuse the llama.cpp adapter's process supervision (spawn, readiness, crash detection, restart with half the
parallelism). A wrong platform is refused before anything is started.
"""

from __future__ import annotations

import sys
from typing import Any

from opagent_core.operation import OperationReporter
from opagent_inference.types import LoadOptions, UnsupportedPlatform

from .llamacpp import LlamaCppServerAdapter


def _here() -> str:
    import platform as pf

    return f"{sys.platform}-{pf.machine().lower()}"


class _Managed(LlamaCppServerAdapter):
    platforms: tuple[str, ...] = ()

    def __init__(self, command: list[str], model: str, *, platform: str | None = None, **kw: Any) -> None:
        super().__init__(command, model, None, **kw)
        self.model = model
        self.platform = platform or _here()

    def supported(self) -> bool:
        return any(self.platform.startswith(p) for p in self.platforms)

    async def load(self, opts: LoadOptions, reporter: OperationReporter | None = None) -> None:
        if not self.supported():
            raise UnsupportedPlatform(f"{self.adapter_key} does not run on {self.platform}")
        await super().load(opts, reporter)


class VllmServerAdapter(_Managed):
    adapter_key = "vllm"
    platforms = ("linux",)

    def __init__(self, command: list[str], model: str, **kw: Any) -> None:
        super().__init__(command, model, **kw)
        self.structured_output = "guided_json"

    def args(self) -> list[str]:
        a = [*self.command, "--model", self.model_file]
        a += [
            "--host", "127.0.0.1", "--port", str(self.port),
            "--max-num-seqs", str(self.parallel), "--max-model-len", str(self.ctx_per_slot),
            "--limit-mm-per-prompt", '{"image": 1}',
        ]  # fmt: skip
        return a + self.extra_args


class MlxServerAdapter(_Managed):
    adapter_key = "mlx"
    platforms = ("darwin-arm64",)

    def __init__(self, command: list[str], model: str, **kw: Any) -> None:
        super().__init__(command, model, **kw)
        self.structured_output = "json_object"  # json_schema support in MLX servers is unverified (08 §3.1)

    def args(self) -> list[str]:
        a = [*self.command, "--model", self.model_file, "--host", "127.0.0.1", "--port", str(self.port)]
        return a + self.extra_args


class TorchServerAdapter(_Managed):
    """The torch sidecar (``opagent_adapters.torch_server``) for JoyCaption / Florence-2. Remote code is passed only
    when the caller confirmed it (the worker checks the stored confirmation against the files' hashes)."""

    adapter_key = "torch"
    platforms = ("linux", "win32", "darwin-arm64")

    def __init__(self, command: list[str], model: str, *, trust_remote_code: bool = False, **kw: Any) -> None:
        super().__init__(command, model, **kw)
        self.trust_remote_code = trust_remote_code

    def args(self) -> list[str]:
        a = [*self.command, "--model", self.model_file, "--host", "127.0.0.1", "--port", str(self.port)]
        if self.trust_remote_code:
            a.append("--trust-remote-code")
        return a + self.extra_args
