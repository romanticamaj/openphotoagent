"""Few-shot prototypes and class scores (03 §3.3.1)."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

Vec = NDArray[np.float64]
MULTI_PROTO_MIN = 8  # ≥ 8 examples → k-medoids(k=2)


def alpha_for(n_examples: int) -> float:
    """Text weight: 1.0 with no examples, decreasing linearly to 0.4 at ≥ 5 examples."""
    return max(0.4, 1.0 - 0.12 * max(0, n_examples))


def _unit(v: Vec) -> Vec:
    n = float(np.linalg.norm(v))
    return v / n if n else v


def medoids(vecs: Vec, k: int = 2, iters: int = 20) -> Vec:
    """Deterministic k-medoids (cosine distance) seeded with the most central point and its farthest point."""
    x = np.stack([_unit(v) for v in np.asarray(vecs, dtype=float)])
    dist = 1.0 - x @ x.T
    first = int(dist.sum(1).argmin())
    centers = [first]
    while len(centers) < min(k, len(x)):
        centers.append(int(dist[centers].min(0).argmax()))
    for _ in range(iters):
        assign = dist[:, centers].argmin(1)
        new = []
        for ci in range(len(centers)):
            members = np.flatnonzero(assign == ci)
            new.append(int(members[dist[np.ix_(members, members)].sum(1).argmin()]) if len(members) else centers[ci])
        if new == centers:
            break
        centers = new
    return x[centers]


def prototypes(examples: Vec) -> Vec:
    ex = np.asarray(examples, dtype=float)
    if len(ex) >= MULTI_PROTO_MIN:
        return medoids(ex, 2)
    return _unit(ex.mean(0))[None, :]


def class_scores(
    img: Vec,
    txt: dict[str, Vec],
    examples: dict[str, Vec],
    n_examples: dict[str, int],
    negatives: dict[str, Vec] | None = None,
) -> dict[str, float]:
    """s_c = α·txt_sim + (1−α)·proto_sim. With several prototypes the max similarity is used. When the image
    is closer to the class's negative examples than to its prototypes, the gap is subtracted from proto_sim."""
    v = _unit(np.asarray(img, dtype=float))
    out: dict[str, float] = {}
    for c, t in txt.items():
        n = n_examples.get(c, 0)
        a = alpha_for(n)
        txt_sim = float(v @ _unit(np.asarray(t, dtype=float)))
        if n == 0 or c not in examples:
            out[c] = txt_sim
            continue
        proto_sim = float((prototypes(examples[c]) @ v).max())
        if negatives and c in negatives and len(negatives[c]):
            neg = np.stack([_unit(x) for x in np.asarray(negatives[c], dtype=float)])
            neg_sim = float((neg @ v).max())
            proto_sim -= max(0.0, neg_sim - proto_sim)
        out[c] = a * txt_sim + (1 - a) * proto_sim
    return out
