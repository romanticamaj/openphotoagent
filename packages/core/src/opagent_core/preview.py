"""10-photo preview sampling (Q-023): cover as many folders and months as possible; deterministic per seed."""

from __future__ import annotations

import random
from typing import Any


def stratified_sample(items: list[dict[str, Any]], k: int = 10, seed: int = 42) -> list[str]:
    pool = list(items)
    random.Random(seed).shuffle(pool)  # noqa: S311 - deterministic sampling, not security
    chosen: list[dict[str, Any]] = []
    folders: set[Any] = set()
    months: set[Any] = set()
    while pool and len(chosen) < k:
        best = max(
            range(len(pool)), key=lambda i: (pool[i]["folder"] not in folders, pool[i]["month"] not in months, -i)
        )
        it = pool.pop(best)
        chosen.append(it)
        folders.add(it["folder"])
        months.add(it["month"])
    return [c["id"] for c in chosen]
