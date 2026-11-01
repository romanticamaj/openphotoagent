"""Worker agent: leases tasks from the API over the Worker protocol and executes them (docs/02 §5.3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class WorkerAgent:
    def __init__(self, client: Any, roots: list[dict[str, str]], home: Path, backend: Any = None) -> None:
        self.client = client

    def register(self) -> None:
        pass

    def run_until_idle(self) -> None:
        pass
