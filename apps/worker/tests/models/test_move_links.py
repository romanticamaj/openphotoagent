"""MDL-014/015 (回歸): moving the model folder keeps the links the downloader made, on an account without symlinks.

Without the symlink privilege (most Windows accounts) a snapshot is a hardlink to its blob (DL-018). The move saw two
regular files and copied each model twice, doubling its size on the target disk; and a store that does hold symlinks
could not be moved at all, because os.symlink had no fallback."""

import os
from pathlib import Path

import pytest
from opagent_worker.downloads import CancelToken
from opagent_worker.model_store import ModelStore

SIZE = 64 * 1024


def make_blob(tmp_path: Path) -> tuple[ModelStore, Path, Path]:
    import hashlib
    import json

    root = tmp_path / "models"
    repo = root / "hf" / "models--org--m"
    blobs, snap = repo / "blobs", repo / "snapshots" / "main"
    blobs.mkdir(parents=True)
    snap.mkdir(parents=True)
    data = b"x" * SIZE
    blob = blobs / hashlib.sha256(data).hexdigest()
    blob.write_bytes(data)
    cfg = tmp_path / "worker.json"
    cfg.write_text(json.dumps({"models_root": str(root)}), encoding="utf-8")
    return ModelStore(cfg), blob, snap / "f.gguf"


def rel_in(dst: Path, root: Path, p: Path) -> Path:
    return dst / p.relative_to(root)


@pytest.mark.unit
@pytest.mark.tc("MDL-014")
def test_mdl_014_a_hardlinked_snapshot_is_copied_once(tmp_path):
    store, blob, snap = make_blob(tmp_path)
    os.link(blob, snap)
    root, dst = store.models_root, tmp_path / "other-disk" / "models"
    events = []
    assert store.move(dst, CancelToken(), on_progress=events.append, cross_fs=True).status == "done"
    assert events[-1]["total_bytes"] == SIZE  # not twice: the estimate is what the target disk will really hold
    moved_blob, moved_snap = rel_in(dst, root, blob), rel_in(dst, root, snap)
    assert moved_snap.read_bytes() == blob.read_bytes()
    assert os.path.samefile(moved_blob, moved_snap)


@pytest.mark.unit
@pytest.mark.tc("MDL-015")
def test_mdl_015_a_symlinked_store_moves_without_the_symlink_privilege(tmp_path, monkeypatch):
    store, blob, snap = make_blob(tmp_path)
    link = f"../../blobs/{blob.name}"
    root, dst = store.models_root, tmp_path / "other-disk" / "models"
    real_files = ModelStore._files

    def files_with_a_link(r):  # what _files reports for a symlink; creating one here would itself need the privilege
        return [*real_files(r), {"rel": snap.relative_to(root).as_posix(), "link": link, "size": 0}]

    def no_privilege(*_a, **_k):
        raise OSError(1314, "A required privilege is not held by the client")

    monkeypatch.setattr(ModelStore, "_files", staticmethod(files_with_a_link))
    monkeypatch.setattr(os, "symlink", no_privilege)
    assert store.move(dst, CancelToken(), cross_fs=True).status == "done"
    assert os.path.samefile(rel_in(dst, root, blob), rel_in(dst, root, snap))
