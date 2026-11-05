"""Build the public-photo stand-in for M0-S3/S3b/S4/S4b from COCO val2017 (no personal photos, nothing committed).

    python spikes/m0/make_coco_subset.py --coco D:/datasets/coco --out D:/datasets/m0

COCO gives no 12-way "photo category" label, so each label here is DERIVED, not ground truth:

* object classes come from instance areas (fraction of the image, annotated by people);
* landscape / city / events come from the 5 human captions (keyword votes), because COCO has no such object;
* an image with a mixed signal is dropped rather than guessed.

`travel`, `documents` and `screenshots` cannot be derived from COCO at all: those classes have 0 images. So a result
on this set is a proxy for `img:by-content-12-large` (09 §6), not that set. Writes subset1000/, subset600/, subset500/
(hardlinks into val2017/) each with labels.json, plus manifest.json with the per-class counts.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

PETS = {"cat", "dog"}
WILD = {"bird", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe"}
VEH = {"bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat"}
OBJ_SUPER = {"electronic", "appliance", "furniture", "kitchen", "indoor", "accessory"}
EVENT = re.compile(r"\b(wedding|bride|groom|birthday|party|concert|ceremony|celebrat\w*|graduation|parade|festival)\b")
LAND = re.compile(r"\b(mountains?|beach|ocean|sea|lake|valley|hills?|hillside|sunset|sunrise|countryside|shore|desert|waterfall|snowy slope|clouds?)\b")
CITY = re.compile(r"\b(city|street|downtown|skyline|buildings?|intersection|sidewalk|skyscrapers?|tower|plaza|urban)\b")
PLANT = re.compile(r"\b(flowers?|plants?|vase of|bouquet|garden|forest|trees?)\b")


def label_of(areas: dict[str, float], caps: list[str]) -> str | None:
    def votes(rx: re.Pattern[str]) -> int:
        return sum(1 for c in caps if rx.search(c.lower()))

    person = areas.get("person", 0.0)
    pets = areas.get("pets", 0.0)
    wild = areas.get("wild", 0.0)
    veh = areas.get("vehicle", 0.0)
    food = areas.get("food", 0.0)
    obj = areas.get("object", 0.0)
    total = person + pets + wild + veh + food + obj
    if votes(EVENT) >= 2 and person > 0.05:
        return "events"
    if pets >= 0.10 and person < 0.05:
        return "pets"
    if food >= 0.25 and person < 0.05:
        return "food"
    if wild >= 0.10 and person < 0.03 and veh < 0.03:
        return "nature"
    if votes(PLANT) >= 3 and total < 0.30 and person < 0.03:
        return "nature"
    if person >= 0.20 and max(pets, wild, veh, food, obj) < 0.10:
        return "people"
    if veh >= 0.20 and person < 0.03:
        return "vehicles"
    if votes(LAND) >= 3 and total < 0.10:
        return "landscape"
    if votes(CITY) >= 3 and total < 0.30 and person < 0.05 and veh < 0.15:
        return "city"
    if obj >= 0.20 and person < 0.03 and max(pets, wild, veh, food) < 0.03:
        return "objects"
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coco", type=Path, required=True, help="dir with annotations/ and val2017/")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20261105)
    a = ap.parse_args()
    inst = json.loads((a.coco / "annotations" / "instances_val2017.json").read_text(encoding="utf-8"))
    caps = json.loads((a.coco / "annotations" / "captions_val2017.json").read_text(encoding="utf-8"))
    cat = {c["id"]: c for c in inst["categories"]}
    img = {i["id"]: i for i in inst["images"]}
    areas: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for an in inst["annotations"]:
        c = cat[an["category_id"]]
        i = img[an["image_id"]]
        frac = an["area"] / (i["width"] * i["height"])
        g = ("person" if c["name"] == "person" else "pets" if c["name"] in PETS else "wild" if c["name"] in WILD
             else "vehicle" if c["name"] in VEH else "food" if c["supercategory"] == "food"
             else "object" if c["supercategory"] in OBJ_SUPER else "other")
        areas[an["image_id"]][g] += frac
    capmap: dict[int, list[str]] = defaultdict(list)
    for c in caps["annotations"]:
        capmap[c["image_id"]].append(c["caption"])
    by: dict[str, list[str]] = defaultdict(list)
    for iid, i in sorted(img.items()):
        lab = label_of(areas[iid], capmap[iid])
        if lab:
            by[lab].append(i["file_name"])
    rng = random.Random(a.seed)
    for v in by.values():
        rng.shuffle(v)
    avail = {k: len(v) for k, v in sorted(by.items())}

    def balanced(n: int) -> dict[str, str]:
        """Round-robin over classes, so a scarce class is used up and the rest fill the remainder."""
        out: dict[str, str] = {}
        idx = dict.fromkeys(by, 0)
        while len(out) < n and any(idx[k] < len(by[k]) for k in by):
            for k in sorted(by):
                if idx[k] < len(by[k]) and len(out) < n:
                    out[by[k][idx[k]]] = k
                    idx[k] += 1
        return out

    manifest: dict[str, object] = {"source": "COCO val2017", "seed": a.seed, "available_per_class": avail, "subsets": {}}
    for n in (1000, 600, 500):
        sub = balanced(n)
        d = a.out / f"subset{n}"
        d.mkdir(parents=True, exist_ok=True)
        for f in sub:
            if not (d / f).exists():
                os.link(a.coco / "val2017" / f, d / f)
        (d / "labels.json").write_text(json.dumps(sub, indent=0), encoding="utf-8")
        manifest["subsets"][f"subset{n}"] = dict(sorted(Counter(sub.values()).items()))  # type: ignore[index]
    (a.out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
