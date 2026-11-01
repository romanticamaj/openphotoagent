"""Runtime packs (08 §4.2–4.4): signed index → pack_api gate → sha-verified download → hardened extraction →
atomic activation → self-test (rollback on failure). Runs on the Worker only.

Signature format: minisign (Ed25519, algorithm ``Ed``): the index is signed, and the trusted comment is covered by
the global signature. The public key ships with the app (B-5: the production private key is the user's secret).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import tarfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import zstandard
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_INFLATION = 1.10  # unpacked size may exceed the declared size by at most 10 % (zip-bomb guard)


class PackError(Exception):
    def __init__(self, code: str, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass
class InstallResult:
    status: str  # installed | broken | already
    pack: str
    version: str
    code: str | None = None
    path: str | None = None


def verify_minisign(data: bytes, sig_text: str, public_key_b64: str) -> None:
    lines = [ln for ln in sig_text.splitlines() if ln.strip()]
    try:
        sig_blob = base64.b64decode(lines[1])
        trusted = lines[2].split("trusted comment: ", 1)[1].encode()
        glob = base64.b64decode(lines[3])
        pk_blob = base64.b64decode(public_key_b64)
    except (IndexError, ValueError) as e:
        raise PackError("PACK_SIGNATURE_INVALID", reason=f"malformed signature: {e}") from None
    if sig_blob[:2] != b"Ed" or pk_blob[:2] != b"Ed" or sig_blob[2:10] != pk_blob[2:10]:
        raise PackError("PACK_SIGNATURE_INVALID", reason="algorithm or key id mismatch")
    key = Ed25519PublicKey.from_public_bytes(pk_blob[10:])
    try:
        key.verify(sig_blob[10:], data)
        key.verify(glob, sig_blob[10:] + trusted)
    except InvalidSignature:
        raise PackError("PACK_SIGNATURE_INVALID", reason="bad signature") from None


def _safe_extract(archive: bytes, dest: Path, declared_size: int, manifest: list[dict[str, Any]]) -> None:
    """Reject absolute paths, ``..``, links, devices, files outside the manifest and inflated archives."""
    raw = zstandard.ZstdDecompressor().decompressobj().decompress(archive)
    if len(raw) > max(declared_size, 1) * MAX_INFLATION + 64 * 1024:  # tar headers/padding allowance
        raise PackError("PACK_UNSAFE_ARCHIVE", reason="inflated beyond declared size")
    allowed = {m["path"]: m for m in manifest}
    import io

    total = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tf:
        members = tf.getmembers()
        for m in members:
            p = PurePosixPath(m.name)
            if m.name.startswith("/") or p.is_absolute() or ".." in p.parts or "\\" in m.name:
                raise PackError("PACK_UNSAFE_ARCHIVE", reason=f"unsafe path {m.name}")
            if m.issym() or m.islnk() or m.isdev() or m.isfifo() or m.ischr() or m.isblk():
                raise PackError("PACK_UNSAFE_ARCHIVE", reason=f"link or device {m.name}")
            if m.isdir():
                continue
            if not m.isfile() or m.name not in allowed:
                raise PackError("PACK_UNSAFE_ARCHIVE", reason=f"not in manifest {m.name}")
            total += m.size
            if total > max(declared_size, 1) * MAX_INFLATION:
                raise PackError("PACK_UNSAFE_ARCHIVE", reason="unpacked size exceeds declaration")
        for m in members:
            if m.isdir():
                (dest / m.name).mkdir(parents=True, exist_ok=True)
                continue
            out = dest / m.name
            out.parent.mkdir(parents=True, exist_ok=True)
            f = tf.extractfile(m)
            assert f is not None
            data = f.read()
            if hashlib.sha256(data).hexdigest() != allowed[m.name]["sha256"]:
                raise PackError("PACK_UNSAFE_ARCHIVE", reason=f"manifest hash mismatch {m.name}")
            out.write_bytes(data)
            os.chmod(out, (m.mode & 0o755) | stat.S_IRUSR | stat.S_IWUSR)
    missing = set(allowed) - {m.name for m in members}
    if missing:
        raise PackError("PACK_UNSAFE_ARCHIVE", reason=f"missing files {sorted(missing)}")


@dataclass
class PackInstaller:
    root: Path
    public_key: str
    index_url: str
    lock: dict[str, dict[str, Any]] = field(default_factory=dict)  # packs.lock: id -> {pack_api: [..], version?}
    self_test: Callable[[Path], bool] = lambda d: True
    fetch: Callable[[str], bytes] | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        (self.root / ".staging").mkdir(parents=True, exist_ok=True)

    # ---- index ----
    def _get(self, url: str) -> bytes:
        if self.fetch:
            return self.fetch(url)
        with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 - index URL comes from settings
            return bytes(r.read())

    def _index(self, data: bytes | None = None, sig: str | None = None) -> dict[str, Any]:
        data = data if data is not None else self._get(self.index_url)
        sig = sig if sig is not None else self._get(self.index_url + ".minisig").decode()
        verify_minisign(data, sig, self.public_key)
        return dict(json.loads(data))

    def _entry(self, index: dict[str, Any], pid: str, version: str | None = None) -> dict[str, Any]:
        cands = [p for p in index["packs"] if p["id"] == pid and (version is None or p["version"] == version)]
        if not cands:
            raise PackError("DL_NOT_FOUND", pack=pid)
        e = cands[-1]
        allowed = (self.lock.get(pid) or {}).get("pack_api") or [1]
        if e.get("pack_api") not in allowed:
            raise PackError("PACK_INCOMPATIBLE", pack=pid, v=e["version"], range=allowed)
        return dict(e)

    # ---- active state ----
    def _active(self) -> dict[str, str]:
        p = self.root / "active.json"
        return dict(json.loads(p.read_text())) if p.exists() else {}

    def _set_active(self, pid: str, version: str) -> None:
        act = self._active()
        act[pid] = version
        tmp = self.root / "active.json.tmp"
        tmp.write_text(json.dumps(act, sort_keys=True))
        os.replace(tmp, self.root / "active.json")
        meta = self.root / pid / version / ".opagent-pack.json"
        if not meta.exists():
            return
        # keep exactly one previous version for rollback (08 §4.3)
        versions = sorted((d for d in (self.root / pid).iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime)
        for old in versions[:-2]:
            if old.name != version:
                shutil.rmtree(old, ignore_errors=True)

    # ---- install ----
    def _stage(self, e: dict[str, Any], archive: bytes) -> Path:
        if hashlib.sha256(archive).hexdigest() != e["sha256"]:
            raise PackError("PACK_CHECKSUM_MISMATCH", pack=e["id"])
        dest = self.root / ".staging" / f"{e['id']}-{e['version']}"
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True)
        try:
            _safe_extract(archive, dest, int(e.get("unpacked_size") or e["size"]), e.get("files_manifest") or [])
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        (dest / ".opagent-pack.json").write_text(
            json.dumps({k: e[k] for k in ("id", "version", "numerics_epoch", "pack_api") if k in e})
        )
        return dest

    def _activate(self, e: dict[str, Any], staged: Path) -> InstallResult:
        final = self.root / e["id"] / e["version"]
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            shutil.rmtree(final)
        os.replace(staged, final)  # atomic on the same filesystem
        if not self.self_test(final):
            return InstallResult("broken", e["id"], e["version"], "RUNTIME_PACK_BROKEN", str(final))
        self._set_active(e["id"], e["version"])
        return InstallResult("installed", e["id"], e["version"], None, str(final))

    def _download_and_stage(self, e: dict[str, Any]) -> Path:
        try:
            return self._stage(e, self._get(e["url"]))
        finally:
            for p in (self.root / ".staging").glob("*.tmp"):
                p.unlink(missing_ok=True)

    def install(self, pid: str, version: str | None = None) -> InstallResult:
        e = self._entry(self._index(), pid, version)
        return self._activate(e, self._download_and_stage(e))

    def import_file(self, archive: Path, index_path: Path, sig_path: Path) -> InstallResult:
        """Offline import (08 §4.3, PACK-009): same verification as a download."""
        index = self._index(index_path.read_bytes(), sig_path.read_text())
        data = archive.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        e = next((p for p in index["packs"] if p["sha256"] == sha), None)
        if e is None:
            raise PackError("PACK_CHECKSUM_MISMATCH", pack=archive.name)
        e = self._entry(index, e["id"], e["version"])
        return self._activate(e, self._stage(e, data))

    def sync_lock(self, idle: bool) -> list[str]:
        """``packs.lock`` changed (app update): download in the background, switch when idle (PACK-008)."""
        staged = []
        index = self._index()
        act = self._active()
        for pid, spec in self.lock.items():
            want = spec.get("version")
            if not want or act.get(pid) == want:
                continue
            e = self._entry(index, pid, want)
            dest = self.root / ".staging" / f"{pid}-{want}"
            if not dest.exists():
                self._download_and_stage(e)
            staged.append(pid)
            if idle:
                self._activate(e, dest)
        return staged if not idle else []

    def fingerprint_for(self, pid: str, adapter: str, weights: str, adapter_version: str = "1") -> str:
        from opagent_core.config_hash import model_fingerprint

        ver = self._active()[pid]
        meta = json.loads((self.root / pid / ver / ".opagent-pack.json").read_text())
        return model_fingerprint(
            adapter, adapter_version, weights, int(meta.get("numerics_epoch", 0)), pack_version=ver
        )


@dataclass
class SmokeResult:
    chosen: str | None
    failures: list[dict[str, Any]]


def smoke_fallback(order: list[str], smoke: Callable[[str], bool]) -> SmokeResult:
    """Onboarding: CUDA smoke test fails → try Vulkan → CPU, keeping the reasons for the UI (PACK-011)."""
    failures: list[dict[str, Any]] = []
    for pack in order:
        if smoke(pack):
            return SmokeResult(pack, failures)
        failures.append({"pack": pack, "code": "RUNTIME_PACK_BROKEN"})
    return SmokeResult(None, failures)
