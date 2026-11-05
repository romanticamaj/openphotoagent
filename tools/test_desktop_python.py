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
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "tools/build_desktop_python.py"), str(out)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,  # below pytest's own timeout, so a stuck build fails with its output (which step)
        )
    except subprocess.TimeoutExpired as e:
        tail = [x.decode() if isinstance(x, bytes) else (x or "") for x in (e.stdout, e.stderr)]
        pytest.fail("runtime build timed out; last output:\n" + tail[0][-3000:] + tail[1][-3000:])
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
        timeout=300,
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
        # every Windows process has a profile; without it tempfile probes a literal "~\\AppData\\Local\\Temp" in the cwd
        env={k: os.environ[k] for k in ("SYSTEMROOT", "USERPROFILE", "TEMP", "TMP")} if os.name == "nt" else {},
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        timeout=300,
    )
    assert r.returncode == 0 and r.stdout.strip() == "ok", (r.stdout + r.stderr)[-2000:]


@pytest.mark.integration
@pytest.mark.tc("DESK-042")
def test_desk_042_runtime_source_is_uv_managed_even_inside_a_system_venv(tmp_path, monkeypatch):
    """回歸 (ADR-0023): ``uv python find`` answered with the active project venv. CI's venv sits on the runner's
    system Python, so the "interpreter prefix" was /usr and the build copied all of /usr (the integration job hung).
    """
    import importlib.util

    system = shutil.which("python3.12", path="/usr/bin:/usr/local/bin")
    if system is None:
        pytest.fail("this check needs a non-uv python3.12 on the machine (CI runner and dev container have one)")
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run([system, "-m", "venv", "--without-pip", str(proj / ".venv")], check=True, timeout=120)
    monkeypatch.chdir(proj)
    monkeypatch.setenv("VIRTUAL_ENV", str(proj / ".venv"))
    spec = importlib.util.spec_from_file_location("build_desktop_python", ROOT / "tools/build_desktop_python.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    managed = Path(subprocess.run(["uv", "python", "dir"], capture_output=True, text=True, check=True).stdout.strip())
    prefix = mod._base_prefix()
    assert prefix.resolve().is_relative_to(managed.resolve()), (prefix, managed)
    assert (prefix / "lib").is_dir() or (prefix / "Lib").is_dir()


@pytest.mark.unit
@pytest.mark.tc("DESK-051")
def test_desk_051_only_the_server_and_its_libraries_ship():
    """回歸: 08 §4.1 puts llama-server in the installer, and it was missing: a fresh install could run no model."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_desktop_llama", ROOT / "tools/build_desktop_llama.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in (
        "llama-server.exe",
        "llama-server",
        "llama-server-impl.dll",
        "llama-common.dll",
        "ggml-vulkan.dll",
        "ggml-cpu-zen4.dll",
        "llama.dll",
        "mtmd.dll",
        "libllama.so.0",
        "libggml.dylib",
        "LICENSE-LLVM-OpenMP",
    ):
        assert mod.keep(name), name
    for name in ("llama-cli.exe", "llama-bench.exe", "llama-bench-impl.dll", "ggml-rpc.dll", "ggml-rpc-server.exe"):
        assert not mod.keep(name), name
    assert not mod.keep("libllama-cli-impl.so") and mod.keep("libllama-server-impl.dylib")
    assert all(len(sha) == 64 for _, sha in mod.ASSETS.values())
    conf = (ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
    assert "build/desktop/llama" in conf


@pytest.mark.unit
@pytest.mark.tc("DESK-054")
def test_desk_054_smoke_fails_on_a_crash_in_the_logs_and_on_early_exit(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("desktop_smoke", ROOT / "tools/desktop_smoke.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "api.log").write_text('{"level": "INFO"}\n', encoding="utf-8")
    assert mod.problems_in_logs(logs) == []
    (logs / "worker.stderr.log").write_text("ModuleNotFoundError: x\n", encoding="utf-8")
    assert mod.problems_in_logs(logs) == ["worker.stderr.log: ModuleNotFoundError"]
    with pytest.raises(SystemExit, match="exited during start-up"):
        mod.wait_for_api(tmp_path, lambda: False, 5)
    workflow = (ROOT / ".github/workflows/desktop-build.yml").read_text(encoding="utf-8")
    assert "tools/desktop_smoke.py" in workflow


@pytest.mark.unit
@pytest.mark.tc("DESK-051")
def test_desk_051_tar_keeps_one_copy_of_each_library_under_its_soname():
    """回歸: the macOS and Linux archives hold libX -> libX.0 -> libX.0.9.0 links; the unpacker died on them."""
    import importlib.util
    import tarfile

    spec = importlib.util.spec_from_file_location("build_desktop_llama", ROOT / "tools/build_desktop_llama.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def member(name, link=None):
        m = tarfile.TarInfo(f"llama-b1/{name}")
        if link:
            m.type, m.linkname = tarfile.SYMTYPE, link
        return m

    tree = {
        "llama-server": member("llama-server"),
        "libllama.so": member("libllama.so", "libllama.so.0"),
        "libllama.so.0": member("libllama.so.0", "libllama.so.0.9.0"),
        "libllama.so.0.9.0": member("libllama.so.0.9.0"),
        "libggml-cpu-zen4.so": member("libggml-cpu-zen4.so"),
        "libllama.dylib": member("libllama.dylib", "libllama.0.dylib"),
        "libllama.0.dylib": member("libllama.0.dylib", "libllama.0.9.0.dylib"),
        "libllama.0.9.0.dylib": member("libllama.0.9.0.dylib"),
        "libllama-bench-impl.so": member("libllama-bench-impl.so"),
    }
    assert sorted(mod.from_tar(tree)) == [
        ("libggml-cpu-zen4.so", "libggml-cpu-zen4.so"),
        ("libllama.0.dylib", "libllama.0.9.0.dylib"),
        ("libllama.so.0", "libllama.so.0.9.0"),
        ("llama-server", "llama-server"),
    ]
