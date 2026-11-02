"""Server-issued handles for state across MCP calls (05 §3.3.1): ``cursor`` (search pages) and ``selection_id``
(a whole result set as a signed query snapshot, not a list of ids).

A handle is ``<kind>_<base64url(json)>.<base64url(hmac)>``; it carries the token id and a key for the token's
folder scope, so a handle made under a wide scope cannot be replayed under a narrower one (MCP-FOLD-002), and
it expires (cursor 10 minutes, selection 24 hours).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import Any

TTL = {"cur": timedelta(minutes=10), "sel": timedelta(hours=24), "plc": timedelta(minutes=10)}


class HandleError(ValueError):
    pass


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Signer:
    def __init__(self, key: bytes) -> None:
        self.key = key

    def _mac(self, kind: str, body: str) -> str:
        return _b64(hmac.new(self.key, f"{kind}.{body}".encode(), hashlib.sha256).digest())

    def issue(self, kind: str, data: dict[str, Any], *, token_id: str, scope_key: str, now: datetime) -> str:
        exp = int((now + TTL[kind]).timestamp())
        body = _b64(json.dumps({"d": data, "t": token_id, "s": scope_key, "e": exp}, sort_keys=True).encode())
        return f"{kind}_{body}.{self._mac(kind, body)}"

    def open(self, kind: str, handle: str, *, token_id: str, scope_key: str, now: datetime) -> dict[str, Any]:
        prefix, _, rest = handle.partition("_")
        body, _, mac = rest.partition(".")
        if prefix != kind or not body or not mac:
            raise HandleError(f"not a {kind} handle")
        if not hmac.compare_digest(mac, self._mac(kind, body)):
            raise HandleError("bad signature")
        try:
            payload = json.loads(_unb64(body))
        except ValueError as e:
            raise HandleError("bad signature") from e
        if payload["t"] != token_id or payload["s"] != scope_key:
            raise HandleError("handle belongs to another token or folder scope")
        if now.timestamp() > payload["e"]:
            raise HandleError("handle expired")
        return dict(payload["d"])
