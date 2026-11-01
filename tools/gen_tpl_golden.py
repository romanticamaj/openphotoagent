#!/usr/bin/env python3
"""Generate fixtures/golden/path_template/cases.json from the Python implementation (CORE-TPL-025).

The TS port (packages/ts-path-template) must reproduce every case exactly. Regenerating requires a
`Golden-Update:` commit footer (docs/09 §1.3).
"""

from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path

from opagent_core.path_template import RenderValues, TemplateError, compile_template, render, sanitize_segment

OUT = Path(__file__).resolve().parents[1] / "fixtures/golden/path_template/cases.json"
ALPHABET = list("aZ09 ._-/\\:*?\"<>|") + ["貓", "旅行", "é", "e\u0301", "\u202e", "\u200b", "\ufeff", "\x00", "\x1f",
    "\uff0f", "\u2215", "🐱", "\u200d", "👤", "👨\u200d👩\u200d👧", "\ufe0f", "🏻", "CON", "nul", "COM1", ". "]
TEMPLATES = [
    "{category}/{taken_at:%Y}/{taken_at:%Y-%m}/{stem}{ext}", "{category}/{date:YYYY}/{stem}{ext}",
    "{taken_at:%Y}/{taken_at:%Y-%m}/{stem}{ext}", "{country}/{city}/{stem}{ext}", "{tag:animal}/{stem}{ext}",
    "{category_key}/{date:YYYY-MM}/{stem}{ext}", "{source}/{admin1}/{date:DD}/{stem}{ext}",
    "photos/{category}/{taken_at:%Y-%m-%d %H%M%S}/{stem}{ext}", "{taken_at:%y%j}/{stem}{ext}",
]
BAD_TEMPLATES = ["{foo}/{stem}{ext}", "/etc/{stem}", "C:\\{stem}", "../{stem}", "{category}/../{stem}",
                 "\\\\server\\{stem}", "{taken_at:%c}/{stem}", "a" * 190 + "{stem}{ext}", "{tag}/{stem}", "a//{stem}"]


def rnd_str(r: random.Random, n: int) -> str:
    return "".join(r.choice(ALPHABET) for _ in range(n))


def main() -> None:
    r = random.Random(20261101)
    cases: list[dict[str, object]] = []
    for t in BAD_TEMPLATES:
        try:
            compile_template(t)
            raise SystemExit(f"expected error for {t}")
        except TemplateError as e:
            cases.append({"kind": "compile_error", "template": t, "code": e.code, "pos": e.pos})
    seg_inputs = ["..", ".", "...", "  旅行. ", "CON", "con", "Nul.txt", "COM1", "LPT9", "AUX ", "a/b\\c:d*e?f\"g<h>i|j",
                  "貓" * 300, "🐱\u200d👤家族👨\u200d👩\u200d👧" * 15, "e\u0301" * 200, ""]
    seg_inputs += [rnd_str(r, r.randint(1, 120)) for _ in range(80)]
    for v in seg_inputs:
        cases.append({"kind": "segment", "value": v, "out": sanitize_segment(v)})
    offsets = ["+08:00", "+09:00", "-05:00", "+00:00", "+05:45"]
    for i in range(140):
        tpl = TEMPLATES[i % len(TEMPLATES)]
        has_date = r.random() > 0.15
        dt = datetime(r.randint(1995, 2030), r.randint(1, 12), r.randint(1, 28), r.randint(0, 23), r.randint(0, 59),
                      r.randint(0, 59))
        iso = dt.isoformat() + r.choice(offsets)
        mtime = datetime(2024, 3, 2, 9, 0).isoformat() + "+08:00"
        vals = {
            "stem": rnd_str(r, r.randint(0, 40)) if i % 7 else "a" * 260,
            "ext": r.choice([".jpg", ".JPG", ".jpeg", ".HEIC", ""]),
            "category": r.choice([None, "美食", "Food", rnd_str(r, 12), ".."]),
            "category_key": r.choice([None, "food", "c1_food"]),
            "country": r.choice([None, "台灣", "Japan", rnd_str(r, 5)]),
            "admin1": r.choice([None, "京都府"]), "city": r.choice([None, "Kyoto", ""]),
            "source": r.choice([None, "phone"]),
            "tags": r.choice([[], [["animal.cat", 0.9], ["animal.dog", 0.4]], [["food.ramen", 1.0]]]),
            "tag_display": r.choice([{}, {"animal.cat": "貓"}]),
        }
        fallback = r.choice([None, {"country": "未知地點"}])
        lang = r.choice(["zh-TW", "en"])
        rv = RenderValues(
            stem=vals["stem"], ext=vals["ext"],
            taken_at=datetime.fromisoformat(iso) if has_date else None,
            mtime=datetime.fromisoformat(mtime), category=vals["category"], category_key=vals["category_key"],
            country=vals["country"], admin1=vals["admin1"], city=vals["city"], source=vals["source"],
            tags=tuple((k, s) for k, s in vals["tags"]), tag_display=vals["tag_display"],
        )
        res = render(compile_template(tpl), rv, fallback_values=fallback, lang=lang)
        cases.append({"kind": "render", "template": tpl, "values": vals, "taken_at": iso if has_date else None,
                      "mtime": mtime, "fallback": fallback, "lang": lang, "rel": res.rel, "date_source": res.date_source})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"version": 1, "cases": cases}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(len(cases), "cases")


if __name__ == "__main__":
    main()
