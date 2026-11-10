"""Offline street map (10 §2.5, ADR-0037). On the worker only: fetch the pinned `pmtiles` tool (go-pmtiles, checked
by SHA-256 before it is unpacked or run), extract the region from the newest Protomaps planet build of the schema the
map style reads, and install the file only when it is a PMTiles v3 archive. Fetching, the build list and running are
injected, so tests touch neither the network nor a binary."""

from __future__ import annotations

import hashlib
import io
import os
import platform
import subprocess
import tarfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BBOX = "118.0,21.7,122.2,26.4"  # Taiwan, Penghu, Kinmen, Matsu
MAXZOOM = 15  # street level; 16+ only adds building detail
REGION = "taiwan"
BUILDS_URL = "https://build-metadata.protomaps.dev/builds.json"
BUILD_BASE = "https://build.protomaps.com/"
SCHEMA_MAJOR = "4"  # what @protomaps/basemaps 5.x styles read
TOOL_VERSION = "1.31.2"
_REL = f"https://github.com/protomaps/go-pmtiles/releases/download/v{TOOL_VERSION}/"


class BasemapError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class ToolPin:
    url: str
    sha256: str
    size: int


# sha256 from the GitHub release's asset digests (2026-11-10)
PINS: dict[tuple[str, str], ToolPin] = {
    ("Windows", "AMD64"): ToolPin(
        _REL + f"go-pmtiles_{TOOL_VERSION}_Windows_x86_64.zip",
        "a658baa4d7e55020aef6ca17bd9ff9faa1582671266b36f58c52db0ac8e785a1",
        17842310,
    ),
    ("Darwin", "arm64"): ToolPin(
        _REL + f"go-pmtiles-{TOOL_VERSION}_Darwin_arm64.zip",
        "40528f7f616fcbf91207cd48c8fc023d213f6d86c0cbf1f748732803d1880f3d",
        15963714,
    ),
    ("Linux", "x86_64"): ToolPin(
        _REL + f"go-pmtiles_{TOOL_VERSION}_Linux_x86_64.tar.gz",
        "3ed7dbf4ec2e6dfe5e25b6f70d1ffc932729f93c86db353bf514dd71010a312f",
        17444324,
    ),
}


@dataclass(frozen=True)
class Installed:
    path: Path
    build: str
    bytes: int


def exe_name() -> str:
    return "pmtiles.exe" if os.name == "nt" else "pmtiles"


def pin_for_this_machine() -> ToolPin:
    pin = PINS.get((platform.system(), platform.machine()))
    if pin is None:
        raise BasemapError("DL_NOT_FOUND", f"no pmtiles build for {platform.system()} {platform.machine()}")
    return pin


def target(data_dir: Path) -> Path:
    return data_dir / "maps" / f"{REGION}.pmtiles"


def _tool(data_dir: Path, pin: ToolPin, fetch: Callable[[str, Path], None]) -> Path:
    """The tool, fetched once; the archive is checked before anything in it is unpacked."""
    d = data_dir / "tools" / f"pmtiles-{TOOL_VERSION}"
    exe = d / exe_name()
    if exe.exists():
        return exe
    d.mkdir(parents=True, exist_ok=True)
    pkg = d / "download.part"
    try:
        fetch(pin.url, pkg)
    except Exception as e:
        raise BasemapError("NET_UNREACHABLE", str(e)[:200]) from None
    data = pkg.read_bytes()
    pkg.unlink()
    if hashlib.sha256(data).hexdigest() != pin.sha256 or (pin.size and len(data) != pin.size):
        raise BasemapError("DL_CHECKSUM_MISMATCH", pin.url)
    if pin.url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            member = next((n for n in z.namelist() if Path(n).name == exe_name()), None)
            if member is None:
                raise BasemapError("DL_NOT_FOUND", "pmtiles not in the archive")
            body = z.read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            m = next((x for x in t.getmembers() if Path(x.name).name == exe_name() and x.isfile()), None)
            f = t.extractfile(m) if m is not None else None
            if f is None:
                raise BasemapError("DL_NOT_FOUND", "pmtiles not in the archive")
            body = f.read()
    tmp = d / (exe_name() + ".part")
    tmp.write_bytes(body)
    if os.name != "nt":
        tmp.chmod(0o755)
    tmp.replace(exe)
    return exe


def _latest_build(get_json: Callable[[str], Any]) -> str:
    try:
        builds = get_json(BUILDS_URL)
    except Exception as e:
        raise BasemapError("NET_UNREACHABLE", str(e)[:200]) from None
    keys = sorted(
        str(b["key"])
        for b in builds or []
        if isinstance(b, dict) and str(b.get("version", "")).split(".")[0] == SCHEMA_MAJOR and b.get("key")
    )
    if not keys:
        raise BasemapError("DL_NOT_FOUND", "no planet build of the schema the map style reads")
    return keys[-1]


def _is_pmtiles_v3(p: Path) -> bool:
    try:
        with p.open("rb") as f:
            head = f.read(8)
    except OSError:
        return False
    return head[:7] == b"PMTiles" and head[7:8] == b"\x03"


def install(
    data_dir: Path,
    *,
    pin: ToolPin | None = None,
    fetch: Callable[[str, Path], None],
    get_json: Callable[[str], Any],
    run: Callable[[list[str], Callable[[str], None]], int],
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> Installed:
    say = on_progress or (lambda _p: None)
    say({"stage": "tool"})
    exe = _tool(data_dir, pin or pin_for_this_machine(), fetch)
    build = _latest_build(get_json)
    out = target(data_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_suffix(".pmtiles.part")
    part.unlink(missing_ok=True)
    say({"stage": "extract", "build": build})
    args = [str(exe), "extract", BUILD_BASE + build, str(part), f"--bbox={BBOX}", f"--maxzoom={MAXZOOM}"]
    try:
        code = run(args, lambda line: say({"stage": "extract", "line": line[-120:]}))
    except Exception as e:
        part.unlink(missing_ok=True)
        raise BasemapError("DL_MIRROR_FAILED", str(e)[:200]) from None
    if code != 0 or not _is_pmtiles_v3(part):
        part.unlink(missing_ok=True)
        raise BasemapError("DL_MIRROR_FAILED", f"extract exited {code}")
    part.replace(out)
    say({"stage": "done", "bytes": out.stat().st_size})
    return Installed(out, build, out.stat().st_size)


# ---- the real I/O, used outside tests ----------------------------------------------------------------------------
def http_fetch(url: str, dest: Path) -> None:
    import httpx

    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)


def http_json(url: str) -> Any:
    import httpx

    r = httpx.get(url, follow_redirects=True, timeout=30.0)
    r.raise_for_status()
    return r.json()


def run_tool(args: list[str], on_line: Callable[[str], None]) -> int:
    """The tool with no shell, output read line by line (its progress)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", creationflags=flags
    ) as p:
        assert p.stdout is not None
        for line in p.stdout:
            on_line(line.rstrip())
        return p.wait()
