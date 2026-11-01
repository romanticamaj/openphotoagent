"""Recorded Hugging Face download server (docs/09 §3.5). Serves seeded, deterministic bytes — never real weights.

``/api/models/{repo}`` (siblings + LFS sha256), ``/{repo}/resolve/{rev}/{path}`` → 302 → ``/cdn/{sha}`` with
``Range`` / ``If-Range`` / ``ETag``. Scenarios (09a DL-*): ``ok``, ``drop-at-50%``, ``bad-sha``, ``bad-sha-once``,
``etag-changed``, ``429-retry-after-5``, ``401``, ``403-gated``, ``404``, ``5xx-x3``. ``rate_bps`` throttles the
CDN. Every request is logged with open/close times so tests can assert "socket closed within 2 s" and
"no GET happened".
"""

from __future__ import annotations

import hashlib
import json
import random
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class HfFile:
    path: str
    size: int
    seed: int = 0

    def content(self) -> bytes:
        return random.Random(f"{self.path}:{self.seed}").randbytes(self.size)  # noqa: S311 - seeded test bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content()).hexdigest()


@dataclass
class HfServer:
    repo: str
    files: list[HfFile]
    scenario: str = "ok"
    rate_bps: float | None = None
    requests: list[dict[str, Any]] = field(default_factory=list)
    _httpd: ThreadingHTTPServer | None = None
    _count: dict[str, int] = field(default_factory=dict)
    _active: int = 0
    max_active: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def url(self) -> str:
        assert self._httpd is not None
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def bytes_served(self, path_prefix: str = "/cdn/") -> int:
        return sum(int(r.get("sent", 0)) for r in self.requests if str(r["path"]).startswith(path_prefix))

    def gets(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["method"] == "GET"]

    def _bump(self, key: str) -> int:
        with self._lock:
            self._count[key] = self._count.get(key, 0) + 1
            return self._count[key]

    def start(self) -> HfServer:
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a: object) -> None:
                pass

            def _json(self, code: int, obj: Any, headers: dict[str, str] | None = None) -> None:
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                rec: dict[str, Any] = {
                    "method": "GET",
                    "path": self.path,
                    "range": self.headers.get("Range"),
                    "if_range": self.headers.get("If-Range"),
                    "opened": time.monotonic(),
                    "sent": 0,
                }
                with srv._lock:
                    srv.requests.append(rec)
                    srv._active += 1
                    srv.max_active = max(srv.max_active, srv._active)
                try:
                    self._route(rec)
                except (TimeoutError, BrokenPipeError, ConnectionResetError):
                    rec["aborted"] = True
                finally:
                    rec["closed"] = time.monotonic()
                    with srv._lock:
                        srv._active -= 1

            def _route(self, rec: dict[str, Any]) -> None:
                sc = srv.scenario
                if self.path.startswith(f"/api/models/{srv.repo}"):
                    if sc == "401":
                        return self._json(401, {"error": "Invalid credentials"})
                    self._json(
                        200,
                        {
                            "id": srv.repo,
                            "sha": "0123456789abcdef0123456789abcdef01234567",
                            "siblings": [
                                {"rfilename": f.path, "size": f.size, "lfs": {"sha256": f.sha256, "size": f.size}}
                                for f in srv.files
                            ],
                        },
                    )
                    return None
                if self.path.startswith(f"/{srv.repo}/resolve/"):
                    rest = self.path[len(f"/{srv.repo}/resolve/") :]
                    _rev, _, path = rest.partition("/")
                    f = next((x for x in srv.files if x.path == path), None)
                    if sc == "401":
                        return self._json(401, {"error": "Invalid credentials"})
                    if sc == "403-gated":
                        return self._json(403, {"error": "Access to model is restricted. You must be authenticated."})
                    if sc == "404" or f is None:
                        return self._json(404, {"error": "Entry not found"})
                    if sc == "429-retry-after-5" and srv._bump("429") == 1:
                        return self._json(429, {"error": "rate limited"}, {"Retry-After": "5"})
                    if sc == "5xx-x3" and srv._bump("5xx") <= 3:
                        return self._json(503, {"error": "unavailable"})
                    self.send_response(302)
                    self.send_header("Location", f"{srv.url}/cdn/{f.sha256}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
                if self.path.startswith("/cdn/"):
                    sha = self.path[5:]
                    f = next((x for x in srv.files if x.sha256 == sha), None)
                    if f is None:
                        return self._json(404, {})
                    data = f.content()
                    etag = f'"{f.sha256}"'
                    if sc == "etag-changed":
                        etag = f'"{f.sha256}-v2"'
                    n = srv._bump("cdn:" + sha)
                    if sc == "bad-sha" or (sc == "bad-sha-once" and n == 1):
                        data = bytes([data[0] ^ 0xFF]) + data[1:]
                    rng = self.headers.get("Range")
                    if_range = self.headers.get("If-Range")
                    start, end = 0, len(data) - 1
                    partial = False
                    if rng and rng.startswith("bytes=") and (if_range is None or if_range == etag):
                        a, _, b = rng[6:].partition("-")
                        start = int(a)
                        end = int(b) if b else len(data) - 1
                        partial = True
                    body = data[start : end + 1]
                    self.send_response(206 if partial else 200)
                    if partial:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                    self.send_header("ETag", etag)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    cut = len(body) // 2 if (sc == "drop-at-50%" and srv._bump("drop:" + sha) == 1) else None
                    self._send_body(rec, body, cut)
                    return None
                return self._json(404, {})

            def _send_body(self, rec: dict[str, Any], body: bytes, cut: int | None) -> None:
                chunk = 64 * 1024
                t0 = time.monotonic()
                i = 0
                while i < len(body):
                    if cut is not None and i >= cut:
                        self.connection.shutdown(socket.SHUT_RDWR)  # simulate a dropped connection mid-file
                        rec["dropped_at"] = i
                        return
                    piece = body[i : i + chunk]
                    self.wfile.write(piece)
                    i += len(piece)
                    rec["sent"] = i
                    if srv.rate_bps:
                        ahead = i / srv.rate_bps - (time.monotonic() - t0)
                        if ahead > 0:
                            time.sleep(ahead)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
