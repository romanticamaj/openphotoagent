"""Deterministic face detector + embedder for tests (09b §4 替身). Never used with real photos.

Faces come from an explicit ``plan`` (file name → faces) or from the file name itself: every ``p<N>`` token in the
stem is one face of person N (``IMG_p4_p7.jpg`` → persons 4 and 7). A person's samples have cosine ≈ 0.7 with
each other; different persons ≈ 0.1. Vectors are 512-d and L2-normalised; tier-0 faces get no vector.
"""

from __future__ import annotations

import hashlib
import math
import posixpath
import random
import re
from dataclasses import dataclass
from typing import Any

from opagent_core.faces import quality_tier

DIM = 512
_C = math.sqrt(0.7)  # same person: cos ≈ C²
_SHARED = math.sqrt(0.1 / 0.7)  # all persons share a little: different persons cos ≈ 0.1


@dataclass(frozen=True)
class FaceSpec:
    person: int | None = None  # None: a stranger nobody will name
    min_side: float = 96
    yaw: float = 5
    laplacian: float = 120
    fiqa: float = 0.8
    det_score: float = 0.95
    bbox: tuple[float, float, float, float] = (0.4, 0.3, 0.2, 0.25)


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _gauss(seed: str) -> list[float]:
    r = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16))  # noqa: S311 - test double
    return [r.gauss(0, 1) for _ in range(DIM)]


class FakeFaceBackend:
    fingerprint = "fake-face-v1"

    def __init__(self, plan: dict[str, list[FaceSpec]] | None = None) -> None:
        self.plan = plan or {}
        self.calls = 0
        self._shared = _unit(_gauss("shared"))

    def base(self, person: int) -> list[float]:
        own = _unit(_gauss(f"person-{person}"))
        return _unit([_SHARED * s + math.sqrt(1 - _SHARED**2) * o for s, o in zip(self._shared, own, strict=True)])

    def sample(self, person: int | None, i: int | str) -> list[float]:
        noise = _unit(_gauss(f"noise-{person}-{i}"))
        if person is None:
            return noise
        b = self.base(person)
        return _unit([_C * x + math.sqrt(1 - _C**2) * n for x, n in zip(b, noise, strict=True)])

    def specs(self, rel: str) -> list[FaceSpec]:
        name = posixpath.basename(rel)
        if name in self.plan:
            return self.plan[name]
        stem = name.rsplit(".", 1)[0]
        return [FaceSpec(person=int(m)) for m in re.findall(r"(?:^|_)p(\d+)(?=_|$)", stem)]

    def detect(self, rel: str, image: Any = None) -> list[dict[str, Any]]:
        """bbox, landmarks, quality and (tier ≥ 1) the embedding of every face in the photo."""
        self.calls += 1
        out = []
        for i, s in enumerate(self.specs(rel)):
            tier = quality_tier(min_side=s.min_side, yaw=s.yaw, laplacian=s.laplacian, fiqa=s.fiqa)
            x, y, w, h = s.bbox
            out.append(
                {
                    "bbox": [x + 0.01 * i, y, w, h],
                    "landmarks": [
                        x + w * 0.3,
                        y + h * 0.4,
                        x + w * 0.7,
                        y + h * 0.4,
                        x + w * 0.5,
                        y + h * 0.6,
                        x + w * 0.35,
                        y + h * 0.8,
                        x + w * 0.65,
                        y + h * 0.8,
                    ],
                    "det_score": s.det_score,
                    "quality": {"min_side_px": s.min_side, "yaw": s.yaw, "blur": s.laplacian, "fiqa": s.fiqa},
                    "quality_tier": tier,
                    "vec": self.sample(s.person, f"{rel}#{i}") if tier >= 1 else None,
                    "person_hint": s.person,  # test-only ground truth; the API never stores it
                }
            )
        return out
