"""Relocatable Python runtime for the desktop bundle (08 §1.2 D3). CI only (desktop-build.yml).

python-build-standalone (``install_only_stripped``) → ``uv venv --relocatable`` → the app's packages from the
lockfile without dev dependencies → strip ``__pycache__``, tests and headers. The shell runs
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


def main(out: Path) -> int:
    out = out.resolve()
    if out.exists():
        shutil.rmtree(out)
    sh("uv", "python", "install", PY)
    sh("uv", "venv", "--relocatable", "--python", PY, str(out))
    env = {**os.environ, "VIRTUAL_ENV": str(out)}
    sh("uv", "export", "--frozen", "--no-dev", "--package", "opagent-cli", "--no-hashes", "-o", str(out / "req.txt"))
    sh("uv", "pip", "install", "--python", str(out), "-r", str(out / "req.txt"), env=env)
    (out / "req.txt").unlink()
    for p in list(out.rglob("__pycache__")) + [d for d in out.rglob("tests") if d.is_dir()]:
        shutil.rmtree(p, ignore_errors=True)
    for p in out.rglob("include"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"python runtime: {size / 2**20:.1f} MB uncompressed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "build/desktop/python")))
