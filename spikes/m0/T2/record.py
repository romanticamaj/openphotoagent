"""M0-T2 — record raw hardware probe output into fixtures/hw/<profile>.yaml (docs/09 §3.4, §6).

Stores RAW probe outputs (not doctor conclusions) so the doctor's parsers are
tested against real data. Output is JSON, which is valid YAML 1.2.

    uv run python spikes/m0/T2/record.py --profile rtx5070ti-win11
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def cap(cmd: list[str]) -> str | None:
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        # errors="replace": Windows tools emit the console code page (e.g. cp950), not UTF-8.
        r = subprocess.run([exe, *cmd[1:]], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60)
        return r.stdout if r.returncode == 0 else f"exit {r.returncode}: {r.stderr[:500]}"
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"error: {e}"


def read(p: str) -> str | None:
    try:
        return Path(p).read_text(errors="replace")
    except OSError:
        return None


def redact_nvidia_xml(xml: str | None) -> str | None:
    """Fixtures are public: strip device identity and the installed-app list, keep the raw shape.

    Process names become ``app-N.exe`` so the parser still sees the real element structure
    (on WDDM ``used_memory`` is ``N/A``), without publishing which apps this machine runs.
    """
    if not xml or xml.startswith(("exit ", "error:")):
        return xml
    xml = re.sub(r"<(uuid|pdi)>[^<]*</\1>", r"<\1>REDACTED</\1>", xml)
    names: dict[str, str] = {}

    def anon(m: re.Match[str]) -> str:
        n = names.setdefault(m.group(1), f"app-{len(names) + 1}.exe")
        return f"<process_name>{n}</process_name>"

    return re.sub(r"<process_name>([^<]*)</process_name>", anon, xml)


def redact_vulkan(text: str | None) -> str | None:
    return re.sub(r"(deviceUUID\s*=\s*)\S+", r"\1REDACTED", text) if text else text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--source", default="recorded", help="recorded | synthesized-from-docs")
    a = ap.parse_args()
    sysname = platform.system()
    probes: dict[str, object] = {
        "platform": {"system": sysname, "release": platform.release(), "machine": platform.machine(),
                     "version": platform.version(), "cpu_count": os.cpu_count()},
        "nvidia_smi_xml": redact_nvidia_xml(cap(["nvidia-smi", "-q", "-x"])),
        "nvidia_smi_query_csv": cap(["nvidia-smi", "--query-gpu=name,memory.total,driver_version,compute_cap", "--format=csv"]),
        "vulkaninfo_summary": redact_vulkan(cap(["vulkaninfo", "--summary"])),
        "rocm_smi": cap(["rocm-smi", "--showmeminfo", "vram", "--json"]),
    }
    if sysname == "Linux":
        probes.update({"os_release": read("/etc/os-release"), "meminfo": read("/proc/meminfo"),
                       "cpuinfo_model": next((ln for ln in (read("/proc/cpuinfo") or "").splitlines() if ln.startswith("model name")), None),
                       "dev_nvidia": sorted(p.name for p in Path("/dev").glob("nvidia*")),
                       "dev_dri": sorted(p.name for p in Path("/dev/dri").glob("*")) if Path("/dev/dri").exists() else [],
                       "power_supply": {p.name: read(str(p / "type")) for p in Path("/sys/class/power_supply").glob("*")} if Path("/sys/class/power_supply").exists() else {},
                       "in_container": Path("/.dockerenv").exists() or "docker" in (read("/proc/1/cgroup") or "")})
    elif sysname == "Darwin":
        probes.update({"system_profiler_displays": cap(["system_profiler", "SPDisplaysDataType", "-json"]),
                       "system_profiler_hardware": cap(["system_profiler", "SPHardwareDataType", "-json"]),
                       "sysctl_memsize": cap(["sysctl", "-n", "hw.memsize"]), "pmset_batt": cap(["pmset", "-g", "batt"])})
    elif sysname == "Windows":
        ps = ["powershell", "-NoProfile", "-Command", "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"]
        # Explicit property lists: the full CIM objects carry the user name, host name and domain.
        vc = "Name,AdapterRAM,DriverVersion,DriverDate,VideoProcessor,AdapterCompatibility,PNPDeviceID,Status"
        cs = "Manufacturer,Model,SystemType,NumberOfProcessors,NumberOfLogicalProcessors,TotalPhysicalMemory,PCSystemType"
        probes.update({"win32_videocontroller": cap([*ps, f"Get-CimInstance Win32_VideoController | Select-Object {vc} | ConvertTo-Json"]),
                       "win32_computersystem": cap([*ps, f"Get-CimInstance Win32_ComputerSystem | Select-Object {cs} | ConvertTo-Json"]),
                       "win32_processor": cap([*ps, "Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json"]),
                       "battery": cap([*ps, "Get-CimInstance Win32_Battery | Select-Object Name,BatteryStatus | ConvertTo-Json"]),
                       "long_paths_enabled": cap([*ps, "(Get-ItemProperty HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem).LongPathsEnabled"])})
        with contextlib.suppress(TypeError, ValueError):
            probes["ram_total_mb"] = int(json.loads(probes["win32_computersystem"])["TotalPhysicalMemory"]) // (1024 * 1024)  # type: ignore[arg-type]
    out = ROOT / "fixtures" / "hw" / f"{a.profile}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {"profile": a.profile, "source": a.source, "schema": "opagent.hwprobe.v1", "probes": probes}
    out.write_text("# Raw probe outputs (JSON is valid YAML). Generated by spikes/m0/T2/record.py\n"
                   + json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
