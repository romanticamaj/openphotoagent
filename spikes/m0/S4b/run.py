"""M0-S4b — SigLIP2 zh-Hant zero-shot quality (docs/09 §6, 03 §9-4).

Compares top-1 accuracy and ECE on img:by-content-12-large (600 images) for three
prompt sets: zh-Hant names+descriptions, auto English translation, and ensemble.
Pass (initial, to be confirmed): zh-Hant ensemble ≥ 0.80 and not > 3 pp below English.

    uv run --with torch,transformers,pillow python spikes/m0/S4b/run.py --model google/siglip2-so400m-patch14-384 \
        --images <dir with labels.json> --prompts spikes/m0/S4b/prompts.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import write_result  # noqa: E402


def ece(conf: list[float], hit: list[bool], bins: int = 10) -> float:
    tot, n = 0.0, len(conf)
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, c in enumerate(conf) if lo < c <= hi or (b == 0 and c == 0)]
        if idx:
            acc = sum(hit[i] for i in idx) / len(idx)
            avg = sum(conf[i] for i in idx) / len(idx)
            tot += len(idx) / n * abs(acc - avg)
    return tot


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="google/siglip2-so400m-patch14-384")
    ap.add_argument("--images", type=Path, required=True)
    ap.add_argument("--prompts", type=Path, default=Path(__file__).with_name("prompts.json"))
    ap.add_argument("--temperature", type=float, default=0.05)
    ap.add_argument("--proxy", default="", help="set when --images is not img:by-content-12-large: say what it is; the pass "
                                               "criterion is then not fully evaluated (status partial, pass null)")
    a = ap.parse_args()
    import torch
    from PIL import Image
    from transformers import AutoModel, AutoProcessor

    model = AutoModel.from_pretrained(a.model).eval()
    proc = AutoProcessor.from_pretrained(a.model)
    labels = json.loads((a.images / "labels.json").read_text())
    prompts = json.loads(a.prompts.read_text(encoding="utf-8"))
    cats = list(prompts["zh"].keys())
    files = sorted(labels)

    def text_emb(texts: list[str]) -> "torch.Tensor":
        t = proc(text=texts, padding="max_length", max_length=64, truncation=True, return_tensors="pt")
        with torch.no_grad():
            e = model.get_text_features(**t)
        return torch.nn.functional.normalize(e, dim=-1)

    with torch.no_grad():
        imgs = []
        for i in range(0, len(files), 16):
            batch = [Image.open(a.images / f).convert("RGB") for f in files[i : i + 16]]
            imgs.append(model.get_image_features(**proc(images=batch, return_tensors="pt")))
        iemb = torch.nn.functional.normalize(torch.cat(imgs), dim=-1)
    sets = {"zh": text_emb([prompts["zh"][c] for c in cats]), "en": text_emb([prompts["en"][c] for c in cats])}
    sets["ensemble"] = torch.nn.functional.normalize(sets["zh"] + sets["en"], dim=-1)
    m: dict[str, object] = {"n": len(files)}
    for name, temb in sets.items():
        p = torch.softmax(iemb @ temb.T / a.temperature, dim=-1)
        conf, top = p.max(-1)
        hit = [cats[int(t)] == labels[f] for t, f in zip(top, files)]
        per = {c: [h for h, f in zip(hit, files) if labels[f] == c] for c in cats}
        m[name] = {"top1": sum(hit) / len(hit), "ece": ece(conf.tolist(), hit),
                   "recall_per_class": {c: sum(v) / len(v) for c, v in per.items() if v}}
    zh_e, en = m["ensemble"]["top1"], m["en"]["top1"]  # type: ignore[index]
    m["criterion_on_this_set"] = {"ensemble_ge_0_80": bool(zh_e >= 0.80), "ensemble_within_3pp_of_en": bool(zh_e >= en - 0.03)}
    # per-class recall of the three prompt sets, to see where a language loses
    write_result("M0-S4b", status="partial" if a.proxy else "measured", metrics=m,
                 passed=None if a.proxy else bool(zh_e >= 0.80 and zh_e >= en - 0.03),
                 notes=f"threshold 0.80 is the initial to-be-confirmed value (09 §6). {a.proxy}".strip())
    print(m)


if __name__ == "__main__":
    main()
