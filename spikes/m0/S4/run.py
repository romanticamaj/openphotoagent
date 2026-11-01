"""M0-S4 — SigLIP2 ONNX export parity (docs/09 §6, 08 S4, 11 V-3).

Exports the SigLIP2 vision and text towers to ONNX and compares embeddings from
PyTorch and ONNX Runtime per execution provider (CPU, DirectML, CoreML, CUDA).
Pass: every vector cosine ≥ 0.999.

Real run (needs Hugging Face access + the target EP):
    uv run --with torch,transformers,onnx,onnxruntime python spikes/m0/S4/run.py \
        --model google/siglip2-so400m-patch14-384 --images <500 imgs> --texts texts.txt --eps CPU,DML

Pipeline self-check without network (random-init tiny SigLIP; NOT a quality result):
    uv run ... python spikes/m0/S4/run.py --tiny-random --eps CPU --id M0-S4-pipeline
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import write_result  # noqa: E402

EP_NAMES = {"CPU": "CPUExecutionProvider", "DML": "DmlExecutionProvider",
            "COREML": "CoreMLExecutionProvider", "CUDA": "CUDAExecutionProvider"}


def _feat(out):  # type: ignore[no-untyped-def]
    # transformers >= 5 returns a ModelOutput; older versions return the tensor
    return out if hasattr(out, "numpy") else out.pooler_output


def load(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    import torch
    from transformers import AutoModel, SiglipConfig, SiglipModel

    if args.tiny_random:
        torch.manual_seed(0)
        cfg = SiglipConfig(
            text_config={"hidden_size": 64, "intermediate_size": 128, "num_hidden_layers": 2, "num_attention_heads": 4,
                         "vocab_size": 1000, "max_position_embeddings": 16},
            vision_config={"hidden_size": 64, "intermediate_size": 128, "num_hidden_layers": 2, "num_attention_heads": 4,
                           "image_size": 64, "patch_size": 16},
        )
        return SiglipModel(cfg).eval(), 64, 16
    m = AutoModel.from_pretrained(args.model).eval()
    return m, m.config.vision_config.image_size, 64


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="google/siglip2-so400m-patch14-384")
    ap.add_argument("--tiny-random", action="store_true")
    ap.add_argument("--images", type=Path)
    ap.add_argument("--texts", type=Path)
    ap.add_argument("--n-random", type=int, default=64, help="random inputs when no images/texts given")
    ap.add_argument("--eps", default="CPU")
    ap.add_argument("--id", default="M0-S4")
    a = ap.parse_args()
    import numpy as np
    import onnxruntime as ort
    import torch

    model, img_size, seq_len = load(a)

    class Vision(torch.nn.Module):
        def __init__(self, m):  # type: ignore[no-untyped-def]
            super().__init__()
            self.m = m

        def forward(self, pixel_values):  # type: ignore[no-untyped-def]
            return _feat(self.m.get_image_features(pixel_values=pixel_values))

    class Text(torch.nn.Module):
        def __init__(self, m):  # type: ignore[no-untyped-def]
            super().__init__()
            self.m = m

        def forward(self, input_ids):  # type: ignore[no-untyped-def]
            return _feat(self.m.get_text_features(input_ids=input_ids))

    g = torch.Generator().manual_seed(1)
    if a.images:
        from PIL import Image
        from transformers import AutoProcessor
        proc = AutoProcessor.from_pretrained(a.model)
        files = sorted(p for p in a.images.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
        pix = proc(images=[Image.open(f).convert("RGB") for f in files], return_tensors="pt")["pixel_values"]
        texts = a.texts.read_text(encoding="utf-8").splitlines() if a.texts else ["a photo"]
        ids = proc(text=texts, padding="max_length", max_length=seq_len, truncation=True, return_tensors="pt")["input_ids"]
    else:
        pix = torch.randn(a.n_random, 3, img_size, img_size, generator=g)
        ids = torch.randint(0, model.config.text_config.vocab_size, (a.n_random, seq_len), generator=g)

    tmp = Path(tempfile.mkdtemp(prefix="m0s4-"))
    vpath, tpath = tmp / "vision.onnx", tmp / "text.onnx"
    torch.onnx.export(Vision(model), (pix[:1],), str(vpath), input_names=["pixel_values"], output_names=["emb"],
                      dynamic_axes={"pixel_values": {0: "b"}, "emb": {0: "b"}}, opset_version=17, dynamo=False)
    torch.onnx.export(Text(model), (ids[:1],), str(tpath), input_names=["input_ids"], output_names=["emb"],
                      dynamic_axes={"input_ids": {0: "b"}, "emb": {0: "b"}}, opset_version=17, dynamo=False)
    with torch.no_grad():
        ref_v = Vision(model)(pix).numpy()
        ref_t = Text(model)(ids).numpy()

    def cos(x: "np.ndarray", y: "np.ndarray") -> "np.ndarray":
        return (x * y).sum(1) / (np.linalg.norm(x, axis=1) * np.linalg.norm(y, axis=1))

    metrics: dict[str, object] = {"model": "tiny-random" if a.tiny_random else a.model,
                                  "n_images": int(pix.shape[0]), "n_texts": int(ids.shape[0]),
                                  "available_providers": ort.get_available_providers()}
    all_ok = True
    for ep in a.eps.split(","):
        prov = EP_NAMES[ep.upper()]
        if prov not in ort.get_available_providers():
            metrics[ep] = "provider not available on this machine"
            all_ok = False
            continue
        sv = ort.InferenceSession(str(vpath), providers=[prov])
        st = ort.InferenceSession(str(tpath), providers=[prov])
        ov = sv.run(None, {"pixel_values": pix.numpy()})[0]
        ot = st.run(None, {"input_ids": ids.numpy().astype("int64")})[0]
        cv, ct = cos(ref_v, ov), cos(ref_t, ot)
        metrics[ep] = {"image_cos_min": float(cv.min()), "image_cos_mean": float(cv.mean()),
                       "text_cos_min": float(ct.min()), "text_cos_mean": float(ct.mean())}
        all_ok &= bool(cv.min() >= 0.999 and ct.min() >= 0.999)
    write_result(a.id, status="measured" if not a.tiny_random else "partial", metrics=metrics,
                 passed=all_ok if not a.tiny_random else None,
                 notes=("Random-init tiny SigLIP: validates the export/compare pipeline only, NOT real-weight parity. "
                        "Real so400m weights pending (HF blocked in cloud) — run on reference machines.")
                 if a.tiny_random else "real weights")
    print(metrics)


if __name__ == "__main__":
    main()
