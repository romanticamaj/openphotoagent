import hashlib
import json
import os
import subprocess
import sys
import threading

import pytest
from opagent_worker.downloads import CancelToken
from opagent_worker.model_store import ModelStore, MoveError

MB = 1024 * 1024


def make_store(tmp_path, size, n=3):
    root = tmp_path / "models"
    blobs = root / "hf" / "models--org--m" / "blobs"
    snap = root / "hf" / "models--org--m" / "snapshots" / "main"
    blobs.mkdir(parents=True)
    snap.mkdir(parents=True)
    shas = []
    for i in range(n):
        tmp = blobs / f"tmp{i}"
        with tmp.open("wb") as f:  # sparse file with a little real data so hashes differ
            f.truncate(size)
            f.seek(size // 2)
            f.write(f"block{i}".encode())
        h = hashlib.sha256()
        with tmp.open("rb") as f:
            for chunk in iter(lambda: f.read(8 * MB), b""):
                h.update(chunk)
        sha = h.hexdigest()
        tmp.rename(blobs / sha)
        os.symlink(f"../../blobs/{sha}", snap / f"f{i}.gguf")
        shas.append(sha)
    cfg = tmp_path / "worker.json"
    cfg.write_text(json.dumps({"models_root": str(root)}))
    return ModelStore(cfg), root, shas


@pytest.mark.integration
@pytest.mark.tc("MDL-008")
@pytest.mark.parametrize(
    "size",
    [pytest.param(64 * MB, id="scaled"), pytest.param(10 * 1024 * MB // 3, id="10GB", marks=pytest.mark.nightly)],
)
def test_mdl_008_move_phases_and_switch(tmp_path, size):
    store, root, shas = make_store(tmp_path, size)
    dst = tmp_path / "other-disk" / "models"
    events = []
    res = store.move(dst, CancelToken(), on_progress=events.append, cross_fs=True)
    assert res.status == "done"
    phases = [e["phase"] for e in events if e.get("phase")]
    assert [p for i, p in enumerate(phases) if i == 0 or phases[i - 1] != p] == ["prepare", "copy", "verify", "switch"]
    fracs = [e["fraction"] for e in events]
    assert fracs == sorted(fracs) and fracs[-1] == 1.0 and any(e.get("eta_s") is not None for e in events)
    assert store.models_root == dst and json.loads((tmp_path / "worker.json").read_text())["models_root"] == str(dst)
    for i, sha in enumerate(shas):
        assert (dst / "hf/models--org--m/snapshots/main" / f"f{i}.gguf").resolve().name == sha
    assert root.exists()  # old copy kept (deleted later after confirmation / 24 h)


@pytest.mark.integration
@pytest.mark.tc("MDL-009")
def test_mdl_009_cancel_rolls_back(tmp_path):
    store, root, shas = make_store(tmp_path, 32 * MB, n=4)
    dst = tmp_path / "dst" / "models"
    tok = CancelToken()

    def on_progress(e):
        if e.get("phase") == "copy" and e["fraction"] >= 0.5:
            tok.cancel()

    res = store.move(dst, tok, on_progress=on_progress, cross_fs=True)
    assert res.status == "cancelled"
    assert store.models_root == root
    assert not dst.exists() and not (dst.parent / (dst.name + ".opagent-moving")).exists()
    assert sorted(p.name for p in (root / "hf/models--org--m/blobs").iterdir()) == sorted(shas)


@pytest.mark.chaos
@pytest.mark.tc("MDL-010")
def test_mdl_010_crash_mid_copy_never_leaves_both_unusable(tmp_path):
    store, root, shas = make_store(tmp_path, 16 * MB, n=4)
    dst = tmp_path / "dst" / "models"
    code = (
        "import sys\nfrom pathlib import Path\n"
        "from opagent_worker.model_store import ModelStore\nfrom opagent_worker.downloads import CancelToken\n"
        f"ModelStore(Path(r'{tmp_path / 'worker.json'}')).move(Path(r'{dst}'), CancelToken(), cross_fs=True)\n"
    )
    env = {**os.environ, "OPAGENT_TEST_MODE": "1", "OPAGENT_CRASH_AT": "model_move_copy", "OPAGENT_CRASH_NTH": "2"}
    p = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=120)
    assert p.returncode == 137
    store2 = ModelStore(tmp_path / "worker.json")
    assert store2.models_root == root  # pointer not switched yet: the old directory stays authoritative
    assert store2.verify(root) == []
    res = store2.recover()
    assert res.status in ("done", "rolled_back")
    usable = store2.models_root
    assert store2.verify(usable) == []
    _ = threading, MoveError
