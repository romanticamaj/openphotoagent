"""Tool-loop limits (10 §8.4): at most 6 tool calls, 60 s and 30 GPU-seconds per turn; small machines and models
without reliable tool calling use only the fast path."""

from __future__ import annotations

MAX_STEPS = 6
MAX_SECONDS = 60.0
MAX_GPU_SECONDS = 30.0


class Budget:
    def __init__(self, started: float = 0.0) -> None:
        self.started = started
        self.steps = 0
        self.gpu_s = 0.0

    def allow_tool(self, now: float) -> str | None:
        if self.steps >= MAX_STEPS:
            return "STEP_LIMIT"
        if now - self.started > MAX_SECONDS:
            return "TIME_LIMIT"
        if self.gpu_s > MAX_GPU_SECONDS:
            return "GPU_LIMIT"
        return None

    def record_tool(self, gpu_s: float) -> None:
        self.steps += 1
        self.gpu_s += gpu_s


def loop_enabled(*, vram_gb: float, model_params_b: float, tool_calling_ok: bool) -> bool:
    """The general loop needs ≥ 12 GB, an ≥ 7B model and a passed tool-calling probe (M0-S7); else fast path."""
    return tool_calling_ok and vram_gb >= 12 and model_params_b >= 7
