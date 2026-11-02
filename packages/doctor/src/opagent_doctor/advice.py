"""Doctor advice derived from runtime events (06 §3, §4.7; INF-OOM-003)."""

from __future__ import annotations

from typing import Any

from opagent_registry.recommend import Advice

FALLBACK_PACK = {
    "llamacpp-cuda13": "llamacpp-vulkan",
    "llamacpp-cuda12": "llamacpp-vulkan",
    "llamacpp-rocm": "llamacpp-vulkan",
}


def advise_runtime(provenance: list[dict[str, Any]]) -> Advice | None:
    """A GPU runtime pack that crashed twice → RUNTIME_CRASHED with "switch to Vulkan" as the primary fix."""
    crashed = [p for p in provenance if p.get("kind") == "runtime.crashed_twice"]
    if not crashed:
        return None
    last = crashed[-1]
    fixes: list[dict[str, Any]] = []
    alt = FALLBACK_PACK.get(str(last.get("pack")))
    if alt:
        fixes.append({"type": "choose_alternative", "pack": alt})
    fixes.append({"type": "open_logs"})
    return Advice("RUNTIME_CRASHED", {"variant": last.get("variant"), "pack": last.get("pack"), "n": 2}, fixes)
