"""VIS-004: visual baselines are rendered only in the official Playwright image pinned to the repo's Playwright
version (ADR-0019). Pinned font files fix the glyphs, not the rasterizer (hinting, subpixel, fallbacks), so a
baseline from any other environment differs by ~1 % of its pixels — CI's first run showed exactly that."""

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github/workflows"


def _pw_version() -> str:
    pkg = json.loads((ROOT / "apps/web/package.json").read_text(encoding="utf-8"))
    return str({**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}["@playwright/test"]).lstrip("^~")


def _jobs(name: str) -> dict:
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))["jobs"]


def _runs(job: dict) -> str:
    return "\n".join(str(s.get("run", "")) for s in job.get("steps", []))


@pytest.mark.contract
@pytest.mark.tc("VIS-004")
def test_vis_004_visual_runs_only_in_the_pinned_image():
    image = f"mcr.microsoft.com/playwright:v{_pw_version()}-noble"
    visual_jobs = []
    for wf in sorted(WF.glob("*.yml")):
        for jname, job in (yaml.safe_load(wf.read_text(encoding="utf-8")).get("jobs") or {}).items():
            cmds = _runs(job)
            runs_visual = "--project=visual" in cmds or re.search(r"just visual\b", cmds) is not None
            runs_all = re.search(r"just e2e\b(?!-)", cmds) is not None  # `just e2e` = both projects
            assert not runs_all, f"{wf.name}:{jname} runs the visual project outside the pinned image"
            if runs_visual:
                c = job.get("container")
                got = c.get("image") if isinstance(c, dict) else c
                assert got == image, f"{wf.name}:{jname} renders baselines in {got!r}, not {image}"
                visual_jobs.append(f"{wf.name}:{jname}")
    assert any(j.startswith("nightly.yml:") for j in visual_jobs), visual_jobs  # 09 §5.1: visual is nightly
    assert any(j.startswith("visual-baselines.yml:") for j in visual_jobs), visual_jobs  # how baselines are made
    assert "workflow_dispatch" in yaml.safe_load((WF / "visual-baselines.yml").read_text(encoding="utf-8"))[True]
    assert re.search(r"just e2e-pr\b", _runs(_jobs("ci.yml")["e2e-smoke"]))
    recipe = re.search(r"^e2e-pr:\n((?:[ \t]+.*\n?)+)", (ROOT / "justfile").read_text(encoding="utf-8"), re.M)
    assert recipe and "--project=chromium" in recipe.group(1) and "visual" not in recipe.group(1)
