"""One recommended combination per machine (08 §3.2–3.3, 06 §4.4; ADR-0002).

``recommend`` is a pure function of (catalog, pin states, hardware summary). It never recommends an unpinned
variant, always yields exactly one combination for supported platforms, and always keeps a CPU path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from opagent_core.hw import HwSummary

from .compat import pack_failure
from .loader import Registry

SUPPORTED = {("windows", "x86_64"), ("macos", "arm64"), ("linux", "x86_64")}
DOCS_PLATFORMS = "https://github.com/romanticamaj/openphotoagent#supported-platforms"
NVIDIA_DRIVER_URL = "https://www.nvidia.com/Download/index.aspx"
DEFAULT_BANDWIDTH_BPS = 20e6  # cold-start download prior until perf_observation(kind=bandwidth) exists 【估算】
CUDA_PACKS = ("llamacpp-cuda13", "llamacpp-cuda12")

Q8B5, Q8B4, Q8B8 = "qwen3-vl-8b-instruct@q5_k_m", "qwen3-vl-8b-instruct@q4_k_m", "qwen3-vl-8b-instruct@q8_0"
Q4B4, Q2B4 = "qwen3-vl-4b-instruct@q4_k_m", "qwen3-vl-2b-instruct@q4_k_m"
EMBED_GPU, EMBED_CPU = "siglip2-so400m-384@onnx-fp16", "siglip2-so400m-384@onnx-int8"

# (min usable VRAM GB = total × 0.92, preference list) — 08 §3.3
GPU_TIERS: list[tuple[float, list[str]]] = [
    (14.0, [Q8B5, Q8B4, Q4B4]),
    (9.0, [Q8B4, Q4B4]),
    (5.0, [Q4B4, Q2B4]),
    (3.0, [Q2B4]),
]
MAC_TIERS: list[tuple[float, list[str]]] = [
    (48, [Q8B8, Q8B5, Q8B4]),
    (24, [Q8B5, Q8B4, Q4B4]),
    (16, [Q4B4, Q2B4]),
    (0, [Q2B4]),
]


@dataclass
class Advice:
    code: str
    params: dict[str, Any] = field(default_factory=dict)
    fixes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Alternative:
    vlm: str | None
    mode: str  # sequential | cpu
    warnings: list[Advice] = field(default_factory=list)


@dataclass
class Recommendation:
    packs: list[str] = field(default_factory=list)
    suggested_packs: list[str] = field(default_factory=list)
    vlm: str | None = None
    embed: str | None = None
    ort_ep: str = "cpu"
    fast_mode: bool = False
    suggest_fast_mode: bool = False
    warnings: list[Advice] = field(default_factory=list)
    alternatives: list[Alternative] = field(default_factory=list)
    not_pinned: list[str] = field(default_factory=list)
    download_bytes: int = 0
    download_minutes: float = 0.0
    download_estimated: bool = False
    cpu_path: str = "llamacpp-cpu"
    unsupported: bool = False
    unsupported_reason: str | None = None
    docs_url: str | None = None


def default_combo_variants(reg: Registry) -> list[str]:
    """Every variant that can be the default of some tier (INF-REG-006 licence gate)."""
    keys = {k for _, lst in GPU_TIERS + MAC_TIERS for k in lst} | {EMBED_GPU, EMBED_CPU}
    return sorted(k for k in keys if k.split("@")[0] in reg.models)


def _pinned(key: str, reg: Registry, pin_states: dict[str, str] | None) -> bool:
    if pin_states is not None:
        return pin_states.get(key) == "pinned"
    mid, _, vid = key.partition("@")
    m = reg.models.get(mid)
    return bool(m and m.status == "valid" and m.pin_state.get(vid) == "pinned")


def _first_pinned(cands: list[str], reg: Registry, pins: dict[str, str] | None, r: Recommendation) -> str | None:
    for k in cands:
        if k.split("@")[0] not in reg.models:
            continue
        if _pinned(k, reg, pins):
            return k
        r.not_pinned.append(k)
    return None


def _llama_pack(reg: Registry, hw: HwSummary, r: Recommendation) -> None:
    gpu = hw.primary_gpu
    if hw.os == "macos" or gpu is None:
        return  # Metal / CPU builds are built in
    if gpu.vendor != "nvidia":
        r.packs.append("llamacpp-vulkan")
        return
    for pid in sorted(hw.pack_ids & set(CUDA_PACKS)):
        if pack_failure(reg.runtimes[pid], hw, reg.gpu_classes) == "GPU_ARCH_UNSUPPORTED":
            r.warnings.append(
                Advice(
                    "GPU_ARCH_UNSUPPORTED",
                    {"pack": pid, "gpu": gpu.name},
                    [
                        {"type": "download", "pack": "llamacpp-cuda13"},
                        {"type": "choose_alternative", "pack": "llamacpp-vulkan"},
                    ],
                )
            )
    failures = {pid: pack_failure(reg.runtimes[pid], hw, reg.gpu_classes) for pid in CUDA_PACKS if pid in reg.runtimes}
    ok = [pid for pid, f in failures.items() if f is None]
    if ok:
        r.packs.append(ok[0])
        return
    if "GPU_DRIVER_TOO_OLD" in failures.values():
        need = min(
            (reg.runtimes[p].requires.nvidia_driver_min or "") for p in failures if failures[p] == "GPU_DRIVER_TOO_OLD"
        )
        r.warnings.append(
            Advice(
                "GPU_DRIVER_TOO_OLD",
                {"have": gpu.driver, "need": need},
                [
                    {"type": "open_url", "url": NVIDIA_DRIVER_URL},
                    {"type": "choose_alternative", "pack": "llamacpp-vulkan"},
                ],
            )
        )
    r.packs.append("llamacpp-vulkan")


def recommend(reg: Registry, hw: HwSummary, pin_states: dict[str, str] | None = None) -> Recommendation:
    r = Recommendation()
    if (hw.os, hw.arch) not in SUPPORTED:
        r.unsupported, r.unsupported_reason, r.docs_url = True, "RUNTIME_UNSUPPORTED_PLATFORM", DOCS_PLATFORMS
        r.cpu_path = "none"
        return r
    gpu = hw.primary_gpu
    if hw.os == "macos":
        gpu = gpu if gpu and gpu.vendor == "apple" else None
    elif gpu and gpu.vendor == "apple":
        gpu = None
    _llama_pack(reg, hw, r)

    # ONNX Runtime execution provider for the small models (08 §3.2 principle 3)
    if hw.os == "windows":
        r.ort_ep = "directml" if gpu else "cpu"
    elif hw.os == "macos":
        r.ort_ep = "coreml" if gpu else "cpu"
    else:
        r.ort_ep = "cpu"
        if gpu and gpu.vendor == "nvidia":
            r.suggested_packs.append("ort-cuda")

    # VLM tier
    if hw.os == "macos" and gpu is not None:
        tiers = MAC_TIERS
        size = hw.ram_gb or gpu.vram_total_gb
        r.suggest_fast_mode = size <= 8
    else:
        tiers = GPU_TIERS
        size = gpu.vram_total_gb * 0.92 if gpu else 0.0
    cands = next((lst for floor, lst in tiers if size >= floor), [])
    if gpu is None or not cands:
        r.fast_mode = True
        code = "GPU_DOCKER_NO_PASSTHROUGH" if hw.in_container else "GPU_NOT_FOUND"
        r.warnings.append(Advice(code, {}, [{"type": "open_docs", "topic": "gpu"}]))
        r.packs = [p for p in r.packs if p != "llamacpp-vulkan"] if gpu is None else r.packs
        cpu_vlm = _first_pinned([Q2B4], reg, pin_states, Recommendation())
        if cpu_vlm:
            r.alternatives.append(Alternative(cpu_vlm, "cpu", [Advice("PERF_CPU_ONLY_SLOW", {"model": cpu_vlm})]))
    else:
        r.vlm = _first_pinned(cands, reg, pin_states, r)
        if r.vlm is None:
            r.fast_mode = True
        if tiers is GPU_TIERS and 5.0 <= size < 9.0 and _pinned(Q8B4, reg, pin_states):
            r.alternatives.append(Alternative(Q8B4, "sequential", [Advice("VRAM_SEQUENTIAL", {"model": Q8B4})]))

    r.embed = _first_pinned([EMBED_GPU, EMBED_CPU] if r.ort_ep != "cpu" else [EMBED_CPU, EMBED_GPU], reg, pin_states, r)
    if r.embed is None:
        r.embed = EMBED_CPU  # still the one combination; download preflight reports MODEL_NOT_PINNED
    r.cpu_path = "siglip-fast" if (r.fast_mode or hw.os == "macos") else "llamacpp-cpu"

    # download size and time (exact when pinned, else estimated from the catalog)
    total, estimated = 0, False
    for pid in r.packs:
        total += reg.runtimes[pid].size_est or 0
        estimated = True
    for key in filter(None, [r.vlm, r.embed]):
        mid, _, vid = key.partition("@")
        m = reg.models[mid]
        files = m.pinned_files(vid)
        exact = sum(f.size or 0 for f in files)
        if exact and all(f.size for f in files):
            total += exact
        else:
            vr = m.spec.variant(vid).vram_gb
            total += int((vr.weights + vr.mmproj) * 1e9)
            estimated = True
    r.download_bytes, r.download_estimated = total, estimated
    r.download_minutes = round(total / DEFAULT_BANDWIDTH_BPS / 60, 1)
    return r


DOCS_DOCKER = "https://github.com/romanticamaj/openphotoagent/blob/main/docs/runtime-packs.md#vllm-docker"


def advanced_packs(reg: Registry, hw: HwSummary, include_blocked: bool = False) -> list[dict[str, Any]]:
    """Packs a user may add on purpose (vLLM, MLX, torch, ROCm, ORT-CUDA) — only those that can run on this machine,
    or with ``include_blocked`` also the others, each with why not and where to read about the alternative."""
    out: list[dict[str, Any]] = []
    for pid in sorted(reg.runtimes):
        p = reg.runtimes[pid]
        if not p.advanced:
            continue
        why = pack_failure(p, hw, reg.gpu_classes)
        if why is None:
            out.append({"pack": pid, "runtime": p.provides.get("runtime"), "size": p.size_est})
        elif include_blocked:
            fixes = [{"type": "open_docs", "url": DOCS_DOCKER}] if why == "RUNTIME_UNSUPPORTED_PLATFORM" else []
            out.append({"pack": pid, "runtime": p.provides.get("runtime"), "reason": why, "fixes": fixes})
    return out
