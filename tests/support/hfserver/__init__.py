"""Recorded Hugging Face download server (docs/09 §3.5). Serves seeded, deterministic bytes — never real weights."""

from __future__ import annotations

import hashlib
import json
import random
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class HfFile:
    path: str
    size: int
    seed: int = 0

    def content(self) -> bytes:
        return random.Random(f"{self.path}:{self.seed}").randbytes(self.size)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content()).hexdigest()


@dataclass
class HfServer:
    repo: str
    files: list[HfFile]
    scenario: str = "ok"
    requests: list[dict[str, str | None]] = field(default_factory=list)
    _httpd: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        assert self._httpd is not None
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def start(self) -> HfServer:
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: object) -> None:
                pass

            def do_GET(self) -> None:
                srv.requests.append({"path": self.path, "range": self.headers.get("Range")})
                if self.path == f"/api/models/{srv.repo}":
                    body = json.dumps(
                        {
                            "id": srv.repo,
                            "siblings": [
                                {"rfilename": f.path, "size": f.size, "lfs": {"sha256": f.sha256, "size": f.size}}
                                for f in srv.files
                            ],
                        }
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                prefix = f"/{srv.repo}/resolve/main/"
                f = next((x for x in srv.files if self.path == prefix + x.path), None)
                if f is None:
                    self.send_error(404)
                    return
                data = f.content()
                rng = self.headers.get("Range")
                start = 0
                if rng and rng.startswith("bytes="):
                    start = int(rng[6:].split("-")[0])
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                self.send_header("ETag", f'"{f.sha256}"')
                self.send_header("Content-Length", str(len(data) - start))
                self.end_headers()
                self.wfile.write(data[start:])

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
