"""Pure job_item status model (02 §4.3) used to reason about counters (Q-021)."""

from __future__ import annotations

from dataclasses import dataclass, field

COUNTED = ("done", "cached", "failed", "review", "skipped", "pending", "leased", "running", "cancelled")


@dataclass
class ItemModel:
    status: dict[str, str] = field(default_factory=dict)

    @classmethod
    def new(cls, n: int) -> ItemModel:
        return cls({f"i{i}": "pending" for i in range(n)})

    def apply(self, event: str, item: str) -> None:
        pass

    def counters(self) -> dict[str, int]:
        return {"total": len(self.status)}
