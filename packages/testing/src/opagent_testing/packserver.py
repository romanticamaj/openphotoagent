"""Test pack server (09 §3.5 / B-5): throwaway ed25519 keys, tar.zst packs, a minisign-format signed
``packs-index.json`` and a static HTTP server. Never used with production keys."""

from __future__ import annotations

import base64
import functools
import hashlib
import io
import json
import os
import tarfile
import threading
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import zstandard
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

KEY_ID = b"\x01\x02\x03\x04\x05\x06\x07\x08"


@dataclass
class TestKey:
    priv: Ed25519PrivateKey

    @classmethod
    def new(cls) -> TestKey:
        return cls(Ed25519PrivateKey.generate())

    def public_minisign(self) -> str:
        raw = self.priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.b64encode(b"Ed" + KEY_ID + raw).decode()

    def sign(self, data: bytes, trusted: str = "timestamp:0") -> str:
        sig = self.priv.sign(data)
        glob = self.priv.sign(sig + trusted.encode())
        return (
            "untrusted comment: signature from test key\n"
            + base64.b64encode(b"Ed" + KEY_ID + sig).decode()
            + "\n"
            + f"trusted comment: {trusted}\n"
            + base64.b64encode(glob).decode()
            + "\n"
        )


def build_tar(entries: list[dict[str, Any]]) -> bytes:
    """entries: {name, data?} files, or {name, type: symlink|hardlink|chr, target?}."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for e in entries:
            ti = tarfile.TarInfo(e["name"])
            t = e.get("type", "file")
            if t == "symlink":
                ti.type, ti.linkname = tarfile.SYMTYPE, e["target"]
                tf.addfile(ti)
            elif t == "hardlink":
                ti.type, ti.linkname = tarfile.LNKTYPE, e["target"]
                tf.addfile(ti)
            elif t == "chr":
                ti.type, ti.devmajor, ti.devminor = tarfile.CHRTYPE, 1, 3
                tf.addfile(ti)
            else:
                data = e.get("data", b"")
                ti.size = len(data)
                ti.mode = e.get("mode", 0o755)
                tf.addfile(ti, io.BytesIO(data))
    return zstandard.ZstdCompressor().compress(buf.getvalue())


def manifest_of(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"path": e["name"], "size": len(e.get("data", b"")), "sha256": hashlib.sha256(e.get("data", b"")).hexdigest()}
        for e in entries
        if e.get("type", "file") == "file"
    ]


class PackServer:
    def __init__(self, root: Path, key: TestKey) -> None:
        self.root = root
        self.key = key
        self.packs: list[dict[str, Any]] = []
        root.mkdir(parents=True, exist_ok=True)
        self._httpd: ThreadingHTTPServer | None = None

    def add(self, pid: str, version: str, entries: list[dict[str, Any]], pack_api: int = 1, numerics_epoch: int = 3, **over: Any) -> dict[str, Any]:
        blob = build_tar(entries)
        name = f"{pid}-{version}.tar.zst"
        (self.root / name).write_bytes(blob)
        man = manifest_of(entries)
        p = {
            "id": pid,
            "version": version,
            "pack_api": pack_api,
            "platform": "linux-x86_64",
            "accel": "cpu",
            "numerics_epoch": numerics_epoch,
            "url": f"{{base}}/{name}",
            "size": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
            "unpacked_size": sum(m["size"] for m in man),
            "files_manifest": man,
            **over,
        }
        self.packs.append(p)
        return p

    def publish(self, tamper: bool = False) -> None:
        idx = json.dumps({"schema_version": 1, "packs": [{**p, "url": p["url"].replace("{base}", self.url)} for p in self.packs]}).encode()
        sig = self.key.sign(idx)
        if tamper:
            idx = idx[:-2] + (b"X" if idx[-2:-1] != b"X" else b"Y") + idx[-1:]
        (self.root / "packs-index.json").write_bytes(idx)
        (self.root / "packs-index.json.minisig").write_text(sig)

    def start(self) -> PackServer:
        handler = functools.partial(SimpleHTTPRequestHandler, directory=str(self.root))

        class Quiet(handler.func):  # type: ignore[misc, name-defined]
            def log_message(self, *a: object) -> None:
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(self.root)))
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


_ = os
