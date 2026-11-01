"""Event fan-out: SSE /events with Last-Event-ID replay and progress throttling (06 §5.7)."""

from __future__ import annotations

from typing import Any


class ProgressThrottle:
    def __init__(self, clock: Any) -> None:
        self.clock = clock

    def should_send(self, op_id: str, fraction: float, visible: bool = True) -> bool:
        return True

    def should_send_status(self, op_id: str) -> bool:
        return False
