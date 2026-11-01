"""Plan summary sentence (OUT-EXE-022, DoD 5.3). Mirrors packages/i18n organize.summary* strings."""

from __future__ import annotations

_T = {
    "zh-TW": {
        "copy": "將複製 {n} 張到 {f} 個資料夾，原檔不動。可隨時復原",
        "move": "將移動 {n} 張到 {f} 個資料夾，原檔會先放進 .opagent-trash。可隨時復原",
    },
    "en": {
        "copy": "Will copy {n} photos into {f} folders; originals stay untouched. You can undo anytime",
        "move": "Will move {n} photos into {f} folders; originals go to .opagent-trash first. You can undo anytime",
    },
}


def plan_summary(copy: int = 0, move: int = 0, folders: int = 0, lang: str = "zh-TW") -> str:
    t = _T.get(lang, _T["en"])
    kind, n = ("move", move) if move else ("copy", copy)
    return t[kind].format(n=f"{n:,}", f=f"{folders:,}")
