"""Guard rails on every assistant answer (10 §8.5–§8.10). The model writes prose; facts come from tools:

* a cited photo ``[[photo:ID]]`` must be one the tools returned in this turn, else the citation is dropped;
* a number in a statistics answer must be a ``count_photos`` result, else it is replaced (or removed);
* when the tools found nothing, the answer is a refusal with concrete ways to broaden — never "found 5";
* missing metadata is said to be missing, never guessed;
* Markdown is limited to a whitelist (no images, no links: nothing a client could fetch);
* text from photos reaches the model only inside ``<photo_text … untrusted>``; suspect captions not at all;
* an action is a plan card built from the plan's own data, never from the model's description.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

CITE = re.compile(r"\[\[photo:([A-Za-z0-9_-]+)\]\]")
NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?![\d,])|(?<![\w.,])\d+(?![\d,])")
STAT = re.compile(r"幾張|多少張|多少|幾個|how many|count|數量|統計", re.IGNORECASE)
MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
MD_LINK = re.compile(r"(?<!\[)\[([^\]\[]+)\]\((?:https?:|//|www\.)[^)]*\)")
BARE_URL = re.compile(r"https?://\S+")
FIELD_LABEL = {"taken_at": "日期", "tags_any": "標籤", "tags_all": "標籤", "place": "地點", "categories": "分類",
               "persons_all": "人物", "persons_any": "人物", "camera": "相機"}


@dataclass
class Grounded:
    text: str
    dropped_citations: int = 0
    number_fixes: int = 0
    refused: bool = False


def is_statistic(question: str) -> bool:
    return bool(STAT.search(question))


def _fmt(n: int) -> str:
    return f"{n:,}"


def ground(
    text: str,
    *,
    seen: set[str],
    counts: list[int],
    total: int | None,
    statistic: bool = False,
    relax: list[tuple[str, int]] | None = None,
) -> Grounded:
    out = Grounded(text)
    if total == 0:  # nothing found: a refusal with the counts of broader searches (10 §8.6)
        hints = "；".join(f"不限{FIELD_LABEL.get(f, f)}有 {_fmt(n)} 張" for f, n in (relax or []) if n > 0)
        out.text = "找不到符合條件的照片。" + (f"放寬條件的話：{hints}。" if hints else "")
        out.refused = True
        return out

    def cite(m: re.Match[str]) -> str:
        if m.group(1) in seen:
            return m.group(0)
        out.dropped_citations += 1
        return ""

    t = CITE.sub(cite, text)
    if statistic:
        allowed = {_fmt(c) for c in counts} | {str(c) for c in counts}

        def num(m: re.Match[str]) -> str:
            if m.group(0) in allowed:
                return m.group(0)
            out.number_fixes += 1
            return _fmt(counts[0]) if len(counts) == 1 else ""

        t = NUMBER.sub(num, t)
    out.text = t
    return out


def location_answer(place: str | None) -> str:
    return place if place else "這張照片沒有位置資訊。"


def safe_markdown(md: str) -> str:
    md = MD_IMAGE.sub("", md)
    md = MD_LINK.sub(lambda m: m.group(1), md)
    return BARE_URL.sub("", md)


def photo_block(p: dict[str, Any]) -> str:
    """One photo as the model sees it: id and tags; caption only as untrusted, escaped data."""
    parts = [f'<photo id="{html.escape(str(p["id"]))}">', f"tags: {', '.join(p.get('tags') or [])}"]
    cap = p.get("caption") or {}
    if cap.get("text") and not cap.get("injection_suspect"):
        parts.append(f'<photo_text origin="{cap.get("origin", "vlm_caption")}" untrusted>'
                     f"{html.escape(cap['text'], quote=False)}</photo_text>")
    elif cap.get("text"):
        parts.append('<photo_text untrusted withheld="injection_suspect"/>')
    parts.append("</photo>")
    return "\n".join(parts)


def plan_card(plan: dict[str, Any], llm_text: str = "") -> dict[str, Any]:
    """What the confirmation card shows comes from the plan, whatever the model wrote (ASK-ACT-002)."""
    s = plan.get("summary") or {}
    return {
        "kind": "plan",
        "plan_id": plan["plan_id"],
        "action": s.get("action"),
        "count": int(s.get("actions", 0)),
        "target": s.get("target"),
        "needs_approval": True,
    }
