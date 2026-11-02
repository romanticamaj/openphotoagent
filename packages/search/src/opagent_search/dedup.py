"""Near-duplicate clustering (05 §2.2): union-find over pairs that are pHash-close (Hamming ≤ max) or
embedding-close (cosine ≥ min). Groups are transitive closures (09b SRCH-DUP-004).

pHash candidates come from multi-index hashing: the 64 bits are split into ``max_hamming + 1`` blocks, and two
hashes within ``max_hamming`` bits must agree exactly on at least one block (pigeonhole), so no pair is missed
without comparing all pairs. Embedding candidates come from an injected k-NN function (pgvector HNSW in the API)
or, by default, all pairs.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from typing import Any


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


class _UF:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _blocks(h: int, n_blocks: int) -> list[tuple[int, int]]:
    out = []
    for b in range(n_blocks):
        lo, hi = (64 * b) // n_blocks, (64 * (b + 1)) // n_blocks
        out.append((b, (h >> lo) & ((1 << (hi - lo)) - 1)))
    return out


def group_near(
    items: list[tuple[str, str | None, Any]],
    max_hamming: int = 6,
    min_cosine: float = 0.97,
    neighbors: Callable[[int], Iterable[int]] | None = None,
) -> list[list[str]]:
    """``items``: (asset_id, phash hex or None, vector or None). Returns groups of ≥ 2 ids, each sorted."""
    n = len(items)
    uf = _UF(n)
    hashes = [int(h, 16) if h else None for _, h, _ in items]
    buckets: dict[tuple[int, int], list[int]] = {}
    for i, h in enumerate(hashes):
        if h is None:
            continue
        for key in _blocks(h, max_hamming + 1):
            buckets.setdefault(key, []).append(i)
    for members in buckets.values():
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                i, j = members[x], members[y]
                hi, hj = hashes[i], hashes[j]
                if hi is not None and hj is not None and bin(hi ^ hj).count("1") <= max_hamming:
                    uf.union(i, j)
    vec_idx = [i for i, (_, _, v) in enumerate(items) if v]
    for i in vec_idx:
        cands = neighbors(i) if neighbors is not None else (j for j in vec_idx if j > i)
        for j in cands:
            if j != i and items[j][2] and cosine(items[i][2], items[j][2]) >= min_cosine:
                uf.union(i, j)
    groups: dict[int, list[str]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(items[i][0])
    return sorted(sorted(g) for g in groups.values() if len(g) > 1)
