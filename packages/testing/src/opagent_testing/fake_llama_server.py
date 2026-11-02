"""FakeRuntime: a stand-in for ``llama-server`` (OpenAI-compatible) used by adapter tests (09 §3.1).

Accepts llama-server's CLI flags (unknown ones ignored) and serves ``/health`` and ``/v1/chat/completions`` with
deterministic fused-JSON content derived from the image bytes. Faults are injected through ``POST /_fault``:
``{"OOM": n, "BAD_INPUT": n, "TIMEOUT": n, "RUNTIME": n, "latency_s": x, "load_s": x}``. ``RUNTIME`` SIGKILLs the
process on the next request (a crash, like a real CUDA fault). ``--fake-state FILE`` keeps counters across restarts
(e.g. ``{"crash_first_n": 1}`` crashes only the first process). Never used in production code.
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
from pathlib import Path
from typing import Any

STATE: dict[str, Any] = {"faults": {}, "cancelled": 0, "requests": 0, "ready_at": 0.0, "args": {}}
LOCK = threading.Lock()


def _state_file() -> Path | None:
    p = STATE["args"].get("fake_state")
    return Path(p) if p else None


def _bump_persistent(key: str) -> int:
    f = _state_file()
    if f is None:
        return 0
    d = json.loads(f.read_text()) if f.exists() else {}
    d[key] = int(d.get(key, 0)) + 1
    f.write_text(json.dumps(d))
    return int(d[key])


def _persistent(key: str, default: int = 0) -> int:
    f = _state_file()
    if f is None or not f.exists():
        return default
    return int(json.loads(f.read_text()).get(key, default))


def _content(body: dict[str, Any]) -> str:
    msgs = body.get("messages") or []
    img = b""
    for m in msgs:
        c = m.get("content")
        if isinstance(c, list):
            for part in c:
                if part.get("type") == "image_url":
                    url = part["image_url"]["url"]
                    img = base64.b64decode(url.split(",", 1)[1]) if "," in url else url.encode()
    schema = ((body.get("response_format") or {}).get("json_schema") or {}).get("schema") or {}
    enum = (((schema.get("properties") or {}).get("category") or {}).get("enum")) or ["other"]
    seed = str(body.get("seed", 0))
    h = hashlib.sha256(img + seed.encode()).digest()
    cat = enum[h[0] % len(enum)]
    return json.dumps({"caption": f"a {cat} photo", "tags": [cat, "fake"], "category": cat, "confidence": "high"})


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a: Any) -> None:  # quiet
        return

    def _json(self, code: int, obj: Any) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            if time.monotonic() < STATE["ready_at"]:
                self._json(503, {"error": {"code": 503, "message": "Loading model"}})
            else:
                self._json(200, {"status": "ok"})
        elif self.path == "/_state":
            self._json(
                200, {k: v for k, v in STATE.items() if k != "args"} | {"parallel": STATE["args"].get("parallel")}
            )
        else:
            self._json(404, {})

    def _client_gone(self) -> bool:
        sock: socket.socket = self.connection
        r, _, _ = select.select([sock], [], [], 0)
        if not r:
            return False
        try:
            return sock.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/_fault":
            with LOCK:
                STATE["faults"].update(body)
            self._json(200, STATE["faults"])
            return
        if self.path != "/v1/chat/completions":
            self._json(404, {})
            return
        with LOCK:
            STATE["requests"] += 1
            f = STATE["faults"]
            fault = next((k for k in ("RUNTIME", "OOM", "BAD_INPUT", "TIMEOUT") if f.get(k, 0) > 0), None)
            if fault:
                f[fault] -= 1
            latency = float(f.get("latency_s", 0))
        if fault is None and _persistent("crashes") < int(STATE["args"].get("crash_first_n") or 0):
            fault = "RUNTIME"
        if fault == "RUNTIME":
            _bump_persistent("crashes")
            os.kill(os.getpid(), signal.SIGKILL)
        if fault == "OOM":
            self._json(
                500, {"error": {"code": 500, "message": "CUDA error: out of memory (failed to allocate buffer)"}}
            )
            return
        if fault == "BAD_INPUT":
            self._json(400, {"error": {"code": 400, "message": "failed to load image"}})
            return
        if fault == "TIMEOUT":
            self._json(408, {"error": {"code": 408, "message": "request timed out"}})
            return
        end = time.monotonic() + latency
        while time.monotonic() < end:
            if self._client_gone():
                with LOCK:
                    STATE["cancelled"] += 1
                return
            time.sleep(0.02)
        content = _content(body)
        self._json(
            200,
            {
                "id": "fake",
                "object": "chat.completion",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--parallel", "-np", type=int, default=1)
    ap.add_argument("--fake-state")
    ap.add_argument("--fake-load-s", type=float, default=0.3)
    ap.add_argument("--crash-first-n", type=int, default=0)
    args, _unknown = ap.parse_known_args(argv)
    STATE["args"] = vars(args)
    STATE["ready_at"] = time.monotonic() + args.fake_load_s
    srv = ThreadingHTTPServer((args.host, args.port), H)
    srv.daemon_threads = True
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
