"""Parse raw probe outputs (nvidia-smi, DXGI, Vulkan, Metal/sysctl, /proc) into a HwSummary (06 §3, 09 §3.4).

Profiles store the *raw* tool outputs, so this parser is exercised by every hardware-dependent test. On a real
machine the same functions receive live probe output from the worker.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from opagent_core.hw import Gpu, HwSummary

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "hw"
GIB = 1024**3
_OS = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}
_ARCH = {"AMD64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64", "ARM64": "arm64"}
_VENDOR_IDS = {4318: "nvidia", 4098: "amd", 32902: "intel", 0x106B: "apple"}


def read_profile(name: str, root: Path = FIXTURES) -> dict[str, Any]:
    text = (root / f"{name}.yaml").read_text(encoding="utf-8")
    doc: dict[str, Any] = json.loads("\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#")))
    return doc


def _mib(s: str) -> float:
    return float(re.sub(r"[^\d.]", "", s) or 0) / 1024


def parse_nvidia(xml: str | None, csv: str | None) -> list[Gpu]:
    if not xml and not csv:
        return []
    gpus: list[Gpu] = []
    rows: list[dict[str, str]] = []
    if csv:
        lines = [ln for ln in csv.strip().splitlines() if ln.strip()]
        head = [h.strip().split(" ")[0] for h in lines[0].split(",")]
        rows = [dict(zip(head, (c.strip() for c in ln.split(",")), strict=False)) for ln in lines[1:]]
    driver = None
    blocks: list[str] = []
    if xml:
        m = re.search(r"<driver_version>([^<]+)</driver_version>", xml)
        driver = m.group(1) if m else None
        blocks = re.findall(r"<gpu>(.*?)</gpu>", xml, flags=re.S)
    for i in range(max(len(blocks), len(rows))):
        b = blocks[i] if i < len(blocks) else ""
        row = rows[i] if i < len(rows) else {}
        name = row.get("name") or (re.search(r"<product_name>([^<]+)</product_name>", b) or [None, "NVIDIA GPU"])[1]
        total = _mib(row["memory.total"]) if row.get("memory.total") else 0.0
        mt = re.search(r"<fb_memory_usage>\s*<total>([^<]+)</total>", b)
        if mt:
            total = _mib(mt.group(1))
        ext = sum(int(x) for x in re.findall(r"<used_memory>(\d+) MiB</used_memory>", b)) / 1024
        names = re.findall(r"<process_name>([^<]+)</process_name>", b)
        procs = tuple(n.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for n in names)
        gpus.append(
            Gpu(
                vendor="nvidia",
                name=str(name),
                vram_total_gb=round(total, 3),
                vram_used_external_gb=round(ext, 3),
                compute_capability=row.get("compute_cap"),
                driver=row.get("driver_version") or driver,
                source="nvml",
                processes=procs,
            )
        )
    return gpus


def parse_dxgi(adapters: list[dict[str, Any]] | None) -> list[Gpu]:
    out = []
    for a in adapters or []:
        vendor = _VENDOR_IDS.get(int(a.get("VendorId", 0)), "other")
        if vendor == "nvidia":
            continue  # NVML is authoritative for NVIDIA
        out.append(
            Gpu(
                vendor=vendor,
                name=str(a.get("Description", "GPU")),
                vram_total_gb=round(int(a.get("DedicatedVideoMemory", 0)) / GIB, 3),
                vram_used_external_gb=round(int(a.get("CurrentUsage", 0)) / GIB, 3),
                budget_gb=round(int(a["Budget"]) / GIB, 3) if a.get("Budget") else None,
                source="dxgi",
            )
        )
    return out


def parse_vulkan(devices: list[dict[str, Any]] | None) -> list[Gpu]:
    out = []
    for d in devices or []:
        vendor = _VENDOR_IDS.get(int(d.get("vendorID", 0)), "other")
        if vendor == "nvidia":
            continue
        local = [h for h in d.get("heaps", []) if h.get("device_local")]
        if not local:
            continue
        total = sum(int(h["size"]) for h in local)
        budget = sum(int(h.get("budget", 0)) for h in local)
        used = sum(int(h.get("usage", 0)) for h in local)
        out.append(
            Gpu(
                vendor=vendor,
                name=str(d.get("deviceName", "GPU")),
                vram_total_gb=round(total / GIB, 3),
                vram_used_external_gb=round(used / GIB, 3),
                budget_gb=round(budget / GIB, 3) if budget else None,
                source="vulkan",
            )
        )
    return out


def parse_apple(sysctl: dict[str, Any] | None, metal: dict[str, Any] | None) -> list[Gpu]:
    if not metal:
        return []
    ram = int((sysctl or {}).get("hw.memsize", 0)) / GIB
    ws = metal.get("recommendedMaxWorkingSetSize")
    return [
        Gpu(
            vendor="apple",
            name=str(metal.get("device", "Apple GPU")),
            vram_total_gb=round(ram, 3),
            unified=bool(metal.get("hasUnifiedMemory", True)),
            budget_gb=round(int(ws) / 1e9, 3) if ws else None,
            source="metal",
        )
    ]


def _ram_gb(p: dict[str, Any]) -> float:
    if p.get("ram_total_mb"):
        return float(p["ram_total_mb"]) / 1024
    if (p.get("sysctl") or {}).get("hw.memsize"):
        return int(p["sysctl"]["hw.memsize"]) / GIB
    m = re.search(r"MemTotal:\s+(\d+) kB", p.get("meminfo") or "")
    return int(m.group(1)) / 1024 / 1024 if m else 0.0


def summarize(probes: dict[str, Any]) -> HwSummary:
    plat = probes.get("platform") or {}
    gpus = (
        parse_nvidia(probes.get("nvidia_smi_xml"), probes.get("nvidia_smi_query_csv"))
        + parse_dxgi(probes.get("dxgi_adapters"))
        + parse_vulkan(probes.get("vulkan_devices"))
        + parse_apple(probes.get("sysctl"), probes.get("metal"))
    )
    os_state = probes.get("os_state") or {}
    return HwSummary(
        os=_OS.get(str(plat.get("system")), str(plat.get("system", "")).lower()),
        arch=_ARCH.get(str(plat.get("machine")), str(plat.get("machine", ""))),
        ram_gb=round(_ram_gb(probes), 3),
        cpu_count=int(plat.get("cpu_count") or 1),
        gpus=tuple(gpus),
        installed_packs=tuple(
            (str(p["id"]), str(p.get("version", "")), p.get("cuda_version"))
            for p in probes.get("installed_packs") or []
        ),
        in_container=bool(probes.get("in_container")),
        on_battery=os_state.get("on_ac") is False,
        fullscreen=bool(os_state.get("fullscreen_d3d")),
    )


def load_profile(name: str, root: Path = FIXTURES) -> HwSummary:
    return summarize(read_profile(name, root)["probes"])


def collect_probes() -> dict[str, Any]:
    """Live raw probes on this machine (best effort; every probe may be missing). Runs on the Worker only."""
    import contextlib
    import os
    import platform
    import shutil
    import subprocess

    probes: dict[str, Any] = {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count() or 1,
        }
    }
    with contextlib.suppress(OSError):
        probes["meminfo"] = Path("/proc/meminfo").read_text()
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            probes["nvidia_smi_xml"] = subprocess.run(
                [smi, "-q", "-x"], capture_output=True, text=True, timeout=5
            ).stdout
            probes["nvidia_smi_query_csv"] = subprocess.run(
                [smi, "--query-gpu=name,memory.total,driver_version,compute_cap", "--format=csv"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            pass
    probes["dev_nvidia"] = sorted(str(p) for p in Path("/dev").glob("nvidia*")) if Path("/dev").exists() else []
    probes["in_container"] = Path("/.dockerenv").exists()
    return probes


def collect_env(roots: list[dict[str, Any]] | None = None, models_root: str | None = None) -> dict[str, Any]:
    """Live raw facts for the doctor checks (cheap, quick group only). Runs on the Worker."""
    import importlib.util
    import os
    import shutil

    env: dict[str, Any] = {
        "imports": {m: importlib.util.find_spec(m) is not None for m in ("pillow_heif", "rawpy")},
        "which": {t: shutil.which(t) for t in ("ffmpeg", "exiftool")},
    }
    if models_root:
        p = Path(models_root)
        while not p.exists() and p != p.parent:
            p = p.parent
        env["disk"] = {"models_free": shutil.disk_usage(p).free}
    states = []
    for r in roots or []:
        path = Path(r["path"])
        st = "ok"
        if not path.exists():
            st = "ENOENT"
        elif not os.access(path, os.R_OK):
            st = "EACCES"
        writable = os.access(path, os.W_OK) if path.exists() else None
        states.append({"alias": r["alias"], "state": st, "mode": r.get("mode", "ro"), "writable": writable})
    env["roots"] = states
    return env
