import errno
import json
import os
from pathlib import Path

import blake3
import pytest
from opagent_fs.executor import Action, Executor
from opagent_fs.fsops import RealFs
from opagent_fs.journal import Journal
from opagent_fs.safepath import RootConfig, Sandbox
from opagent_schemas.sidecar import SidecarV1
from opagent_testing.fakefs import Fault, FaultFs


def b3(p: Path) -> str:
    return blake3.blake3(p.read_bytes()).hexdigest()


@pytest.fixture
def mk(sandbox_tree, tmp_path):
    def make(faults=(), **kw):
        sb = Sandbox([RootConfig(**r) for r in sandbox_tree.roots])
        fs = FaultFs(RealFs(), list(faults))
        ex = Executor(sb, fs, Journal(tmp_path / "j.db"), **kw)
        acts = []
        for i in range(10):
            p = sandbox_tree.photos / f"{i}.jpg"
            p.write_bytes(os.urandom(64) + bytes([i]))
            acts.append(Action(f"a{i}", "copy", "photos", f"{i}.jpg", "sorted", f"out/{i}.jpg", b3(p)))
        return ex, acts, fs

    return make


@pytest.mark.chaos
@pytest.mark.tc("OUT-EXE-015")
def test_out_exe_015_target_goes_offline_then_resumes(mk, sandbox_tree):
    def unplug(*a, **k):
        os.rename(sandbox_tree.sorted, sandbox_tree.base / "unplugged")

    ex, acts, _ = mk(faults=[Fault("write_tmp_copy", nth=3, before=unplug)])
    rep = ex.apply("p", acts)
    assert rep.status == "waiting" and rep.waiting_reason == "FS_ROOT_UNAVAILABLE"
    assert [r.status for r in rep.results] == ["done", "done"]
    os.rename(sandbox_tree.base / "unplugged", sandbox_tree.sorted)
    assert not list(sandbox_tree.sorted.rglob(".opagent-tmp-*"))
    rep2 = ex.apply("p", acts)
    assert rep2.status == "applied" and len(list((sandbox_tree.sorted / "out").iterdir())) == 10


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-016")
def test_out_exe_016_locked_file_retries_then_fails(mk, sandbox_tree):
    sleeps: list[float] = []
    ex, acts, _ = mk(faults=[Fault("rename_noreplace", nth=1, err=errno.EBUSY, times=4)], sleep=sleeps.append)
    rep = ex.apply("p", acts[:2])
    assert sleeps == [1, 4, 10]
    assert (rep.results[0].status, rep.results[0].error) == ("failed", "FS_FILE_LOCKED")
    assert rep.results[1].status == "done"
    assert not list(sandbox_tree.sorted.rglob(".opagent-tmp-*"))
    sleeps.clear()
    ex2, acts2, _ = mk(faults=[Fault("rename_noreplace", nth=1, err=errno.EBUSY, times=2)], sleep=sleeps.append)
    assert ex2.apply("q", acts2[:1]).results[0].status == "done" and sleeps == [1, 4]


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-017")
def test_out_exe_017_cancel_stops_after_current_action(mk, sandbox_tree):
    ex, acts, _ = mk()
    state = {"n": 0}

    def control():
        state["n"] += 1
        return "cancel" if state["n"] > 3 else "run"

    rep = ex.apply("p", acts, control=control)
    assert rep.status == "partially_applied" and [r.status for r in rep.results] == ["done"] * 3
    undone = ex.undo("p")
    assert [r.status for r in undone] == ["undone"] * 3
    assert not (sandbox_tree.sorted / "out").exists()


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-018")
def test_out_exe_018_pause_resume_no_redo_no_skip(mk, sandbox_tree, tmp_path):
    ex, acts, fs = mk()
    rep = ex.apply("p", acts, control=iter(["run"] * 4 + ["pause"]).__next__)
    assert rep.status == "paused" and len(rep.results) == 4
    writes_before = fs.calls.get("write_tmp_copy", 0)
    rep2 = ex.apply("p", acts)
    assert rep2.status == "applied"
    assert [r.status for r in rep2.results] == ["skipped"] * 4 + ["done"] * 6
    assert fs.calls["write_tmp_copy"] - writes_before == 6
    assert len(list((sandbox_tree.sorted / "out").iterdir())) == 10


@pytest.mark.integration
@pytest.mark.contract
@pytest.mark.tc("OUT-EXE-020")
def test_out_exe_020_sidecar_json(mk, sandbox_tree):
    ex, acts, _ = mk()
    src = sandbox_tree.photos / "0.jpg"
    before = b3(src)
    payload = {
        "schema_version": 1,
        "category": "food",
        "tags": ["ramen"],
        "caption": "a bowl of ramen",
        "date_source": "exif_offset",
        "provenance": {"model": "fake", "fingerprint": "f"},
    }
    side = Action("s0", "sidecar_json", "photos", "0.jpg", "sorted", "out/0.opagent.json", before, payload=payload)
    rep = ex.apply("p", [acts[0], side])
    assert [r.status for r in rep.results] == ["done", "done"]
    doc = json.loads((sandbox_tree.sorted / "out/0.opagent.json").read_text())
    assert SidecarV1.model_validate(doc).category == "food"
    assert b3(src) == before


@pytest.mark.unit
@pytest.mark.tc("OUT-EXE-021")
def test_out_exe_021_csv_export_quoting_and_formula_injection():
    from opagent_core.export import to_csv

    rows = [
        {"file": "a.jpg", "caption": "ramen, hot\nnew line"},
        {"file": "b.jpg", "caption": "=SUM(A1)"},
        {"file": "@c.jpg", "caption": 'say "hi"'},
        {"file": "-d", "caption": "+1"},
    ]
    out = to_csv(rows, ["file", "caption"])
    assert out.splitlines()[0] == "file,caption"
    assert '"ramen, hot\nnew line"' in out
    assert "'=SUM(A1)" in out and "'@c.jpg" in out and "'-d" in out and "'+1" in out
    assert '"say ""hi"""' in out
    assert to_csv(rows[:1], ["file"], bom=True).startswith("﻿")
    assert not to_csv(rows[:1], ["file"]).startswith("﻿")


@pytest.mark.unit
@pytest.mark.tc("OUT-EXE-022")
def test_out_exe_022_plan_summary_sentence():
    from opagent_core.summary import plan_summary

    assert plan_summary(copy=28412, folders=13, lang="zh-TW") == "將複製 28,412 張到 13 個資料夾，原檔不動。可隨時復原"
    assert plan_summary(copy=28412, folders=13, lang="en") == (
        "Will copy 28,412 photos into 13 folders; originals stay untouched. You can undo anytime"
    )
    mv = plan_summary(move=5, folders=2, lang="zh-TW")
    assert mv.startswith("將移動 5 張到 2 個資料夾") and "原檔不動" not in mv and "復原" in mv
