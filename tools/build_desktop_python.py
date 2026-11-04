"""Relocatable Python runtime for the desktop bundle (08 §1.2 D3). CI only (desktop-build.yml).

python-build-standalone (uv-managed) copied whole → the app's packages from the lockfile without dev
dependencies, installed into it → drop unused stdlib/site modules, ``__pycache__``, tests, headers. No ``strip``: with it, linuxdeploy's ldd failed on
a scipy extension on the Ubuntu 22.04 runner (ADR-0022). The shell runs
``<res>/python/bin/python3 -I -m opagent_cli …`` (``-I``: the user's PYTHONPATH / site-packages are ignored).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = "3.12"


def sh(*args: str, **kw: object) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True, cwd=ROOT, **kw)  # type: ignore[call-overload]


# never shipped: the app imports none of these, and they are large (ADR-0022)
STDLIB_DROP = ("tkinter", "idlelib", "turtledemo", "ensurepip", "lib2to3", "pydoc_data")
SITE_DROP = ("jieba/lac_small", "jieba/posseg", "jieba/analyse")  # only Tokenizer().cut / add_word are used


def _base_prefix() -> Path:
    """The uv-managed python-build-standalone install itself (not a venv pointing at it)."""
    out = subprocess.run(
        ["uv", "python", "find", "--managed-python", "--no-project", PY], capture_output=True, text=True, check=True
    ).stdout.strip()
    prefix = subprocess.run(
        [out, "-c", "import sys; print(sys.base_prefix)"], capture_output=True, text=True, check=True
    )
    return Path(prefix.stdout.strip())


def _python(out: Path) -> Path:
    return out / "python.exe" if os.name == "nt" else out / "bin" / "python3"


def main(out: Path) -> int:
    out = out.resolve()
    if out.exists():
        shutil.rmtree(out)
    sh("uv", "python", "install", PY)
    # the interpreter and its standard library travel with the app (DESK-039): a venv only points at the build
    # machine's install, which no user has
    shutil.copytree(_base_prefix(), out, symlinks=True)
    for marker in out.glob("lib/python3*/EXTERNALLY-MANAGED"):
        marker.unlink()
    for marker in out.glob("Lib/EXTERNALLY-MANAGED"):
        marker.unlink()
    sh("uv", "export", "--frozen", "--no-dev", "--package", "opagent-cli", "--no-hashes", "-o", str(out / "req.txt"))
    sh("uv", "pip", "install", "--python", str(_python(out)), "--break-system-packages", "-r", str(out / "req.txt"))
    (out / "req.txt").unlink()
    lib = next(iter(sorted(out.glob("lib/python3*"))), out / "Lib")
    site = lib / "site-packages"
    for name in STDLIB_DROP:
        shutil.rmtree(lib / name, ignore_errors=True)
    for name in SITE_DROP:
        shutil.rmtree(site / name, ignore_errors=True)
    for p in site.glob("pip*"):  # the app installs nothing at runtime; packs ship as signed archives (ADR-0022)
        if p.name == "pip" or p.name.startswith("pip-"):
            shutil.rmtree(p, ignore_errors=True)
    for p in [*out.glob("bin/pip*"), *out.glob("Scripts/pip*")]:
        p.unlink()
    for p in list(out.rglob("__pycache__")) + [d for d in out.rglob("tests") if d.is_dir()]:
        shutil.rmtree(p, ignore_errors=True)
    for p in list(out.glob("include")) + list(out.glob("share")):
        shutil.rmtree(p, ignore_errors=True)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file() and not f.is_symlink())
    print(f"python runtime: {size / 2**20:.1f} MB uncompressed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "build/desktop/python")))
