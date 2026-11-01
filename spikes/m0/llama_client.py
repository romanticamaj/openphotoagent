"""Minimal OpenAI-compatible client for llama-server (stdlib only)."""

from __future__ import annotations

import base64
import json
import mimetypes
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

CATEGORIES_12 = [
    "people", "pets", "food", "landscape", "city", "documents",
    "screenshots", "events", "travel", "nature", "vehicles", "objects",
]


def fused_schema(categories: list[str], key_order: str = "caption_first") -> dict[str, Any]:
    props: dict[str, Any] = {
        "caption": {"type": "string", "maxLength": 400},
        "tags": {"type": "array", "items": {"type": "string", "maxLength": 40}, "maxItems": 20},
        "category": {"type": "string", "enum": [*categories, "other"]},
    }
    order = ["caption", "tags", "category"] if key_order == "caption_first" else ["category", "caption", "tags"]
    return {
        "$id": "opagent.fused.v1",
        "type": "object",
        "properties": {k: props[k] for k in order},
        "required": order,
        "additionalProperties": False,
    }


def image_data_url(p: Path) -> str:
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def post(url: str, body: dict[str, Any], timeout: float = 300) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - local server
        return json.loads(r.read())


class VramSampler:
    """Polls nvidia-smi for peak used VRAM (MiB). No-op when nvidia-smi is absent."""

    def __init__(self, interval: float = 0.5) -> None:
        self.peak_mib: int | None = None
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self.interval = interval

    def _loop(self) -> None:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return
        while not self._stop.is_set():
            out = subprocess.run([exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"], text=True, capture_output=True).stdout
            vals = [int(x) for x in out.split() if x.strip().isdigit()]
            if vals:
                self.peak_mib = max(self.peak_mib or 0, max(vals))
            time.sleep(self.interval)

    def __enter__(self) -> "VramSampler":
        self._t.start()
        return self

    def __exit__(self, *a: object) -> None:
        self._stop.set()
