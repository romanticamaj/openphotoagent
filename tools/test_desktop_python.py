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
    site = stdlib[0].parent.parent / "site-packages"
    installers = sorted(p.name for p in site.iterdir() if p.name.split("-")[0] in ("pip", "setuptools", "wheel"))
    assert installers == [], f"package installers in an installed app: {installers}"  # ADR-0022: dead weight
    cfg = tree / "pyvenv.cfg"
    assert not cfg.exists() or "home = /" not in cfg.read_text(encoding="utf-8"), "pyvenv.cfg points outside"
    moved = tmp_path / "elsewhere" / "python"  # a user's install location
    shutil.copytree(tree, moved, symlinks=True)
    exe = moved / ("python.exe" if os.name == "nt" else "bin/python3")
    r = subprocess.run(
        [
            str(exe),
            "-I",
            "-c",
            "import encodings, ssl, sqlite3, json, opagent_cli, opagent_api, numpy, scipy.integrate, sklearn.cluster, onnxruntime, PIL.Image, pillow_heif, psycopg, sys, os\n"
            # the build tree is still on this machine, so an import that only works through a path back into it
            # (an editable .pth, a symlink) passes here and fails on every user's machine: every module must load
            # from inside the moved copy
            "home = os.path.realpath(sys.prefix)\n"
            "out = sorted({os.path.realpath(f) for m in list(sys.modules.values()) if (f := getattr(m, '__file__', None))})\n"
            "outside = [f for f in out if not f.startswith(home + os.sep)]\n"
            "print('ok' if not outside else 'outside: ' + ', '.join(outside[:5]))",
        ],
        env={"SYSTEMROOT": os.environ["SYSTEMROOT"]} if os.name == "nt" else {},
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr[-2000:]


APP_DATA_PROBE = r"""
import os, sys
home = os.path.realpath(sys.prefix)
opened = []
def audit(event, args):
    if event == "open" and isinstance(args[0], (str, os.PathLike)):
        opened.append(os.path.realpath(args[0]))
sys.addaudithook(audit)
from opagent_registry.loader import load_registry
reg = load_registry()
assert reg.models and reg.runtimes and reg.gpu_classes, "model registry is empty"
from opagent_api.organize import _builtin
assert _builtin("by-date") and _builtin("by-content-12"), "built-in organize presets missing"
from opagent_api.i18n_labels import _catalog
assert _catalog(), "zh-TW labels missing"
from opagent_inference.tags import load_vocabulary
assert load_vocabulary().terms, "tag vocabulary missing"
from opagent_search.tokenize import Tokenizer
assert Tokenizer()._index, "search vocabulary missing"
import tempfile
tmp = os.path.realpath(tempfile.gettempdir())  # runtime scratch (jieba's dictionary cache), not shipped data
outside = sorted({f for f in opened if not f.startswith((home + os.sep, tmp + os.sep))} - {"/dev/null", "/dev/urandom"})
print("ok" if not outside else "outside: " + ", ".join(outside[:5]))
"""


@pytest.mark.integration
@pytest.mark.tc("DESK-040")
def test_desk_040_app_data_ships_inside_the_runtime(runtime, tmp_path):
    """回歸 (ADR-0022): registry, presets, labels and vocabularies were read from the build machine's repo via
    ``Path(__file__).parents[N]``; an installed app has no repo. Load each one through the app's own loader in a
    moved copy and audit every file opened."""
    moved = tmp_path / "elsewhere" / "python"
    shutil.copytree(runtime, moved, symlinks=True)
    exe = moved / ("python.exe" if os.name == "nt" else "bin/python3")
    r = subprocess.run(
        [str(exe), "-I", "-c", APP_DATA_PROBE],
        env={"SYSTEMROOT": os.environ["SYSTEMROOT"]} if os.name == "nt" else {},
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert r.returncode == 0 and r.stdout.strip() == "ok", (r.stdout + r.stderr)[-2000:]
