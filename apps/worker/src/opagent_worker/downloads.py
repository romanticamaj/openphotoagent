"""Download manager (06 §2): exact totals, Range/If-Range resume, segmented parallel fetch, true cancel,
sha256 verification, disk precheck, retries with backoff, mirror failover and an atomic HF-cache finalize.

Runs on the Worker only (it writes the models directory). Files land in ``{root}/.downloads/{op}/{sha}.part`` with a
``.part.json`` sidecar; finalize moves them to ``{root}/hf/models--org--name/blobs/{sha}`` and links
``snapshots/{revision}/{path}`` (symlink → hardlink → copy).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from opagent_core.clock import Clock, SystemClock
from opagent_fs.crash import crash_point

CHUNK = 1024 * 1024
META_EVERY = 8 * CHUNK  # resume-state writes (ADR-0007: keeps the download's CPU cost low)
BACKOFF_S = (2, 8, 30, 120, 300)  # 06 §2.9, ±20 % jitter
MAX_TRIES = 5
SWITCH_AFTER = 3  # consecutive transient failures on one source → next source (06 §2.6)
GB = 1024**3
PERMANENT = {401: "HF_AUTH_REQUIRED", 403: "HF_GATED_ACCESS_PENDING", 404: "DL_NOT_FOUND"}


@dataclass(frozen=True)
class DlFile:
    repo: str
    revision: str
    path: str
    size: int
    sha256: str


class CancelToken:
    """Thread-safe pause/cancel request, checked after every chunk."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.mode: str | None = None  # "pause" | "cancel"
        self.keep_part = True

    @property
    def requested(self) -> bool:
        return self.mode is not None

    def pause(self) -> None:
        with self._lock:
            self.mode, self.keep_part = "pause", True

    def cancel(self, keep_part: bool = False) -> None:
        with self._lock:
            self.mode, self.keep_part = "cancel", keep_part


class DownloadError(Exception):
    def __init__(self, code: str, permanent: bool = True, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.permanent = permanent
        self.details = details


@dataclass
class Result:
    status: str  # done | paused | cancelled | failed
    error_code: str | None = None
    permanent: bool = False
    warnings: list[str] = field(default_factory=list)
    installed: list[dict[str, Any]] = field(default_factory=list)
    waiting_reason: str | None = None


class _Stop(Exception):
    pass


class _RemoteChanged(Exception):
    pass


class _AutoPause(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def queue_precheck(sizes: list[int], free_bytes: int) -> dict[str, Any] | None:
    """DL-012: queued downloads count against the same free space."""
    need = sum(sizes)
    if need <= free_bytes:
        return None
    return {
        "code": "DISK_INSUFFICIENT_MODELS",
        "params": {"queued": len(sizes), "required_bytes": need, "free_bytes": free_bytes},
        "message": f"排隊的 {len(sizes)} 個下載共需 {need / GB:.0f} GB，可用 {free_bytes / GB:.0f} GB",
    }


def _cache_dir(root: Path, repo: str) -> Path:
    return root / "hf" / ("models--" + repo.replace("/", "--"))


class Downloader:
    def __init__(
        self,
        root: Path,
        sources: list[str],
        reserve_floor: int | None = None,
        disk_free: Callable[[str], int] | None = None,
        clock: Clock | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        segment_threshold: int = 512 * 1024 * 1024,
        segments: int = 4,
        max_connections: int = 8,
        hf_token: str | None = None,
        disk_check_every_bytes: int = 64 * 1024 * 1024,
        rate_limit_bps: float | None = None,
        disk_margin: int = GB,
    ) -> None:
        self.root = Path(root)
        self.sources = list(sources)
        self.src = 0
        self.reserve_floor = reserve_floor
        self.disk_free = disk_free or (lambda p: shutil.disk_usage(p).free)
        self.clock = clock or SystemClock()
        self.on_progress = on_progress
        self.segment_threshold = segment_threshold
        self.segments = segments
        self.max_connections = max_connections
        self.hf_token = hf_token
        self.disk_check_every_bytes = disk_check_every_bytes
        self.rate_limit_bps = rate_limit_bps
        self.disk_margin = disk_margin
        self._since_check = 0
        self._rl_start: float | None = None
        self._rl_bytes = 0

    # ---- state ----
    def _opdir(self, op_id: str) -> Path:
        return self.root / ".downloads" / op_id

    def _paths(self, op_id: str, f: DlFile) -> tuple[Path, Path]:
        d = self._opdir(op_id)
        return d / f"{f.sha256}.part", d / f"{f.sha256}.part.json"

    def _blob(self, f: DlFile) -> Path:
        return _cache_dir(self.root, f.repo) / "blobs" / f.sha256

    def _snapshot(self, f: DlFile) -> Path:
        return _cache_dir(self.root, f.repo) / "snapshots" / f.revision / f.path

    def installed_state(self, files: list[DlFile]) -> str:
        ok = all(self._snapshot(f).exists() and self._blob(f).exists() for f in files)
        return "installed" if ok else "not_installed"

    @staticmethod
    def _load_meta(p: Path) -> dict[str, Any]:
        if p.exists():
            try:
                return dict(json.loads(p.read_text(encoding="utf-8")))
            except ValueError:
                return {}
        return {}

    @staticmethod
    def _save_meta(p: Path, meta: dict[str, Any]) -> None:
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta), encoding="utf-8")
        os.replace(tmp, p)

    # ---- public ----
    async def download(self, op_id: str, files: list[DlFile], token: CancelToken) -> Result:
        res = Result(status="done")
        todo = [f for f in files if not (self._blob(f).exists() and self._snapshot(f).exists())]
        metas = {f: self._load_meta(self._paths(op_id, f)[1]) for f in todo}
        remaining = sum(f.size - int(metas[f].get("written", 0)) for f in todo if not self._blob(f).exists())
        total = sum(f.size for f in files)
        done = total - remaining
        # precheck (06 §2.5): before any request
        free = self.disk_free(str(self.root if self.root.exists() else self.root.parent))
        reserve = self.reserve_floor if self.reserve_floor is not None else max(2 * GB, int(free * 0.02))
        required = int(remaining * 1.05)
        if required > free - reserve:
            raise DownloadError(
                "DISK_INSUFFICIENT_MODELS",
                True,
                required_bytes=required,
                free_bytes=free - reserve,
                path=str(self.root),
            )
        self._opdir(op_id).mkdir(parents=True, exist_ok=True)
        prog: dict[str, Any] = {"op_id": op_id, "total_bytes": total, "done_bytes": done, "file": None}
        self._emit(prog)
        sem = asyncio.Semaphore(self.max_connections)
        rng = random.Random(op_id)  # noqa: S311 - backoff jitter, not cryptography
        try:
            for f in sorted(todo, key=lambda x: (x.size, x.path)):  # small files first (06 §2.4)
                if self._blob(f).exists():
                    continue
                prog["file"] = f.path
                for attempt in (0, 1):
                    await self._fetch(op_id, f, token, sem, rng, prog, res)
                    if self._verify(op_id, f):
                        break
                    part, meta = self._paths(op_id, f)
                    prog["done_bytes"] -= f.size
                    part.unlink(missing_ok=True)
                    meta.unlink(missing_ok=True)
                    if attempt == 1:
                        raise DownloadError("DL_CHECKSUM_MISMATCH", True, path=f.path, expected=f.sha256)
                self._to_blob(op_id, f)
            for f in files:
                self._link(f)
                res.installed.append(
                    {"path": f.path, "sha256": f.sha256, "size": f.size, "repo": f.repo, "revision": f.revision}
                )
        except _AutoPause as p:  # 06 §2.5: paused_auto; resumes when space is back (DL-011)
            res.status, res.waiting_reason = "paused", p.reason
            return res
        except _Stop:
            res.status = "paused" if token.mode == "pause" else "cancelled"
            if token.mode == "cancel" and not token.keep_part:
                shutil.rmtree(self._opdir(op_id), ignore_errors=True)
            return res
        except DownloadError as e:
            res.status, res.error_code, res.permanent = "failed", e.code, e.permanent
            return res
        shutil.rmtree(self._opdir(op_id), ignore_errors=True)
        return res

    # ---- internals ----
    async def _throttle(self, n: int) -> None:
        if not self.rate_limit_bps:
            return
        now = self.clock.monotonic()
        if self._rl_start is None:
            self._rl_start = now
        self._rl_bytes += n
        ahead = self._rl_bytes / self.rate_limit_bps - (now - self._rl_start)
        if ahead > 0:
            await self.clock.sleep(ahead)

    def _check_disk(self, n: int, prog: dict[str, Any]) -> None:
        self._since_check += n
        if self._since_check < self.disk_check_every_bytes:
            return
        self._since_check = 0
        remaining = prog["total_bytes"] - prog["done_bytes"]
        free = self.disk_free(str(self.root))
        if free < remaining + min(self.disk_margin, max(remaining, 1)):
            raise _AutoPause("DISK_INSUFFICIENT_MODELS")

    async def resolve(self, repo: str, revision: str, paths: list[str], allow_unverified: bool = False) -> list[DlFile]:
        """Sizes and hashes for files that have none (user YAML without sha256). Official metadata first; a
        mirror's metadata needs the user's explicit confirmation (DL_UNVERIFIED_SOURCE, 06 §2.6)."""
        for i, src in enumerate(self.sources):
            try:
                async with httpx.AsyncClient(timeout=10.0) as c:
                    r = await c.get(f"{src.rstrip('/')}/api/models/{repo}/revision/{revision}", headers=self._headers())
            except httpx.TransportError:
                continue
            if r.status_code != 200:
                continue
            sib = {x["rfilename"]: x for x in r.json().get("siblings", [])}
            if i > 0 and not allow_unverified:
                raise DownloadError("DL_UNVERIFIED_SOURCE", False, source=src)
            return [
                DlFile(repo, revision, p, int(sib[p]["size"]), str((sib[p].get("lfs") or {}).get("sha256")))
                for p in paths
                if p in sib
            ]
        raise DownloadError("NET_UNREACHABLE", False, host=self.sources[0] if self.sources else "")

    async def _backoff(self, delay: float, prog: dict[str, Any], token: CancelToken) -> None:
        """Retry backoff (06 §2.9) shown as ``waiting: NET_UNREACHABLE`` with a heartbeat every second, so the
        Operation explains itself and a pause/cancel arriving on the control channel still ends it within 2 s."""

        async def heartbeat() -> None:
            while not token.requested:
                self._emit({**prog, "waiting_reason": "NET_UNREACHABLE", "retry_in_s": round(delay)})
                await asyncio.sleep(1.0)

        hb = asyncio.create_task(heartbeat())
        sl = asyncio.create_task(self.clock.sleep(delay))
        await asyncio.wait({hb, sl}, return_when=asyncio.FIRST_COMPLETED)
        hb.cancel()
        sl.cancel()
        if token.requested:
            raise _Stop()

    def _emit(self, prog: dict[str, Any]) -> None:
        if self.on_progress:
            self.on_progress(dict(prog))

    def _verify(self, op_id: str, f: DlFile) -> bool:
        part, _ = self._paths(op_id, f)
        h = hashlib.sha256()
        with part.open("rb") as fh:
            for chunk in iter(lambda: fh.read(CHUNK), b""):
                h.update(chunk)
        return h.hexdigest() == f.sha256

    def _to_blob(self, op_id: str, f: DlFile) -> None:
        part, meta = self._paths(op_id, f)
        blob = self._blob(f)
        blob.parent.mkdir(parents=True, exist_ok=True)
        os.replace(part, blob)  # atomic on the same filesystem
        meta.unlink(missing_ok=True)

    def _link(self, f: DlFile) -> None:
        snap, blob = self._snapshot(f), self._blob(f)
        crash_point("download_finalize")  # chaos: die between blob and snapshot (DL-017)
        if snap.exists():
            return
        snap.parent.mkdir(parents=True, exist_ok=True)
        tmp = snap.with_name(snap.name + ".tmp-link")
        tmp.unlink(missing_ok=True)
        try:
            os.symlink(os.path.relpath(blob, snap.parent), tmp)
        except OSError:  # Windows without symlink privilege (DL-018): hardlink, else copy
            try:
                os.link(blob, tmp)
            except OSError:
                shutil.copyfile(blob, tmp)
        os.replace(tmp, snap)

    def _url(self, f: DlFile) -> str:
        return f"{self.sources[self.src].rstrip('/')}/{f.repo}/resolve/{f.revision}/{f.path}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.hf_token}"} if self.hf_token else {}

    async def _fetch(
        self,
        op_id: str,
        f: DlFile,
        token: CancelToken,
        sem: asyncio.Semaphore,
        rng: random.Random,
        prog: dict[str, Any],
        res: Result,
    ) -> None:
        part, meta_p = self._paths(op_id, f)
        meta = self._load_meta(meta_p)
        if not part.exists():
            meta = {}
        if not meta:
            nseg = self.segments if f.size >= self.segment_threshold and self.segments > 1 else 1
            step = -(-f.size // nseg)
            meta = {
                "size": f.size,
                "etag": None,
                "written": 0,
                "segments": [
                    {"start": s, "end": min(f.size, s + step) - 1, "written": 0} for s in range(0, f.size, step)
                ],
            }
            with part.open("wb") as fh:
                fh.truncate(f.size)
        self._save_meta(meta_p, meta)
        fd = os.open(part, os.O_RDWR)
        lock = asyncio.Lock()
        try:
            while True:
                try:
                    await asyncio.gather(
                        *(
                            self._segment(f, seg, meta, meta_p, fd, token, sem, rng, prog, res, lock)
                            for seg in meta["segments"]
                            if seg["written"] < seg["end"] - seg["start"] + 1
                        )
                    )
                    return
                except _RemoteChanged:
                    res.warnings.append("DL_REMOTE_CHANGED") if "DL_REMOTE_CHANGED" not in res.warnings else None
                    prog["done_bytes"] -= meta["written"]
                    meta["written"] = 0
                    meta["etag"] = None
                    meta["segments"] = [{"start": 0, "end": f.size - 1, "written": 0}]
                    self._save_meta(meta_p, meta)
        finally:
            os.close(fd)

    async def _segment(
        self,
        f: DlFile,
        seg: dict[str, Any],
        meta: dict[str, Any],
        meta_p: Path,
        fd: int,
        token: CancelToken,
        sem: asyncio.Semaphore,
        rng: random.Random,
        prog: dict[str, Any],
        res: Result,
        lock: asyncio.Lock,
    ) -> None:
        tries = 0
        consecutive = 0
        while True:
            if token.requested:
                raise _Stop()
            start = seg["start"] + seg["written"]
            headers = self._headers()
            ranged = start > 0 or seg["end"] < f.size - 1
            if ranged:
                headers["Range"] = f"bytes={start}-{seg['end']}" if seg["end"] < f.size - 1 else f"bytes={start}-"
                if meta.get("etag"):
                    headers["If-Range"] = meta["etag"]
            try:
                async with (
                    sem,
                    httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0)) as client,
                    client.stream("GET", self._url(f), headers=headers) as r,
                ):
                    if r.status_code in PERMANENT:
                        raise DownloadError(PERMANENT[r.status_code], True, path=f.path)
                    if r.status_code == 429:
                        wait = float(r.headers.get("Retry-After") or 5)
                        if "DL_RATE_LIMITED" not in res.warnings:
                            res.warnings.append("DL_RATE_LIMITED")
                            self.segments = max(1, self.segments // 2)
                        await self.clock.sleep(wait)
                        continue
                    if r.status_code >= 500:
                        raise httpx.HTTPStatusError("server error", request=r.request, response=r)
                    if ranged and r.status_code == 200 and headers.get("If-Range"):
                        raise _RemoteChanged()
                    etag = r.headers.get("ETag")
                    if etag and not meta.get("etag"):
                        meta["etag"] = etag
                    unsaved = 0
                    async for chunk in r.aiter_bytes(CHUNK):
                        off = seg["start"] + seg["written"]
                        os.pwrite(fd, chunk, off)
                        async with lock:
                            seg["written"] += len(chunk)
                            meta["written"] += len(chunk)
                            prog["done_bytes"] += len(chunk)
                            unsaved += len(chunk)
                            # resume state at most every 8 MB: a lagging offset only re-fetches bytes (sha checked)
                            if unsaved >= META_EVERY or seg["written"] >= seg["end"] - seg["start"] + 1:
                                self._save_meta(meta_p, meta)
                                unsaved = 0
                        self._emit(prog)
                        crash_point("download_chunk")  # chaos: kill -9 mid-file (DL-014)
                        consecutive = 0
                        if token.requested:
                            async with lock:
                                self._save_meta(meta_p, meta)  # exact offset for the resume (DL-003)
                            raise _Stop()
                        await self._throttle(len(chunk))
                        self._check_disk(len(chunk), prog)
                if seg["written"] >= seg["end"] - seg["start"] + 1:
                    return
                raise httpx.RemoteProtocolError("short body")
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                tries += 1
                consecutive += 1
                if consecutive >= SWITCH_AFTER and self.src + 1 < len(self.sources):
                    self.src += 1
                    consecutive = 0
                    if "DL_SOURCE_SWITCHED" not in res.warnings:
                        res.warnings.append("DL_SOURCE_SWITCHED")
                    continue
                if tries > MAX_TRIES:
                    code = "DL_MIRROR_FAILED" if len(self.sources) > 1 else "NET_UNREACHABLE"
                    raise DownloadError(code, True, path=f.path, error=str(e)) from None
                base = BACKOFF_S[min(tries - 1, len(BACKOFF_S) - 1)]
                await self._backoff(base * rng.uniform(0.8, 1.2), prog, token)


class DownloadQueue:
    """One download at a time per worker (06 §2.4); ``prioritize`` pauses the current one and keeps its
    breakpoint."""

    def __init__(self, d: Downloader) -> None:
        self.d = d
        self.queue: list[tuple[str, list[DlFile]]] = []
        self.log: list[tuple[str, str]] = []
        self.progress: dict[str, int] = {}
        self.current: tuple[str, CancelToken] | None = None
        self.results: dict[str, Result] = {}
        user_cb = d.on_progress

        def cb(ev: dict[str, Any]) -> None:
            self.progress[ev["op_id"]] = ev["done_bytes"]
            if user_cb:
                user_cb(ev)

        d.on_progress = cb

    def enqueue(self, op_id: str, files: list[DlFile]) -> None:
        self.queue.append((op_id, files))

    def prioritize(self, op_id: str) -> None:
        item = next((q for q in self.queue if q[0] == op_id), None)
        if item is None:
            return
        self.queue.remove(item)
        self.queue.insert(0, item)
        if self.current is not None and self.current[0] != op_id:
            self.current[1].pause()

    async def run(self) -> None:
        while self.queue:
            op_id, files = self.queue.pop(0)
            tok = CancelToken()
            self.current = (op_id, tok)
            self.log.append(("start", op_id))
            res = await self.d.download(op_id, files, tok)
            self.current = None
            self.results[op_id] = res
            self.log.append((res.status, op_id))
            if res.status == "paused":
                self.queue.insert(1 if self.queue else 0, (op_id, files))
