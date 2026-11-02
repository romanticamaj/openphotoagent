"""Output executor: plan -> approve -> journal -> execute (docs/02 §6.2, invariant 3).

copy: dst conflict check -> src hash precheck -> journal intent (durable) -> mkdirs -> tmp copy (copystat)
-> verify BLAKE3 -> fsync file + dir -> rename(noreplace) -> [move: src to .opagent-trash] -> journal done.
Never overwrites (EEXIST at commit re-applies the conflict policy) and never modifies a source in place.
undo: reverse journal order; delete dst only if its hash still equals what we wrote; remove created dirs.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import posixpath
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

import blake3
from opagent_core.planner import suffixed

from .crash import crash_point
from .fsops import TMP_PREFIX, FsOps
from .journal import Journal
from .safepath import RootUnavailable, SafePath, Sandbox

TRASH_DIR = ".opagent-trash"


@dataclass(frozen=True)
class Action:
    action_id: str
    op: Literal["copy", "move", "trash", "sidecar_json", "sidecar_csv", "sidecar_xmp"]
    src_alias: str
    src_rel: str
    dst_alias: str
    dst_rel: str
    src_hash: str
    conflict_policy: str = "rename"
    payload: dict[str, Any] | None = None


@dataclass
class ActionResult:
    action_id: str
    status: Literal["done", "skipped", "failed", "undone", "conflict"]
    dst_rel: str | None = None
    error: str | None = None
    conflict: str = "none"
    downgraded: bool = False


@dataclass
class ApplyReport:
    results: list[ActionResult] = field(default_factory=list)
    status: str = "applied"
    waiting_reason: str | None = None


class _Locked(OSError):
    pass


def _is_lock_error(e: OSError) -> bool:
    """Windows sharing violation (EACCES/winerror 32) or EBUSY: transient (OUT-EXE-016)."""
    return e.errno in (errno.EBUSY, errno.ETXTBSY) or getattr(e, "winerror", None) in (32, 33)


class _Wait(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def effective_op(sb: Sandbox, a: Action, allow_move_from_ro_roots: bool = False) -> tuple[str, bool]:
    """A move whose source root is read-only becomes a copy and is flagged ``downgraded`` (FS-018)."""
    if a.op == "move" and sb.roots[a.src_alias].mode != "rw" and not allow_move_from_ro_roots:
        return "copy", True
    return a.op, False


def _state(fs: FsOps, p: Path) -> dict[str, Any]:
    """pre/post state of a path for the journal (OUT-JRN-003)."""
    if not fs.exists(p):
        return {"exists": False, "hash": None, "mtime": None}
    return {"exists": True, "hash": fs.read_hash(p), "mtime": p.stat().st_mtime_ns}


class Executor:
    LOCK_RETRY_DELAYS = (1, 4, 10)  # OUT-EXE-016: transient sharing violation retries (seconds)

    def __init__(self, sandbox: Sandbox, fs: FsOps, journal: Journal, sleep: Any = None, clock: Any = None) -> None:
        self.sb = sandbox
        self.fs = fs
        self.journal = journal
        self._sleep = sleep or (lambda s: threading.Event().wait(s))
        self.clock = clock

    # ---- apply -----------------------------------------------------------
    def apply(
        self,
        plan_id: str,
        actions: list[Action],
        move_confirmed: bool = False,
        control: Callable[[], str | None] | None = None,
    ) -> ApplyReport:
        """Apply actions in order. ``control()`` is consulted between actions: ``pause`` stops with status
        ``paused``; ``cancel`` stops with ``partially_applied`` (OUT-EXE-017/018). A full disk or an unplugged
        target stops with ``waiting`` and a ``waiting_reason`` (OUT-EXE-014/015); re-running resumes."""
        rep = ApplyReport()
        for a in actions:
            if control is not None:
                c = control()
                if c in ("pause", "cancel"):
                    done = any(r.status == "done" for r in rep.results)
                    rep.status = "paused" if c == "pause" else ("partially_applied" if done else "cancelled")
                    return rep
            try:
                rep.results.append(self._one(plan_id, a, move_confirmed))
            except _Wait as w:
                rep.status, rep.waiting_reason = "waiting", w.reason
                return rep
            except RootUnavailable:
                rep.status, rep.waiting_reason = "waiting", "FS_ROOT_UNAVAILABLE"
                return rep
        if any(r.status == "failed" for r in rep.results):
            rep.status = "partially_applied" if any(r.status == "done" for r in rep.results) else "failed"
        return rep

    def _free_name(self, alias: str, rel: str) -> str:
        n = 1
        while self.fs.exists(self.sb.open_write(alias, suffixed(rel, n)).real):
            n += 1
        return suffixed(rel, n)

    def _one(self, plan_id: str, a: Action, move_confirmed: bool) -> ActionResult:
        if a.op in ("sidecar_json", "sidecar_csv", "sidecar_xmp"):
            return self._sidecar(plan_id, a)
        if a.op == "trash":
            return self._trash_action(plan_id, a)
        op, downgraded = effective_op(self.sb, a)
        if op == "move" and not move_confirmed:
            return ActionResult(a.action_id, "failed", error="MOVE_NOT_CONFIRMED")
        dst = self.sb.open_write(a.dst_alias, a.dst_rel)
        conflict = "none"
        if self.fs.exists(dst.real):
            if self.fs.read_hash(dst.real) == a.src_hash:
                return ActionResult(a.action_id, "skipped", dst.rel, conflict="exists_same", downgraded=downgraded)
            conflict = "exists_diff"
            if a.conflict_policy == "rename":
                dst = self.sb.open_write(a.dst_alias, self._free_name(a.dst_alias, a.dst_rel))
            elif a.conflict_policy == "fail":
                return ActionResult(a.action_id, "failed", dst.rel, "DST_EXISTS", conflict)
            else:  # skip / overwrite_if_same_hash with a different hash: never overwrite
                return ActionResult(a.action_id, "skipped", dst.rel, conflict=conflict)
        src = self.sb.resolve(a.src_alias, a.src_rel)
        if not self.fs.exists(src.real) or self.fs.read_hash(src.real) != a.src_hash:
            return ActionResult(a.action_id, "skipped", error="PLAN_STALE")
        self.sb.check_overwrite(dst)
        src_state = {"exists": True, "hash": a.src_hash, "mtime": src.real.stat().st_mtime_ns}
        crash_point("before_intent")
        seq = self.journal.intent(
            plan_id,
            a.action_id,
            op,
            {
                "src": [a.src_alias, a.src_rel],
                "dst": [a.dst_alias, dst.rel],
                "src_hash": a.src_hash,
                "planned_dirs": self._missing_dirs(dst),
            },
            {"src": src_state, "dst": {"exists": False, "hash": None, "mtime": None}},
        )
        crash_point("after_intent")
        created: list[Path] = []
        steps: list[dict[str, Any]] = []
        try:
            created = self.fs.makedirs(dst.real.parent)
            if created:
                steps.append(
                    {
                        "op": "mkdir",
                        "pre": {"exists": False},
                        "post": {"dirs": [self._rel(dst.alias, d) for d in created]},
                    }
                )
            tmp = self.fs.write_tmp_copy(src.real, dst.real.parent)
            steps.append({"op": "copy", "pre": src_state, "post": {"tmp": tmp.name}})
            crash_point("after_tmp")
        except OSError as e:
            self._rollback(plan_id, a, op, created, dst.alias, str(e.errno))
            self._raise_if_waiting(e, dst.alias, a.src_alias)
            return ActionResult(a.action_id, "failed", error="COPY_FAILED")
        try:
            got = self.fs.read_hash(tmp)
            steps.append({"op": "verify", "pre": {"expected": a.src_hash}, "post": {"hash": got}})
            if got != a.src_hash:
                raise OSError("verify failed")
            crash_point("after_verify")
            self.fs.fsync_file(tmp)
            self.fs.fsync_dir(dst.real.parent)
            steps.append({"op": "fsync", "pre": {}, "post": {"file": True, "dir": True}})
            dst = self._commit(tmp, dst, a, plan_id, op)
            steps.append({"op": "rename", "pre": {"exists": False}, "post": {"dst_rel": dst.rel, "exists": True}})
            crash_point("after_rename")
        except OSError as e:
            with contextlib.suppress(OSError):
                self.fs.unlink(tmp)
            self._rollback(plan_id, a, op, created, dst.alias, "COPY_FAILED")
            self._raise_if_waiting(e, dst.alias, a.src_alias)
            code = (
                "DST_EXISTS"
                if isinstance(e, FileExistsError)
                else "FS_FILE_LOCKED"
                if isinstance(e, _Locked)
                else "COPY_FAILED"
            )
            return ActionResult(a.action_id, "failed", error=code)
        post: dict[str, Any] = {
            "dst_rel": dst.rel,
            "dst_hash": a.src_hash,
            "created_dirs": [self._rel(dst.alias, d) for d in created],
        }
        if op == "move":
            post["trash_rel"] = self._trash(plan_id, src)
            post["trashed_at"] = self.clock.now().isoformat() if self.clock is not None else None
            steps.append({"op": "trash_move", "pre": {"src_rel": src.rel}, "post": {"trash_rel": post["trash_rel"]}})
        post["steps"] = steps
        self.journal.done(seq, post)
        return ActionResult(a.action_id, "done", dst.rel, conflict=conflict, downgraded=downgraded)

    def _missing_dirs(self, dst: SafePath) -> list[str]:
        root = self.sb.resolve(dst.alias, "").real
        out: list[str] = []
        cur = dst.real.parent
        while cur != root and not self.fs.exists(cur):
            out.append(cur.relative_to(root).as_posix())
            cur = cur.parent
        return list(reversed(out))

    def _commit(self, tmp: Path, dst: SafePath, a: Action, plan_id: str = "", op: str = "copy") -> SafePath:
        """rename(noreplace); a dst appearing after the probe is never overwritten (OUT-EXE-008)."""
        delays = list(self.LOCK_RETRY_DELAYS)
        while True:
            try:
                self.fs.rename_noreplace(tmp, dst.real)
                return dst
            except FileExistsError:
                if a.conflict_policy != "rename":
                    raise
                dst = self.sb.open_write(dst.alias, self._free_name(dst.alias, a.dst_rel))
                self.journal.record(plan_id, a.action_id, op, "redirect", {"dst_rel": dst.rel})
            except OSError as e:
                if not _is_lock_error(e):
                    raise
                if not delays:
                    raise _Locked(str(e)) from None
                self._sleep(delays.pop(0))

    def _raise_if_waiting(self, e: OSError, *aliases: str) -> None:
        if e.errno == errno.ENOSPC:
            raise _Wait("DISK_INSUFFICIENT_OUTPUT") from None
        for alias in aliases:
            self.sb._root(alias)  # raises RootUnavailable when an external drive disappeared mid-copy

    def _sidecar(self, plan_id: str, a: Action) -> ActionResult:
        """Write ``<stem>.opagent.json``, a batch CSV or an XMP sidecar atomically (tmp + rename); the photo is never
        touched (XMP-004)."""
        dst = self.sb.open_write(a.dst_alias, a.dst_rel)
        if a.op == "sidecar_csv":
            data = str((a.payload or {}).get("text", "")).encode()
        elif a.op == "sidecar_xmp":  # merged into an existing XMP, which is backed up below (XMP-003)
            from opagent_core.xmp import build_xmp

            existing = dst.real.read_bytes() if self.fs.exists(dst.real) else None
            data = build_xmp(a.payload or {}, existing)
        else:
            data = (json.dumps(a.payload or {}, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        digest = blake3.blake3(data).hexdigest()
        if self.fs.exists(dst.real) and self.fs.read_hash(dst.real) == digest:
            return ActionResult(a.action_id, "skipped", dst.rel, conflict="exists_same")
        backup_rel = None
        if self.fs.exists(dst.real):  # keep the user's sidecar: back it up so undo can restore it (OUT-UNDO-009)
            backup_rel = posixpath.join(TRASH_DIR, plan_id, "sidecar-backup", dst.rel)
            b = self.sb.open_write(dst.alias, backup_rel)
            self.fs.makedirs(b.real.parent)
            self.fs.rename_noreplace(dst.real, b.real)
        seq = self.journal.intent(
            plan_id,
            a.action_id,
            "write_sidecar",
            {"dst": [a.dst_alias, dst.rel], "digest": digest, "planned_dirs": self._missing_dirs(dst)},
            {"dst": _state(self.fs, dst.real)},
        )
        created = self.fs.makedirs(dst.real.parent)
        steps: list[dict[str, Any]] = []
        if created:
            steps.append(
                {"op": "mkdir", "pre": {"exists": False}, "post": {"dirs": [self._rel(dst.alias, d) for d in created]}}
            )
        tmp = self.fs.write_tmp_bytes(data, dst.real.parent)
        steps.append({"op": "write_sidecar", "pre": {"exists": False}, "post": {"tmp": tmp.name, "hash": digest}})
        crash_point("sidecar_partial")
        self.fs.fsync_file(tmp)
        self.fs.fsync_dir(dst.real.parent)
        steps.append({"op": "fsync", "pre": {}, "post": {"file": True, "dir": True}})
        self.fs.rename_noreplace(tmp, dst.real)
        steps.append({"op": "rename", "pre": {"exists": False}, "post": {"dst_rel": dst.rel, "exists": True}})
        self.journal.done(
            seq,
            {
                "dst_rel": dst.rel,
                "dst_hash": digest,
                "steps": steps,
                "backup_rel": backup_rel,
                "created_dirs": [self._rel(dst.alias, d) for d in created],
            },
        )
        return ActionResult(a.action_id, "done", dst.rel)

    def _trash_action(self, plan_id: str, a: Action) -> ActionResult:
        """`trash` (09b TRASH-*): into ``<root>/.opagent-trash/<plan>/<rel>`` by one atomic rename on the same root.
        A read-only root refuses — never silently turned into another action (TRASH-004)."""
        if self.sb.roots[a.src_alias].mode != "rw":
            return ActionResult(a.action_id, "failed", error="FS_NOT_WRITABLE")
        src = self.sb.open_write(a.src_alias, a.src_rel)
        if not self.fs.exists(src.real) or self.fs.read_hash(src.real) != a.src_hash:
            return ActionResult(a.action_id, "skipped", error="PLAN_STALE")
        trash_rel = posixpath.join(TRASH_DIR, plan_id, src.rel)
        crash_point("before_intent")
        seq = self.journal.intent(
            plan_id,
            a.action_id,
            "trash_move",
            {"src": [a.src_alias, src.rel], "dst": [a.src_alias, trash_rel], "src_hash": a.src_hash},
            {"src": {"exists": True, "hash": a.src_hash}, "dst": {"exists": False, "hash": None}},
        )
        crash_point("after_intent")
        try:
            self._trash(plan_id, src)
        except OSError as e:
            self.journal.record(plan_id, a.action_id, "trash_move", "rolled_back", {"error": str(e.errno)})
            self._raise_if_waiting(e, a.src_alias)
            return ActionResult(a.action_id, "failed", error="FS_FILE_LOCKED" if _is_lock_error(e) else "TRASH_FAILED")
        post = {
            "dst_rel": trash_rel,
            "dst_hash": a.src_hash,
            "trash_rel": trash_rel,
            "trashed_at": self.clock.now().isoformat() if self.clock is not None else None,
            "created_dirs": [],
            "steps": [{"op": "trash_move", "pre": {"src_rel": src.rel}, "post": {"trash_rel": trash_rel}}],
        }
        self.journal.done(seq, post)
        return ActionResult(a.action_id, "done", trash_rel)

    def _undo_trash(self, plan_id: str, e: dict[str, Any], it: dict[str, Any]) -> ActionResult:
        src_alias, src_rel = it["args"]["src"]
        src = self.sb.open_write(src_alias, src_rel)
        trash = self.sb.open_write(src_alias, e["post_state"]["trash_rel"])
        if self.fs.exists(trash.real):
            if self.fs.exists(src.real):
                return ActionResult(e["action_id"], "conflict", src.rel, "UNDO_SOURCE_OCCUPIED")
            self.fs.makedirs(src.real.parent)
            crash_point("undo_before_restore")
            self.fs.rename_noreplace(trash.real, src.real)
        elif not self.fs.exists(src.real):
            return ActionResult(e["action_id"], "conflict", src.rel, "UNDO_TRASH_EXPIRED")
        self.journal.record(plan_id, e["action_id"], "trash_move", "undone", {}, ref=e["seq"])
        return ActionResult(e["action_id"], "undone", src.rel)

    def _trash(self, plan_id: str, src: SafePath) -> str:
        """Move the source into ``<root>/.opagent-trash/<plan_id>/<rel>`` (same filesystem, so a rename)."""
        trash_rel = posixpath.join(TRASH_DIR, plan_id, src.rel)
        t = self.sb.open_write(src.alias, trash_rel)
        self.fs.makedirs(t.real.parent)
        crash_point("during_trash")
        self.fs.rename_noreplace(src.real, t.real)  # same filesystem: atomic, the file is always in one place
        self.fs.fsync_dir(t.real.parent)
        crash_point("after_trash")
        return trash_rel

    def _rel(self, alias: str, p: Path) -> str:
        return p.relative_to(self.sb.resolve(alias, "").real).as_posix()

    def _rollback(self, plan_id: str, a: Action, op: str, created: list[Path], alias: str, error: str) -> None:
        for d in reversed(created):
            if self.fs.is_empty_dir(d):
                self.fs.rmdir(d)
        self.journal.record(plan_id, a.action_id, op, "rolled_back", {"error": error})

    # ---- undo ------------------------------------------------------------
    def recover(self) -> dict[str, Any]:
        """Crash recovery (02 §6.2): every intent without done/rolled_back is rolled forward if its dst was
        committed with the expected hash, otherwise rolled back; stray temp files are removed. Idempotent."""
        completed: list[str] = []
        rolled: list[str] = []
        tmp_removed = 0
        for plan_id, aid, intent, redirect_rel in self.journal.open_intents():
            crash_point("recovery_mid")
            op = intent["op"]
            args = intent["args"]
            dst_alias, dst_rel = args["dst"]
            dst_rel = redirect_rel or dst_rel
            dst = self.sb.open_write(dst_alias, dst_rel)
            if self.fs.exists(dst.real.parent):
                for name in os.listdir(dst.real.parent):
                    if name.startswith(TMP_PREFIX):
                        self.fs.unlink(dst.real.parent / name)
                        tmp_removed += 1
            expected = args.get("digest") if op == "write_sidecar" else args.get("src_hash")
            planned = list(args.get("planned_dirs", []))
            if self.fs.exists(dst.real) and self.fs.read_hash(dst.real) == expected:
                post: dict[str, Any] = {
                    "dst_rel": dst.rel,
                    "dst_hash": expected,
                    "created_dirs": planned,
                    "recovered": True,
                    "steps": [],
                }
                if op == "trash_move":
                    post["trash_rel"] = dst.rel
                if op == "move":
                    src = self.sb.resolve(*args["src"])
                    trash_rel = posixpath.join(TRASH_DIR, plan_id, src.rel)
                    if self.fs.exists(src.real):
                        self._trash(plan_id, src)
                    post["trash_rel"] = trash_rel
                self.journal.done(intent["seq"], post)
                completed.append(aid)
            else:
                for d in reversed(planned):
                    p = self.sb.open_write(dst_alias, d).real
                    if self.fs.is_empty_dir(p):
                        self.fs.rmdir(p)
                self.journal.record(plan_id, aid, op, "rolled_back", {"error": "RECOVERED_ROLLBACK"})
                rolled.append(aid)
        return {"completed": completed, "rolled_back": rolled, "tmp_removed": tmp_removed}

    def purge_trash(self, retention_days: int = 30) -> list[str]:
        """Delete ``.opagent-trash/<plan>`` folders whose moves are older than the retention (default 30 days).
        Purges are journaled first; undo of a purged move becomes ``UNDO_TRASH_EXPIRED`` (OUT-UNDO-007)."""
        if self.clock is None:
            return []
        from datetime import datetime, timedelta

        cutoff = self.clock.now() - timedelta(days=retention_days)
        due: dict[str, set[str]] = {}
        for plan_id, alias, at in self.journal.trash_records():
            if at and datetime.fromisoformat(at) < cutoff:
                due.setdefault(plan_id, set()).add(alias)
        for plan_id, aliases in due.items():
            self.journal.record(plan_id, "*", "purge_trash", "purged", {"aliases": sorted(aliases)})
            for alias in aliases:
                self._rmtree(self.sb.open_write(alias, posixpath.join(TRASH_DIR, plan_id)).real)
        return sorted(due)

    def _rmtree(self, root: Path) -> None:
        if not self.fs.exists(root):
            return
        for dirpath, dirnames, filenames in os.walk(root, topdown=False):
            for f in filenames:
                self.fs.unlink(Path(dirpath) / f)
            for d in dirnames:
                self.fs.rmdir(Path(dirpath) / d)
        self.fs.rmdir(root)

    UNDO_PHASE_WEIGHTS: ClassVar[dict[str, int]] = {"verify": 20, "revert": 80}  # 06 §5.2 output.undo

    def undo(self, plan_id: str, reporter: Any = None, control: Any = None) -> list[ActionResult]:
        """Reverse done actions in reverse order. Idempotent: each step checks the current state, so an undo
        interrupted by a crash can simply be run again (OUT-CRASH-010)."""
        entries = self.journal.entries(plan_id)
        intents = {e["seq"]: e for e in entries if e["phase"] == "intent"}
        undone = {e["ref_seq"] for e in entries if e["phase"] == "undone" and e["ref_seq"] is not None}
        todo = [e for e in reversed([e for e in entries if e["phase"] == "done"]) if e["seq"] not in undone]
        results: list[ActionResult] = []
        if reporter is not None:
            reporter.start("output.undo", len(todo))
            reporter.phase("verify")
            for e in todo:  # verify phase: read-only hash checks
                dst = self.sb.open_write(intents[e["ref_seq"]]["args"]["dst"][0], e["post_state"]["dst_rel"])
                if self.fs.exists(dst.real):
                    self.fs.read_hash(dst.real)
            reporter.phase("revert")
        for n, e in enumerate(todo):
            if control is not None and control() == "pause":
                if reporter is not None:
                    reporter.finish("paused")
                return list(reversed(results))
            if reporter is not None:
                reporter.progress(n, len(todo))
            it = intents[e["ref_seq"]]
            if it["op"] == "trash_move":
                results.append(self._undo_trash(plan_id, e, it))
                continue
            dst_alias, _ = it["args"]["dst"]
            post = e["post_state"]
            dst = self.sb.open_write(dst_alias, post["dst_rel"])
            crash_point("undo_before_unlink")
            if self.fs.exists(dst.real):
                if self.fs.read_hash(dst.real) != post["dst_hash"]:
                    results.append(ActionResult(e["action_id"], "conflict", dst.rel, "UNDO_TARGET_MODIFIED"))
                    continue
                self.fs.unlink(dst.real)
            crash_point("undo_after_unlink")
            if it["op"] == "move":
                src_alias, src_rel = it["args"]["src"]
                src = self.sb.open_write(src_alias, src_rel)
                trash = self.sb.open_write(src_alias, post["trash_rel"])
                crash_point("undo_before_restore")
                if self.fs.exists(trash.real):
                    if self.fs.exists(src.real):
                        results.append(ActionResult(e["action_id"], "conflict", dst.rel, "UNDO_SOURCE_OCCUPIED"))
                        continue
                    self.fs.makedirs(src.real.parent)
                    self.fs.rename_noreplace(trash.real, src.real)
                elif not self.fs.exists(src.real):
                    results.append(ActionResult(e["action_id"], "conflict", dst.rel, "UNDO_TRASH_EXPIRED"))
                    continue
            if post.get("backup_rel"):
                b = self.sb.open_write(dst_alias, post["backup_rel"])
                if self.fs.exists(b.real) and not self.fs.exists(dst.real):
                    self.fs.rename_noreplace(b.real, dst.real)
            for d in reversed(post.get("created_dirs", [])):
                p = self.sb.open_write(dst_alias, d).real
                if self.fs.is_empty_dir(p):
                    self.fs.rmdir(p)
            crash_point("undo_after_rmdir")
            self.journal.record(plan_id, e["action_id"], it["op"], "undone", {}, ref=e["seq"])
            results.append(ActionResult(e["action_id"], "undone", dst.rel))
        if reporter is not None:
            reporter.finish("succeeded")
        return list(reversed(results))
