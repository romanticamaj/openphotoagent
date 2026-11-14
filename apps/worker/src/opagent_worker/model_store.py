"""Models directory relocation, Operation ``model.move`` (06 §1.7; MDL-008..010).

prepare 2 % → copy 85 % (bytes) → verify 10 % → switch 3 %. Copies into ``<dst>.opagent-moving`` with a journal next
to the worker config; only the final ``switch`` (rename + atomic config update) makes the new location live, so a
crash at any earlier point leaves the old directory authoritative and recovery simply rolls back.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opagent_core.clock import Clock, SystemClock
from opagent_fs.crash import crash_point

CHUNK = 8 * 1024 * 1024
WEIGHTS = {"prepare": (0.0, 0.02), "copy": (0.02, 0.87), "verify": (0.87, 0.97), "switch": (0.97, 1.0)}


class MoveError(Exception):
    def __init__(self, code: str, **params: Any) -> None:
        super().__init__(code)
        self.code, self.params = code, params


def _hardlink_or_copy(src: Path, target: Path) -> None:
    try:
        os.link(src, target)
    except OSError:  # the target volume has no hardlinks (FAT, exFAT, some network shares)
        shutil.copyfile(src, target)


def _symlink_or_fallback(link: str, target: Path) -> None:
    """The downloader's order, symlink → hardlink → copy (DL-018): the account moving a store need not be the one
    that filled it, and only some Windows accounts may create symlinks (MDL-015)."""
    try:
        os.symlink(link, target)
    except OSError:
        _hardlink_or_copy(target.parent / link, target)


@dataclass
class MoveResult:
    status: str  # done | cancelled | rolled_back | failed
    code: str | None = None


class ModelStore:
    def __init__(self, cfg: Path, clock: Clock | None = None) -> None:
        self.cfg = Path(cfg)
        self.clock = clock or SystemClock()
        self.journal = self.cfg.with_name("model-move.json")

    @property
    def models_root(self) -> Path:
        return Path(json.loads(self.cfg.read_text(encoding="utf-8"))["models_root"])

    def _set_root(self, p: Path) -> None:
        d = json.loads(self.cfg.read_text(encoding="utf-8"))
        d["models_root"] = str(p)
        tmp = self.cfg.with_suffix(".tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        os.replace(tmp, self.cfg)

    @staticmethod
    def _files(root: Path) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        first: dict[tuple[int, int], str] = {}  # blobs sort before snapshots, so a blob is what its links point at
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                out.append({"rel": rel, "link": os.readlink(p), "size": 0})
            elif p.is_file():
                st = p.stat()
                same = first.setdefault((st.st_dev, st.st_ino), rel) if st.st_nlink > 1 else rel
                out.append(
                    {"rel": rel, "size": st.st_size} if same == rel else {"rel": rel, "hardlink": same, "size": 0}
                )
        return out

    def verify(self, root: Path) -> list[str]:
        """Problems in ``root``: blobs whose content no longer matches their sha-named file, dangling links."""
        bad = []
        for p in root.rglob("*"):
            if p.is_symlink() and not p.exists():
                bad.append(f"dangling:{p.relative_to(root)}")
            elif p.is_file() and p.parent.name == "blobs" and len(p.name) == 64 and _sha(p) != p.name:
                bad.append(f"corrupt:{p.relative_to(root)}")
        return bad

    def move(
        self,
        dst: Path,
        token: Any,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        cross_fs: bool | None = None,
    ) -> MoveResult:
        src = self.models_root
        dst = Path(dst)
        emit = on_progress or (lambda _e: None)
        if dst == src or str(dst).startswith(str(src) + os.sep):
            raise MoveError("FS_NOT_WRITABLE", path=str(dst), reason="target inside the current models folder")
        staging = dst.with_name(dst.name + ".opagent-moving")
        # prepare
        files = self._files(src)
        total = sum(f["size"] for f in files) or 1
        self.journal.write_text(
            json.dumps({"src": str(src), "dst": str(dst), "staging": str(staging), "state": "copying"}),
            encoding="utf-8",
        )
        self._emit(emit, "prepare", 1.0, 0, total)
        same_fs = (not cross_fs) and _same_device(src, dst.parent)
        if same_fs:  # a rename is instantaneous; nothing to copy or verify
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.rename(src, staging)
            return self._switch(staging, dst, emit, total)
        staging.mkdir(parents=True, exist_ok=True)
        t0 = self.clock.monotonic()
        done = 0
        for f in files:
            target = staging / f["rel"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if "link" in f:
                if not target.is_symlink():
                    _symlink_or_fallback(f["link"], target)
                continue
            if "hardlink" in f:
                _hardlink_or_copy(staging / f["hardlink"], target)
                continue
            crash_point("model_move_copy")
            with (src / f["rel"]).open("rb") as fi, target.open("wb") as fo:
                for chunk in iter(lambda: fi.read(CHUNK), b""):
                    fo.write(chunk)
                    done += len(chunk)
                    rate = done / max(self.clock.monotonic() - t0, 1e-6)
                    self._emit(emit, "copy", done / total, done, total, eta_s=(total - done) / rate if rate else None)
                    if token.requested:
                        break
            if token.requested:  # outside the with: Windows cannot delete the file while it is still open
                return self._rollback(staging, "cancelled")
        # verify (sha-named blobs are checked against their names: no need to re-hash the source)
        problems = self.verify(staging)
        self._emit(emit, "verify", 1.0, total, total)
        if problems:
            self._rollback(staging, "failed")
            raise MoveError("MODEL_CORRUPT", problems=problems[:10])
        return self._switch(staging, dst, emit, total)

    def _switch(self, staging: Path, dst: Path, emit: Callable[[dict[str, Any]], None], total: int) -> MoveResult:
        os.replace(staging, dst)
        j = json.loads(self.journal.read_text(encoding="utf-8"))
        j["state"] = "switched"
        self.journal.write_text(json.dumps(j), encoding="utf-8")
        crash_point("model_move_switch")
        self._set_root(dst)
        self.journal.unlink(missing_ok=True)
        self._emit(emit, "switch", 1.0, total, total)
        return MoveResult("done")

    def _rollback(self, staging: Path, status: str) -> MoveResult:
        shutil.rmtree(staging, ignore_errors=True)
        self.journal.unlink(missing_ok=True)
        return MoveResult(status)

    def recover(self) -> MoveResult:
        """After a crash: finish a move that already switched, otherwise roll back to the old directory."""
        if not self.journal.exists():
            return MoveResult("done")
        j = json.loads(self.journal.read_text(encoding="utf-8"))
        if j.get("state") == "switched" and Path(j["dst"]).exists():
            self._set_root(Path(j["dst"]))
            self.journal.unlink(missing_ok=True)
            return MoveResult("done")
        return self._rollback(Path(j["staging"]), "rolled_back")

    @staticmethod
    def _emit(
        emit: Callable[[dict[str, Any]], None],
        phase: str,
        within: float,
        done: int,
        total: int,
        eta_s: float | None = None,
    ) -> None:
        a, b = WEIGHTS[phase]
        emit(
            {
                "phase": phase,
                "fraction": round(a + (b - a) * min(1.0, within), 6),
                "done_bytes": done,
                "total_bytes": total,
                "eta_s": eta_s,
            }
        )


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_device(a: Path, b: Path) -> bool:
    while not b.exists():
        b = b.parent
    return os.stat(a).st_dev == os.stat(b).st_dev
