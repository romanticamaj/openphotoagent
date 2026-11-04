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
    subprocess.run(args, check=True, cwd=ROOT, stdin=subprocess.DEVNULL, **kw)  # type: ignore[call-overload]


# never shipped: the app imports none of these, and they are large (ADR-0022)
STDLIB_DROP = ("tkinter", "idlelib", "turtledemo", "ensurepip", "lib2to3", "pydoc_data")
SITE_DROP = ("jieba/lac_small", "jieba/posseg", "jieba/analyse")  # only Tokenizer().cut / add_word are used


def _base_prefix() -> Path:
    """The uv-managed python-build-standalone install itself: never a venv, never the system Python.

    ``uv python find`` answers with the project's active ``.venv``; on CI that venv sits on the runner's system
    Python, whose prefix is /usr (DESK-042). Pick from uv's own install dir instead."""
    import json

    def uv(*a: str) -> str:
        return subprocess.run(["uv", *a], capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout

    managed = Path(uv("python", "dir").strip()).resolve()
    found = json.loads(uv("python", "list", PY, "--only-installed", "--managed-python", "--output-format", "json"))
    exes = [Path(e["path"]) for e in found if Path(e["path"]).resolve().is_relative_to(managed)]
    if not exes:
        raise SystemExit(f"no uv-managed Python {PY} under {managed}")
    exe = max(exes, key=lambda x: [int(n) for n in uv_version(x).split(".")])
    prefix = subprocess.run(
        [str(exe), "-I", "-c", "import sys; print(sys.base_prefix)"], capture_output=True, text=True, check=True
    )
    out = Path(prefix.stdout.strip()).resolve()
    if not out.is_relative_to(managed):
        raise SystemExit(f"refusing to bundle {out}: not under {managed}")
    return out


def uv_version(exe: Path) -> str:
    r = subprocess.run(
        [str(exe), "-I", "-c", "import platform; print(platform.python_version())"],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def _project_name(project: Path) -> str:
    import tomllib

    return str(tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"]["name"])


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
    sh(
        "uv",
        "export",
        "--frozen",
        "--no-dev",
        "--no-editable",
        "--package",
        "opagent-cli",
        "--no-hashes",
        "-o",
        str(out / "req.txt"),
    )
    # real wheels, not editable .pth files back into this checkout (DESK-039); the app's own packages are rebuilt
    # every time, or uv reuses a cached wheel built before a data file moved (DESK-040)
    own = [
        _project_name(ROOT / ln.strip())
        for ln in (out / "req.txt").read_text(encoding="utf-8").splitlines()
        if ln.startswith("./")
    ]
    sh(
        "uv",
        "pip",
        "install",
        "--no-sources",
        *[a for name in own for a in ("--refresh-package", name)],
        "--python",
        str(_python(out)),
        "--break-system-packages",
        "-r",
        str(out / "req.txt"),
    )
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
