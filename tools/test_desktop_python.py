"""DESK-039 (回歸): the desktop bundle's Python runtime is self-contained.

`uv venv --relocatable` makes the packages relocatable, not the interpreter: bin/python was a symlink to the build
machine's uv-managed CPython and the tree had no standard library, so the installed app could not start its
sidecar on any user's machine (found by the installer-size deliberation, ADR-0022)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    out = tmp_path_factory.mktemp("rt") / "python"
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools/build_desktop_python.py"), str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return out


@pytest.mark.integration
@pytest.mark.tc("DESK-039")
def test_desk_039_runtime_carries_its_own_interpreter_and_stdlib(runtime, tmp_path):
    tree = runtime.resolve()
    outside = [
        str(p.relative_to(tree))
        for p in tree.rglob("*")
        if p.is_symlink() and not str(Path(os.path.realpath(p))).startswith(str(tree))
    ]
    assert outside == [], f"symlinks leaving the bundle: {outside[:5]}"
    stdlib = [*tree.glob("lib/python3*/encodings/__init__.py"), *tree.glob("Lib/encodings/__init__.py")]
    assert stdlib, "no standard library inside the bundle"
    cfg = tree / "pyvenv.cfg"
    assert not cfg.exists() or "home = /" not in cfg.read_text(encoding="utf-8"), "pyvenv.cfg points outside"
    moved = tmp_path / "elsewhere" / "python"  # a user's install location
    shutil.copytree(tree, moved, symlinks=True)
    exe = moved / ("python.exe" if os.name == "nt" else "bin/python3")
    r = subprocess.run(
        [str(exe), "-I", "-c", "import encodings, ssl, sqlite3, json, opagent_cli, opagent_api; print('ok')"],
        env={"SYSTEMROOT": os.environ["SYSTEMROOT"]} if os.name == "nt" else {},
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr[-2000:]
