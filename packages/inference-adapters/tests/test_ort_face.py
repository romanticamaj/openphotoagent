"""The real face path (07 §2.2): decoding, NMS and alignment need no weights, so they run everywhere."""

import numpy as np
import pytest
from opagent_adapters.ort_face import TEMPLATE, align, decode_yunet, nms, sharpness, similarity, yaw_degrees
from PIL import Image


def heads(side: int) -> dict[str, np.ndarray]:
    out = {}
    for s in (8, 16, 32):
        n = (side // s) ** 2
        out |= {
            f"cls_{s}": np.zeros((1, n, 1), np.float32),
            f"obj_{s}": np.zeros((1, n, 1), np.float32),
            f"bbox_{s}": np.zeros((1, n, 4), np.float32),
            f"kps_{s}": np.zeros((1, n, 10), np.float32),
        }
    return out


@pytest.mark.unit
@pytest.mark.tc("FACE-ORT-001")
def test_face_ort_001_decode_places_a_face_at_its_grid_cell():
    h = heads(64)
    cols = 64 // 8
    cell = 2 * cols + 3  # row 2, column 3 at stride 8
    h["cls_8"][0, cell, 0] = 0.9
    h["obj_8"][0, cell, 0] = 0.9
    h["bbox_8"][0, cell] = [0.5, 0.5, 1.0, 0.0]  # centre offset, log size
    h["kps_8"][0, cell, 0:2] = [0.25, 0.75]
    rows = decode_yunet(h, 64, 0.7)
    assert rows.shape == (1, 15)
    x, y, w, ht = rows[0, :4]
    assert w == pytest.approx(np.e * 8) and ht == pytest.approx(8)
    assert x + w / 2 == pytest.approx((3 + 0.5) * 8) and y + ht / 2 == pytest.approx((2 + 0.5) * 8)
    assert rows[0, 4:6].tolist() == pytest.approx([(3 + 0.25) * 8, (2 + 0.75) * 8])
    assert rows[0, 14] == pytest.approx(0.9)


@pytest.mark.unit
@pytest.mark.tc("FACE-ORT-001")
def test_face_ort_001_scores_below_the_threshold_are_dropped():
    h = heads(64)
    h["cls_8"][0, 0, 0] = 0.4
    h["obj_8"][0, 0, 0] = 0.9
    assert decode_yunet(h, 64, 0.7).shape == (0, 15)


@pytest.mark.unit
@pytest.mark.tc("FACE-ORT-002")
def test_face_ort_002_nms_keeps_the_best_of_overlapping_boxes():
    def row(x, score):
        return [x, 0, 10, 10, *[0] * 10, score]

    rows = np.array([row(0, 0.8), row(1, 0.9), row(50, 0.7)], dtype=np.float64)
    kept = nms(rows, 0.3)
    assert kept[:, 14].tolist() == [0.9, 0.7]


@pytest.mark.unit
@pytest.mark.tc("FACE-ORT-003")
def test_face_ort_003_similarity_recovers_scale_rotation_and_translation():
    a = np.deg2rad(20)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    src = TEMPLATE / 2.5 + 7
    dst = (1.7 * rot @ src.T).T + np.array([3.0, -4.0])
    m = similarity(src, dst)
    assert np.allclose(m[:, :2], 1.7 * rot, atol=1e-6) and np.allclose(m[:, 2], [3, -4], atol=1e-6)


@pytest.mark.unit
@pytest.mark.tc("FACE-ORT-003")
def test_face_ort_003_alignment_puts_the_landmarks_on_the_template():
    """A bright dot on each landmark of a bigger picture must land on the template's position in the crop."""
    big = np.zeros((448, 448, 3), np.uint8)
    pts = TEMPLATE * 3 + np.array([20.0, 30.0])
    y, x = round(pts[0, 1]), round(pts[0, 0])
    big[y - 6 : y + 6, x - 6 : x + 6] = 255
    face = np.asarray(align(Image.fromarray(big), pts).convert("L"), dtype=np.float64)
    cy, cx = np.argwhere(face > 128).mean(0)
    assert (cx, cy) == pytest.approx((TEMPLATE[0, 0], TEMPLATE[0, 1]), abs=1.5)


@pytest.mark.unit
@pytest.mark.tc("FACE-ORT-004")
def test_face_ort_004_quality_signals():
    front = TEMPLATE.copy()
    assert abs(yaw_degrees(front)) < 5
    turned = front.copy()
    turned[2, 0] = turned[1, 0] - 2
    assert yaw_degrees(turned) > 45
    noise = np.random.default_rng(1).integers(0, 255, (112, 112), dtype=np.uint8)
    assert sharpness(Image.fromarray(noise)) > sharpness(Image.new("L", (112, 112), 128)) == 0
