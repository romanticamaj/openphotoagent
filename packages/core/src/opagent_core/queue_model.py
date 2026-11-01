"""Pure job_item status model (02 §4.3). Every event keeps the counters partition intact (Q-021)."""

from __future__ import annotations

from dataclasses import dataclass, field

COUNTED = ("done", "cached", "failed", "review", "skipped", "pending", "leased", "running", "cancelled")
TERMINAL = {"done", "cached", "failed", "skipped", "cancelled", "review"}
_TRANSITIONS = {
    "lease": ({"pending"}, "leased"),
    "result": ({"leased", "running"}, "done"),
    "fail": ({"leased", "running"}, "pending"),
    "fail_perm": ({"leased", "running"}, "failed"),
    "expire": ({"leased", "running"}, "pending"),
    "cancel": ({"pending", "leased", "running"}, "cancelled"),
    "cache": ({"pending"}, "cached"),
}


@dataclass
class ItemModel:
    status: dict[str, str] = field(default_factory=dict)

    @classmethod
    def new(cls, n: int) -> ItemModel:
        return cls({f"i{i}": "pending" for i in range(n)})

    def apply(self, event: str, item: str) -> bool:
        """Apply if legal for the item's current status; otherwise a no-op (e.g. a late duplicate)."""
        src, dst = _TRANSITIONS[event]
        if self.status.get(item) in src:
            self.status[item] = dst
            return True
        return False

    def counters(self) -> dict[str, int]:
        out = {k: 0 for k in COUNTED}
        for s in self.status.values():
            out[s] += 1
        out["total"] = len(self.status)
        return out
