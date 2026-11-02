"""FakeFaceBackend (09b §4 替身): deterministic faces; same person cosine ≈ 0.7 ± 0.05, different persons ≈ 0.1 ± 0.1."""

import itertools
import statistics

import pytest
from opagent_inference.fake_faces import FaceSpec, FakeFaceBackend


def _cos(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


@pytest.mark.unit
@pytest.mark.tc("FACE-CLU-001")
def test_vectors_have_the_specified_geometry():
    fb = FakeFaceBackend()
    same = [_cos(*p) for p in itertools.combinations([fb.sample(1, i) for i in range(30)], 2)]
    diff = [_cos(fb.sample(a, 0), fb.sample(b, 0)) for a in range(10) for b in range(10) if a < b]
    assert abs(statistics.mean(same) - 0.7) < 0.05 and abs(statistics.mean(diff) - 0.1) < 0.1
    assert fb.sample(3, 7) == FakeFaceBackend().sample(3, 7)  # deterministic
    assert abs(_cos(fb.sample(3, 7), fb.sample(3, 7)) - 1.0) < 1e-9 and len(fb.sample(1, 0)) == 512


@pytest.mark.unit
@pytest.mark.tc("FACE-DET-001")
def test_faces_from_plan_or_file_name():
    fb = FakeFaceBackend(plan={"crowd.jpg": [FaceSpec(person=1), FaceSpec(person=None, min_side=20)]})
    faces = fb.detect("2025/crowd.jpg")
    assert [f["quality_tier"] for f in faces] == [3, 0] and faces[1]["vec"] is None  # tier 0: bbox only
    named = fb.detect("trip/IMG_p4_p7.jpg")
    assert [f["person_hint"] for f in named] == [4, 7] and fb.calls == 2
    assert fb.detect("landscape.jpg") == []
