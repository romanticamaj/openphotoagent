"""Generate the synthesized fixtures/hw profiles listed in docs/09 §3.4.

Every profile written here is ``source: synthesized-from-docs``: the raw probe outputs are shaped like the real
tools' outputs (nvidia-smi XML / --query-gpu CSV, sysctl + Metal, DXGI adapter descs, Vulkan memory budget), with
values taken from docs/09 §3.4, docs/08 §3 and public vendor specs. M0-T2 replaces them with recorded profiles
(``source: recorded``). Profiles already marked ``recorded`` are never overwritten.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "hw"
GIB = 1024**3


def nvidia(
    name: str, total_mib: int, driver: str, cc: str, used_mib: int = 0, procs: list[tuple[str, int]] = ()
) -> dict[str, Any]:  # type: ignore[assignment]
    pi = "".join(
        f"<process_info><pid>{1000 + i}</pid><type>C+G</type><process_name>{p}</process_name>"
        f"<used_memory>{m} MiB</used_memory></process_info>"
        for i, (p, m) in enumerate(procs)
    )
    xml = (
        f"<nvidia_smi_log><driver_version>{driver}</driver_version><gpu><product_name>{name}</product_name>"
        f"<fb_memory_usage><total>{total_mib} MiB</total><used>{used_mib} MiB</used>"
        f"<free>{total_mib - used_mib} MiB</free></fb_memory_usage><processes>{pi}</processes></gpu></nvidia_smi_log>"
    )
    csv = f"name, memory.total [MiB], driver_version, compute_cap\n{name}, {total_mib} MiB, {driver}, {cc}\n"
    return {"nvidia_smi_xml": xml, "nvidia_smi_query_csv": csv, "dev_nvidia": ["/dev/nvidia0", "/dev/nvidiactl"]}


def win(release: str = "11") -> dict[str, Any]:
    return {"system": "Windows", "release": release, "machine": "AMD64", "cpu_count": 16}


def linux(machine: str = "x86_64", cpus: int = 16) -> dict[str, Any]:
    return {"system": "Linux", "release": "6.8.0", "machine": machine, "cpu_count": cpus}


def mac(machine: str = "arm64") -> dict[str, Any]:
    return {"system": "Darwin", "release": "23.6.0", "machine": machine, "cpu_count": 8}


def apple(chip: str, ram_gib: int, ws_gb: float) -> dict[str, Any]:
    return {
        "sysctl": {"hw.memsize": ram_gib * GIB, "machdep.cpu.brand_string": chip},
        "metal": {"device": chip, "recommendedMaxWorkingSetSize": int(ws_gb * 1e9), "hasUnifiedMemory": True},
    }


PROFILES: dict[str, dict[str, Any]] = {
    "rtx5070ti-win11": {
        "desc": "RTX 5070 Ti 16 GB, cc 12.0, driver 580, Windows 11 (reference machine 1)",
        "probes": {
            "platform": win(),
            "ram_total_mb": 32768,
            **nvidia("NVIDIA GeForce RTX 5070 Ti", 16303, "580.88", "12.0"),
            "os_state": {"on_ac": True, "fullscreen_d3d": False, "idle_s": 0},
        },
    },
    "rtx4060-laptop": {
        "desc": "RTX 4060 Laptop 8 GB, Windows 11, on battery (reference machine 2)",
        "probes": {
            "platform": win(),
            "ram_total_mb": 16384,
            **nvidia("NVIDIA GeForce RTX 4060 Laptop GPU", 8188, "576.02", "8.9"),
            "os_state": {"on_ac": False, "fullscreen_d3d": False, "idle_s": 0},
        },
    },
    "m2-air-16": {
        "desc": "MacBook Air M2 16 GB, macOS 14 (reference machine 3)",
        "probes": {"platform": mac(), **apple("Apple M2", 16, 10.7)},
    },
    "m3-16": {
        "desc": "M3 16 GB with recommendedMaxWorkingSetSize fixture",
        "probes": {"platform": mac(), **apple("Apple M3", 16, 10.7)},
    },
    "m1-8": {"desc": "M1 8 GB", "probes": {"platform": mac(), **apple("Apple M1", 8, 5.3)}},
    "no-gpu-linux": {
        "desc": "Ubuntu 22.04, no GPU, 8 cores",
        "probes": {
            "platform": linux(cpus=8),
            "ram_total_mb": 16384,
            "nvidia_smi_xml": None,
            "dev_nvidia": [],
            "vulkan_devices": [],
        },
    },
    "amd-rx7800-win": {
        "desc": "Radeon RX 7800 XT 16 GB on Windows (DXGI budget)",
        "probes": {
            "platform": win(),
            "ram_total_mb": 32768,
            "nvidia_smi_xml": None,
            "dxgi_adapters": [
                {
                    "Description": "AMD Radeon RX 7800 XT",
                    "VendorId": 4098,
                    "DedicatedVideoMemory": 16 * GIB,
                    "Budget": int(15.2 * GIB),
                    "CurrentUsage": int(0.6 * GIB),
                }
            ],
        },
    },
    "intel-arc-linux": {
        "desc": "Intel Arc A770 16 GB on Linux (VK_EXT_memory_budget)",
        "probes": {
            "platform": linux(),
            "ram_total_mb": 32768,
            "nvidia_smi_xml": None,
            "vulkan_devices": [
                {
                    "deviceName": "Intel(R) Arc(tm) A770 Graphics (DG2)",
                    "vendorID": 32902,
                    "heaps": [
                        {"size": 16 * GIB, "budget": int(15.0 * GIB), "usage": int(0.3 * GIB), "device_local": True},
                        {"size": 16 * GIB, "budget": 8 * GIB, "usage": 0, "device_local": False},
                    ],
                }
            ],
        },
    },
    "nvidia-drv535": {
        "desc": "RTX 3080 10 GB with driver 535 (too old)",
        "probes": {
            "platform": win(),
            "ram_total_mb": 32768,
            **nvidia("NVIDIA GeForce RTX 3080", 10240, "535.98", "8.6"),
        },
    },
    "blackwell-cuda12pack": {
        "desc": "RTX 5070 Ti + installed llamacpp-cuda12 (CUDA 12.4) pack",
        "probes": {
            "platform": win(),
            "ram_total_mb": 32768,
            **nvidia("NVIDIA GeForce RTX 5070 Ti", 16303, "580.88", "12.0"),
            "installed_packs": [{"id": "llamacpp-cuda12", "version": "b6000-1", "cuda_version": "12.4"}],
        },
    },
    "linux-rtx3090": {
        "desc": "Linux, RTX 3090 24 GB",
        "probes": {
            "platform": linux(),
            "ram_total_mb": 65536,
            **nvidia("NVIDIA GeForce RTX 3090", 24576, "580.95", "8.6"),
        },
    },
    "docker-no-passthrough": {
        "desc": "Inside a container without /dev/nvidia*",
        "probes": {
            "platform": linux(cpus=8),
            "ram_total_mb": 16384,
            "nvidia_smi_xml": None,
            "dev_nvidia": [],
            "in_container": True,
        },
    },
    "intel-mac": {
        "desc": "Intel Mac (unsupported)",
        "probes": {
            "platform": mac("x86_64"),
            "sysctl": {"hw.memsize": 16 * GIB, "machdep.cpu.brand_string": "Intel(R) Core(TM) i7-9750H"},
        },
    },
    "linux-arm64": {
        "desc": "Linux arm64 (unsupported)",
        "probes": {"platform": linux("aarch64", 8), "ram_total_mb": 8192, "nvidia_smi_xml": None},
    },
}


def main() -> int:
    for name, p in PROFILES.items():
        path = OUT / f"{name}.yaml"
        if path.exists() and '"source": "recorded"' in path.read_text(encoding="utf-8"):
            continue
        doc = {
            "profile": name,
            "source": "synthesized-from-docs",
            "schema": "opagent.hwprobe.v1",
            "description": p["desc"],
            "probes": p["probes"],
        }
        path.write_text(
            "# Synthesized from docs/09 §3.4, docs/08 §3 and public vendor specs (source: synthesized-from-docs).\n"
            "# Replace with a recorded profile (M0-T2, spikes/m0/T2/record.py). Generated by tools/gen_hw_profiles.py\n"
            + json.dumps(doc, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
