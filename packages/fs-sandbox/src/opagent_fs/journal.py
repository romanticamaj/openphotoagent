"""Append-only executor journal in local SQLite/WAL (docs/02 §3.2 journal_entry, §6.2).

Each ``intent`` is committed with ``synchronous=FULL`` before the caller performs any
side effect, so it is durable (fsync) when ``intent()`` returns (OUT-JRN-001).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id TEXT NOT NULL,
  action_id TEXT NOT NULL,
  op TEXT NOT NULL,
  phase TEXT NOT NULL,
  ref_seq INTEGER,
  args TEXT NOT NULL,
  pre_state TEXT NOT NULL,
  post_state TEXT
);
CREATE INDEX IF NOT EXISTS journal_plan ON journal(plan_id, seq);
CREATE TRIGGER IF NOT EXISTS journal_no_update BEFORE UPDATE ON journal
BEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;
CREATE TRIGGER IF NOT EXISTS journal_no_delete BEFORE DELETE ON journal
BEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;
"""


class Journal:
    def __init__(self, path: Path, on_event: Callable[[str], None] | None = None) -> None:
        self.path = Path(path)
        self.on_event = on_event
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(SCHEMA)

    def _emit(self, ev: str) -> None:
        if self.on_event:
            self.on_event(ev)

    def _insert(
        self,
        plan_id: str,
        action_id: str,
        op: str,
        phase: str,
        args: dict[str, Any],
        pre: dict[str, Any],
        post: dict[str, Any] | None = None,
        ref: int | None = None,
    ) -> int:
        cur = self._db.execute(
            "INSERT INTO journal(plan_id, action_id, op, phase, ref_seq, args, pre_state, post_state) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                plan_id,
                action_id,
                op,
                phase,
                ref,
                json.dumps(args),
                json.dumps(pre),
                None if post is None else json.dumps(post),
            ),
        )
        return int(cur.lastrowid or 0)

    def intent(self, plan_id: str, action_id: str, op: str, args: dict[str, Any], pre_state: dict[str, Any]) -> int:
        seq = self._insert(plan_id, action_id, op, "intent", args, pre_state)
        self._emit("journal.intent")
        return seq

    def done(self, seq: int, post_state: dict[str, Any]) -> None:
        row = self._db.execute("SELECT plan_id, action_id, op FROM journal WHERE seq=?", (seq,)).fetchone()
        self._insert(row[0], row[1], row[2], "done", {}, {}, post_state, ref=seq)
        self._emit("journal.done")

    def record(
        self, plan_id: str, action_id: str, op: str, phase: str, post_state: dict[str, Any], ref: int | None = None
    ) -> None:
        self._insert(plan_id, action_id, op, phase, {}, {}, post_state, ref=ref)
        self._emit(f"journal.{phase}")

    def trash_records(self) -> list[tuple[str, str, str | None]]:
        """(plan_id, source alias, trashed_at) for every committed move or trash action."""
        out = []
        purged = {r[0] for r in self._db.execute("SELECT plan_id FROM journal WHERE phase='purged'")}
        rows = self._db.execute(
            "SELECT d.plan_id, i.args, d.post_state FROM journal d JOIN journal i ON i.seq = d.ref_seq "
            "WHERE d.phase='done' AND d.op IN ('move', 'trash_move')"
        ).fetchall()
        for plan_id, args, post in rows:
            if plan_id in purged:
                continue
            out.append((plan_id, json.loads(args)["src"][0], json.loads(post).get("trashed_at")))
        return out

    def open_intents(self) -> list[tuple[str, str, dict[str, Any], str | None]]:
        """(plan_id, action_id, intent, redirected dst_rel) for intents without done/rolled_back."""
        rows = self._db.execute(
            "SELECT seq, plan_id, action_id, op, phase, ref_seq, args, pre_state, post_state FROM journal ORDER BY seq"
        ).fetchall()
        current: dict[tuple[str, str], dict[str, Any]] = {}
        redirect: dict[tuple[str, str], str] = {}
        for seq, plan, aid, op, phase, _ref, args, pre, post in rows:
            k = (plan, aid)
            if phase == "intent":
                current[k] = {"seq": seq, "op": op, "args": json.loads(args), "pre_state": json.loads(pre)}
                redirect.pop(k, None)
            elif phase == "redirect" and post:
                redirect[k] = json.loads(post)["dst_rel"]
            elif phase in ("done", "rolled_back"):
                current.pop(k, None)
        return [(k[0], k[1], v, redirect.get(k)) for k, v in current.items()]

    def entries(self, plan_id: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT seq, action_id, op, phase, ref_seq, args, pre_state, post_state FROM journal "
            "WHERE plan_id=? ORDER BY seq",
            (plan_id,),
        ).fetchall()
        return [
            {
                "seq": r[0],
                "action_id": r[1],
                "op": r[2],
                "phase": r[3],
                "ref_seq": r[4],
                "args": json.loads(r[5]),
                "pre_state": json.loads(r[6]),
                "post_state": json.loads(r[7]) if r[7] else None,
            }
            for r in rows
        ]
