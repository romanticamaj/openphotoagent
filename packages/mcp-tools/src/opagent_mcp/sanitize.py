"""Untrusted text for MCP responses (05 §3.3.2 ``UntrustedText``, §3.9; CLAUDE.md invariant 6).

Text that came from a photo (VLM caption, OCR, EXIF/XMP/IPTC, file and folder names) or from a user-chosen
name is data, never instructions. Before it leaves the server it is:

* stripped of control, zero-width and bidi characters (so hidden or reversed text cannot hide instructions);
* neutralised: Markdown links and images become plain ``label (url)`` text and every URL is rewritten to
  ``hxxp(s)://`` so a client cannot render or fetch it; angle brackets become full-width so ``<system>`` tags
  do not look like markup;
* truncated (captions 2,000 characters, EXIF strings 200);
* checked with lightweight rules for instruction-like text, flagged as ``injection_suspect``.

Paths and file names are never decoded (``%2F`` stays as typed).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

Origin = Literal["vlm_caption", "ocr", "exif", "filename", "user"]
TEXT_MAX = 2000
EXIF_MAX = 200
_LIMITS = {"exif": EXIF_MAX}

_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿­]")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
_URL = re.compile(r"\b(?:h[tx]{2}p)(s?)://", re.IGNORECASE)
_TOOLS = (
    "apply_organize",
    "undo_organize",
    "propose_organize",
    "update_labels",
    "describe_photo",
    "run_labeling_job",
    "cancel_job",
    "create_collection",
    "add_to_collection",
    "remove_from_collection",
)
_RULES = [
    r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier|all)?\s*(?:instructions|rules|prompts)",
    r"ignore\s+all\s+rules",
    r"disregard\s+(?:all\s+|the\s+)?(?:previous|prior|above)",
    r"\byou\s+are\s+now\b",
    r"\bDAN\b",
    r"<\s*/?\s*(?:system|assistant|user|tool)\s*>",
    r"^\s*(?:system|assistant|user)\s*:",
    r"system\s+override",
    r"tool_result",
    r"approval\s+granted",
    r"\byou\s+must\s+call\b",
    r"\bcall\s+\w+_\w+",
    r"\bmove\s+everything\b",
    r"\bdelete\s+(?:all|everything)\b",
    r"\bupload\b.*\bto\b",
    r"\bsend\b.*\bto\b.*@",
    r"忽略.{0,6}(?:指示|指令|規則)",
    r"你現在是",
    r"沒有任何限制",
    r"系統管理員",
    r"請(?:呼叫|執行|調用)",
    r"我已核准",
    r"已核准此",
    r"上傳到",
    r"寄給",
    r"把所有照片",
    r"所有照片的\s*tag",
]
_DETECT = re.compile("|".join(f"(?:{r})" for r in _RULES), re.IGNORECASE | re.MULTILINE)


def _strip(s: str) -> str:
    s = _INVISIBLE.sub("", s)
    return "".join(c for c in s if c in "\n\t" or unicodedata.category(c) != "Cc")


def _neutralise(s: str) -> str:
    s = _MD_IMAGE.sub(lambda m: f"{m.group(1)} ({m.group(2)})", s)
    s = _MD_LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", s)
    s = _URL.sub(lambda m: f"hxxp{m.group(1).lower()}://", s)
    return s.replace("<", "＜").replace(">", "＞")


def suspect(text: str) -> bool:
    """Instruction-like text (rules only; the Worker may add a classifier score later)."""
    probe = _strip(text).replace("_", " ").replace("＜", "<").replace("＞", ">")
    return bool(_DETECT.search(probe)) or any(t in text for t in _TOOLS)


def untrusted(
    text: str | None, origin: Origin, *, contains_image_text: bool | None = None, max_len: int | None = None
) -> dict[str, Any] | None:
    """Wrap text from a photo or a user-chosen name as ``UntrustedText``; ``None`` stays ``None``."""
    if text is None:
        return None
    raw = _strip(str(text))
    flagged = suspect(raw)
    clean = _neutralise(raw)
    limit = max_len or _LIMITS.get(origin, TEXT_MAX)
    truncated = len(clean) > limit
    if truncated:
        clean = clean[: limit - 1] + "…"
    out: dict[str, Any] = {"text": clean, "origin": origin, "injection_suspect": flagged}
    if truncated:
        out["truncated"] = True
    if contains_image_text is not None:
        out["contains_image_text"] = contains_image_text
    return out
