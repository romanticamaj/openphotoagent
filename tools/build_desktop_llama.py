"""llama-server for the desktop bundle (08 §4.1: every platform has a path that works with no download).

The official llama.cpp release, pinned by tag and sha256. Only the server and the libraries it loads are kept: the
release also ships about twenty tools the app never runs. Usage: build_desktop_llama.py <out-dir>
"""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

TAG = "b12121"
BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{TAG}/"
# Windows takes the Vulkan build (ADR-0024): GPU on any vendor with no signed pack, CPU backends included
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


def keep(name: str) -> bool:
    """The server, its libraries and the licence; not the other tools or their ``*-impl`` libraries."""
    low = name.lower()
    if low.startswith("license"):
        return True
    if low.endswith((".dll", ".so", ".dylib", ".metal")) or ".so." in low or ".dylib" in low:
        if low.startswith("llama-") and not low.startswith(("llama-server", "llama-common")):
            return False
        return "rpc" not in low
    return low in ("llama-server", "llama-server.exe")


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
        for m in t.getmembers():
            base = Path(m.name).name
            if not keep(base):
                continue
            if m.issym():  # versioned .so / .dylib names are links in the tarball
                (out / base).write_bytes(b"")
                continue
            f = t.extractfile(m) if m.isfile() else None
            if f:
                (out / base).write_bytes(f.read())
                (out / base).chmod(0o755)
    for link in [m for m in out.iterdir() if m.stat().st_size == 0]:
        target = next((p for p in sorted(out.iterdir()) if p.name.startswith(link.name) and p.stat().st_size), None)
        if target is None:
            target = next(p for p in sorted(out.iterdir()) if link.name.split(".so")[0] in p.name and p.stat().st_size)
        shutil.copy2(target, link)


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
