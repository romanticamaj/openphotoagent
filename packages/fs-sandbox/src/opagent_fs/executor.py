"""Output executor: plan -> approve -> journal -> execute (docs/02 §6.2, invariant 3).

copy: precheck src hash -> journal intent (durable) -> mkdirs -> tmp copy (copystat) -> verify BLAKE3
-> fsync file + dir -> rename(noreplace) -> journal done. Never overwrites; never touches the source.
undo: reverse journal order; delete dst only if its hash still equals what we wrote; remove created dirs.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Literal

from .fsops import FsOps
from .journal import Journal
from .safepath import Sandbox


@dataclass(frozen=True)
class Action:
    action_id: str
    op: Literal["copy", "move"]
    src_alias: str
    src_rel: str
    dst_alias: str
    dst_rel: str
    src_hash: str


@dataclass
class ActionResult:
    action_id: str
    status: Literal["done", "skipped", "failed", "undone", "conflict"]
    dst_rel: str | None = None
    error: str | None = None


def effective_op(sb: Sandbox, a: Action, allow_move_from_ro_roots: bool = False) -> tuple[str, bool]:
    """A move whose source root is read-only becomes a copy and is flagged ``downgraded`` (FS-018)."""
    if a.op == "move" and sb.roots[a.src_alias].mode != "rw" and not allow_move_from_ro_roots:
        return "copy", True
    return a.op, False


class Executor:
    def __init__(self, sandbox: Sandbox, fs: FsOps, journal: Journal) -> None:
        self.sb = sandbox
        self.fs = fs
        self.journal = journal

    def apply(self, plan_id: str, actions: list[Action]) -> list[ActionResult]:
        return [self._copy(plan_id, a) for a in actions]

    def _copy(self, plan_id: str, a: Action) -> ActionResult:
        src = self.sb.resolve(a.src_alias, a.src_rel)
        dst = self.sb.open_write(a.dst_alias, a.dst_rel)
        if self.fs.read_hash(src.real) != a.src_hash:
            return ActionResult(a.action_id, "skipped", error="PLAN_STALE")
        if self.fs.exists(dst.real):
            return ActionResult(a.action_id, "failed", error="DST_EXISTS")
        seq = self.journal.intent(
            plan_id,
            a.action_id,
            "copy",
            {"src": [a.src_alias, a.src_rel], "dst": [a.dst_alias, a.dst_rel], "src_hash": a.src_hash},
            {"dst_exists": False},
        )
        created = self.fs.makedirs(dst.real.parent)
        tmp = self.fs.write_tmp_copy(src.real, dst.real.parent)
        try:
            if self.fs.read_hash(tmp) != a.src_hash:
                raise OSError("verify failed")
            self.fs.fsync_file(tmp)
            self.fs.fsync_dir(dst.real.parent)
            self.fs.rename_noreplace(tmp, dst.real)
        except OSError as e:
            with contextlib.suppress(OSError):
                self.fs.unlink(tmp)
            code = "DST_EXISTS" if isinstance(e, FileExistsError) else "COPY_FAILED"
            self.journal.record(plan_id, a.action_id, "copy", "rolled_back", {"error": code})
            return ActionResult(a.action_id, "failed", error=code)
        root = self.sb.resolve(a.dst_alias, "").real
        self.journal.done(
            seq, {"dst_hash": a.src_hash, "created_dirs": [d.relative_to(root).as_posix() for d in created]}
        )
        return ActionResult(a.action_id, "done", dst_rel=dst.rel)

    def undo(self, plan_id: str) -> list[ActionResult]:
        entries = self.journal.entries(plan_id)
        intents = {e["seq"]: e for e in entries if e["phase"] == "intent"}
        undone = {e["action_id"] for e in entries if e["phase"] == "undone"}
        results: list[ActionResult] = []
        for e in reversed([e for e in entries if e["phase"] == "done"]):
            it = intents[e["ref_seq"]]
            if e["action_id"] in undone:
                continue
            dst_alias, dst_rel = it["args"]["dst"]
            dst = self.sb.open_write(dst_alias, dst_rel)
            post = e["post_state"]
            if self.fs.exists(dst.real):
                if self.fs.read_hash(dst.real) != post["dst_hash"]:
                    results.append(ActionResult(e["action_id"], "conflict", dst_rel, "UNDO_TARGET_MODIFIED"))
                    continue
                self.fs.unlink(dst.real)
            for d in reversed(post.get("created_dirs", [])):
                p = self.sb.open_write(dst_alias, d).real
                if self.fs.is_empty_dir(p):
                    self.fs.rmdir(p)
            self.journal.record(plan_id, e["action_id"], "copy", "undone", {})
            results.append(ActionResult(e["action_id"], "undone", dst_rel))
        return list(reversed(results))
