"""DESK-041 (ADR-0023): application code does not find its data by walking up the source tree.

``Path(__file__).parents[N]`` only works in a checkout; the installed app (desktop bundle, wheels) has no repo
around it. Data the app reads ships as package data. The allowlist is for test fixtures that production never
reads (hw probe profiles and FakeBackend vectors: used only when a test passes a profile or semantic fakes)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_ONLY = {
    "apps/worker/src/opagent_worker/policy.py",  # FakeGpu.from_profile: tests only
    "packages/doctor/src/opagent_doctor/probes.py",  # read_profile/load_profile: hw_profile is set only by tests
    "packages/inference-sdk/src/opagent_inference/_fixtures.py",  # FakeBackend(vectors="semantic"): tests only
}


@pytest.mark.contract
@pytest.mark.tc("DESK-041")
def test_desk_041_no_source_tree_relative_data_paths():
    hits = []
    for f in sorted([*ROOT.glob("apps/*/src/**/*.py"), *ROOT.glob("packages/*/src/**/*.py")]):
        rel = f.relative_to(ROOT).as_posix()
        if "/opagent_testing/" in rel or rel in TEST_ONLY:
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.parents\[\d+\]", line):
                hits.append(f"{rel}:{n}")
    assert hits == [], hits
