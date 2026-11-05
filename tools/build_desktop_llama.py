"""llama-server for the desktop bundle (08 §4.1: every platform has a path that works with no download).

The official llama.cpp release, pinned by tag and sha256. Only the server and the libraries it loads are kept: the
release also ships about twenty tools the app never runs. Usage: build_desktop_llama.py <out-dir>
"""

from __future__ import annotations

import hashlib
import io
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

TAG = "b12121"
BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{TAG}/"
# Windows takes the Vulkan build (ADR-0025): GPU on any vendor with no signed pack, CPU backends included
ASSETS = {
    "win32": (
        f"llama-{TAG}-bin-win-vulkan-x64.zip",
        "73111d348dcbfa86050611c28b8bef523c849d80a6e5529e25b67f1b2055e918",
    ),
    "darwin": (
        f"llama-{TAG}-bin-macos-arm64.tar.gz",
        "75e2176148b2d79ff20f503fa131f3d66d74176ed06c58f48e273e20383c32c2",
    ),
    "linux": (f"llama-{TAG}-bin-ubuntu-x64.tar.gz", "462cdbf73907c799bd29c02799af7cee6bf2ca1257a206c2797846e5db004b86"),
}


SONAME = re.compile(r"\.so\.\d+$|\.\d+\.dylib$")  # what the loader asks for: libllama.so.0, libllama.0.dylib
VERSIONED = re.compile(r"\.so\.\d+\.\d+\.\d+$|\.\d+\.\d+\.\d+\.dylib$")


def keep(name: str) -> bool:
    """The server, its libraries and the licence; not the other tools or their ``*-impl`` libraries."""
    low = name.lower()
    if low.startswith("license"):
        return True
    if low in ("llama-server", "llama-server.exe"):
        return True
    if low.endswith((".dll", ".so", ".dylib", ".metal")) or ".so." in low:
        stem = low.removeprefix("lib")
        if stem.startswith("llama-") and not stem.startswith(("llama-server", "llama-common")):
            return False
        return "rpc" not in low
    return False


def from_tar(members: dict[str, tarfile.TarInfo]) -> list[tuple[str, str]]:
    """(file to write, member holding its bytes). A library ships as libX.so -> libX.so.0 -> libX.so.0.9.0: only the
    middle name is ever loaded, so it alone is kept, and the bundle needs no symlinks."""
    out = []
    for name, m in sorted(members.items()):
        if not keep(name):
            continue
        if m.issym():
            if SONAME.search(name):
                target = m
                while target.issym():
                    target = members[Path(target.linkname).name]
                out.append((name, target.name.rsplit("/", 1)[-1]))
        elif not VERSIONED.search(name):
            out.append((name, name))
    return out


def unpack(data: bytes, name: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for i in z.infolist():
                base = Path(i.filename).name
                if base and keep(base):
                    (out / base).write_bytes(z.read(i))
        return
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        members = {Path(m.name).name: m for m in t.getmembers() if m.isfile() or m.issym()}
        for dest, src in from_tar(members):
            f = t.extractfile(members[src])
            assert f is not None
            (out / dest).write_bytes(f.read())
            (out / dest).chmod(0o755)


def main(out: Path) -> int:
    name, sha = ASSETS[sys.platform]
    with urllib.request.urlopen(BASE + name, timeout=300) as r:  # nosec B310 - a fixed https URL
        data = r.read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        raise SystemExit(f"{name}: sha256 {got} != pinned {sha}")
    print(f"{name}: sha256 {got}")
    shutil.rmtree(out, ignore_errors=True)
    unpack(data, name, out)
    exe = out / ("llama-server.exe" if sys.platform == "win32" else "llama-server")
    v = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60, check=False)
    print((v.stdout + v.stderr).strip()[-200:])
    if v.returncode:
        raise SystemExit(f"llama-server --version exited {v.returncode}")
    size = sum(p.stat().st_size for p in out.iterdir())
    print(f"llama runtime: {size / 2**20:.1f} MB uncompressed, {len(list(out.iterdir()))} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
