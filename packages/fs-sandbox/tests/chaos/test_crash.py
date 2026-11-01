"""OUT-CRASH-*: kill -9 at every executor step, then recovery; common invariant INV (09a §3.3)."""

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import blake3
import pytest

RUNNER = Path(__file__).with_name("runner.py")


def b3(p: Path) -> str:
    return blake3.blake3(p.read_bytes()).hexdigest()


def tree_snapshot(root: Path, with_mtime: bool = True) -> dict[str, object]:
    out: dict[str, object] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = "dir" if p.is_dir() else ((b3(p), p.stat().st_mtime_ns) if with_mtime else b3(p))
    return out


class Lab:
    def __init__(self, base: Path, n: int, op: str = "copy", sidecar: bool = False, seed: int = 0):
        self.base = base
        self.photos, self.sorted = base / "photos", base / "sorted"
        self.photos.mkdir(parents=True)
        self.sorted.mkdir()
        rnd = random.Random(seed)
        self.actions = []
        for i in range(n):
            rel = f"d{i % 3}/IMG_{i:04d}.jpg"
            p = self.photos / rel
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(rnd.randbytes(2000 + i))
            os.utime(p, ns=(1_600_000_000_000_000_000 + i, 1_600_000_000_000_000_000 + i))
            self.actions.append({"action_id": f"a{i}", "op": op, "src_alias": "photos", "src_rel": rel,
                                 "dst_alias": "sorted", "dst_rel": f"{2020 + i % 2}/{i % 5:02d}/IMG_{i:04d}.jpg",
                                 "src_hash": b3(p)})
            if sidecar:
                self.actions.append({"action_id": f"s{i}", "op": "sidecar_json", "src_alias": "photos", "src_rel": rel,
                                     "dst_alias": "sorted", "dst_rel": f"{2020 + i % 2}/{i % 5:02d}/IMG_{i:04d}.opagent.json",
                                     "src_hash": "", "payload": {"schema_version": 1, "i": i}})
        mode = "rw" if op == "move" else "ro"
        self.spec = base / "spec.json"
        self.spec.write_text(json.dumps({"roots": [{"alias": "photos", "path": str(self.photos), "mode": mode},
                                                   {"alias": "sorted", "path": str(self.sorted), "mode": "rw"}],
                                         "journal": str(base / "journal.db"), "plan_id": "plan",
                                         "actions": self.actions}))
        self.src_contents = {a["src_rel"]: (a["src_hash"], (self.photos / a["src_rel"]).stat().st_mtime_ns)
                             for a in self.actions if a["op"] != "sidecar_json"}
        self.sorted_before = tree_snapshot(self.sorted)

    def run(self, cmd: str, crash: str | None = None, nth: int = 1) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "OPAGENT_TEST_MODE": "1"}
        env.pop("OPAGENT_CRASH_AT", None)
        if crash:
            env.update({"OPAGENT_CRASH_AT": crash, "OPAGENT_CRASH_NTH": str(nth)})
        return subprocess.run([sys.executable, str(RUNNER), str(self.spec), cmd], env=env, capture_output=True,
                              text=True, timeout=600)

    def journal_terminal(self) -> bool:
        import sqlite3

        con = sqlite3.connect(self.base / "journal.db")
        rows = con.execute("SELECT action_id, phase FROM journal ORDER BY seq").fetchall()
        open_ = {}
        for aid, phase in rows:
            if phase == "intent":
                open_[aid] = True
            elif phase in ("done", "rolled_back"):
                open_[aid] = False
        return not any(open_.values())

    def check_inv(self, reference: dict[str, object]) -> None:
        # (a) every source still exists (in place or in .opagent-trash) with the same hash and mtime
        for rel, (h, mt) in self.src_contents.items():
            cands = [self.photos / rel, *self.photos.glob(f".opagent-trash/*/{rel}")]
            assert any(c.exists() and b3(c) == h and c.stat().st_mtime_ns == mt for c in cands), rel
        # (b) each dst is absent or identical to its source
        for a in self.actions:
            d = self.sorted / a["dst_rel"]
            if a["op"] != "sidecar_json" and d.exists():
                assert b3(d) == a["src_hash"], a["dst_rel"]
        # (c) no temp files anywhere
        assert not list(self.base.rglob(".opagent-tmp-*"))
        # (d) journal: every action ends done or rolled back
        assert self.journal_terminal()
        # (e) applying again converges to the no-crash result
        r = self.run("apply")
        assert r.returncode == 0, r.stderr
        assert tree_snapshot(self.sorted, with_mtime=False) == reference
        # (f) undo returns the output tree to its pre-apply state
        r = self.run("undo")
        assert r.returncode == 0, r.stderr
        assert tree_snapshot(self.sorted) == self.sorted_before


def reference_tree(tmp_path: Path, n: int, op: str, sidecar: bool = False) -> dict[str, object]:
    lab = Lab(tmp_path / "ref", n, op, sidecar)
    r = lab.run("apply")
    assert r.returncode == 0, r.stderr
    return tree_snapshot(lab.sorted, with_mtime=False)


CASES = [
    ("OUT-CRASH-001", "before_intent", "copy", False),
    ("OUT-CRASH-002", "after_intent", "copy", False),
    ("OUT-CRASH-003", "tmp_partial", "copy", False),
    ("OUT-CRASH-004", "after_tmp", "copy", False),
    ("OUT-CRASH-005", "after_verify", "copy", False),
    ("OUT-CRASH-006", "after_rename", "copy", False),
    ("OUT-CRASH-007", "after_trash", "move", False),
    ("OUT-CRASH-008", "during_trash", "move", False),
    ("OUT-CRASH-009", "sidecar_partial", "copy", True),
]


@pytest.mark.chaos
@pytest.mark.parametrize(("tc", "point", "op", "sidecar"), [pytest.param(*c, marks=pytest.mark.tc(c[0])) for c in CASES])
def test_out_crash_point_recovers(tmp_path, tc, point, op, sidecar):
    ref = reference_tree(tmp_path, 6, op, sidecar)
    lab = Lab(tmp_path / "lab", 6, op, sidecar)
    r = lab.run("apply", crash=point, nth=2)
    assert r.returncode == 137, f"crash point {point} not reached: {r.stdout} {r.stderr}"
    r = lab.run("recover")
    assert r.returncode == 0, r.stderr
    lab.check_inv(ref)


UNDO_POINTS = ["undo_before_unlink", "undo_after_unlink", "undo_before_restore", "undo_after_rmdir"]


@pytest.mark.chaos
@pytest.mark.tc("OUT-CRASH-010")
@pytest.mark.parametrize("point", UNDO_POINTS)
@pytest.mark.parametrize("op", ["copy", "move"])
def test_out_crash_010_crash_during_undo(tmp_path, point, op):
    lab = Lab(tmp_path / "lab", 6, op)
    assert lab.run("apply").returncode == 0
    r = lab.run("undo", crash=point, nth=2)
    if point == "undo_before_restore" and op == "copy":
        assert r.returncode == 0  # copies have no restore step
    else:
        assert r.returncode == 137, r.stderr
    assert lab.run("recover").returncode == 0
    assert lab.run("undo").returncode == 0
    assert tree_snapshot(lab.sorted) == lab.sorted_before
    for rel, (h, mt) in lab.src_contents.items():
        assert b3(lab.photos / rel) == h and (lab.photos / rel).stat().st_mtime_ns == mt


@pytest.mark.chaos
@pytest.mark.tc("OUT-CRASH-011")
def test_out_crash_011_recovery_is_idempotent_even_if_it_crashes(tmp_path):
    ref = reference_tree(tmp_path, 6, "move")
    lab = Lab(tmp_path / "lab", 6, "move")
    assert lab.run("apply", crash="after_rename", nth=3).returncode == 137
    assert lab.run("recover", crash="recovery_mid", nth=1).returncode == 137
    first = lab.run("recover")
    second = lab.run("recover")
    assert first.returncode == 0 and second.returncode == 0
    assert json.loads(second.stdout) == {"completed": [], "rolled_back": [], "tmp_removed": 0}
    lab.check_inv(ref)


@pytest.mark.chaos
@pytest.mark.nightly
@pytest.mark.tc("OUT-CRASH-012")
def test_out_crash_012_ten_thousand_actions_fifty_random_crashes(tmp_path):
    n, crashes = 10_000, 50
    rnd = random.Random(12)
    ref = reference_tree(tmp_path, n, "move")
    lab = Lab(tmp_path / "lab", n, "move")
    points = [p for _, p, op, sc in CASES if not sc]
    for _ in range(crashes):
        r = lab.run("apply", crash=rnd.choice(points), nth=rnd.randint(1, 40))
        assert r.returncode in (0, 137), r.stderr
        assert lab.run("recover").returncode == 0
    lab.check_inv(ref)
