import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import blake3
import pytest
from opagent_fs.executor import Action, Executor
from opagent_fs.fsops import RealFs
from opagent_fs.journal import Journal
from opagent_fs.safepath import RootConfig, Sandbox


def b3(p: Path) -> str:
    return blake3.blake3(p.read_bytes()).hexdigest()


@pytest.fixture
def setup(sandbox_tree, tmp_path):
    roots = [dict(r) for r in sandbox_tree.roots]
    roots[0]["mode"] = "rw"
    sb = Sandbox([RootConfig(**r) for r in roots])
    j = Journal(tmp_path / "j.db")
    return sb, j, Executor(sb, RealFs(), j), sandbox_tree


@pytest.mark.integration
@pytest.mark.os_macos
@pytest.mark.os_windows
@pytest.mark.tc("OUT-EXE-010")
def test_out_exe_010_case_insensitive_target(setup):
    sb, j, ex, t = setup
    (t.photos / "a.jpg").write_bytes(b"one")
    (t.photos / "b.jpg").write_bytes(b"two")
    acts = [
        Action("1", "copy", "photos", "a.jpg", "sorted", "Food/a.jpg", b3(t.photos / "a.jpg")),
        Action("2", "copy", "photos", "b.jpg", "sorted", "food/A.JPG", b3(t.photos / "b.jpg")),
    ]
    res = ex.apply("p", acts).results
    assert [r.status for r in res] == ["done", "done"]
    assert sorted(os.listdir(t.sorted / "Food")) == ["a (1).JPG", "a.jpg"] or sorted(os.listdir(t.sorted / "Food")) == [
        "A (1).JPG",
        "a.jpg",
    ]


@pytest.mark.integration
@pytest.mark.tc("OUT-EXE-023")
def test_out_exe_023_idempotent_replay(setup):
    sb, j, ex, t = setup
    acts = []
    for i in range(4):
        (t.photos / f"{i}.jpg").write_bytes(os.urandom(32))
        acts.append(Action(str(i), "copy", "photos", f"{i}.jpg", "sorted", f"o/{i}.jpg", b3(t.photos / f"{i}.jpg")))
    assert [r.status for r in ex.apply("p", acts).results] == ["done"] * 4
    files_before = sorted(p.name for p in t.sorted.rglob("*"))
    again = ex.apply("p", acts).results
    assert [(r.status, r.conflict) for r in again] == [("skipped", "exists_same")] * 4
    assert sorted(p.name for p in t.sorted.rglob("*")) == files_before


@pytest.mark.unit
@pytest.mark.tc("OUT-JRN-002")
def test_out_jrn_002_append_only(tmp_path):
    j = Journal(tmp_path / "j.db")
    s1 = j.intent("p", "a", "copy", {}, {})
    s2 = j.intent("p", "b", "copy", {}, {})
    assert s2 > s1
    db = sqlite3.connect(tmp_path / "j.db")
    for sql in ("UPDATE journal SET op='x'", "DELETE FROM journal"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db.execute(sql)
    assert [e["seq"] for e in j.entries("p")] == [s1, s2]


@pytest.mark.unit
@pytest.mark.tc("OUT-JRN-003")
def test_out_jrn_003_pre_post_state_for_every_op(setup):
    sb, j, ex, t = setup
    (t.photos / "c.jpg").write_bytes(b"copy-me")
    (t.photos / "m.jpg").write_bytes(b"move-me")
    acts = [
        Action("c", "copy", "photos", "c.jpg", "sorted", "x/c.jpg", b3(t.photos / "c.jpg")),
        Action("m", "move", "photos", "m.jpg", "sorted", "x/m.jpg", b3(t.photos / "m.jpg")),
        Action("s", "sidecar_json", "photos", "c.jpg", "sorted", "x/c.opagent.json", "", payload={"schema_version": 1}),
    ]
    assert [r.status for r in ex.apply("p", acts, move_confirmed=True).results] == ["done"] * 3
    entries = j.entries("p")
    intents = {e["action_id"]: e for e in entries if e["phase"] == "intent"}
    dones = {e["action_id"]: e for e in entries if e["phase"] == "done"}
    for aid in ("c", "m", "s"):
        pre = intents[aid]["pre_state"]
        assert {"exists", "hash", "mtime"} <= set(pre["dst"]), aid
        steps = dones[aid]["post_state"]["steps"]
        for st in steps:
            assert set(st) >= {"op", "pre", "post"}
    ops = {aid: [s["op"] for s in dones[aid]["post_state"]["steps"]] for aid in dones}
    assert ops["c"] == ["mkdir", "copy", "verify", "fsync", "rename"]
    assert ops["m"] == ["copy", "verify", "fsync", "rename", "trash_move"]
    assert ops["s"] == ["write_sidecar", "fsync", "rename"]
    assert {"exists", "hash", "mtime"} <= set(intents["c"]["pre_state"]["src"])


@pytest.mark.chaos
@pytest.mark.tc("OUT-JRN-004")
def test_out_jrn_004_wal_survives_kill_9(tmp_path):
    db = tmp_path / "j.db"
    code = textwrap.dedent(f"""
        import sys
        from opagent_fs.journal import Journal
        j = Journal({str(db)!r})
        i = 0
        while True:
            j.intent("p", f"a{{i}}", "copy", {{"n": i, "pad": "x" * 2000}}, {{}})
            i += 1
            if i == 200:
                print("ready", flush=True)
    """)
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "ready"
    proc.kill()  # SIGKILL on POSIX, TerminateProcess on Windows: no handler, no cleanup
    proc.wait()
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    rows = con.execute("SELECT args FROM journal ORDER BY seq").fetchall()
    assert len(rows) >= 200
    ns = [json.loads(r[0])["n"] for r in rows]
    assert ns == list(range(len(ns)))  # contiguous: no torn/partial row survives
