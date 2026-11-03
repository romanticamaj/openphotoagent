"""Remote worker: pairing, signed tokens, the egress policy and the outbound channel (02 §1.4, §5.2–§5.3, §6.3).

* ``enroll`` makes an ed25519 key pair here (private key 0600, never sent) and trades a one-time code for a
  worker id and a refresh token bound to the public key.
* ``RemoteClient`` signs ``worker_id|ts|nonce`` for every 15-minute access token and refreshes on 401. Every request
  goes through the ``EgressFilter`` first, so whatever code path produced it, data the workspace policy does not
  allow never leaves the machine. The policy is the stricter of what ``welcome`` says and the local floor set with
  the CLI: a tampered control plane cannot loosen it (MU-PRIV-005).
* ``Channel``: WSS ``/connect`` when it works (the CP pushes ``work.available``), long-poll ``GET /poll?wait=30``
  when it does not, retrying WSS every ``ws_retry_s`` and switching back as soon as it connects.
* The worker never listens on a port (MU-WORKER-001).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import secrets
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "1.0"
MODES = ("full", "thumbs", "edge-thumbs", "results-only")  # loosest → strictest
THUMB_LIMIT = {"full": 1024, "thumbs": 512, "edge-thumbs": 0, "results-only": 0}
VARIANTS = {"t256": 256, "t512": 512, "p1024": 1024}
# task kinds whose output is an image or a vector: never run under results-only (02 §1.4)
RESULTS_ONLY_DENIED = {"embed_assets", "embed_text", "embed_captions", "embed_image", "face_embed_query", "thumb_fetch"}
EDGE_DENIED: set[str] = set()
HASHED = "~h/"
_log = logging.getLogger("opagent.worker.egress")


# ---------------------------------------------------------------- pairing


@dataclass
class Credentials:
    server: str
    worker_id: str
    refresh_token: str
    server_pubkey_pin: str
    name: str = "worker"

    @staticmethod
    def path(home: Path) -> Path:
        return Path(home) / "remote.json"

    def save(self, home: Path) -> None:
        p = self.path(home)
        p.write_text(json.dumps(asdict(self)))
        os.chmod(p, 0o600)

    @classmethod
    def load(cls, home: Path) -> Credentials:
        return cls(**json.loads(cls.path(home).read_text()))


def _key_path(home: Path) -> Path:
    return Path(home) / "worker.key"


def load_key(home: Path) -> Any:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.from_private_bytes(_key_path(home).read_bytes())


def enroll(server: str, code: str, home: Path, name: str, http: Any = None) -> Credentials:
    """``opagent worker enroll --server … --code …`` (02 §5.2)."""
    import httpx
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    sk = Ed25519PrivateKey.generate()
    raw = sk.private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw, ser.NoEncryption())
    kp = _key_path(home)
    fd = os.open(kp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    pub = base64.b64encode(sk.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)).decode()
    own = http is None
    http = http or httpx.Client(base_url=server, timeout=30)
    try:
        r = http.post(
            "/worker-api/v1/enroll",
            json={"code": code, "public_key": pub, "name": name, "protocol_version": PROTOCOL_VERSION},
        )
    finally:
        if own:
            http.close()
    if r.status_code != 201:
        kp.unlink(missing_ok=True)
        raise RuntimeError(f"enrollment refused: {r.status_code} {r.text[:200]}")
    b = r.json()
    cred = Credentials(server, b["worker_id"], b["refresh_token"], b["server_pubkey_pin"], name)
    cred.save(home)
    (home / "worker_id").write_text(cred.worker_id)
    return cred


# ---------------------------------------------------------------- egress


def strictest(*modes: str | None) -> str:
    known = [m for m in modes if m in MODES]
    return max(known, key=MODES.index) if known else "thumbs"


class EgressDenied(Exception):
    pass


class EgressFilter:
    """Applies the workspace egress policy to every outbound request (02 §1.4)."""

    def __init__(self, floor: str | None = None, log_path: Path | None = None, clock: Any = None) -> None:
        from opagent_core.clock import SystemClock

        self.clock = clock or SystemClock()
        self.floor = floor
        self.mode = strictest(floor, "thumbs") if floor else "thumbs"
        self.strip_gps = True
        self.hash_paths = False
        self.log_path = log_path
        self.path_map: dict[str, str] = {}
        self.map_path = log_path.parent / "pathmap.jsonl" if log_path is not None else None
        if self.map_path is not None and self.map_path.exists():  # hashed paths must survive restarts
            for line in self.map_path.read_text().splitlines():
                if line.strip():
                    k, v = json.loads(line)
                    self.path_map[k] = v

    def apply(self, policy: dict[str, Any]) -> None:
        self.mode = strictest(str(policy.get("mode")), self.floor)
        strict = self.mode == "results-only"
        self.strip_gps = bool(policy.get("strip_gps", True)) or strict or self.floor == "results-only"
        self.hash_paths = bool(policy.get("hash_paths", False)) and strict

    def deny(self, what: str) -> None:
        _log.warning("EGRESS_POLICY_DENIED: %s (mode %s)", what, self.mode)
        if self.log_path is not None:
            with open(self.log_path, "a") as f:
                f.write(json.dumps({"at": self.clock.now().isoformat(), "mode": self.mode, "denied": what}) + "\n")
        raise EgressDenied(what)

    def task_allowed(self, kind: str) -> None:
        if self.mode == "results-only" and kind in RESULTS_ONLY_DENIED:
            self.deny(f"task {kind}")
        if self.mode != "edge-thumbs" and self.mode != "results-only":
            return
        if kind in EDGE_DENIED:
            self.deny(f"task {kind}")

    def hashed(self, rel: str) -> str:
        if not self.hash_paths:
            return rel
        ext = os.path.splitext(rel)[1].lower()[:10]
        h = HASHED + hashlib.sha256(rel.encode()).hexdigest()[:32] + ext
        if h not in self.path_map:
            self.path_map[h] = rel
            if self.map_path is not None:
                with open(self.map_path, "a") as f:
                    f.write(json.dumps([h, rel]) + "\n")
        return h

    def resolve(self, rel: str) -> str:
        return self.path_map.get(rel, rel) if rel.startswith(HASHED) else rel

    def outbound(self, method: str, path: str, body: Any) -> Any:
        """The body that may leave, or ``EgressDenied``."""
        p = path.split("?", 1)[0]
        if "/thumbs/" in p:
            variant = p.rstrip("/").rsplit("/", 1)[-1]
            if VARIANTS.get(variant, 10**9) > THUMB_LIMIT[self.mode]:
                self.deny(f"thumbnail {variant}")
            return body
        if self.mode == "results-only" and ("embedding" in p or "/faces" in p):
            self.deny(f"vectors {p}")
        if isinstance(body, dict) and isinstance(body.get("entries"), list):
            body = {**body, "entries": [self._entry(e) for e in body["entries"]]}
            if "cursor" in body and isinstance(body["cursor"], str):
                body["cursor"] = self.hashed(body["cursor"])
        relay = isinstance(body, dict) and isinstance(body.get("result"), dict) and "thumbnail_b64" in body["result"]
        if relay and self.mode == "results-only":
            self.deny("thumbnail relay")
        return body

    def _entry(self, e: Any) -> Any:
        if not isinstance(e, dict):
            return e
        e = dict(e)
        if "rel" in e:
            e["rel"] = self.hashed(str(e["rel"]))
        if self.strip_gps and isinstance(e.get("exif"), dict):
            e["exif"] = {k: v for k, v in e["exif"].items() if k.lower() not in ("gps", "gpsinfo", "location")}
        if self.mode in ("edge-thumbs", "results-only"):
            e.pop("thumbhash", None)  # a tiny picture is still a picture
        if self.mode == "results-only":
            e.pop("thumbs", None)
        return e


# ---------------------------------------------------------------- the client


class RemoteClient:
    """An httpx-like client for the control plane: signed access tokens, refresh on 401, egress filter."""

    def __init__(self, home: Path, http: Any = None, egress: EgressFilter | None = None, clock: Any = None) -> None:
        import httpx

        self.home = Path(home)
        self.cred = Credentials.load(self.home)
        self.sk = load_key(self.home)
        self.http = http or httpx.Client(base_url=self.cred.server, timeout=60)
        from opagent_core.clock import SystemClock

        self.clock = clock or SystemClock()
        self.egress = egress or EgressFilter(log_path=self.home / "egress-denied.log", clock=self.clock)
        self._access: str | None = None
        self._lock = threading.Lock()

    @property
    def worker_id(self) -> str:
        return self.cred.worker_id

    def access_token(self, force: bool = False) -> str:
        with self._lock:
            if self._access is None or force:
                ts, nonce = self.clock.now().isoformat(), secrets.token_hex(16)
                sig = base64.b64encode(self.sk.sign(f"{self.worker_id}|{ts}|{nonce}".encode())).decode()
                r = self.http.post(
                    "/worker-api/v1/token",
                    json={"worker_id": self.worker_id, "ts": ts, "nonce": nonce, "sig": sig},
                    headers={"Authorization": f"Bearer {self.cred.refresh_token}"},
                )
                if r.status_code != 200:
                    raise PermissionError(f"token refused: {r.status_code} {r.text[:200]}")
                self._access = str(r.json()["access_token"])
            return self._access

    def request(self, method: str, path: str, **kw: Any) -> Any:
        if "json" in kw:
            kw["json"] = self.egress.outbound(method, path, kw["json"])
        elif "content" in kw:
            self.egress.outbound(method, path, None)
        for attempt in (0, 1):
            h = {**(kw.pop("headers", None) or {}), "Authorization": f"Bearer {self.access_token(force=attempt == 1)}"}
            r = self.http.request(method, path, headers=h, **kw)
            if r.status_code != 401:
                return r
            kw["headers"] = {k: v for k, v in h.items() if k != "Authorization"}
        return r

    def post(self, path: str, **kw: Any) -> Any:
        return self.request("POST", path, **kw)

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def put(self, path: str, **kw: Any) -> Any:
        return self.request("PUT", path, **kw)

    def ws_url(self) -> str:
        base = str(self.http.base_url).rstrip("/")
        return base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/worker-api/v1/connect"


# ---------------------------------------------------------------- the channel


class Channel:
    """WSS when it works, long-poll when it does not (02 §5.3; MU-WORKER-004)."""

    def __init__(self, client: RemoteClient, ws_retry_s: float = 5.0, connect: Any = None) -> None:
        self.client = client
        self.ws_retry_s = ws_retry_s
        self.connect = connect or self._connect
        self.mode = "poll"
        self.welcome: dict[str, Any] | None = None
        self._ws: Any = None
        self._next_ws_try = 0.0

    def _connect(self, url: str, headers: dict[str, str]) -> Any:
        from websockets.sync.client import connect

        return connect(url, additional_headers=headers, open_timeout=5)

    def _now(self) -> float:
        return float(self.client.clock.monotonic())

    def _try_ws(self) -> None:
        if self._ws is not None or self._now() < self._next_ws_try:
            return
        try:
            ws = self.connect(self.client.ws_url(), {"Authorization": f"Bearer {self.client.access_token()}"})
            ws.send(json.dumps({"type": "hello", "protocol_version": PROTOCOL_VERSION}))
            self.welcome = json.loads(ws.recv(timeout=5))
            self._ws, self.mode = ws, "ws"
        except Exception as e:
            _log.info("WSS unavailable (%s); long-polling", type(e).__name__)
            self._ws, self.mode = None, "poll"
            self._next_ws_try = self._now() + self.ws_retry_s

    def wait_for_work(self, wait_s: float) -> bool:
        """WSS: wait for ``work.available`` (True) or timeout. Poll mode returns True: the poll itself waits."""
        self._try_ws()
        if self._ws is None:
            return True
        try:
            msg = json.loads(self._ws.recv(timeout=wait_s))
        except TimeoutError:
            return False
        except Exception:
            self.close()
            self._next_ws_try = self._now() + self.ws_retry_s
            return False
        return bool(msg.get("type") == "work.available")

    def ack(self) -> None:
        if self._ws is not None:
            try:
                self._ws.send(json.dumps({"type": "ack"}))
            except Exception:
                self.close()

    def lease(self, wait_s: float, boot_id: str | None = None) -> dict[str, Any] | None:
        """One task or None. WSS mode leases right away after a push; poll mode long-polls."""
        if self.mode == "ws":
            r = self.client.post(
                "/worker-api/v1/lease", json={"worker_id": self.client.worker_id, "wait_s": 0, "boot_id": boot_id}
            )
        else:
            r = self.client.get("/worker-api/v1/poll", params={"wait": wait_s, "boot_id": boot_id or ""})
        if r.status_code == 204:
            return None
        if r.status_code == 426:
            raise UpgradeRequired()
        r.raise_for_status()
        self.ack()
        return r.json()  # type: ignore[no-any-return]

    def close(self) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                self._ws.close()
        self._ws, self.mode = None, "poll"


class UpgradeRequired(Exception):
    pass
