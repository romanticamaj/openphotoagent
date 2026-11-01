"""FS-026: import-linter forbids apps/api and packages/db from importing fs-sandbox or inference-* (invariant 1)."""

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PKGS = ["opagent_api", "opagent_worker", "opagent_db", "opagent_core", "opagent_fs", "opagent_inference", "opagent_schemas"]


def _lint(cfg_dir: Path, pythonpath: Path | None = None) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("lint-imports") or str(Path(sys.executable).with_name("lint-imports"))
    env = None
    if pythonpath is not None:
        import os

        env = {**os.environ, "PYTHONPATH": str(pythonpath)}
    return subprocess.run([exe, "--config", str(cfg_dir / "pyproject.toml")], capture_output=True, text=True,
                          env=env, cwd=cfg_dir, check=False)


def _fake_tree(tmp_path: Path, violation: tuple[str, str] | None) -> Path:
    src = tmp_path / "src"
    for p in PKGS:
        (src / p).mkdir(parents=True)
        (src / p / "__init__.py").write_text("")
    if violation:
        importer, imported = violation
        (src / importer / "bad.py").write_text(f"import {imported}\n")
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"]
    lines = ["[tool.importlinter]", f"root_packages = {cfg['root_packages']!r}".replace("'", '"')]
    for c in cfg["contracts"]:
        lines.append("[[tool.importlinter.contracts]]")
        for k, v in c.items():
            lines.append(f"{k} = " + (repr(v).replace("'", '"') if isinstance(v, list) else f'"{v}"'))
    (tmp_path / "pyproject.toml").write_text("\n".join(lines) + "\n")
    return src


@pytest.mark.contract
@pytest.mark.tc("FS-026")
def test_fs_026_repo_satisfies_import_contracts():
    r = _lint(ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.contract
@pytest.mark.tc("FS-026")
@pytest.mark.parametrize(
    "violation",
    [("opagent_api", "opagent_fs"), ("opagent_api", "opagent_inference"), ("opagent_db", "opagent_fs"),
     ("opagent_db", "opagent_inference")],
)
def test_fs_026_violation_fails_lint(tmp_path, violation):
    src = _fake_tree(tmp_path, violation)
    r = _lint(tmp_path, src)
    assert r.returncode != 0, r.stdout
    assert "BROKEN" in r.stdout or "broken" in r.stdout.lower()


@pytest.mark.contract
@pytest.mark.tc("FS-026")
def test_fs_026_clean_fake_tree_passes(tmp_path):
    src = _fake_tree(tmp_path, None)
    assert _lint(tmp_path, src).returncode == 0
