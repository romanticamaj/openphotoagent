"""Torch sidecar (08 §4.2 ``torch-*`` packs, v0.4): an OpenAI-compatible ``/v1/chat/completions`` server in front of
transformers models that only transformers can run (JoyCaption, Florence-2).

* Runs inside the torch pack's Python; the worker talks to it like to llama-server (``TorchServerAdapter``).
* Before loading, ``arch_check`` refuses a CUDA build that has no kernels for this GPU (Blackwell needs ``sm_120``,
  RT-TORCH-002) — the error tells the worker to offer ``torch-cu128``.
* Models with remote code load only with ``--trust-remote-code``, which the worker passes after the user confirmed
  the exact files (invariant 10, RT-TORCH-003).
* ``--fake-pipeline`` answers deterministically without torch, and adds ``/_fault`` and ``/_state`` so the INF-CONF
  suite can inject OOM/TIMEOUT/BAD_INPUT/RUNTIME and observe cancellation (tests only).

Usage: ``python -m opagent_adapters.torch_server --model ID --port N [--trust-remote-code] [--fake-pipeline]``
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import select
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

EXIT_ARCH = 3  # the supervisor maps this exit code to PACK_INCOMPATIBLE (reason cuda_arch)


def arch_check(cc: str | None, arch_list: list[str]) -> str | None:
    """None when the build has kernels this GPU can run, else ``"cuda_arch"``. A cubin for sm_XY runs on devices
    of the same major with minor ≥ Y; a new major (Blackwell 12.x) needs its own entry."""
    if not cc:
        return None
    major, minor = (int(x) for x in cc.split(".")[:2])
    for a in arch_list:
        digits = a.removeprefix("sm_").removeprefix("compute_")
        if not digits.isdigit() or len(digits) < 2:
            continue
        am, an = int(digits[:-1]), int(digits[-1])
        if am == major and an <= minor:
            return None
    return "cuda_arch"


class FakePipeline:
    def __init__(self) -> None:
        self.faults: dict[str, float] = {}
        self.cancelled = 0
        self.lock = threading.Lock()

    def answer(self, image: bytes, categories: list[str], seed: str) -> str:
        h = hashlib.sha256(image + seed.encode()).digest()
        cat = categories[h[0] % len(categories)] if categories else "other"
        return json.dumps({"caption": f"a {cat} photo", "tags": [cat], "category": cat, "confidence": "medium"})


class TransformersPipeline:  # pragma: no cover - needs torch and weights (nightly GPU runner)
    def __init__(self, model: str, trust_remote_code: bool) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor

        if torch.cuda.is_available():
            cc = "{}.{}".format(*torch.cuda.get_device_capability())
            if arch_check(cc, torch.cuda.get_arch_list()):
                print(json.dumps({"error": "PACK_INCOMPATIBLE", "reason": "cuda_arch", "cc": cc}), file=sys.stderr)
                sys.exit(EXIT_ARCH)
        self.device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        self.processor = AutoProcessor.from_pretrained(model, trust_remote_code=trust_remote_code)
        self.model = AutoModelForCausalLM.from_pretrained(
            model, trust_remote_code=trust_remote_code, use_safetensors=True
        ).to(self.device)

    def answer(self, image: bytes, categories: list[str], seed: str) -> str:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(image)).convert("RGB")
        inputs = self.processor(text="<MORE_DETAILED_CAPTION>", images=img, return_tensors="pt").to(self.device)
        out = self.model.generate(**inputs, max_new_tokens=128, do_sample=False)
        caption = self.processor.batch_decode(out, skip_special_tokens=True)[0].strip()
        return json.dumps({"caption": caption, "tags": [], "category": "other", "confidence": "low"})


def _image_and_categories(body: dict[str, Any]) -> tuple[bytes, list[str]]:
    img = b""
    for m in body.get("messages") or []:
        c = m.get("content")
        if isinstance(c, list):
            for part in c:
                if part.get("type") == "image_url":
                    url = part["image_url"]["url"]
                    if not url.startswith("data:"):
                        raise ValueError("only in-memory images (data URLs) are accepted")
                    img = base64.b64decode(url.split(",", 1)[1])
    rf = body.get("response_format") or {}
    schema = (rf.get("json_schema") or {}).get("schema") or {}
    cats = ((schema.get("properties") or {}).get("category") or {}).get("enum") or ["other"]
    return img, list(cats)


def serve(pipe: Any, host: str, port: int, fake: bool) -> None:
    state = {"ready": True}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a: Any) -> None:
            pass

        def _json(self, code: int, obj: Any) -> None:
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _gone(self) -> bool:
            r, _, _ = select.select([self.connection], [], [], 0)
            if not r:
                return False
            try:
                return bool(self.connection.recv(1, socket.MSG_PEEK) == b"")
            except OSError:
                return True

        def do_GET(self) -> None:
            if self.path == "/health":
                self._json(200 if state["ready"] else 503, {"status": "ok"})
            elif self.path == "/_state" and fake:
                self._json(200, {"cancelled": pipe.cancelled})
            else:
                self._json(404, {})

        def do_POST(self) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/_fault" and fake:
                with pipe.lock:
                    pipe.faults.update(body)
                self._json(200, pipe.faults)
                return
            if self.path != "/v1/chat/completions":
                self._json(404, {})
                return
            fault, latency = None, 0.0
            if fake:
                with pipe.lock:
                    fault = next(
                        (k for k in ("RUNTIME", "OOM", "BAD_INPUT", "TIMEOUT") if pipe.faults.get(k, 0) > 0), None
                    )
                    if fault:
                        pipe.faults[fault] -= 1
                    latency = float(pipe.faults.get("latency_s", 0))
            if fault == "RUNTIME":
                os.kill(os.getpid(), signal.SIGKILL)
            if fault == "OOM":
                self._json(500, {"error": {"message": "CUDA out of memory"}})
                return
            if fault == "TIMEOUT":
                self._json(408, {"error": {"message": "request timed out"}})
                return
            try:
                img, cats = _image_and_categories(body)
                if fault == "BAD_INPUT" or not img:
                    raise ValueError("failed to load image")
            except ValueError as e:
                self._json(400, {"error": {"message": str(e)}})
                return
            end = time.monotonic() + latency
            while time.monotonic() < end:
                if self._gone():
                    with pipe.lock:
                        pipe.cancelled += 1
                    return
                time.sleep(0.02)
            try:
                content = pipe.answer(img, cats, str(body.get("seed", 0)))
            except Exception as e:
                msg = "CUDA out of memory" if "out of memory" in str(e).lower() else f"runtime error: {e}"[:300]
                self._json(500, {"error": {"message": msg}})
                return
            self._json(
                200,
                {
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                    ],
                    "usage": {},
                },
            )

    srv = ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    srv.serve_forever()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--trust-remote-code", action="store_true")
    ap.add_argument("--fake-pipeline", action="store_true")
    args, _ = ap.parse_known_args(argv)
    pipe: Any = FakePipeline() if args.fake_pipeline else TransformersPipeline(args.model, args.trust_remote_code)
    serve(pipe, args.host, args.port, args.fake_pipeline)
    return 0


if __name__ == "__main__":
    sys.exit(main())
