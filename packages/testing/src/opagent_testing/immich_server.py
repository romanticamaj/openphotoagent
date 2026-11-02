"""A small stand-in for the Immich API (02 §8.2) used by IMM-* tests. Never used in production.

Implements just what the integration calls: ping, users/me (auth), search by checksum, tags (create, tag/untag
assets), albums (create, add assets) and asset description. Counts every write, so tests can assert idempotence.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeImmich:
    def __init__(self, api_key: str = "immich-key") -> None:
        self.api_key = api_key
        self.assets: dict[str, dict[str, Any]] = {}  # id -> {checksum (base64 sha1), description, tags:set}
        self.tags: dict[str, dict[str, Any]] = {}  # id -> {name, value}
        self.albums: dict[str, dict[str, Any]] = {}  # id -> {albumName, assets:set}
        self.writes = 0
        self.up = True
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n:04d}"

    def add_asset(self, checksum_b64: str, description: str = "", tags: tuple[str, ...] = ()) -> str:
        aid = self._id("as")
        tag_ids = set()
        for t in tags:
            tag_ids.add(self._tag(t))
        self.assets[aid] = {"checksum": checksum_b64, "description": description, "tags": tag_ids}
        return aid

    def _tag(self, value: str) -> str:
        for tid, t in self.tags.items():
            if t["value"] == value:
                return tid
        tid = self._id("tg")
        self.tags[tid] = {"name": value.rsplit("/", 1)[-1], "value": value}
        return tid

    def tag_values(self, aid: str) -> set[str]:
        return {self.tags[t]["value"] for t in self.assets[aid]["tags"]}

    # ---- HTTP ----
    def start(self) -> FakeImmich:
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def _send(self, code: int, obj: Any = None) -> None:
                data = json.dumps(obj if obj is not None else {}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self) -> Any:
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}")

            def _auth(self) -> bool:
                if self.headers.get("x-api-key") != fake.api_key:
                    self._send(401, {"message": "Invalid API key"})
                    return False
                return True

            def do_GET(self) -> None:
                if self.path == "/api/server/ping":
                    return self._send(200, {"res": "pong"})
                if not self._auth():
                    return None
                if self.path == "/api/users/me":
                    return self._send(200, {"id": "u1", "email": "me@example.invalid"})
                if self.path == "/api/tags":
                    return self._send(200, [{"id": k, **v} for k, v in fake.tags.items()])
                if self.path == "/api/albums":
                    return self._send(200, [{"id": k, "albumName": v["albumName"]} for k, v in fake.albums.items()])
                if self.path.startswith("/api/albums/"):
                    al = fake.albums.get(self.path.rsplit("/", 1)[-1])
                    if al is None:
                        return self._send(404)
                    return self._send(200, {"albumName": al["albumName"], "assets": [{"id": a} for a in al["assets"]]})
                if self.path.startswith("/api/assets/"):
                    a = fake.assets.get(self.path.rsplit("/", 1)[-1])
                    if a is None:
                        return self._send(404)
                    return self._send(
                        200,
                        {
                            "id": self.path.rsplit("/", 1)[-1],
                            "exifInfo": {"description": a["description"]},
                            "tags": [{"id": t, **fake.tags[t]} for t in a["tags"]],
                        },
                    )
                return self._send(404)

            def do_POST(self) -> None:
                if not self._auth():
                    return None
                b = self._body()
                with fake._lock:
                    if self.path == "/api/search/metadata":
                        items = [{"id": k} for k, v in fake.assets.items() if v["checksum"] == b.get("checksum")]
                        return self._send(200, {"assets": {"items": items, "total": len(items)}})
                    if self.path == "/api/tags":
                        fake.writes += 1
                        tid = fake._tag(
                            b["name"] if not b.get("parentId") else f"{fake.tags[b['parentId']]['value']}/{b['name']}"
                        )
                        return self._send(201, {"id": tid, **fake.tags[tid]})
                    if self.path == "/api/albums":
                        fake.writes += 1
                        aid = fake._id("al")
                        fake.albums[aid] = {"albumName": b["albumName"], "assets": set()}
                        return self._send(201, {"id": aid, "albumName": b["albumName"]})
                return self._send(404)

            def do_PUT(self) -> None:
                if not self._auth():
                    return None
                b = self._body()
                parts = self.path.strip("/").split("/")
                with fake._lock:
                    if len(parts) == 4 and parts[1] == "tags" and parts[3] == "assets":
                        fake.writes += 1
                        for aid in b["ids"]:
                            fake.assets[aid]["tags"].add(parts[2])
                        return self._send(200, [{"id": a, "success": True} for a in b["ids"]])
                    if len(parts) == 4 and parts[1] == "albums" and parts[3] == "assets":
                        fake.writes += 1
                        fake.albums[parts[2]]["assets"].update(b["ids"])
                        return self._send(200, [{"id": a, "success": True} for a in b["ids"]])
                    if len(parts) == 3 and parts[1] == "assets":
                        fake.writes += 1
                        fake.assets[parts[2]]["description"] = b.get("description", "")
                        return self._send(200, {"id": parts[2]})
                return self._send(404)

            def do_DELETE(self) -> None:
                if not self._auth():
                    return None
                b = self._body()
                parts = self.path.strip("/").split("/")
                with fake._lock:
                    if len(parts) == 4 and parts[1] == "tags" and parts[3] == "assets":
                        fake.writes += 1
                        for aid in b["ids"]:
                            fake.assets[aid]["tags"].discard(parts[2])
                        return self._send(200, [{"id": a, "success": True} for a in b["ids"]])
                    if len(parts) == 4 and parts[1] == "albums" and parts[3] == "assets":
                        fake.writes += 1
                        fake.albums[parts[2]]["assets"].difference_update(b["ids"])
                        return self._send(200, [])
                return self._send(404)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    @property
    def url(self) -> str:
        assert self._httpd is not None
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
