"""DOCK-004 (回歸): the all-in-one image carries every data file the app reads at runtime.

The first CI run of docker.yml built an image without `registry/`: the catalog loaded empty and job.start answered
500 (KeyError in recommend). Docker is not available everywhere, so this rebuilds the image's /app tree from the
Dockerfile's own COPY lines and loads, from that tree alone, what `opagent dev` loads."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("node_modules", "__pycache__", "dist", "*.pyc", ".pytest_cache")
PROBE = r"""
import sys
from pathlib import Path
app = Path(sys.argv[1])
sys.path[:0] = [str(p) for p in sorted(app.glob("packages/*/src")) + sorted(app.glob("apps/*/src"))]
import opagent_registry.loader as L, opagent_api.organize as O, opagent_doctor.probes as P, opagent_inference._fixtures as F
for mod, path in ((L, L.BUILTIN_DIR), (O, O.PRESETS_DIR), (P, P.FIXTURES), (F, F.FIXTURES)):
    assert str(Path(mod.__file__).resolve()).startswith(str(app)), mod.__file__  # loaded from the image tree
reg = L.load_registry()
assert "siglip2-so400m-384" in reg.models and reg.runtimes, ("empty catalog", sorted(reg.models))
assert any(O.PRESETS_DIR.glob("*.yaml")), O.PRESETS_DIR
P.load_profile("rtx5070ti-win11")
F.fake_vectors()
print("ok")
"""


def _app_copies() -> list[tuple[str, str]]:
    text = (ROOT / "deploy/docker/Dockerfile").read_text(encoding="utf-8")
    stage = text.split(" AS app", 1)[1]
    out = []
    for m in re.finditer(r"^COPY (?!--from)(\S+) (\S+)$", stage, re.M):
        out.append((m.group(1), m.group(2)))
    return out


@pytest.mark.contract
@pytest.mark.tc("DOCK-004")
def test_dock_004_image_tree_has_the_runtime_data(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    for src, dst in _app_copies():
        s = ROOT / src.rstrip("/")
        d = app / dst.rstrip("/") if dst not in ("./", ".") else app / s.name
        if s.is_dir():
            shutil.copytree(s, d, ignore=IGNORE, dirs_exist_ok=True)
        else:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d if not dst.endswith("/") else d / s.name)
    r = subprocess.run([sys.executable, "-c", PROBE, str(app)], capture_output=True, text=True, check=False)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr[-2000:]
