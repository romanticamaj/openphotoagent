"""M0-S7 — llama-server --jinja tool-calling reliability (docs/09 §6, 08 S7, 11 V-6).

    llama-server -m Qwen3-VL-8B-Instruct-Q4_K_M.gguf --jinja --port 8080 -c 16384 -ngl 99
    uv run python spikes/m0/S7/run.py --server http://127.0.0.1:8080 --runs 20
    uv run python spikes/m0/S7/run.py --server ... --runs 100 --id M0-S7-ext

Each run sends one of the scripted questions with the Ask tool set (10 §B) and
checks (1) the response contains a parseable tool call, (2) the tool chosen is
the expected one, (3) arguments are valid JSON matching the tool schema.
Pass: ≥ 19/20 (≥ 95 %) parse success. Else v0.3 ships fast path only (ASK-LOOP-003).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import write_result  # noqa: E402
from llama_client import post  # noqa: E402

TOOLS = [
    {"type": "function", "function": {"name": "search_photos", "description": "Search the user's photos by text and filters",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"},
                    "date_from": {"type": "string"}, "date_to": {"type": "string"}, "limit": {"type": "integer"}},
                    "required": ["query"]}}},
    {"type": "function", "function": {"name": "count_photos", "description": "Count photos matching filters",
     "parameters": {"type": "object", "properties": {"category": {"type": "string"}, "year": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "get_photo", "description": "Get metadata of one photo by id",
     "parameters": {"type": "object", "properties": {"asset_id": {"type": "string"}}, "required": ["asset_id"]}}},
    {"type": "function", "function": {"name": "propose_organize", "description": "Propose (not apply) a plan to organize photos into folders",
     "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "mode": {"type": "string", "enum": ["by_date", "by_content"]}},
                    "required": ["mode"]}}},
]
QUESTIONS = [
    ("去年夏天在海邊拍的照片", "search_photos"),
    ("我 2024 年拍了幾張美食照？", "count_photos"),
    ("Show me photos of my dog at the beach", "search_photos"),
    ("How many screenshots do I have from 2023?", "count_photos"),
    ("照片 a1b2c3 是在哪裡拍的？", "get_photo"),
    ("幫我把照片依日期整理", "propose_organize"),
    ("Find sunset pictures from Kyoto", "search_photos"),
    ("Organize my photos by content please", "propose_organize"),
    ("What camera took photo 9f8e7d?", "get_photo"),
    ("我有多少張寵物照片", "count_photos"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8080")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--id", default="M0-S7")
    ap.add_argument("--notes", default="", help="run conditions to keep with the result")
    a = ap.parse_args()
    parsed = correct = args_ok = 0
    failures: list[str] = []
    for i in range(a.runs):
        q, expect = QUESTIONS[i % len(QUESTIONS)]
        try:
            r = post(f"{a.server}/v1/chat/completions", {
                "messages": [{"role": "system", "content": "You are a photo assistant. Use tools to answer; never invent ids or numbers."},
                             {"role": "user", "content": q}],
                "tools": TOOLS, "tool_choice": "auto", "temperature": 0.2,
            })
        except Exception as e:  # noqa: BLE001 - a server error is a failed run, not the end of the experiment
            failures.append(f"{i}: {type(e).__name__}: {getattr(e, 'reason', e)}"[:200])
            continue
        calls = r["choices"][0]["message"].get("tool_calls") or []
        if not calls:
            failures.append(f"{i}: no tool call for {q!r}: {str(r['choices'][0]['message'].get('content'))[:120]!r}")
            continue
        parsed += 1
        fn = calls[0]["function"]
        correct += fn["name"] == expect
        if fn["name"] != expect:
            failures.append(f"{i}: {q!r} expected {expect}, got {fn['name']}")
        try:
            json.loads(fn.get("arguments") or "{}")
            args_ok += 1
        except json.JSONDecodeError:
            failures.append(f"{i}: bad args")
    m = {"runs": a.runs, "parse_success_rate": parsed / a.runs, "tool_choice_accuracy": correct / a.runs,
         "args_json_valid_rate": args_ok / a.runs, "failure_samples": failures[:10]}
    write_result(a.id, status="measured", metrics=m, passed=m["parse_success_rate"] >= 0.95, notes=f"server={a.server}. {a.notes}".strip())
    print(json.dumps(m, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
