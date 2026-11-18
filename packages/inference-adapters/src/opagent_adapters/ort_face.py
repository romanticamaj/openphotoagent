"""Face detection and embedding on ONNX Runtime (07 §1.4, §2.2): YuNet finds faces and five landmarks, a similarity
transform aligns each one to the ArcFace 112x112 template, AuraFace embeds it. Runs only on the worker, on the decoded
image; nothing here touches a path or the network. Same output shape as ``FakeFaceBackend.detect``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import onnxruntime as ort
from opagent_core.faces import quality_tier
from PIL import Image

DET_SIDE = 640  # YuNet 2023mar has a fixed input
STRIDES = (8, 16, 32)
MAX_FACES = 50
# ArcFace's canonical landmark positions in a 112x112 crop: eyes, nose, mouth corners
TEMPLATE = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]],
    dtype=np.float64,
)


def decode_yunet(outputs: dict[str, np.ndarray], side: int, score_threshold: float) -> np.ndarray:
    """Rows of ``x, y, w, h, 5 x (lx, ly), score`` in input pixels (the decoding OpenCV's FaceDetectorYN does)."""
    rows = []
    for stride in STRIDES:
        cols = side // stride
        cls = outputs[f"cls_{stride}"].reshape(-1)
        obj = outputs[f"obj_{stride}"].reshape(-1)
        box = outputs[f"bbox_{stride}"].reshape(-1, 4)
        kps = outputs[f"kps_{stride}"].reshape(-1, 10)
        score = np.sqrt(np.clip(cls, 0, 1) * np.clip(obj, 0, 1))
        for i in np.nonzero(score >= score_threshold)[0]:
            r, c = divmod(int(i), cols)
            cx, cy = (c + box[i, 0]) * stride, (r + box[i, 1]) * stride
            w, h = math.exp(box[i, 2]) * stride, math.exp(box[i, 3]) * stride
            flat = [(kps[i, 2 * n] + c) * stride if axis == 0 else (kps[i, 2 * n + 1] + r) * stride
                    for n in range(5) for axis in (0, 1)]  # fmt: skip
            rows.append([cx - w / 2, cy - h / 2, w, h, *flat, float(score[i])])
    return np.array(rows, dtype=np.float64).reshape(-1, 15)


def nms(rows: np.ndarray, threshold: float) -> np.ndarray:
    order = np.argsort(-rows[:, 14])
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        x1 = np.maximum(rows[i, 0], rows[rest, 0])
        y1 = np.maximum(rows[i, 1], rows[rest, 1])
        x2 = np.minimum(rows[i, 0] + rows[i, 2], rows[rest, 0] + rows[rest, 2])
        y2 = np.minimum(rows[i, 1] + rows[i, 3], rows[rest, 1] + rows[rest, 3])
        inter = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
        union = rows[i, 2] * rows[i, 3] + rows[rest, 2] * rows[rest, 3] - inter
        order = rest[inter / np.maximum(union, 1e-9) <= threshold]
    return rows[keep]


def similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Least-squares scale + rotation + translation taking ``src`` to ``dst`` (Umeyama), as a 2x3 matrix."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    s, d = src - mu_s, dst - mu_d
    u, sig, vt = np.linalg.svd(d.T @ s / len(src))
    sign = np.ones(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        sign[-1] = -1
    rot = (u * sign) @ vt
    scale = float((sig * sign).sum() / max((s**2).sum() / len(src), 1e-12))
    t = mu_d - scale * rot @ mu_s
    return np.hstack([scale * rot, t[:, None]])


def align(image: Image.Image, landmarks: np.ndarray) -> Image.Image:
    m = similarity(landmarks.reshape(5, 2).astype(np.float64), TEMPLATE)
    inverse = np.linalg.inv(np.vstack([m, [0, 0, 1]]))[:2]  # PIL maps output pixels back to input pixels
    return image.transform((112, 112), Image.Transform.AFFINE, tuple(inverse.reshape(-1)), Image.Resampling.BILINEAR)


def yaw_degrees(landmarks: np.ndarray) -> float:
    """From how far the nose sits off the line between the eyes: a heuristic, used only for quality tiers."""
    left, right, nose = landmarks[0], landmarks[1], landmarks[2]
    a, b = abs(nose[0] - left[0]), abs(right[0] - nose[0])
    return float(90.0 * (a - b) / max(a + b, 1e-6))


def sharpness(face: Image.Image) -> float:
    """Variance of the Laplacian: low on a blurred face."""
    g = np.asarray(face.convert("L"), dtype=np.float64)
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


class OrtFaceBackend:
    def __init__(
        self,
        detector: str | bytes,
        embedder: str | bytes,
        *,
        providers: Sequence[str] = ("CPUExecutionProvider",),
        fingerprint: str = "ort-face",
        score_threshold: float = 0.7,
        nms_threshold: float = 0.3,
    ) -> None:
        self.detector, self.embedder = detector, embedder
        self.providers = list(providers)
        self.fingerprint = fingerprint
        self.score_threshold, self.nms_threshold = score_threshold, nms_threshold
        self._det: ort.InferenceSession | None = None
        self._emb: ort.InferenceSession | None = None

    def describe(self) -> Any:
        return type("FaceInfo", (), {"model_fingerprint": self.fingerprint, "task_types": ("face",)})()

    def _sessions(self) -> tuple[ort.InferenceSession, ort.InferenceSession]:
        if self._det is None or self._emb is None:
            self._det = ort.InferenceSession(
                self.detector, providers=["CPUExecutionProvider"]
            )  # 0.2 MB: not worth a GPU
            self._emb = ort.InferenceSession(self.embedder, providers=self.providers)
        return self._det, self._emb

    def _find(self, image: Image.Image) -> tuple[np.ndarray, float]:
        det, _ = self._sessions()
        scale = DET_SIDE / max(image.size)
        small = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))))
        canvas = np.zeros((DET_SIDE, DET_SIDE, 3), dtype=np.float32)
        canvas[: small.height, : small.width] = np.asarray(small, dtype=np.float32)[:, :, ::-1]  # the model reads BGR
        blob = canvas.transpose(2, 0, 1)[None]
        names = [o.name for o in det.get_outputs()]
        outputs = dict(zip(names, det.run(None, {det.get_inputs()[0].name: blob}), strict=True))
        rows = nms(decode_yunet(outputs, DET_SIDE, self.score_threshold), self.nms_threshold)
        return rows[:MAX_FACES], scale

    def _embed(self, face: Image.Image) -> list[float]:
        _, emb = self._sessions()
        x = (np.asarray(face, dtype=np.float32) - 127.5) / 127.5
        v = emb.run(None, {emb.get_inputs()[0].name: x.transpose(2, 0, 1)[None]})[0][0]
        n = float(np.linalg.norm(v))
        return [float(a) / n for a in v] if n else [float(a) for a in v]

    def detect(self, rel: str, image: Any = None) -> list[dict[str, Any]]:
        """bbox, landmarks, quality and (tier >= 1) the embedding of every face. Coordinates are 0..1 of the photo."""
        rgb: Image.Image = image.convert("RGB")
        rows, scale = self._find(rgb)
        width, height = rgb.size
        out = []
        for row in rows:
            x, y, w, h = (row[:4] / scale).tolist()
            pts = (row[4:14] / scale).reshape(5, 2)
            face = align(rgb, pts)
            det_score = float(row[14])
            quality = {
                "min_side_px": float(min(w, h)),
                "yaw": yaw_degrees(pts),
                "blur": sharpness(face),
                "fiqa": det_score,  # the heuristic stage of 07 §1.3: eDifFIQA(T) is not installed
            }
            tier = quality_tier(
                min_side=quality["min_side_px"], yaw=quality["yaw"], laplacian=quality["blur"], fiqa=quality["fiqa"]
            )
            out.append(
                {
                    "bbox": [x / width, y / height, w / width, h / height],
                    "landmarks": [v for px, py in pts for v in (float(px) / width, float(py) / height)],
                    "det_score": det_score,
                    "quality": quality,
                    "quality_tier": tier,
                    "vec": self._embed(face) if tier >= 1 else None,
                }
            )
        return out
